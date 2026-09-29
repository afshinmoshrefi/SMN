import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import smn_models
import smn_research


def entry(value, quote):
    def record(rid):
        return {'id': rid, 'value': value, 'unit': 'percent change', 'period': 'Q3', 'status': 'reported',
                'source_id': 'reuters-q3', 'locator': 'p1', 'quote': quote}
    excerpt = 'HP said its total PC shipments dropped 16% for the quarter as commercial demand stayed weak.'
    return {'angle': 'PC_SHIPMENTS_AND_GUIDANCE', 'company': 'HP Inc.', 'category': 'Stocks / Hardware',
            'question': 'Does the PC slump change the seasonal picture for HP?', 'brief': 'b' * 220,
            'hero_alt': 'Laptops on a warehouse line',
            'sources': [{'id': 'reuters-q3', 'title': 'T', 'url': 'https://example.com/a', 'date': '2026-09-01',
                         'excerpt': excerpt},
                        {'id': 'reuters-q3b', 'title': 'T2', 'url': 'https://example.com/a', 'date': '2026-09-01',
                         'excerpt': excerpt}],
            'chart': {'spec': {'unit': 'percent change', 'rows': [{'record_id': 'biz-0', 'label': 'PC shipments'},
                                                                   {'record_id': 'biz-1', 'label': 'PC shipments'}]},
                      'records': [record('biz-0'), record('biz-1')]}}


class ResearchCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ctx = Path(self.tmp.name)/'production/HPQ/audit'
        ctx.mkdir(parents=True)
        (ctx/'research_context.txt').write_text('URL: https://example.com/a\nIts total PC shipments dropped 16% for the quarter')

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, value, quote='Its total PC shipments dropped 16% for the quarter'):
        return smn_research.check(entry(value, quote), self.tmp.name, '2026-09-23', 'HPQ')

    def test_decline_as_negative_value_passes(self):
        self.assertEqual(self.check(-16), [])

    def test_decline_as_positive_value_is_rejected(self):
        self.assertTrue(any('decrease but the value is positive' in p for p in self.check(16)))

    def test_invented_quote_and_url_are_rejected(self):
        e = entry(-16, 'shipments dropped 16% again')
        e['sources'][0]['url'] = 'https://invented.example/b'
        problems = smn_research.check(e, self.tmp.name, '2026-09-23', 'HPQ')
        self.assertTrue(any('not verbatim' in p for p in problems))
        self.assertTrue(any('URL' in p for p in problems))

    def test_value_missing_from_quote_is_rejected(self):
        self.assertTrue(any('does not appear' in p for p in self.check(-61)))


    def test_length_rules_are_checked_in_code_with_exact_messages(self):
        # Sept 28-29: schema minLength misses burned all CLI retries without saying what was wrong.
        self.assertNotIn('minLength', json.dumps(smn_research.SCHEMA))
        e = entry(-16, 'Its total PC shipments dropped 16% for the quarter')
        e.update(company='HP', category='Stocks', question='Short?', brief='too short', hero_alt='x')
        e['chart']['records'].pop()
        e['sources'][1]['excerpt'] = 'x'
        problems = smn_research.check(e, self.tmp.name, '2026-09-23', 'HPQ')
        self.assertIn('brief is 9 characters; it needs at least 200', problems)
        self.assertTrue(any(p.startswith('chart.records has 1 items') for p in problems))
        self.assertTrue(any('excerpt is 1 characters' in p for p in problems))

    def test_billions_value_quoted_in_millions_passes(self):
        quote = 'Revenue$96,221$81,615$46,74318 %106 %'
        e = entry(46.743, quote)
        e['chart']['spec']['unit'] = 'USD billions'
        for r in e['chart']['records']:
            r['unit'] = 'USD billions'
        (Path(self.tmp.name)/'production/HPQ/audit/research_context.txt').write_text('URL: https://example.com/a\n' + quote)
        self.assertEqual(smn_research.check(e, self.tmp.name, '2026-09-23', 'HPQ'), [])
        e['chart']['records'][0]['value'] = 46.8
        self.assertTrue(any('does not appear' in p for p in smn_research.check(e, self.tmp.name, '2026-09-23', 'HPQ')))

    def test_quote_wrapped_across_lines_in_the_page_passes(self):
        # Sept 28 XLK: the release wraps mid-sentence and uses no-break spaces.
        (Path(self.tmp.name)/'production/HPQ/audit/research_context.txt').write_text(
            'URL: https://example.com/a\nIts total PC shipments dropped 16%\n for the\xa0quarter')
        self.assertEqual(self.check(-16), [])
        self.assertTrue(any('not verbatim' in p for p in self.check(-16, 'Its PC shipments dropped 16% for the quarter')))

class ModelSettingsTests(unittest.TestCase):
    def write(self, **changes):
        data = json.loads(smn_models.DEFAULT.read_text())
        data.update(changes)
        path = Path(tempfile.mkdtemp())/'m.json'
        path.write_text(json.dumps(data))
        return path

    def test_default_file_uses_high_end_model_only_for_writing(self):
        roles = smn_models.load()
        self.assertEqual(roles['write']['model'], 'claude-opus-5-5')
        self.assertTrue(all(r['model'] != 'claude-opus-5-5' for k, r in roles.items() if k != 'write'))

    def test_switch_writer_to_codex_by_one_line(self):
        roles = smn_models.load(self.write(write={'provider': 'codex', 'model': 'gpt-6-astra', 'effort': 'xhigh'}))
        self.assertEqual(roles['write']['provider'], 'codex')

    def test_named_chatgpt_profile_uses_three_model_tiers(self):
        roles = smn_models.load(profile='chatgpt')
        self.assertEqual((roles['write']['model'], roles['write']['effort']), ('gpt-6-astra', 'high'))
        self.assertEqual((roles['review']['model'], roles['research']['model']), ('gpt-6-sol', 'gpt-6-sol'))
        self.assertEqual((roles['visual']['model'], roles['hero_check']['model']), ('gpt-6-luna', 'gpt-6-luna'))

    def test_invalid_settings_are_rejected(self):
        for bad in ({'write': {'provider': 'grok', 'model': 'x', 'effort': 'low'}},
                    {'visual': {'provider': 'codex', 'model': 'gpt-unknown', 'effort': 'low'}},
                    {'review': {'provider': 'claude', 'model': 'claude-unknown', 'effort': 'low'}}):
            with self.assertRaises(ValueError):
                smn_models.load(self.write(**bad))

    def test_stage_roles(self):
        self.assertEqual([smn_models.role_of(s) for s in ('write', 'repair', 'repair-two', 'review', 'rereview')],
                         ['write', 'write', 'write', 'review', 'review'])


if __name__ == '__main__':
    unittest.main()
