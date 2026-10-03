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
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, 'html.parser')
    opening = soup.select_one('section[data-role="opening"] p, .lede, .lead')
    if opening is None:
        opening = soup.select_one('.article-body > p, article > p, main > p')
    text = _plain(str(opening)) if opening else _plain(post.get('dek'))
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


def commit_post(posts, old, updated, manifest, transaction_id=None):
    """Caller holds posts_lock. A durable intent makes a crash recoverable."""
    import article_index
    transaction_id = transaction_id or uuid.uuid4().hex
    canonical = path_for(updated)
    previous_revision = (old or {}).get('membership_revision')
    marker = store().root / 'publishing' / (sha(transaction_id.encode()) + '.json')
    intent = {'transaction_id': transaction_id, 'canonical': canonical,
        'old_post': old, 'new_post': updated, 'expected_revision': previous_revision,
        'revision': manifest['revision']}
    _write(marker, intent)
    store().activate_revision(canonical, manifest['revision'], transaction_id, previous_revision)
    index = next((i for i, item in enumerate(posts) if path_for(item) == canonical), None)
    if index is None:
        posts.append(updated)
    else:
        posts[index] = updated
    article_index.save_posts(posts)
    marker.unlink()
    return updated


def recover():
    """Caller holds posts_lock. Finish only the exact interrupted catalog write."""
    import article_index
    for marker in sorted((store().root / 'publishing').glob('*.json')):
        intent = json.loads(marker.read_text('utf-8'))
        posts = article_index.load_posts()
        index = next((i for i, item in enumerate(posts) if path_for(item) == intent['canonical']), None)
        actual = posts[index] if index is not None else None
        if actual not in (intent['old_post'], intent['new_post']):
            raise ContentError('Interrupted membership publication conflicts with a newer catalog change')
        store().activate_revision(intent['canonical'], intent['revision'],
                                  intent['transaction_id'], intent['expected_revision'])
        if index is None:
            posts.append(intent['new_post'])
        else:
            posts[index] = intent['new_post']
        article_index.save_posts(posts)
        marker.unlink()


def source(post):
    manifest = store().resolve(path_for(post))
    if manifest['revision'] != post.get('membership_revision'):
        raise ContentError('Private article and catalog revisions differ; recover publication first')
    return store().read_revision(path_for(post), 'full'), manifest


def publish_edited(posts, post, updated, raw, actor):
    previous = store().resolve(path_for(post))
    # Editing the full text invalidates its generated summary. Publish a fresh,
    # exact opening until a new independent derivative review is complete.
    new, manifest = prepare(updated, raw, previous=previous)
    commit_post(posts, post, new, manifest)
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
