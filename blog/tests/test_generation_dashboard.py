import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import pub_dashboard
from tests.test_pub_dashboard import DashboardFixture


class GenerationDashboardTest(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.generation_root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.generation_root))
        p = patch.object(pub_dashboard, 'SMN_DAILY_STATE_ROOT', self.generation_root)
        p.start()
        self.addCleanup(p.stop)
        self.activation_marker = self.generation_root / 'activation.json'
        p = patch.object(pub_dashboard, 'SMN_SUBSCRIPTION_ACTIVATION', self.activation_marker)
        p.start()
        self.addCleanup(p.stop)

    def test_subscription_selection_and_disabled_api_profiles(self):
        response = self.client.get('/api/generation/settings')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()['data']
        self.assertEqual(data['mode'], 'legacy')
        self.assertTrue(data['selector_enabled'])
        self.assertEqual(data['selection']['profile'], 'claude')
        self.assertEqual([x['available'] for x in data['options']], [True, True, False, False])
        self.assertEqual(self.client.put('/api/generation/settings', json={'profile': 'chatgpt'}).status_code, 403)
        response = self.client.put('/api/generation/settings', json={'profile': 'chatgpt'},
                                   headers={'X-SMN-Dashboard': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/generation/settings').get_json()['data']['selection']['profile'],
                         'chatgpt')
        self.assertEqual(self.client.put('/api/generation/settings', json={'profile': 'claude_api'},
                                         headers={'X-SMN-Dashboard': '1'}).status_code, 400)
        self.assertEqual(self.client.put('/api/generation/settings', json={'profile': 'openai_api'},
                                         headers={'X-SMN-Dashboard': '1'}).status_code, 400)
        self.assertEqual(self.client.put('/api/generation/settings', json={'profile': 'claude'},
                                         headers={'X-SMN-Dashboard': '1', 'Origin': 'https://evil.test'}).status_code, 403)

    def test_fixed_subscription_primary_mode_from_activation_marker(self):
        self.activation_marker.write_text(json.dumps({'reader_provider': 'chatgpt',
            'source_commit': 'a' * 40, 'record': '/var/private/release-record'}))
        data = self.client.get('/api/generation/settings').get_json()['data']
        self.assertEqual(data['mode'], 'subscription-primary')
        self.assertFalse(data['selector_enabled'])
        self.assertEqual((data['selection']['profile'], data['selection']['comparison_profile'],
                          data['selection']['comparison_editions']), ('chatgpt', 'claude', 2))
        response = self.client.put('/api/generation/settings', json={'profile': 'claude'},
                                   headers={'X-SMN-Dashboard': '1'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error']['code'], 'fixed_subscription_workflow')
        self.assertFalse((self.generation_root / 'settings.json').exists())
        page = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('ChatGPT/Codex runner sign-in is pending verification', page)

    def test_fixed_mode_from_direct_comparison_state_includes_sanitized_last_run(self):
        (self.generation_root / 'comparison-state.json').write_text(json.dumps({
            'reader_provider': 'chatgpt', 'comparison_dates': ['2026-09-30'], 'target': 'dev'}))
        (self.generation_root / 'last-run.json').write_text(json.dumps({
            'date': '2026-09-30', 'utc': '2026-09-30T12:00:00Z', 'status': 'held', 'target': 'dev',
            'publish': False, 'exit_code': 2, 'reason': 'Auth at /root/private and sk-test-token',
            'result': {'providers': {'chatgpt': {'passed': True, 'status': 'ready'},
                                    'claude': {'passed': False, 'status': 'held', 'reason': 'timeout'}},
                       'path': '/secret/path'}}))
        data = self.client.get('/api/generation/settings').get_json()['data']
        self.assertEqual(data['mode'], 'subscription-primary')
        self.assertEqual(data['last_run']['providers']['chatgpt']['status'], 'ready')
        body = json.dumps(data)
        for secret in ('/root/private', 'sk-test-token', '/secret/path'):
            self.assertNotIn(secret, body)

    def test_detail_only_reads_discovered_run_under_state_root(self):
        root = self.generation_root / '2026-09-26'
        root.mkdir()
        summary = {'run_id': '2026-09-26', 'status': 'complete', 'jobs': [], 'totals': {}}
        discovery = [{'run_id': '2026-09-26', 'kind': 'daily', 'status': 'complete'}]
        with patch.object(pub_dashboard.smn_cost_report, 'discover_runs', return_value=discovery), \
             patch.object(pub_dashboard.smn_cost_report, 'summarize_run', return_value=summary) as summarize:
            self.assertEqual(self.client.get('/api/generation/runs').get_json()['data'], discovery)
            response = self.client.get('/api/generation/runs/2026-09-26?kind=daily')
            self.assertEqual(response.get_json()['data']['run_id'], summary['run_id'])
            self.assertEqual(response.get_json()['data']['previews'], {})
            summarize.assert_called_once_with(root.resolve())
            self.assertEqual(self.client.get('/api/generation/runs/missing?kind=daily').status_code, 404)
            self.assertEqual(self.client.get('/api/generation/runs/2026-09-26?kind=comparison').status_code, 404)
            self.assertEqual(self.client.get('/api/generation/runs/%2e%2e?kind=daily').status_code, 400)
            self.assertEqual(self.client.get('/api/generation/runs/2026-09-26?kind=other').status_code, 400)
            summarize.assert_called_once()

    def test_preview_requires_review_and_only_serves_article_or_image_assets(self):
        root = self.generation_root / '2026-09-26'
        result = root / 'results' / 'AAPL'
        assets = result / 'assets'
        assets.mkdir(parents=True)
        (result / 'article.html').write_text('<html>Reviewed</html>')
        (assets / 'chart.svg').write_text('<svg/>')
        (result / 'review-binding.json').write_text('{}')
        (result / 'visual-checks.json').write_text('{"passed": true}')
        (root / 'jobs').mkdir()
        discovery = [{'run_id': '2026-09-26', 'kind': 'daily'}]
        with patch.object(pub_dashboard.smn_cost_report, 'discover_runs', return_value=discovery):
            path = '/api/generation/previews/daily/2026-09-26/AAPL/'
            self.assertEqual(self.client.get(path + 'article.html').status_code, 404)
            (root / 'smn-daily-state.json').write_text('{"date":"2026-09-26","articles":{"AAPL":{"finalized":true,"review_stage":"review"}}}')
            review = root / 'jobs' / 'AAPL-20260926-review'
            review.mkdir()
            (review / 'output.json').write_text('{}')
            with patch.object(pub_dashboard.subscription_publication, 'reviewed', return_value={}):
                response = self.client.get(path + 'article.html')
                self.assertEqual(response.status_code, 200)
                self.assertIn('sandbox allow-scripts', response.headers['Content-Security-Policy'])
                self.assertNotIn('allow-same-origin', response.headers['Content-Security-Policy'])
                self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(self.client.get(path + 'assets/chart.svg').status_code, 200)
                self.assertEqual(self.client.get(path + 'review-binding.json').status_code, 404)
                self.assertEqual(self.client.get(path + 'assets/secret.json').status_code, 404)
                self.assertEqual(self.client.get(path.replace('/daily/', '/comparison/') + 'article.html').status_code, 404)

    def test_nested_subscription_primary_run_detail_and_preview(self):
        (self.generation_root / 'comparison-state.json').write_text(json.dumps({
            'reader_provider': 'chatgpt', 'comparison_dates': ['2026-09-26'], 'target': 'dev'}))
        root = self.generation_root / '2026-09-26' / 'chatgpt'
        result = root / 'results' / 'AAPL'
        assets = result / 'assets'
        assets.mkdir(parents=True)
        (result / 'article.html').write_text('<html>Reviewed</html>')
        (assets / 'chart.svg').write_text('<svg/>')
        (root / 'smn-daily-state.json').write_text(json.dumps({'date': '2026-09-26', 'articles': {
            'AAPL': {'finalized': True, 'review_stage': 'review'}}}))
        review = root / 'jobs' / 'AAPL-20260926-review'
        review.mkdir(parents=True)
        (review / 'output.json').write_text('{}')
        with patch.object(pub_dashboard.subscription_publication, 'reviewed', return_value={}):
            runs = self.client.get('/api/generation/runs').get_json()['data']
            self.assertIn(('2026-09-26-chatgpt', 'subscription-primary-chatgpt'),
                          [(r['run_id'], r['kind']) for r in runs])
            path = '/api/generation/runs/2026-09-26-chatgpt?kind=subscription-primary-chatgpt'
            detail = self.client.get(path).get_json()['data']
            self.assertEqual((detail['run_id'], detail['provider']), ('2026-09-26-chatgpt', 'chatgpt'))
            preview = self.client.get('/api/generation/previews/subscription-primary-chatgpt/2026-09-26-chatgpt/AAPL/article.html')
            self.assertEqual(preview.status_code, 200)
            self.assertIn('sandbox allow-scripts', preview.headers['Content-Security-Policy'])


if __name__ == '__main__':
    unittest.main()
