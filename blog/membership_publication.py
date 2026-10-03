"""Join the private reader registry to the existing, locked publication catalog."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
import uuid

from article_content_store import ContentStore, ContentError, canonical_path
from reader_app import asset_url
from visual_evidence import digest


def configured():
    return bool(os.environ.get('SMN_READER_PRIVATE_ROOT'))


def store():
    import article_index
    if not configured():
        raise ContentError('Reader storage is not configured')
    return ContentStore(os.environ['SMN_READER_PRIVATE_ROOT'], [article_index.NEWS_ROOT])


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def path_for(post):
    parsed = urlsplit(post.get('url', ''))
    if parsed.hostname not in {'smn-dev.trxstat.com', 'seasonalmarketnews.com', 'www.seasonalmarketnews.com'}:
        raise ContentError('Article origin is not an SMN origin')
    return canonical_path(parsed.path)


def _plain(value):
    from article_index import plain_text
    return ' '.join(plain_text(value or '').split()).replace('<', '').replace('>', '')


def opening_preview(post, raw):
    """An exact source excerpt is a supported preview mode, not generated copy."""
    from html.parser import HTMLParser
    class OpeningParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.paragraphs, self.current = [], [], None
        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == 'p':
                priority = 0 if ('lede' in attributes.get('class', '').split() or
                    'lead' in attributes.get('class', '').split() or
                    any(a.get('data-role') == 'opening' for _, a in self.stack)) else 1
                self.current = [priority, []]
            if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
                self.stack.append((tag, attributes))
        def handle_endtag(self, tag):
            if tag == 'p' and self.current:
                self.paragraphs.append(self.current)
                self.current = None
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    break
        def handle_data(self, value):
            if self.current and not any(t in {'script','style','sup'} for t, _ in self.stack):
                self.current[1].append(value)
    parser = OpeningParser()
    parser.feed(raw)
    opening = next((''.join(parts) for priority, parts in parser.paragraphs if priority == 0), None)
    text = _plain(opening) if opening else _plain(post.get('dek'))
    if not text:
        raise ContentError('Article has no usable opening; write and review a public preview first')
    # Keep complete source sentences, with a hard bound for archive anomalies.
    if len(text.split()) > 160:
        sentences = re.split(r'(?<=[.!?])\s+', text)
        selected = []
        for sentence in sentences:
            if len((' '.join(selected + [sentence])).split()) > 160:
                break
            selected.append(sentence)
        if not selected:
            raise ContentError('Opening is too long; write and review its public preview')
        text = ' '.join(selected)
    statement = lambda value: {'text': value, 'source_ids': [], 'article_refs': ['source-opening']}
    return {'content': {'headline': statement(_plain(post.get('title'))),
        'preview': [statement(text)],
        'full_article_value': statement('Read the complete article for the TradeWave study, charts, source evidence, and the limits of the historical comparison.'),
        'qualification': statement('Historical patterns describe past observations, not a forecast or a guarantee. The complete article explains the study and its limitations.'),
        'social': [], 'video': None},
        'provenance': {'mode': 'source_opening', 'source_html_sha256': sha(raw.encode()),
                       'source_original_url': post.get('production_original') or post.get('url')}}


class AssetRewriter:
    def __init__(self, post, revision, previous=None, source_root=None):
        import article_index
        self.post, self.revision = post, revision
        self.canonical = path_for(post)
        self.root = Path(source_root or article_index.NEWS_ROOT).resolve()
        self.previous = previous
        self.assets, self.public = {}, set()

    def _read(self, path):
        if any(p.is_symlink() for p in (path, *path.parents)) or self.root not in path.resolve().parents:
            raise ContentError('Article asset escaped its inventoried source root')
        if not path.is_file():
            raise ContentError('Article asset is missing: ' + path.name)
        return path.read_bytes()

    def url(self, value, public=False):
        value = html.unescape(value)
        if not value or value.startswith(('#', 'data:', 'mailto:', 'tel:')):
            return value
        absolute = urlsplit(urljoin(self.post['url'], value))
        if absolute.hostname not in {'smn-dev.trxstat.com', 'seasonalmarketnews.com', 'www.seasonalmarketnews.com'}:
            return value
        if absolute.path in {'/member/assets', '/member/public-assets'}:
            query = parse_qs(absolute.query)
            if (not self.previous or query.get('article') != [self.canonical]
                    or query.get('revision') != [self.previous['revision']]):
                raise ContentError('Protected asset does not belong to this article')
            name = query.get('name', [''])[0]
            asset = self.previous['assets'].get(name)
            if not asset:
                raise ContentError('Protected asset is not in this revision')
            data = store()._private_file(self.previous, 'assets/' + name).read_bytes()
            if sha(data) != asset['sha256']:
                raise ContentError('Protected asset changed')
        else:
            if not absolute.path.startswith(('/articles/', '/editions/', '/datasets/')) or absolute.path.endswith(('.html', '/')):
                return value
            if absolute.query or unquote(absolute.path) != absolute.path or '\\' in absolute.path:
                raise ContentError('Unsupported article asset URL')
            source = self.root / absolute.path.lstrip('/')
            data = self._read(source)
            name = 'assets/' + sha(absolute.path.encode())[:16] + '-' + source.name
        self.assets[name] = data
        if public:
            if Path(name).suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
                raise ContentError('Public hero must be a raster image')
            self.public.add(name)
        return asset_url(self.canonical, self.revision, name, public=public)

    def rewrite(self, raw):
        def tag(match):
            def attribute(attr):
                name, quote, value = attr.group(1), attr.group(2), attr.group(3)
                if name.lower() == 'srcset':
                    candidates = []
                    for part in html.unescape(value).split(','):
                        items = part.strip().split()
                        if items:
                            candidates.append(' '.join([self.url(items[0]), *items[1:]]))
                    new = ', '.join(candidates)
                else:
                    new = self.url(value)
                return name + '=' + quote + html.escape(new, quote=True) + quote
            return re.sub(r'(?<![\w-])(src|href|poster|srcset)\s*=\s*([\"\'])(.*?)\2', attribute, match.group(), flags=re.I | re.S)
        rendered = re.sub(r'<[A-Za-z][^>]*>', tag, raw)
        # Source-generated styles may refer to a native chart as a background.
        rendered = re.sub(r'url\(([\"\']?)([^)\"\']+)\1\)',
            lambda m: 'url("' + self.url(m.group(2)) + '")', rendered)
        # Legacy charts load their engine-owned JSON through a quoted fetch URL.
        rendered = re.sub(r'([\"\'])(/datasets/[^\"\']+)\1',
            lambda m: m.group(1) + self.url(m.group(2)) + m.group(1), rendered)
        return rendered


def prepare(post, raw, preview=None, reviewer=None, previous=None, source_root=None):
    preview = deepcopy(preview or opening_preview(post, raw))
    content_hash = digest(preview['content'])
    revision = 'r-' + sha((raw + content_hash).encode())[:32]
    rewriter = AssetRewriter(post, revision, previous, source_root)
    rendered = rewriter.rewrite(raw)
    hero = rewriter.url(post.get('hero_image', ''), public=True)
    # Asset hashes participate in revision identity; URL rewrites are derived.
    revision = 'r-' + digest({'source': sha(raw.encode()), 'content': content_hash,
                              'assets': {k: sha(v) for k, v in rewriter.assets.items()}})[:32]
    rewriter.revision = revision
    rendered = rewriter.rewrite(raw)
    hero = rewriter.url(post.get('hero_image', ''), public=True)
    preview['provenance'].update(revision=revision, source_html_sha256=sha(raw.encode()))
    approval = {'passed': True, 'derivative_sha256': digest(preview),
        'full_html_sha256': sha(rendered.encode()), 'reviewer': reviewer or 'source-opening-policy-v1',
        'reviewed_at': now(), 'scope': 'exact_source_excerpt' if reviewer is None else 'editor_approved_public_copy'}
    private_store = store()
    with private_store.database() as db:
        existing = db.execute('SELECT manifest FROM revisions WHERE path=? AND revision=?', (path_for(post), revision)).fetchone()
    if existing:
        saved = json.loads(existing[0])
        if (saved['full_html_sha256'] != sha(rendered.encode()) or saved['preview'] != preview
                or saved['assets'] != {name: {'sha256': sha(data), 'public': name in rewriter.public}
                                      for name, data in rewriter.assets.items()}):
            raise ContentError('Immutable article revision changed')
        approval = saved['approval']
    manifest = private_store.prepare_revision(path_for(post), revision, rendered, preview, approval,
        assets=rewriter.assets, public_asset_ids=rewriter.public,
        aliases=previous.get('aliases', []) if previous else [])
    updated = dict(post, path=str(store()._private_file(manifest, 'full.html')),
        hero_image=hero, membership_revision=revision, preview_mode=preview['provenance'].get('mode', 'summary'),
        dek=preview['content']['preview'][0]['text'], meta_description=preview['content']['preview'][0]['text'][:160])
    return updated, manifest


def _write(path, value):
    from article_index import _atomic_write_json
    _atomic_write_json(path, value)


def _catalog_row(posts, canonical):
    # Unrelated legacy origins are not part of this transaction.
    matches = [(i, p) for i, p in enumerate(posts) if urlsplit(p.get('url', '')).path == canonical]
    if len(matches) > 1:
        raise ContentError('Duplicate canonical catalog entries require administrator recovery')
    return matches[0] if matches else (None, None)


def _pointer(canonical):
    with store().database() as db:
        row = db.execute('SELECT revision FROM articles WHERE path=?', (canonical,)).fetchone()
    return row[0] if row else None


def _editor_draft(intent, db):
    import article_editor as editor
    context = intent['editor']
    draft = editor.read(db, context['draft_id'], context['owner'])
    if (draft['version'] != context['version'] or draft['base_fingerprint'] != context['base_fingerprint']
            or sha(editor.current(draft)['html'].encode()) != context['html_sha256']
            or draft['status'] not in {'idle', 'failed', 'published'}):
        raise ContentError('Interrupted publication conflicts with a newer editor draft')
    return draft


def _finish_editor(intent, db=None):
    context = intent.get('editor')
    if not context:
        return
    import article_editor as editor
    if db is None:
        with editor.database() as opened:
            _finish_editor(intent, opened)
        return
    draft = _editor_draft(intent, db)
    rendered, _ = source(intent['new_post'])
    fingerprint = editor.fingerprint(intent['new_post'], rendered)
    if draft['status'] == 'published' and draft.get('published_fingerprint') != fingerprint:
        raise ContentError('Published editor receipt does not match its private revision')
    draft.update(status='published', error='', published_fingerprint=fingerprint,
                 published_revision=intent['revision'])
    editor.save(db, draft)
    db.commit()  # The durable journal remains until this succeeds.


def _apply_intent(marker, intent, editor_db=None):
    import article_index
    if intent.get('editor'):
        if editor_db is None:
            import article_editor as editor
            with editor.database() as opened:
                return _apply_intent(marker, intent, opened)
        _editor_draft(intent, editor_db)
    posts = article_index.load_posts()
    index, actual = _catalog_row(posts, intent['canonical'])
    if actual not in (intent['old_post'], intent['new_post']):
        raise ContentError('Interrupted membership publication conflicts with a newer catalog change')
    held = article_index.load_unpublished() if 'slug' in intent else None
    if held is not None and held.get(intent['slug']) not in (intent['old_held'], intent['new_held']):
        raise ContentError('Interrupted publication conflicts with a newer held record')
    current = _pointer(intent['canonical'])
    if current not in (intent['expected_revision'], intent['revision']):
        raise ContentError('Interrupted publication conflicts with a newer private revision')
    private = store()
    with private.database() as db:
        applied = db.execute('SELECT payload FROM transactions WHERE id=?', (intent['transaction_id'],)).fetchone()
    if applied and current != intent['revision']:
        raise ContentError('Completed pointer transaction conflicts with a newer private revision')
    # Removal denies access before changing catalogs. Addition opens access only
    # after the held record is removed and its public catalog row is durable.
    if intent['revision'] is None:
        private.withdraw(intent['canonical'], intent['transaction_id'])
    if actual != intent['new_post']:
        if index is not None:
            if intent['new_post'] is None: posts.pop(index)
            else: posts[index] = intent['new_post']
        elif intent['new_post'] is not None:
            posts.append(intent['new_post'])
        article_index.save_posts(posts)
    if held is not None and held.get(intent['slug']) != intent['new_held']:
        if intent['new_held'] is None: held.pop(intent['slug'], None)
        else: held[intent['slug']] = intent['new_held']
        article_index.save_unpublished(held)
    if intent['revision'] is not None:
        private.activate_revision(intent['canonical'], intent['revision'],
                                  intent['transaction_id'], intent['expected_revision'])
    _finish_editor(intent, editor_db)
    _write(store().root / 'publishing-receipts' / marker.name, intent)
    marker.unlink()


def commit_state(old_post, new_post, slug=None, old_held=None, new_held=None,
                 transaction_id=None, editor_context=None, editor_db=None):
    """Caller holds posts_lock; journal only exact row states, preserving peers."""
    identity = new_post or old_post or (new_held or old_held)['post']
    canonical = path_for(identity)
    if not identity.get('membership_revision'):
        raise ContentError('A prepared private article revision is required')
    for value in (old_post, new_post, (old_held or {}).get('post'), (new_held or {}).get('post')):
        if value is not None and path_for(value) != canonical:
            raise ContentError('Publication cannot change the canonical article identity')
    import article_index
    _, actual = _catalog_row(article_index.load_posts(), canonical)
    if old_post is not None and actual is not None and dict(actual, slug=old_post.get('slug')) == old_post:
        old_post = actual
    transaction_id = transaction_id or uuid.uuid4().hex
    intent = {'transaction_id': transaction_id, 'canonical': canonical,
              'old_post': old_post, 'new_post': new_post,
              'expected_revision': (old_post or {}).get('membership_revision'),
              'revision': (new_post or {}).get('membership_revision')}
    if slug is not None:
        intent.update(slug=slug, old_held=old_held, new_held=new_held)
    if editor_context:
        intent['editor'] = editor_context
    marker = store().root / 'publishing' / (sha(transaction_id.encode()) + '.json')
    receipt = store().root / 'publishing-receipts' / marker.name
    if receipt.exists():
        if json.loads(receipt.read_text('utf-8')) != intent:
            raise ContentError('Publication transaction ID reused for another operation')
        return new_post
    if marker.exists():
        if json.loads(marker.read_text('utf-8')) != intent:
            raise ContentError('Publication transaction ID reused for another operation')
    else:
        if actual != old_post or _pointer(canonical) != intent['expected_revision']:
            raise ContentError('Article changed before publication; reload before retrying')
        if slug is not None and article_index.load_unpublished().get(slug) != old_held:
            raise ContentError('Held article changed before publication; reload before retrying')
        _write(marker, intent)
    _apply_intent(marker, intent, editor_db)
    return new_post


def commit_post(posts, old, updated, manifest, transaction_id=None, editor_context=None, editor_db=None):
    if manifest['revision'] != updated.get('membership_revision') or manifest['canonical_path'] != path_for(updated):
        raise ContentError('Prepared revision does not match its catalog article')
    result = commit_state(old, updated, transaction_id=transaction_id,
                          editor_context=editor_context, editor_db=editor_db)
    import article_index
    posts[:] = article_index.load_posts()
    return result


def recover():
    """Authenticated caller holds posts_lock. Finish exact interrupted writes."""
    for marker in sorted((store().root / 'publishing').glob('*.json')):
        _apply_intent(marker, json.loads(marker.read_text('utf-8')))


def source(post):
    manifest = store().resolve(path_for(post))
    if manifest['revision'] != post.get('membership_revision'):
        raise ContentError('Private article and catalog revisions differ; recover publication first')
    return store().read_revision(path_for(post), 'full'), manifest


def stored_source(post):
    """Verified immutable revision for an authenticated held-article editor."""
    private = store()
    with private.database() as db:
        manifest = private._manifest(db, path_for(post), post['membership_revision'])
    body = private._private_file(manifest, 'full.html').read_bytes()
    if sha(body) != manifest['full_html_sha256']:
        raise ContentError('Private article changed')
    return body.decode('utf-8'), manifest


def publish_edited(posts, post, updated, raw, actor, editor_context=None, editor_db=None):
    previous = store().resolve(path_for(post))
    # Editing the full text invalidates its generated summary. Publish a fresh,
    # exact opening until a new independent derivative review is complete.
    new, manifest = prepare(updated, raw, previous=previous)
    commit_post(posts, post, new, manifest, editor_context=editor_context, editor_db=editor_db)
    return new


def archive_source_allowed(path):
    root = store().root
    path = Path(path)
    return (path.is_file() and root in path.resolve().parents
            and not any(p.is_symlink() for p in (path, *path.parents)))


def withdraw(post, transaction_id=None):
    store().withdraw(path_for(post), transaction_id or uuid.uuid4().hex)


def restore(post, transaction_id=None):
    store().activate_revision(path_for(post), post['membership_revision'],
                              transaction_id or uuid.uuid4().hex, None)
