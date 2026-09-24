import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import smn_models
import smn_research


def entry(value, quote):
    return {'angle': 'PC_SHIPMENTS_AND_GUIDANCE', 'sources': [
                {'id': 'reuters-q3', 'title': 'T', 'url': 'https://example.com/a', 'date': '2026-09-01', 'excerpt': 'x'}],
            'chart': {'spec': {'unit': 'percent change', 'rows': [{'record_id': 'biz-0', 'label': 'PC shipments'}]},
                      'records': [{'id': 'biz-0', 'value': value, 'unit': 'percent change', 'period': 'Q3',
                                   'status': 'reported', 'source_id': 'reuters-q3', 'locator': 'p1', 'quote': quote}]}}


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

    def test_invalid_settings_are_rejected(self):
        for bad in ({'write': {'provider': 'grok', 'model': 'x', 'effort': 'low'}},
                    {'visual': {'provider': 'codex', 'model': 'gpt-6-astra', 'effort': 'low'}},
                    {'review': {'provider': 'claude', 'model': 'claude-unknown', 'effort': 'low'}}):
            with self.assertRaises(ValueError):
                smn_models.load(self.write(**bad))

    def test_stage_roles(self):
        self.assertEqual([smn_models.role_of(s) for s in ('write', 'repair', 'repair-two', 'review', 'rereview')],
                         ['write', 'write', 'write', 'review', 'review'])


if __name__ == '__main__':
    unittest.main()
