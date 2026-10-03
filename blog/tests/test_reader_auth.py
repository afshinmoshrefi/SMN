from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

import requests
from reader_auth import ReaderAuth, ReaderError, return_path


class Transport:
    def __init__(self, now):
        self.now, self.calls, self.can_read, self.fail, self.other_subject = now, [], True, False, False
        self.identity = {'reader_authority': 'PRIVATE_AUTHORITY_SENTINEL', 'user_id': 7,
                         'workos_user_id': 'reader_7', 'workos_session_id': 'session_7', 'env': 'dev',
                         'expires_at': datetime.fromtimestamp(now + 3600, timezone.utc).isoformat()}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.fail: raise requests.Timeout()
        path = urlsplit(url).path
        if path.endswith('/authenticate'):
            data = {'user': {'id': 'reader_7'}, 'access_token': 'PRIVATE_WORKOS_TOKEN_SENTINEL'}
        elif path.endswith('/authorize'):
            data = self.identity.copy()
        else:
            entitlement = {**self.identity, 'can_read': self.can_read, 'reason': 'free_launch', 'access_ends_at': None,
                           'mode': 'free', 'offer_version': 'v1', 'trial_days': 0, 'grant_id': 1}
            entitlement.pop('reader_authority')
            if self.other_subject: entitlement['workos_user_id'] = 'other_subject'
            if path.endswith('/entitlement'): data = entitlement
            elif path.endswith('/account'):
                data = {'identity': {'user_id': 7, 'name': 'Reader', 'email': 'reader@example.com'},
                        'entitlement': entitlement, 'subscription': {'status': None},
                        'offer': {'mode': 'free', 'version': 'v1', 'intervals': []}}
            else: data = {'ok': True}
        class Response:
            status_code = 200
            def json(self): return data
        return Response()


class ReaderAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = 1_800_000_000
        self.config = {'CLIENT_ID': 'reader-client', 'CALLBACK_URL': 'https://smn-dev.trxstat.com/member/callback',
                       'AUTHORITY_URL': 'https://tw2.trxstat.com', 'SERVICE_KEY': 'PRIVATE_SERVICE_KEY', 'ENV': 'dev'}
        self.transport = Transport(self.now)
        self.auth = ReaderAuth(Path(self.tmp.name) / 'sessions', self.config, self.transport, lambda: self.now)

    def login(self):
        sid, url = self.auth.begin('/editions/day/article.html')
        state = parse_qs(urlsplit(url).query)['state'][0]
        new_sid, target = self.auth.complete(sid, {'state': state, 'code': 'code'})
        return new_sid

    def test_pkce_rotation_replay_and_cookie_contains_no_authority(self):
        sid, url = self.auth.begin('/editions/day/article.html')
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        new_sid, target = self.auth.complete(sid, {'state': query['state'][0], 'code': 'code'})
        self.assertNotEqual(sid, new_sid)
        self.assertNotIn('PRIVATE', new_sid)
        self.assertEqual(target, '/editions/day/article.html')
        with self.assertRaises(ReaderError): self.auth.complete(sid, {'state': query['state'][0], 'code': 'code'})
        self.assertEqual(self.transport.calls[1][2]['headers']['X-SMN-Reader-Key'], 'PRIVATE_SERVICE_KEY')

    def test_state_expiry_and_external_returns(self):
        for path in ('https://evil.com/a', '//evil.com/a', '/%2f%2fevil.com', '/member/logout', '/editions/../x.html'):
            self.assertEqual(return_path(path), '/')
        sid, url = self.auth.begin()
        self.now += 600
        with self.assertRaises(ReaderError): self.auth.complete(sid, {'state': parse_qs(urlsplit(url).query)['state'][0], 'code': 'code'})

    def test_entitlement_rechecked_and_cross_subject_rejected(self):
        sid = self.login()
        self.assertTrue(self.auth.entitlement(sid)[0]['can_read'])
        self.transport.can_read = False
        self.assertFalse(self.auth.entitlement(sid)[0]['can_read'])
        self.transport.other_subject = True
        with self.assertRaisesRegex(ReaderError, 'did not match'): self.auth.entitlement(sid)

    def test_outage_and_authority_expiry_fail_closed(self):
        sid = self.login(); self.transport.fail = True
        with self.assertRaises(ReaderError): self.auth.entitlement(sid)
        self.transport.fail = False; self.now += 3600
        self.assertFalse(self.auth.entitlement(sid)[0]['can_read'])

    def test_logout_requires_csrf_and_consumes_local_session(self):
        sid = self.login()
        with self.assertRaises(ReaderError): self.auth.logout(sid, 'wrong')
        self.assertIsNotNone(self.auth.session(sid))
        url = self.auth.logout(sid, self.auth.session(sid)['csrf'])
        self.assertIsNone(self.auth.session(sid))
        self.assertIn('session_id=session_7', url)

    def test_authorize_subject_and_environment_must_match_provider(self):
        for key, value in (('workos_user_id', 'wrong_user'), ('env', 'prod')):
            original = self.transport.identity[key]
            self.transport.identity[key] = value
            with self.assertRaises(ReaderError): self.login()
            self.transport.identity[key] = original

    def test_exclusive_access_end_boundary(self):
        sid = self.login()
        original = self.transport.request
        def at_boundary(method, url, **kwargs):
            response = original(method, url, **kwargs)
            if url.endswith('/entitlement'):
                result = response.json()
                result['access_ends_at'] = datetime.fromtimestamp(self.now, timezone.utc).isoformat()
            return response
        self.transport.request = at_boundary
        self.assertFalse(self.auth.entitlement(sid)[0]['can_read'])
