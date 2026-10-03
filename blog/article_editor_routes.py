"""Owner-only chat API; the existing dashboard guard supplies authentication/CSRF."""
import html
import base64
from html.parser import HTMLParser
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import tempfile
import re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from flask import Blueprint, Response, g, request

import article_editor as editor
import article_index
import dashboard_auth
import pin_store
import membership_publication as membership


def preview_assets(raw, post, private, manifest):
    """Inline inventoried raster bytes only in the owner-only rendered response."""
    canonical = membership.path_for(post)
    if manifest['canonical_path'] != canonical or manifest['revision'] != post['membership_revision']:
        raise membership.ContentError('Editor asset revision differs from the article')
    cache = {}

    def image(value):
        value = html.unescape(value)
        parsed = urlsplit(value)
        if value.startswith(('#', 'data:')):
            return value
        absolute = urlsplit(urljoin(post['url'], value))
        if absolute.scheme in {'http', 'https'} and absolute.hostname not in {'smn-dev.trxstat.com', 'seasonalmarketnews.com', 'www.seasonalmarketnews.com'}:
            return value  # Existing external references are not fetched or inventoried here.
        if (not value or any(ord(c) < 32 for c in value) or '\\' in value
                or unquote(parsed.path) != parsed.path
                or any(p in {'.', '..'} for p in parsed.path.split('/'))):
            raise membership.ContentError('Unsafe editor image URL')
        if (absolute.scheme not in {'http', 'https'} or absolute.username or absolute.password
                or absolute.hostname not in {'smn-dev.trxstat.com', 'seasonalmarketnews.com', 'www.seasonalmarketnews.com'}
                or absolute.port not in {None, 80 if absolute.scheme == 'http' else 443}
                or absolute.fragment):
            raise membership.ContentError('Editor images must belong to this private article')
        if absolute.path in {'/member/assets', '/member/public-assets'}:
            query = parse_qs(absolute.query, keep_blank_values=True)
            if (set(query) != {'article', 'revision', 'name'} or query['article'] != [canonical]
                    or query['revision'] != [manifest['revision']] or len(query['name']) != 1):
                raise membership.ContentError('Protected editor image belongs to another revision')
            name = query['name'][0]
        else:
            if (not absolute.path.startswith(('/articles/', '/editions/', '/datasets/'))
                    or (absolute.query and not re.fullmatch(r'v=[a-f0-9]{8,64}', absolute.query))):
                raise membership.ContentError('Unsupported editor image URL')
            name = 'assets/' + membership.sha(absolute.path.encode())[:16] + '-' + Path(absolute.path).name
        from article_content_store import asset_name
        asset_name(name)
        asset = manifest['assets'].get(name)
        if not asset:
            raise membership.ContentError('Editor image is not in this article revision')
        if name not in cache:
            data = private._private_file(manifest, 'assets/' + name).read_bytes()
            if membership.sha(data) != asset['sha256']:
                raise membership.ContentError('Private editor image changed')
            from PIL import Image
            types = {'.png': ('PNG', 'image/png'), '.jpg': ('JPEG', 'image/jpeg'),
                     '.jpeg': ('JPEG', 'image/jpeg'), '.webp': ('WEBP', 'image/webp')}
            expected = types.get(Path(name).suffix.lower())
            if expected is None:
                raise membership.ContentError('Editor images must be raster images')
            with Image.open(BytesIO(data)) as raster:
                if raster.format != expected[0]:
                    raise membership.ContentError('Editor image MIME differs from its bytes')
                raster.verify()
            cache[name] = (data, 'data:' + expected[1] + ';base64,' + base64.b64encode(data).decode('ascii'))
        data, uri = cache[name]
        if absolute.path not in {'/member/assets', '/member/public-assets'} and absolute.query:
            if not membership.sha(data).startswith(absolute.query[2:]):
                raise membership.ContentError('Editor image version differs from its bytes')
        return uri

    def srcset(value):
        remaining, candidates = html.unescape(value).strip(), []
        while remaining:
            token = re.match(r'data:[^\s]+|[^,\s]+', remaining)
            if not token:
                raise membership.ContentError('Unsupported editor image candidate')
            url = token.group()
            remaining = remaining[token.end():]
            descriptor = ''
            if url.endswith(','):
                url = url.rstrip(',')
            else:
                end = remaining.find(',')
                descriptor = (remaining if end < 0 else remaining[:end]).strip()
                remaining = '' if end < 0 else remaining[end + 1:]
            if descriptor and not re.fullmatch(r'(?:[1-9][0-9]*w|(?:[0-9]+(?:\.[0-9]+)?)x)', descriptor):
                raise membership.ContentError('Unsupported editor image candidate')
            candidates.append(image(url) + (' ' + descriptor if descriptor else ''))
            remaining = remaining.lstrip()
        return ', '.join(candidates)

    def css(value):
        def replace(match):
            original = match.group(2).strip()
            if Path(urlsplit(original).path).suffix.lower() in {'.woff', '.woff2', '.ttf', '.otf'}:
                return match.group()
            replacement = image(original)
            return match.group() if replacement == original else 'url("' + replacement + '")'
        return re.sub(r'url\(\s*([\"\']?)([^)\"\']+)\1\s*\)', replace, value, flags=re.I)

    class Images(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.changes, self.styles = [], 0
            self.offsets = [0]
            for line in raw.splitlines(keepends=True):
                self.offsets.append(self.offsets[-1] + len(line))

        def change(self, before, after):
            if before != after:
                line, column = self.getpos()
                start = self.offsets[line - 1] + column
                self.changes.append((start, start + len(before), after))

        def handle_starttag(self, tag, attrs):
            before = self.get_starttag_text()
            def attribute(match):
                key = match['name'].lower()
                value = match['value'] if match['quote'] else match['bare']
                if key == 'style':
                    new = css(html.unescape(value))
                elif key == 'srcset' and tag in {'img', 'source'}:
                    new = srcset(value)
                elif (key == 'src' and tag == 'img') or (key == 'poster' and tag == 'video'):
                    new = image(value)
                else:
                    return match.group()
                if new == html.unescape(value):
                    return match.group()
                quote = match['quote'] or '"'
                return match['lead'] + match['name'] + match['equal'] + quote + html.escape(new, quote=True) + quote
            after = re.sub(r'(?P<lead>\s)(?P<name>[\w:-]+)(?P<equal>\s*=\s*)(?:(?P<quote>[\"\'])(?P<value>.*?)(?P=quote)|(?P<bare>[^\s>]+))', attribute, before, flags=re.S)
            self.change(before, after)
            if tag == 'style': self.styles += 1

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag == 'style': self.styles = max(0, self.styles - 1)

        def handle_endtag(self, tag):
            if tag == 'style': self.styles = max(0, self.styles - 1)

        def handle_data(self, data):
            if self.styles: self.change(data, css(data))

    parser = Images()
    parser.feed(raw)
    for start, end, replacement in reversed(parser.changes):
        raw = raw[:start] + replacement + raw[end:]
    return raw


def register(app, dashboard):
    bp = Blueprint("article_editor", __name__)

    def owner():
        ident = getattr(g, "identity", {}) or {}
        configured = os.environ.get("SMN_EDITOR_OWNER_USER_ID", "").strip()
        if app.testing and not dashboard_auth.auth_required():
            return "test-owner"
        if not configured:
            raise editor.EditorError("Subscription editing has not been connected to its owner on this server.", 503)
        if ident.get("kind") != "admin" or str(ident.get("user_id")) != configured:
            raise editor.EditorError("This subscription editor is available only to its signed-in owner.", 403)
        return configured

    def state(draft):
        return dashboard.ok(editor.public(draft, request.script_root))

    def source(slug):
        posts = article_index.load_posts()
        post = next((p for p in posts if pin_store.article_slug(p) == slug), None)
        if post is None:
            raise editor.EditorError("Published article not found.", 404)
        path = Path(post.get("path") or "")
        protected = bool(post.get('membership_revision')) and membership.configured()
        if protected:
            try:
                raw, _ = membership.source(post)
            except membership.ContentError as exc:
                raise editor.EditorError(str(exc)) from None
            return posts, dict(post, slug=slug), path, raw
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(article_index.NEWS_ROOT.resolve()):
            raise editor.EditorError("The article source is unavailable.", 409)
        post = dict(post, slug=slug)
        return posts, post, path, path.read_text("utf-8")

    @bp.errorhandler(editor.EditorError)
    def failed(exc):
        return dashboard.fail("editor", str(exc), exc.status)

    @bp.after_request
    def private(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @bp.before_request
    def recover_interrupted_publish():
        owner()
        # A durable intent bridges SQLite and the two existing publication files.
        with article_index.posts_lock():
            if membership.configured():
                membership.recover()
            for marker in sorted((editor.root() / "publishing").glob("*.json")):
                recover_publish(marker, dashboard)

    @bp.post("/api/articles/<slug>/editor")
    def open_editor(slug):
        who = owner()
        with article_index.posts_lock():
            _, post, _, raw = source(slug)
        return state(editor.open_draft(who, post, raw))

    @bp.get("/api/editor/<ident>")
    def get_editor(ident):
        return state(editor.get(ident, owner()))

    @bp.post("/api/editor/<ident>/messages")
    def message(ident):
        who = owner()
        data = dashboard.body_json()
        return state(editor.start(ident, who, data.get("version"), data.get("provider"), data.get("message")))

    @bp.post("/api/editor/<ident>/restore")
    def restore(ident):
        who = owner()
        data = dashboard.body_json()
        return state(editor.restore(ident, who, data.get("version"), data.get("restore_version")))

    @bp.post("/api/editor/<ident>/discard")
    def discard(ident):
        who = owner()
        return state(editor.discard(ident, who, dashboard.body_json().get("version")))

    @bp.get("/api/editor/<ident>/preview")
    def preview(ident):
        draft = editor.get(ident, owner())
        requested = request.args.get("version", type=int)
        revision = next((r for r in draft["revisions"] if r["version"] == requested), editor.current(draft))
        raw = revision["html"]
        if membership.configured():
            try:
                with article_index.posts_lock():
                    post = draft['post']
                    legacy = not post.get('membership_revision')
                    if not post.get('membership_revision'):
                        post = next((p for p in article_index.load_posts()
                                     if pin_store.article_slug(p) == draft['slug']), {})
                        if not post:
                            raise membership.ContentError('Private editor article is unavailable')
                        if post.get('membership_revision') and membership.path_for(post) != membership.path_for(draft['post']):
                            raise membership.ContentError('Editor article identity changed')
                    if post.get('membership_revision'):
                        private = membership.store()
                        with private.database() as db:
                            manifest = private._manifest(db, membership.path_for(post), post['membership_revision'])
                        if legacy and manifest['preview']['provenance'].get('source_html_sha256') != membership.sha(draft['revisions'][0]['html'].encode()):
                            raise membership.ContentError('Private editor assets differ from the original draft source')
                        raw = preview_assets(raw, post, private, manifest)
            except (membership.ContentError, OSError, ValueError) as exc:
                raise editor.EditorError('Private editor images are unavailable: ' + str(exc), 409) from None
        url = draft["post"].get("url", "")
        if urlsplit(url).scheme in {"http", "https"}:
            base = '<base href="' + html.escape(url, quote=True) + '">'
            match = re.search(r"<head\b[^>]*>", raw, re.I)
            raw = raw[:match.end()] + base + raw[match.end():] if match else base + raw
        response = Response(raw, mimetype="text/html")
        response.headers["Content-Security-Policy"] = (
            "sandbox allow-scripts; default-src 'none'; img-src https: http: data:; "
            "style-src 'unsafe-inline' https: http:; font-src https: http: data:; "
            "script-src 'unsafe-inline' https: http:; connect-src 'none'; "
            "form-action 'none'; object-src 'none'; frame-ancestors 'self'")
        return response

    @bp.post("/api/editor/<ident>/publish")
    def publish(ident):
        who = owner()
        version = dashboard.body_json().get("version")
        with article_index.posts_lock(), editor.database() as db:
            draft = editor.read(db, ident, who)
            if draft["status"] == "published" and version == draft["version"]:
                return state(draft)  # Safe retry after a lost response.
            editor.check_version(draft, version)
            if not editor.public(draft)["dirty"]:
                raise editor.EditorError("There are no changes to publish.", 400)
            posts, post, path, original = source(draft["slug"])
            if editor.fingerprint(post, original) != draft["base_fingerprint"]:
                raise editor.EditorError("The live article changed since this draft began. Discard this draft and reopen the article to avoid overwriting another update.")
            revision = editor.current(draft)
            editor.validate(draft["revisions"][0]["html"], revision["html"])
            editor.validate_metadata(revision["title"])
            editor.validate_metadata(revision["dek"])
            previous = [dict(p) for p in posts]
            updated = dict(post, title=revision["title"], dek=revision["dek"], updated_date=pin_store.iso(pin_store.utcnow()))
            if post.get('membership_revision'):
                context = {'draft_id': ident, 'owner': who, 'version': version,
                           'base_fingerprint': draft['base_fingerprint'],
                           'html_sha256': membership.sha(revision['html'].encode())}
                updated = membership.publish_edited(posts, post, updated, revision['html'], dashboard.actor(),
                                                    editor_context=context, editor_db=db)
                draft = editor.read(db, ident, who)
                dashboard.sync_redis(updated)
                dashboard.audit('editor_publish', draft['slug'], dashboard.actor(), draft_id=ident, version=version, provider=draft['provider'])
                dashboard.queue_refresh(True)
                return state(draft)
            for index, item in enumerate(posts):
                if pin_store.article_slug(item) == draft["slug"]:
                    posts[index] = updated
            marker = editor.root() / "publishing" / (ident + ".json")
            editor.subscription_writer.save_json(marker, {"draft_id": ident, "owner": who,
                "old_post": post, "new_post": updated, "old_html": original, "new_html": revision["html"]})
            try:
                atomic_html(path, revision["html"])
                article_index.save_posts(posts)
                draft.update(status="published", error="", published_fingerprint=editor.fingerprint(updated, revision["html"]))
                editor.save(db, draft)
                db.commit()  # Include commit failure in the filesystem rollback boundary.
            except Exception:
                atomic_html(path, original)
                article_index.save_posts(previous, backup=False)
                marker.unlink(missing_ok=True)
                raise
            marker.unlink(missing_ok=True)
        dashboard.sync_redis(updated)
        dashboard.audit("editor_publish", draft["slug"], dashboard.actor(), draft_id=ident, version=version, provider=draft["provider"])
        dashboard.queue_refresh(True)
        return state(draft)

    app.register_blueprint(bp)


def recover_publish(marker, dashboard):
    intent = json.loads(marker.read_text("utf-8"))
    old, new = intent["old_post"], intent["new_post"]
    path = Path(old["path"])
    if path.is_symlink() or not path.resolve().is_relative_to(article_index.NEWS_ROOT.resolve()) or not path.is_file():
        raise editor.EditorError("An interrupted publication needs administrator recovery; its source moved.")
    posts = article_index.load_posts()
    index = next((i for i, p in enumerate(posts) if pin_store.article_slug(p) == old["slug"]), None)
    if index is None:
        raise editor.EditorError("An interrupted publication needs administrator recovery; its article was removed.")
    actual = dict(posts[index], slug=old["slug"])
    raw = path.read_text("utf-8")
    if actual not in (old, new) or raw not in (intent["old_html"], intent["new_html"]):
        raise editor.EditorError("An interrupted publication conflicts with a newer edit; administrator recovery is required.")
    complete = actual == new and raw == intent["new_html"]
    with editor.database() as db:
        draft = editor.read(db, intent["draft_id"], intent["owner"])
        if complete:
            draft.update(status="published", error="")
        else:
            atomic_html(path, intent["old_html"])
            posts[index] = old
            article_index.save_posts(posts)
            draft.update(status="failed", error="An interrupted publication was rolled back. Your draft is preserved.")
        editor.save(db, draft)
    dashboard.sync_redis(new if complete else old)
    dashboard.queue_refresh(True)
    marker.unlink()


def atomic_html(path, content):
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".editor-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
