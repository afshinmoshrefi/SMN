import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import smn_primary_sources as primary
import smn_research


class PrimarySourcesTests(unittest.TestCase):
    def test_connection_pins_public_address_and_preserves_tls_hostname(self):
        conn = primary._PublicHTTPSConnection('example.com')
        conn._context = Mock()
        answer = [(2, 1, 6, '', ('93.184.216.34', 443))]
        with patch.object(primary.socket, 'getaddrinfo', return_value=answer), \
             patch.object(primary.socket, 'create_connection') as connect:
            conn.connect()
            self.assertEqual(connect.call_args.args[0], ('93.184.216.34', 443))
            self.assertEqual(conn._context.wrap_socket.call_args.kwargs['server_hostname'], 'example.com')

    def test_connection_rejects_rebinding_to_private_address(self):
        conn = primary._PublicHTTPSConnection('example.com')
        answer = [(2, 1, 6, '', ('127.0.0.1', 443))]
        with patch.object(primary.socket, 'getaddrinfo', return_value=answer), \
             patch.object(primary.socket, 'create_connection') as connect:
            with self.assertRaisesRegex(primary.Held, 'non-public'):
                conn.connect()
            connect.assert_not_called()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'production/ABC/audit').mkdir(parents=True)
        (self.root/'production/posts.json').write_text(json.dumps([
            {'symbol': 'ABC', 'title': 'ABC reports earnings', 'dek': 'Quarterly results',
             'published_date': '2026-09-26'}]))
        (self.root/'production/ABC/audit/research_context.txt').write_text(
            'URL: https://old-news.example/abc\nOld news')
        self.rows = [
            {'title': 'Results', 'url': 'https://abc.example/results', 'date': '2026-09-25',
             'publisher': 'ABC', 'reason': 'Quarterly results'},
            {'title': 'Filing', 'url': 'https://filings.example/abc', 'date': '2026-09-24',
             'publisher': 'Regulator', 'reason': 'Official filing'}]

    def test_capture_and_reuse_bound_evidence(self):
        prepared = []
        def prepare(*args, **kwargs):
            prepared.append((args, kwargs))
            job = self.root/'jobs'/args[3]
            job.mkdir(parents=True)
            (job/'output.json').write_text(json.dumps({'sources': self.rows}))
        def fetch(url):
            date = '2026-09-25' if 'results' in url else '2026-09-24'
            return url, ('Official Results and Filing ' + date +
                         ' records quarterly business results. ') * 12
        with patch.object(primary.smn_models, 'prepare', side_effect=prepare), \
             patch.object(primary, 'fetch_page', side_effect=fetch) as fetched:
            primary.collect(self.root, '2026-09-26', ['ABC'], {'research': {}}, {}, lambda job: None)
            primary.collect(self.root, '2026-09-26', ['ABC'], {'research': {}}, {}, lambda job: None)
        self.assertEqual(len(prepared), 1)
        self.assertTrue(prepared[0][1]['web_search'])
        self.assertIn('Search beyond the saved', prepared[0][0][4])
        self.assertEqual(fetched.call_count, 2)
        text = (self.root/'primary/ABC.txt').read_text()
        self.assertIn('URL: https://abc.example/results', text)
        self.assertNotIn('old-news.example', text)
        self.assertEqual(primary.sha256((self.root/'primary/ABC.txt').read_bytes()),
                         json.loads((self.root/'primary/ABC.receipt.json').read_text())['text_sha256'])
        entry = {'angle': 'QUARTERLY_RESULTS', 'sources': [
            {'id': 'old-news', 'url': 'https://old-news.example/abc', 'date': '2026-09-25'}],
            'chart': {'spec': {'unit': 'USD', 'rows': []}, 'records': []}}
        problems = smn_research.check(entry, self.root, '2026-09-26', 'ABC')
        self.assertIn('cite at least two captured primary sources', problems)
        entry['sources'] = [{'id': 'results', 'url': self.rows[0]['url'], 'date': '2026-09-25'},
                            {'id': 'filing', 'url': self.rows[1]['url'], 'date': '2026-09-24'}]
        self.assertNotIn('cite at least two captured primary sources',
                         smn_research.check(entry, self.root, '2026-09-26', 'ABC'))

    def test_missing_evidence_and_bad_discovery_hold(self):
        self.rows[0]['date'] = '2026-09-27'
        job = self.root/'jobs/ABC-20260926-primary-discovery'
        job.mkdir(parents=True)
        (job/'output.json').write_text(json.dumps({'sources': self.rows}))
        with self.assertRaisesRegex(primary.Held, 'dated after'):
            primary.collect(self.root, '2026-09-26', ['ABC'], {}, {}, lambda job: None)
        self.assertFalse((self.root/'primary/ABC.txt').exists())

    def test_existing_text_without_receipt_holds(self):
        (self.root/'primary').mkdir()
        (self.root/'primary/ABC.txt').write_text('unbound text')
        with self.assertRaisesRegex(primary.Held, 'incomplete'):
            primary.collect(self.root, '2026-09-26', ['ABC'], {}, {}, lambda job: None)

    def test_failed_fetch_does_not_publish_partial_evidence(self):
        job = self.root/'jobs/ABC-20260926-primary-discovery'
        job.mkdir(parents=True)
        (job/'output.json').write_text(json.dumps({'sources': self.rows}))
        prepared = []
        def prepare(*args, **kwargs):
            prepared.append(args[3])
            retry = self.root/'jobs'/args[3]
            retry.mkdir(parents=True)
            (retry/'output.json').write_text(json.dumps({'sources': [
                {'title': 'Update', 'url': 'https://abc.example/update', 'date': '2026-09-23',
                 'publisher': 'ABC', 'reason': 'Official update'},
                {'title': 'Notice', 'url': 'https://filings.example/notice', 'date': '2026-09-22',
                 'publisher': 'Regulator', 'reason': 'Official notice'}]}))
        with patch.object(primary.smn_models, 'prepare', side_effect=prepare), \
             patch.object(primary, 'fetch_page', side_effect=[
                ('https://abc.example/results', ('Results 2026-09-25. ' * 30)),
                primary.Held('second source unavailable'),
                primary.Held('retry source unavailable'),
                primary.Held('another retry unavailable')]):
            with self.assertRaisesRegex(primary.Held, 'fewer than two accessible'):
                primary.collect(self.root, '2026-09-26', ['ABC'], {}, {}, lambda job: None)
            with self.assertRaisesRegex(primary.Held, 'fewer than two accessible'):
                primary.collect(self.root, '2026-09-26', ['ABC'], {}, {}, lambda job: None)
        self.assertFalse((self.root/'primary/ABC.txt').exists())
        self.assertFalse((self.root/'primary/ABC.receipt.json').exists())
        cache = json.loads((self.root/'primary/ABC.fetch-cache.json').read_text())
        self.assertEqual(len(cache['pages']), 1)
        self.assertEqual(len(cache['failed_urls']), 3)
        self.assertEqual(prepared, ['ABC-20260926-primary-discovery-two'])

    def test_one_of_three_failed_urls_still_yields_two_pages(self):
        self.rows.append({'title': 'Update', 'url': 'https://abc.example/update',
                          'date': '2026-09-23', 'publisher': 'ABC', 'reason': 'Official update'})
        job = self.root/'jobs/ABC-20260926-primary-discovery'
        job.mkdir(parents=True)
        (job/'output.json').write_text(json.dumps({'sources': self.rows}))
        def fetch(url):
            if url == self.rows[0]['url']:
                raise primary.Held('HTTP 403')
            row = next(r for r in self.rows if r['url'] == url)
            return url, (row['title'] + ' ' + row['date'] + ' reported results. ') * 30
        with patch.object(primary, 'fetch_page', side_effect=fetch):
            primary.collect(self.root, '2026-09-26', ['ABC'], {}, {}, lambda job: None)
        proof = json.loads((self.root/'primary/ABC.receipt.json').read_text())
        self.assertEqual(len(proof['sources']), 2)
        self.assertEqual(proof['failed_urls'], {self.rows[0]['url']: 'HTTP 403'})

    def test_private_and_non_https_urls_rejected(self):
        with self.assertRaises(primary.Held):
            primary._safe_url('http://company.example/report')
        with self.assertRaises(primary.Held):
            primary._safe_url('https://127.0.0.1/report')
        with self.assertRaises(primary.Held):
            primary._safe_url('https://name:secret@company.example/report')
        with patch.object(primary.socket, 'getaddrinfo', return_value=[
                (None, None, None, None, ('10.0.0.1', 443))]):
            with self.assertRaisesRegex(primary.Held, 'non-public'):
                primary._safe_url('https://company.example/report')


if __name__ == '__main__':
    unittest.main()
