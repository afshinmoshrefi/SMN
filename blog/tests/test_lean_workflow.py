import json, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
import lean_research as lr
from engine_edition_workflow import unsupported_numbers
from subscription_writer import prepare_job, verify_job, run_job, load_json
from subscription_daily import DailyController
from tests.test_subscription_daily import FakeEdition, SHA

FAKE_CODEX = '''#!/usr/bin/env python3
import json, sys
a = sys.argv; out = a[a.index('--output-last-message') + 1]
json.dump({'title': 'x'}, open(out, 'w'))
open(out + '.argv', 'w').write(json.dumps(a))
if 'web_search="live"' in a:
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'web_search'}}))
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}}))
'''


def snapshot(*_):
    efforts = [{'reasoningEffort': e} for e in ('low', 'medium', 'high', 'xhigh')]
    catalog = [{'model': m, 'supportedReasoningEfforts': efforts} for m in ('gpt-6-astra', 'gpt-5.6-terra')]
    return {'utc': 'now', 'auth_type': 'chatgpt', 'plan_type': 'pro', 'astra_catalog': catalog[:1], 'model_catalog': catalog}


class WriterModels(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.codex = self.root / 'codex'
        self.codex.write_text(FAKE_CODEX); self.codex.chmod(0o755)
        self.schema = {'type': 'object', 'properties': {'title': {'type': 'string'}}, 'required': ['title'], 'additionalProperties': False}

    def job(self, name, **kw):
        now = datetime.now(timezone.utc)
        return prepare_job(self.root / 'jobs', name, 'evidence', self.schema, as_of=now.isoformat(),
                           valid_until=(now + timedelta(hours=1)).isoformat(), evidence_sha256='a' * 64, **kw)

    def test_terra_research_with_search_and_image(self):
        image = self.root / 'hero.jpg'; image.write_bytes(b'jpg')
        job = self.job('research', model='gpt-5.6-terra', effort='high', web_search=True, images=[image])
        self.assertEqual(verify_job(job)['images'], ['image-0.jpg'])
        with patch('subscription_writer.account_snapshot', snapshot):
            receipt = run_job(job, self.codex)
        argv = json.loads((job / 'output.json.argv').read_text())
        self.assertIn('web_search="live"', argv)
        self.assertEqual(argv[argv.index('-i') + 1], str(job / 'image-0.jpg'))
        self.assertEqual(argv[argv.index('--model') + 1], 'gpt-5.6-terra')
        self.assertEqual(receipt['web_searches'], 1)

    def test_search_is_still_refused_in_writing_jobs(self):
        job = self.job('write')
        (self.codex).write_text(FAKE_CODEX.replace("if 'web_search=\"live\"' in a:", 'if True:'))
        with patch('subscription_writer.account_snapshot', snapshot):
            with self.assertRaisesRegex(RuntimeError, 'Unexpected tools'):
                run_job(job, self.codex)

    def test_unknown_model_and_changed_image_rejected(self):
        with self.assertRaises(ValueError):
            self.job('bad', model='gpt-5.6-luna')
        image = self.root / 'hero.png'; image.write_bytes(b'png')
        job = self.job('img', images=[image])
        (job / 'image-0.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            verify_job(job)


class NumbersCheck(unittest.TestCase):
    article = {'title': 'Merck rallied in 14 of 15 windows', 'dek': 'Median 17.55%',
               'sections': [{'heading': 'Sales', 'paragraphs': [{'text': 'Sales were $16.6 billion; WINREVAIR $588 million; Q2 up 5%.'}]}],
               'takeaways': [{'text': 'It fell 14.14% below the start in 2010.'}]}
    evidence = {'stats': {'n': 15, 'wins': 14, 'median': 17.5512}, 'rows': [[2010, -14.14]],
                'excerpt': 'Worldwide sales were $16.63 billion, up 5%', 'records': [{'value': 0.588}]}

    def test_rounding_sign_and_scale_are_allowed(self):
        self.assertEqual(unsupported_numbers(self.article, self.evidence), [])

    def test_invented_or_calculated_number_flagged(self):
        a = json.loads(json.dumps(self.article))
        a['takeaways'][0]['text'] = 'That is 3.41 points above the broader median.'
        self.assertEqual(unsupported_numbers(a, self.evidence), ['3.41'])


PAGE = lr.normalize('Merck announces results. Worldwide sales were $16.6 billion, an increase of 5% from the second quarter of 2025. KEYTRUDA sales grew 5% to $8.4 billion. WINREVAIR sales were $588 million.')


def entry():
    return {'company': 'Merck & Co.', 'angle': 'KEYTRUDA and the October window', 'category': 'Stocks / Pharma',
            'question': 'Can growth broaden?', 'brief': 'b', 'hero_alt': 'a lab',
            'sources': [{'id': 'q2', 'title': 'Q2 results', 'url': 'https://www.merck.com/q2', 'date': '2026-08-04',
                         'excerpt': 'KEYTRUDA sales grew 5% to $8.4 billion. WINREVAIR sales were $588 million.'},
                        {'id': 'news', 'title': 'CHMP', 'url': 'https://example.com/n', 'date': '2026-09-18',
                         'excerpt': 'The committee adopted a positive opinion for the combination.'}],
            'chart': {'title': 't', 'subtitle': 's', 'question': 'q', 'note': 'n', 'unit': 'USD billions',
                      'rows': [{'record_id': 'k', 'label': 'KEYTRUDA'}, {'record_id': 'w', 'label': 'WINREVAIR'}]},
            'records': [{'id': 'k', 'value': 8.4, 'unit': 'USD billions', 'period': 'Q2 2026', 'status': 'reported', 'source_id': 'q2', 'locator': 'highlights'},
                        {'id': 'w', 'value': 0.588, 'unit': 'USD billions', 'period': 'Q2 2026', 'status': 'reported', 'source_id': 'q2', 'locator': 'highlights'}]}


class ResearchChecks(unittest.TestCase):
    def fetch(self, url):
        return PAGE if 'merck' in url else None  # the news page cannot be read

    def test_good_entry_becomes_sources_json(self):
        spec, problems = lr.verify(entry(), '2026-09-22', self.fetch)
        self.assertEqual(problems, [])
        self.assertEqual([s['fetch_verified'] for s in spec['sources']], [True, False])
        self.assertEqual(spec['chart'][0]['id'], 'business-context')
        self.assertTrue(all(s['max_derived_words'] == 200 for s in spec['sources']))

    def test_paraphrased_excerpt_future_date_and_wrong_value_held(self):
        e = entry()
        e['sources'][0]['excerpt'] = 'KEYTRUDA revenue rose five percent to about $8.4 billion in the quarter.'
        e['sources'][1]['date'] = '2026-09-30'
        e['records'][1]['value'] = 0.61
        _, problems = lr.verify(e, '2026-09-22', self.fetch)
        self.assertTrue(any('excerpt not found' in p for p in problems))
        self.assertTrue(any('after the edition' in p for p in problems))
        self.assertTrue(any('0.61' in p for p in problems))

    def test_nothing_readable_and_mixed_units_held(self):
        e = entry(); e['records'][1]['unit'] = 'USD millions'
        _, problems = lr.verify(e, '2026-09-22', lambda url: None)
        self.assertIn('no source could be read back from its page', problems)
        self.assertIn('chart mixes units', problems)


class RepairLoop(unittest.TestCase):
    def make(self):
        d = Path(tempfile.mkdtemp()); (d / 'production').mkdir()
        posts = [{'symbol': f'S{i}', 'published_date': '2026-09-10'} for i in range(6)]
        (d / 'production/posts.json').write_text(json.dumps(posts)); (d / 'sources.json').write_text('{}')
        (d / 'production-engine-export.json').write_text('{}')
        for p in posts: (d / 'production' / p['symbol']).mkdir()
        return d

    def edition(self, fail_first_review):
        class Repairing(FakeEdition):
            def finalize(self, s, stage='review'):
                if stage == 'review' and s in fail_first_review:
                    self.calls.append(('failed', s)); raise ValueError('Independent editorial review has not passed')
                super().finalize(s, stage)
            def code_checks(self, s): return {'passed': True}
            def repair(self, s, issues, stage): self.calls.append(('repair', s)); self.job(s, stage).mkdir(parents=True)
            def run(self, s, stage):
                super().run(s, stage)
                if stage == 'review' and s in fail_first_review:
                    j = self.job(s, stage)
                    (j / 'output.json').write_text(json.dumps({'checks': {'reader_value': {'passed': False, 'reason': 'thin'}},
                        'issues': [{'severity': 'major', 'problem': 'p'}, {'severity': 'minor', 'problem': 'q'}]}))
                    import hashlib
                    (j / 'receipt.json').write_text(json.dumps({'output_sha256': hashlib.sha256((j / 'output.json').read_bytes()).hexdigest()}))
        return Repairing

    def test_failed_review_gets_one_repair_and_second_review(self):
        d = self.make(); FakeEdition.calls = []; E = self.edition({'S2'})
        with patch('subscription_daily.Edition', E):
            out = DailyController(d, '2026-09-10', max_new_model_jobs=24, source_commit=SHA).run()
        self.assertEqual(out['status'], 'awaiting_visual_review')
        runs = [c for c in FakeEdition.calls if c[0] == 'run']
        self.assertIn(('run', 'repair', 'S2'), runs); self.assertIn(('run', 'review2', 'S2'), runs)
        self.assertEqual(len(runs), 14)
        issues = load_json(d / 'results/S2/repair-issues.json')
        self.assertEqual([i['problem'] for i in issues['issues']], ['p'])


if __name__ == '__main__':
    unittest.main()
