import json
from pathlib import Path
import tempfile
import types
import unittest

from flask import Flask, g, request
from membership_dashboard import register
from article_content_store import ContentError


class MembershipPanelTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.kind = 'admin'
        self.app = Flask(__name__); self.app.testing = True
        @self.app.before_request
        def verified_dashboard_identity():
            g.identity = {'kind': self.kind, 'user_id': 7}
        def settings(identity, **kwargs):
            self.calls.append(('settings', identity, kwargs)); return {'settings_version': 1, 'draft_id': 2}
        self.client_api = types.SimpleNamespace(request_settings=settings)
        def callback(name, result=None):
            def invoke(*args):
                self.calls.append((name, args)); return {'revision': 'r2'} if result is None else result
            return invoke
        self.handlers = {name: callback(name) for name in ('get_preview', 'save_preview', 'review_preview', 'generate_preview', 'create_job', 'get_job', 'job_action')}
        self.handlers['list_articles'] = callback('list_articles', [{'slug': 'test', 'title': 'Test article'}])
        self.handlers['list_jobs'] = callback('list_jobs', [])
        self.handlers['list_briefings'] = callback('list_briefings', [{'briefing_id':'2026-10-03-source','date':'2026-10-03','kind':'source_bundle'}])
        self.handlers['get_controls'] = callback('get_controls', {'all':False,'kinds':{}})
        self.handlers['set_controls'] = callback('set_controls', {'all':True,'kinds':{}})
        register(self.app, self.handlers, self.client_api)
        self.client = self.app.test_client()
        self.headers = {'X-SMN-Dashboard': '1', 'Origin': 'http://localhost'}

    def free_offer(self):
        return {'mode': 'free', 'currency': 'usd', 'monthly_amount': 0, 'annual_mode': 'explicit',
                'annual_amount': 0, 'annual_discount_bps': None, 'trial_days': 0, 'intervals': []}

    def test_service_api_open_and_reader_identities_never_get_admin_authority(self):
        for kind in ('service', 'api', 'open', 'reader'):
            self.kind = kind
            self.assertEqual(self.client.get('/api/membership/settings').status_code, 403)
            self.assertEqual(self.client.get('/api/promotion/jobs').status_code, 403)
            self.assertEqual(self.client.put('/api/membership/settings', json={'expected_version': 1, 'offer': self.free_offer()}, headers=self.headers).status_code, 403)
        self.assertEqual(self.calls, [])

    def test_settings_free_and_paid_integer_contract(self):
        response = self.client.put('/api/membership/settings', json={'expected_version': 1, 'offer': self.free_offer()}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[-1][2]['method'], 'POST')
        paid = dict(self.free_offer(), mode='paid', monthly_amount=1000, annual_mode='discount', annual_amount=None, annual_discount_bps=5000, trial_days=28, intervals=['month','year'])
        self.assertEqual(self.client.put('/api/membership/settings', json={'expected_version': 1, 'offer': paid}, headers=self.headers).status_code, 200)
        self.assertEqual(self.calls[-1][2]['body']['offer']['annual_amount'], None)
        # No local annual billing calculation replaces the central authority.
        for field, invalid in (('monthly_amount', 10.5), ('trial_days', 366), ('annual_discount_bps', 10000)):
            bad = dict(paid); bad[field] = invalid
            self.assertEqual(self.client.put('/api/membership/settings', json={'expected_version': 1, 'offer': bad}, headers=self.headers).status_code, 400)

    def test_mutation_csrf_origin_and_unknown_fields_rejected(self):
        body = {'expected_version': 1, 'offer': self.free_offer()}
        self.assertEqual(self.client.put('/api/membership/settings', json=body).status_code, 403)
        self.assertEqual(self.client.put('/api/membership/settings', json=body, headers={'X-SMN-Dashboard':'1','Origin':'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.put('/api/membership/settings', json=dict(body, api_key='secret'), headers=self.headers).status_code, 400)
        self.assertEqual(self.calls, [])

    def test_activate_preserves_expected_version_and_admin_identity(self):
        response = self.client.post('/api/membership/activate', json={'expected_version': 9, 'draft_id': 2}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.calls[-1][2]['activate'])
        self.assertEqual(self.calls[-1][2]['body']['expected_version'], 9)
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')

    def test_source_paths_and_credentials_cannot_enter_promotion_creation(self):
        for extra in ({'source_path':'/private/full.html'}, {'source_hash':'a'*64}, {'api_key':'secret'}):
            response = self.client.post('/api/promotion/jobs', json=dict(kind='article_video', slug='test', **extra), headers=self.headers)
            self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/promotion/jobs', json={'kind':'article_video','slug':'test'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[-1][1][1], '7')

    def test_review_exact_hash_and_nested_job_data(self):
        data = {'expected_version': 2, 'data': {'decision':'approved','payload_sha256':'a'*64}}
        response = self.client.post('/api/promotion/jobs/test/review', json=data, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[-1][1][2], data)
        data['data']['path'] = '/private/secret'
        self.assertEqual(self.client.post('/api/promotion/jobs/test/review', json=data, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post('/api/promotion/jobs/test/dispatch', json={'expected_version':2}, headers=self.headers).status_code, 400)

    def test_preview_schema_and_actor_are_server_owned(self):
        statement = {'text':'Public finding', 'source_ids':['history'], 'article_refs':['title']}
        content = {'headline':statement, 'preview':[statement], 'full_article_value':statement,
                   'qualification':statement, 'social':[], 'video':None}
        payload = {'expected_revision':'r1','content':content}
        response = self.client.put('/api/membership/articles/test/preview', json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[-1][1][2], '7')
        statement['source_ids'] = []  # Server-bound exact-source excerpts have no external source ID.
        self.assertEqual(self.client.put('/api/membership/articles/test/preview', json=payload, headers=self.headers).status_code, 200)
        statement['article_refs'] = []
        self.assertEqual(self.client.put('/api/membership/articles/test/preview', json=payload, headers=self.headers).status_code, 400)
        statement['article_refs'] = ['source-opening']
        content['cta'] = 'Lifetime guaranteed returns'
        self.assertEqual(self.client.put('/api/membership/articles/test/preview', json=payload, headers=self.headers).status_code, 400)

    def test_provider_errors_never_expose_private_paths_or_credentials(self):
        self.handlers['list_jobs'] = lambda: (_ for _ in ()).throw(RuntimeError('SECRET /private/path'))
        response = self.client.get('/api/promotion/jobs')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'SECRET', response.data)
        self.assertNotIn(b'/private/path', response.data)

    def test_dated_briefing_controls_and_import_keep_authority_on_server(self):
        self.assertEqual(self.client.get('/api/promotion/briefings').status_code, 200)
        self.assertEqual(self.client.get('/api/promotion/controls').status_code, 200)
        self.assertEqual(self.client.put('/api/promotion/controls',json={'scope':'daily_briefing','paused':True},headers=self.headers).status_code, 200)
        self.assertEqual(self.calls[-1][1], ({'scope':'daily_briefing','paused':True},'7'))
        for body in ({'scope':'all','paused':'false'},{'scope':'unknown','paused':True},{'scope':'all','paused':True,'path':'/private'}):
            self.assertEqual(self.client.put('/api/promotion/controls',json=body,headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post('/api/promotion/jobs',json={'kind':'daily_briefing'},headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post('/api/promotion/jobs',json={'kind':'daily_briefing','briefing_id':'2026-10-03-source'},headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post('/api/promotion/jobs/test/import',json={'expected_version':2},headers=self.headers).status_code, 200)
        self.assertEqual(self.calls[-1][1], ('test','import',{'expected_version':2},'7'))
        self.assertEqual(self.client.post('/api/promotion/jobs/test/import',json={'expected_version':2,'data':{'path':'/private'}},headers=self.headers).status_code, 400)

    def test_actionable_source_errors_are_bounded_and_sanitized(self):
        self.handlers['list_jobs'] = lambda: (_ for _ in ()).throw(ContentError('Requalify this article before generating promotion.'))
        response = self.client.get('/api/promotion/jobs')
        self.assertEqual(response.status_code, 409)
        self.assertIn('Requalify this article', response.get_json()['error']['message'])
        for message in ('Missing /var/tmp/private/key','C:\\Users\\private\\key','Bearer abc123','secret abc123','<script>private</script>'):
            self.handlers['list_jobs'] = lambda: (_ for _ in ()).throw(ContentError(message))
            self.assertNotIn(message, self.client.get('/api/promotion/jobs').get_json()['error']['message'])
