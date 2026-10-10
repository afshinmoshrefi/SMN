"""Private revision storage; posts.json remains the publication catalog authority."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
from urllib.parse import unquote, urlsplit

from visual_evidence import digest


class ContentError(ValueError):
    pass


# 'open' (default) is readable by anyone; 'members' needs reader entitlement.
ACCESS_CHOICES = ('open', 'members')


def is_open(manifest):
    return manifest.get('access', 'open') != 'members'


def canonical_path(value):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ContentError('Invalid article path')
    parsed = urlsplit(value)
    path = parsed.path
    if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not path.startswith('/')
            or path.startswith('//') or '\\' in path or unquote(path) != path
            or any(p in ('.', '..') for p in path.split('/')) or '\x00' in path):
        raise ContentError('An exact same-site canonical path is required')
    if not path.startswith(('/articles/', '/editions/')) or not path.endswith('.html'):
        raise ContentError('Unsupported article path')
    return path


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def asset_name(name):
    if not isinstance(name, str) or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ContentError('Invalid asset name')
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or '\\' in name or '%' in name or '\x00' in name
            or any(p in ('.', '..') for p in name.split('/')) or str(path) != name):
        raise ContentError('Unsafe asset name')
    return name


class ContentStore:
    def __init__(self, root, public_roots):
        if not public_roots:
            raise ContentError('Public roots must be explicitly inventoried')
        candidate = Path(root).absolute()
        if any(p.is_symlink() for p in (candidate, *candidate.parents)):
            raise ContentError('Private storage cannot use symlinks')
        self.root = candidate.resolve()
        for public in public_roots:
            public = Path(public).resolve()
            if self.root == public or public in self.root.parents or self.root in public.parents:
                raise ContentError('Private storage overlaps a public root')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.revisions = self.root / 'revisions'
        if self.revisions.is_symlink():
            raise ContentError('Revision storage cannot be a symlink')
        self.revisions.mkdir(exist_ok=True, mode=0o700)
        with self.database() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT OR IGNORE INTO settings VALUES ('enabled','0');
                CREATE TABLE IF NOT EXISTS articles (path TEXT PRIMARY KEY,revision TEXT);
                CREATE TABLE IF NOT EXISTS aliases (path TEXT PRIMARY KEY,canonical TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (path TEXT,revision TEXT,manifest TEXT NOT NULL,PRIMARY KEY(path,revision));
                CREATE TABLE IF NOT EXISTS transactions (id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            ''')

    @contextmanager
    def database(self):
        if (self.root / 'registry.sqlite3').is_symlink():
            raise ContentError('Private registry cannot be a symlink')
        db = sqlite3.connect(str(self.root / 'registry.sqlite3'), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ContentError('Explicit registry enabled flag required')
        with self.database() as db:
            db.execute("UPDATE settings SET value=? WHERE key='enabled'", ('1' if enabled else '0',))

    def enabled(self):
        with self.database() as db:
            return db.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()[0] == '1'

    def prepare_revision(self, canonical, revision, full_html, preview, approval, assets=None,
                         public_asset_ids=None, aliases=None, access='open'):
        canonical = canonical_path(canonical)
        if access not in ACCESS_CHOICES:
            raise ContentError('Article access must be open or members')
        if not isinstance(revision, str) or not revision.strip() or len(revision) > 200:
            raise ContentError('Explicit revision required')
        if not isinstance(full_html, str) or not full_html.strip():
            raise ContentError('Full HTML required')
        content = preview.get('content', {})
        statements = [content.get('headline', {}), *content.get('preview', []),
                      content.get('full_article_value', {}), content.get('qualification', {})]
        if (not content.get('preview') or any(not isinstance(s.get('text'), str) or not s['text'].strip()
                or '<' in s['text'] or '>' in s['text'] for s in statements)):
            raise ContentError('Reviewed plain public copy and qualification required')
        if not preview.get('provenance') or preview['provenance'].get('revision') != revision:
            raise ContentError('Derivative provenance required')
        body = full_html.encode('utf-8')
        if (approval.get('passed') is not True or approval.get('derivative_sha256') != digest(preview)
                or approval.get('full_html_sha256') != _sha(body) or not approval.get('reviewer')
                or not approval.get('reviewed_at')):
            raise ContentError('Exact public/full revision review approval required')
        assets, allowed = assets or {}, set(public_asset_ids or [])
        if allowed - assets.keys():
            raise ContentError('Public asset is absent')
        known_aliases = sorted(set(canonical_path(p) for p in aliases or []))
        key = _sha(canonical.encode())
        revision_key = _sha(revision.encode())
        relative = key + '/' + revision_key
        folder = self.revisions / relative
        manifest = {'canonical_path': canonical, 'revision': revision, 'storage': relative,
                    'full_html_sha256': _sha(body), 'preview': preview, 'approval': approval,
                    'aliases': known_aliases, 'assets': {}, 'public_asset_ids': sorted(allowed)}
        # Open (no key) serves the complete article to everyone, so existing
        # revision bytes stay identical. Only a members-only lock is recorded.
        if access == 'members':
            manifest['access'] = 'members'
        blobs = {}
        for name, value in assets.items():
            asset_name(name)
            if not isinstance(value, bytes) and any(p.is_symlink() for p in (Path(value), *Path(value).parents)):
                raise ContentError('Asset source symlink is unsafe')
            data = value if isinstance(value, bytes) else Path(value).read_bytes()
            if name in allowed and PurePosixPath(name).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
                raise ContentError('Public assets must be approved raster images')
            blobs[name] = data
            manifest['assets'][name] = {'sha256': _sha(data), 'public': name in allowed}
        with self.database() as db:
            old = db.execute('SELECT manifest FROM revisions WHERE path=? AND revision=?', (canonical, revision)).fetchone()
            if old:
                if json.loads(old[0]) != manifest:
                    raise ContentError('Immutable revision already exists with different bytes')
                return manifest
            folder.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if any(p.is_symlink() for p in (folder.parent, *folder.parent.parents)):
                raise ContentError('Revision parent cannot be a symlink')
            if folder.exists():
                raise ContentError('Uncommitted revision exists; inspect before recovery')
            with tempfile.TemporaryDirectory(dir=str(folder.parent), prefix='.prepare-') as temporary:
                stage = Path(temporary) / 'revision'
                stage.mkdir(mode=0o700)
                (stage / 'full.html').write_bytes(body)
                for name, data in blobs.items():
                    path = stage / 'assets' / name
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    path.write_bytes(data)
                os.replace(str(stage), str(folder))
            db.execute('INSERT INTO revisions VALUES (?,?,?)', (canonical, revision, json.dumps(manifest)))
        return manifest

    def activate_revision(self, canonical, revision, transaction_id, expected_revision=None):
        canonical = canonical_path(canonical)
        payload = {'action': 'activate', 'path': canonical, 'revision': revision, 'expected_revision': expected_revision}
        with self.database() as db:
            if self._transaction(db, transaction_id, payload):
                return self._manifest(db, canonical, revision)
            manifest = self._manifest(db, canonical, revision)
            if _sha(self._private_file(manifest, 'full.html').read_bytes()) != manifest['full_html_sha256']:
                raise ContentError('Prepared full article changed before activation')
            for name, asset in manifest['assets'].items():
                if _sha(self._private_file(manifest, 'assets/' + name).read_bytes()) != asset['sha256']:
                    raise ContentError('Prepared asset changed before activation')
            current = db.execute('SELECT revision FROM articles WHERE path=?', (canonical,)).fetchone()
            if (current[0] if current else None) != expected_revision:
                raise ContentError('Active revision changed')
            for alias in [canonical, *manifest['aliases']]:
                existing = db.execute('SELECT canonical FROM aliases WHERE path=?', (alias,)).fetchone()
                if existing and existing[0] != canonical:
                    raise ContentError('Alias belongs to another article')
                db.execute('INSERT OR REPLACE INTO aliases VALUES (?,?)', (alias, canonical))
            db.execute('INSERT OR REPLACE INTO articles VALUES (?,?)', (canonical, revision))
            db.execute('INSERT INTO transactions VALUES (?,?)', (transaction_id, json.dumps(payload)))
            return manifest

    @staticmethod
    def _transaction(db, ident, payload):
        if not isinstance(ident, str) or not ident.strip():
            raise ContentError('Root publication transaction ID required')
        row = db.execute('SELECT payload FROM transactions WHERE id=?', (ident,)).fetchone()
        if row and json.loads(row[0]) != payload:
            raise ContentError('Transaction ID reused for another operation')
        return bool(row)

    @staticmethod
    def _manifest(db, canonical, revision):
        row = db.execute('SELECT manifest FROM revisions WHERE path=? AND revision=?', (canonical, revision)).fetchone()
        if not row:
            raise ContentError('Revision not found')
        return json.loads(row[0])

    def resolve(self, path):
        path = canonical_path(path)
        with self.database() as db:
            if db.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()[0] != '1':
                raise ContentError('Reader registry is disabled')
            row = db.execute('SELECT a.path,a.revision FROM aliases l JOIN articles a ON a.path=l.canonical WHERE l.path=?', (path,)).fetchone()
            if not row or row['revision'] is None:
                raise ContentError('Article is not active')
            return self._manifest(db, row['path'], row['revision'])

    def read_revision(self, path, representation='public', revision=None):
        manifest = self.resolve(path)
        if revision is not None and manifest['revision'] != revision:
            raise ContentError('Requested revision is stale')
        if representation == 'public':
            return manifest['preview']['content']
        if representation != 'full':
            raise ContentError('Unknown representation')
        body = self._private_file(manifest, 'full.html').read_bytes()
        if _sha(body) != manifest['full_html_sha256']:
            raise ContentError('Private article changed')
        return body.decode('utf-8')

    def _private_file(self, manifest, relative):
        path = self.revisions / manifest['storage'] / relative
        if any(p.is_symlink() for p in (path, *path.parents)) or self.root not in path.resolve().parents:
            raise ContentError('Private file path escaped storage')
        return path

    def private_asset_path(self, path, revision, name, public=False):
        asset_name(name)
        manifest = self.resolve(path)
        if manifest['revision'] != revision or name not in manifest['assets']:
            raise ContentError('Asset is absent or stale')
        if public and name not in manifest['public_asset_ids']:
            raise ContentError('Asset is protected')
        file = self._private_file(manifest, 'assets/' + name)
        if _sha(file.read_bytes()) != manifest['assets'][name]['sha256']:
            raise ContentError('Private asset changed')
        return file

    def withdraw(self, path, transaction_id):
        path = canonical_path(path)
        payload = {'action': 'withdraw', 'path': path}
        with self.database() as db:
            if self._transaction(db, transaction_id, payload):
                return
            db.execute('UPDATE articles SET revision=NULL WHERE path=?', (path,))
            db.execute('INSERT INTO transactions VALUES (?,?)', (transaction_id, json.dumps(payload)))

    def inventory(self):
        with self.database() as db:
            return {'enabled': db.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()[0] == '1',
                    'articles': [dict(r) for r in db.execute('SELECT * FROM articles ORDER BY path')],
                    'aliases': [dict(r) for r in db.execute('SELECT * FROM aliases ORDER BY path')]}
