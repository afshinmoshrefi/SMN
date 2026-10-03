from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from article_content_store import ContentStore
from reader_app import create_app, asset_url
from reader_auth import ReaderAuth
from tests.test_article_content_store import stage, CANONICAL
from tests.test_reader_auth import Transport


class ReaderAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = ContentStore(root / 'private', [root / 'public'])
        stage(self.store); self.store.activate_revision(CANONICAL, 'r1', 'publish1'); self.store.set_enabled(True)
        self.now = 1_800_000_000
        config = {'CLIENT_ID': 'reader-client', 'CALLBACK_URL': 'https://smn-dev.trxstat.com/member/callback',
                  'AUTHORITY_URL': 'https://tw2.trxstat.com', 'SERVICE_KEY': 'secret', 'ENV': 'dev'}
        self.transport = Transport(self.now)
        self.auth = ReaderAuth(root / 'private' / 'sessions', config, self.transport, lambda: self.now)
        self.app = create_app(config, self.store, self.auth); self.app.testing = True
        self.client = self.app.test_client()

    def login(self):
        response = self.client.get('/member/login?return_to=' + CANONICAL)
        state = parse_qs(urlsplit(response.location).query)['state'][0]
        return self.client.get('/member/callback?state=' + state + '&code=code')

    def test_anonymous_dom_source_and_assets_exclude_protected_content(self):
        response = self.client.get(CANONICAL)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'PROTECTED_BODY_SENTINEL', response.data)
        self.assertIn(b'History is not a forecast.', response.data)
        self.assertNotIn(b'PRIVATE_AUTHORITY', response.data)
        denied = self.client.get(asset_url(CANONICAL, 'r1', 'chart.png'))
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn(b'PRIVATE_CHART_SENTINEL', denied.data)
        response = self.client.get(asset_url(CANONICAL, 'r1', 'hero.png', True))
        self.assertEqual(response.data, b'PUBLIC_HERO'); response.close()
        self.assertEqual(self.client.get(asset_url(CANONICAL, 'r1', 'chart.png', True)).status_code, 404)

    def test_signed_in_access_no_cache_and_revocation(self):
        response = self.login()
        self.assertEqual(urlsplit(response.location).path, CANONICAL)
        self.assertIn('Secure', response.headers['Set-Cookie'])
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertNotIn('PRIVATE', response.headers['Set-Cookie'])
        full = self.client.get(CANONICAL)
        self.assertIn(b'PROTECTED_BODY_SENTINEL', full.data)
        self.assertEqual(full.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(full.headers['Vary'], 'Cookie')
        response = self.client.get(asset_url(CANONICAL, 'r1', 'chart.png'))
        self.assertEqual(response.data, b'PRIVATE_CHART_SENTINEL'); response.close()
        self.transport.can_read = False
        self.assertNotIn(b'PROTECTED_BODY_SENTINEL', self.client.get(CANONICAL).data)

    def test_provider_outage_keeps_public_and_denies_private(self):
        self.login(); self.transport.fail = True
        response = self.client.get(CANONICAL)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'temporarily unavailable', response.data)
        self.assertNotIn(b'PROTECTED_BODY_SENTINEL', response.data)
        self.assertEqual(self.client.get(asset_url(CANONICAL, 'r1', 'chart.png')).status_code, 503)

    def test_unknown_legacy_urls_and_disabled_registry_never_fall_back(self):
        for path in ('/articles/unknown.html', '/editions/old/raw.json', '/static/full.html', '/api/editor/secret'):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.store.set_enabled(False)
        self.assertEqual(self.client.get(CANONICAL).status_code, 404)
        self.assertEqual(self.client.get('/member/login').status_code, 503)

    def test_account_excludes_authority_and_mutations_require_csrf(self):
        self.login()
        account = self.client.get('/member/account')
        self.assertEqual(account.status_code, 200)
        self.assertNotIn(b'PRIVATE_AUTHORITY_SENTINEL', account.data)
        self.assertNotIn(b'PRIVATE_WORKOS_TOKEN_SENTINEL', account.data)
        self.assertEqual(self.client.post('/member/logout').status_code, 403)
        self.assertEqual(self.client.post('/member/cancel', json={}).status_code, 403)
