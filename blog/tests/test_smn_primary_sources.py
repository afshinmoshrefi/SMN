import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import smn_primary_sources as primary
import smn_research


class PrimarySourcesTests(unittest.TestCase):
    def extracted(self,html):
        parser=primary._Text();parser.feed(html)
        return parser.text()

    def test_large_navigation_does_not_displace_press_release(self):
        page=('<html><head><meta property="article:published_time" content="2026-06-24">'
              '<title>Page title</title></head><nav>'+('Memory products navigation '*1000)+
              '</nav><div>'+('Secondary links '*1000)+'</div><main><article>'
              '<header><h1>Micron Reports Record Results</h1><time datetime="2026-06-24">June 24, 2026</time></header>'
              '<p>Reported quarterly revenue is the company-owned financial value.</p>'
              '</article></main><footer>Footer links</footer></html>')
        text=self.extracted(page)[:primary.MAX_TEXT_CHARS]
        self.assertIn('Micron Reports Record Results',text)
        self.assertIn('2026-06-24',text)
        self.assertIn('quarterly revenue',text)
        self.assertNotIn('navigation',text);self.assertNotIn('Secondary links',text)

    def test_sec_hidden_inline_xbrl_does_not_displace_visible_report(self):
        page=('<html><head><title>Filing</title></head><body><ix:header><ix:hidden>'+
              ('us-gaap:DebtCurrent xbrli:shares '*1000)+'</ix:hidden></ix:header>'
              '<div hidden>Hidden attribute</div><div aria-hidden="true">Hidden aria</div>'
              '<div style="display: none">Hidden CSS<span>Nested hidden</span></div>'
              '<script>window.unreadable()</script><style>unreadable css</style>'
              '<h1>Quarterly report</h1><p>June 24, 2026</p>'
              '<p>Revenue and earnings are reported in the visible financial tables.</p></body></html>')
        text=self.extracted(page)[:primary.MAX_TEXT_CHARS]
        self.assertIn('Quarterly report',text);self.assertIn('Revenue and earnings',text)
        for excluded in ('us-gaap','xbrli','Hidden','unreadable'):self.assertNotIn(excluded,text)

    def test_role_main_and_article_fallback_preserve_visible_dates(self):
        self.assertEqual(self.extracted('<div>Navigation</div><div role="main"><p>Visible report</p></div>').strip(),'Visible report')
        text=self.extracted('<div>Navigation</div><article><p>September 30, 2026</p><p>Visible report</p></article>')
        self.assertIn('September 30, 2026',text);self.assertNotIn('Navigation',text)

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

    def test_stale_source_is_dropped_not_held(self):
        old = {'title': 'Old results', 'url': 'https://abc.example/q1', 'date': '2026-05-07',
               'publisher': 'ABC', 'reason': 'Older quarter'}
        future = {**old, 'url': 'https://abc.example/next', 'date': '2026-09-27'}
        kept = primary._validate_sources(self.rows + [old, future], '2026-09-26')
        self.assertEqual(kept, self.rows)
        with self.assertRaisesRegex(primary.Held, 'blank field'):
            primary._validate_sources(self.rows + [{**old, 'title': ' '}], '2026-09-26')

    def test_only_stale_sources_trigger_the_retry(self):
        stale = [{**r, 'date': '2026-01-02'} for r in self.rows]
        fresh = [{**r, 'url': r['url'] + '/new'} for r in self.rows]
        def prepare(*args, **kwargs):
            job = self.root/'jobs'/args[3]
            job.mkdir(parents=True)
            rows = fresh if args[3].endswith('-two') else stale
            (job/'output.json').write_text(json.dumps({'sources': rows}))
        def fetch(url):
            return url, ('Official Results and Filing 2026-09-25 2026-09-24 records quarterly results. ') * 12
        with patch.object(primary.smn_models, 'prepare', side_effect=prepare), \
             patch.object(primary, 'fetch_page', side_effect=fetch) as fetched:
            primary.collect(self.root, '2026-09-26', ['ABC'], {'research': {}}, {}, lambda job: None)
        self.assertEqual(sorted(c.args[0] for c in fetched.call_args_list), sorted(r['url'] for r in fresh))

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
        # An impossible date drops that page (Sept 30 fix); with one page left and a retry that
        # also fails, the subject still holds and no partial evidence is written.
        self.rows[0]['date'] = '2026-09-31'
        for name in ('ABC-20260926-primary-discovery', 'ABC-20260926-primary-discovery-two'):
            job = self.root/'jobs'/name
            job.mkdir(parents=True)
            (job/'output.json').write_text(json.dumps({'sources': self.rows}))
        fetch = lambda url: (url, 'Filing ' + self.rows[1]['date'] + ' reported results. ' * 30)
        with patch.object(primary, 'fetch_page', side_effect=fetch), \
             self.assertRaisesRegex(primary.Held, 'fewer than two accessible primary pages'):
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

    def test_sec_exhibit_date_is_read_from_the_filing_index(self):
        # Sept 28 MCD: SEC exhibits do not print their date; EDGAR's index does.
        sec = 'https://www.sec.gov/Archives/edgar/data/63908/000006390826000076/exhibit991.htm'
        self.rows = [dict(self.rows[0], url=sec, date='2026-09-23', title='Investor update'),
                     dict(self.rows[0], url=sec.replace('0076', '0073'), date='2026-08-04', title='Quarterly report')]
        cache, path = {'pages': {}, 'failed_urls': {}}, self.root/'cache.json'
        def fetch(url):
            if url.endswith('-index.htm'):
                return url, 'Filing Date %s Accepted' % ('2026-09-23' if '0076' in url else '2026-08-07')
            return url, 'Investor update quarterly report with reported results. ' * 30
        with patch.object(primary, 'fetch_page', side_effect=fetch):
            primary._capture_rows(self.rows, cache, path)
        self.assertEqual(list(cache['pages']), [sec])
        self.assertIn('published date not visible', cache['failed_urls'][sec.replace('0076', '0073')])

    def test_date_with_a_note_is_read_and_undated_rows_are_dropped(self):
        # Sept 30 MU: a date followed by a note held the whole article.
        rows = [dict(self.rows[0], date='2026-09-24 (period end; exact filing date not verified)'),
                dict(self.rows[1], date='not stated')]
        kept = primary._validate_sources(rows, '2026-09-26')
        self.assertEqual([r['date'] for r in kept], ['2026-09-24'])

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
