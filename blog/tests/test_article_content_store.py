import hashlib
from pathlib import Path
import tempfile
import unittest

from article_content_store import ContentStore, ContentError, is_open
from visual_evidence import digest


CANONICAL = '/editions/2026-10-03/test/article.html'
FULL = '<!doctype html><title>Full study</title><p>PROTECTED_BODY_SENTINEL</p>'


# Fixtures lock with 'members' so the gated-reader tests keep exercising the gate.
def stage(store, revision='r1', aliases=None, access='members'):
    statement = lambda text: {'text': text, 'source_ids': ['engine'], 'article_refs': ['title']}
    preview = {'provenance': {'article_id': 'https://seasonalmarketnews.com/articles/original.html', 'revision': revision},
               'content': {'headline': statement('Useful public finding'), 'preview': [statement('Public evidence and its limits.')],
                           'qualification': statement('History is not a forecast.'), 'full_article_value': statement('Explore the risk evidence.')}}
    approval = {'passed': True, 'reviewer': 'test-only', 'reviewed_at': '2026-10-03T00:00:00Z',
                'derivative_sha256': digest(preview), 'full_html_sha256': hashlib.sha256(FULL.encode()).hexdigest()}
    return store.prepare_revision(CANONICAL, revision, FULL, preview, approval,
        assets={'chart.png': b'PRIVATE_CHART_SENTINEL', 'hero.png': b'PUBLIC_HERO'}, public_asset_ids=['hero.png'], aliases=aliases,
        access=access)


class ContentStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = ContentStore(self.root / 'private', [self.root / 'public'])

    def test_private_public_root_overlap_rejected(self):
        for private in (self.root / 'public', self.root / 'public' / 'private', self.root):
            with self.assertRaises(ContentError):
                ContentStore(private, [self.root / 'public'])

    def test_prepared_and_disabled_articles_unreachable(self):
        stage(self.store)
        with self.assertRaises(ContentError): self.store.resolve(CANONICAL)
        self.store.activate_revision(CANONICAL, 'r1', 'publish1')
        with self.assertRaises(ContentError): self.store.resolve(CANONICAL)
        self.store.set_enabled(True)
        self.assertNotIn('PROTECTED', str(self.store.read_revision(CANONICAL)))
        self.assertIn('PROTECTED', self.store.read_revision(CANONICAL, 'full'))

    def test_activate_idempotence_and_compare_and_swap(self):
        stage(self.store)
        self.store.activate_revision(CANONICAL, 'r1', 'publish1')
        self.store.activate_revision(CANONICAL, 'r1', 'publish1')
        stage(self.store, 'r2')
        with self.assertRaises(ContentError): self.store.activate_revision(CANONICAL, 'r2', 'publish2')
        with self.assertRaises(ContentError): self.store.activate_revision(CANONICAL, 'r2', 'publish1', 'r1')
        self.store.activate_revision(CANONICAL, 'r2', 'publish2', 'r1')
        self.store.set_enabled(True)
        self.assertEqual(self.store.resolve(CANONICAL)['revision'], 'r2')

    def test_public_asset_allowlist_and_stale_revision(self):
        stage(self.store); self.store.activate_revision(CANONICAL, 'r1', 'publish1'); self.store.set_enabled(True)
        with self.assertRaises(ContentError): self.store.private_asset_path(CANONICAL, 'r1', 'chart.png', public=True)
        self.assertEqual(self.store.private_asset_path(CANONICAL, 'r1', 'hero.png', public=True).read_bytes(), b'PUBLIC_HERO')
        for name in ('../full.html', '/full.html', 'a\\b', '%2e%2e/full.html'):
            with self.assertRaises(ContentError): self.store.private_asset_path(CANONICAL, 'r1', name)
        with self.assertRaises(ContentError): self.store.private_asset_path(CANONICAL, 'old', 'chart.png')

    def test_immutable_bytes_and_withdrawal(self):
        manifest = stage(self.store); stage(self.store)
        self.store.activate_revision(CANONICAL, 'r1', 'publish1'); self.store.set_enabled(True)
        full_path = self.store.revisions / manifest['storage'] / 'full.html'
        full_path.write_text('Changed')
        with self.assertRaises(ContentError): self.store.read_revision(CANONICAL, 'full')
        self.store.withdraw(CANONICAL, 'withdraw1'); self.store.withdraw(CANONICAL, 'withdraw1')
        with self.assertRaises(ContentError): self.store.resolve(CANONICAL)

    def test_only_explicit_alias_resolves(self):
        alias = '/articles/verified-equivalent.html'
        stage(self.store, aliases=[alias]); self.store.activate_revision(CANONICAL, 'r1', 'publish1'); self.store.set_enabled(True)
        self.assertEqual(self.store.resolve(alias)['canonical_path'], CANONICAL)
        with self.assertRaises(ContentError): self.store.resolve('/articles/original.html')

    def test_open_by_default_and_members_lock_is_explicit(self):
        opened = stage(self.store, 'r-open', access='open')
        self.assertNotIn('access', opened)
        self.assertTrue(is_open(opened))
        locked = stage(self.store)
        self.assertEqual(locked['access'], 'members')
        self.assertFalse(is_open(locked))
        with self.assertRaises(ContentError): stage(self.store, 'r-bad', access='public')

    def test_changed_review_approval_rejected(self):
        manifest = stage(self.store)
        with self.assertRaises(ContentError):
            self.store.prepare_revision(CANONICAL, 'r2', FULL + 'changed', manifest['preview'], manifest['approval'])

    def test_changed_prepared_bytes_cannot_activate(self):
        manifest = stage(self.store)
        (self.store.revisions / manifest['storage'] / 'assets' / 'chart.png').write_bytes(b'changed')
        with self.assertRaises(ContentError): self.store.activate_revision(CANONICAL, 'r1', 'publish1')
        self.assertEqual(self.store.inventory()['articles'], [])

    def test_inventory_does_not_generate_or_activate(self):
        from article_content_migrate import inventory_public
        public = self.root / 'public'; public.mkdir()
        (public / 'old.html').write_text(FULL)
        report = inventory_public([public])
        self.assertEqual(report['files'][0]['url_path'], '/old.html')
        self.assertNotIn('PROTECTED', str(report))
        self.assertFalse(report['publish'])
