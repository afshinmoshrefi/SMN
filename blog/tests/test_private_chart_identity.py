"""Private previews must not recycle a chart for another sample/instrument."""
import copy
import hashlib
import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from article_chart_evidence import validate_chart_evidence, chart_source_sha256


class PrivateChartIdentityTests(unittest.TestCase):
    def setUp(self):
        self.card = {'symbol':'JPM','resource_id':'2','selection_evidence':{'mode':'private_preview'},
                     'instrument':{'resource_id':'2','symbol':'JPM','semantics':{'measurement':'adjusted_price_return'}},
                     'story_cell':{'anchor_date':'2026-08-21','days':90,'years':'20','n':2,
                                   'per_year':[{'year':2024,'net':2,'mfe':4,'mae':-1},
                                               {'year':2025,'net':3,'mfe':5,'mae':-2}]}}
        self.image = {'variant':'bars','semantics':{'symbol':'JPM','resource_id':'2','years':'20',
                      'observed_years':[2024,2025],'direction':'long','n':2,
                      'window_start':'2026-08-21','window_end':'2026-11-18'}}
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name)/'renderer.png'
        path.write_bytes(b'synthetic renderer bytes for hashing tests')
        self.image.update(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.image['semantics'].update(measurement='adjusted_price_return',source_sha256=chart_source_sha256(self.card))

    def test_matched_inclusive_sample_passes(self):
        self.assertTrue(validate_chart_evidence(self.card,[self.image],['bars'])['ok'])

    def test_same_count_wrong_years_and_ticker_fail(self):
        for field,value,code in [('observed_years',[2023,2024],'CHART_YEARS_MISMATCH'),
                                  ('symbol','WMT','CHART_INSTRUMENT_MISMATCH'),
                                  ('years','pe2-10','CHART_LOOKBACK_MISMATCH'),
                                  ('direction','short','CHART_RETURN_BASIS_MISMATCH')]:
            image=copy.deepcopy(self.image); image['semantics'][field]=value
            with self.subTest(field=field):
                result=validate_chart_evidence(self.card,[image],['bars'])
                self.assertIn(code,[x['code'] for x in result['errors']])

    def test_missing_metadata_or_selected_artifact_fails_only_private_mode(self):
        result=validate_chart_evidence(self.card,[{'variant':'bars'}],['bars'])
        self.assertFalse(result['ok'])
        self.assertFalse(validate_chart_evidence(self.card,[],['bars'])['ok'])
        legacy={k:v for k,v in self.card.items() if k!='selection_evidence'}
        self.assertTrue(validate_chart_evidence(legacy,[{'variant':'bars'}],['bars'])['ok'])

    def test_raw_price_context_is_not_supported_by_private_return_only_renderer(self):
        image={'variant':'price','semantics':{'symbol':'JPM','resource_id':'2'}}
        result=validate_chart_evidence(self.card,[image],['price'])
        self.assertIn('CHART_CONTEXT_NOT_SUPPORTED',[x['code'] for x in result['errors']])

    def test_same_labels_cannot_hide_different_returns_or_raster_bytes(self):
        card=copy.deepcopy(self.card)
        card['story_cell']['per_year'][0]['net']=7
        self.assertFalse(validate_chart_evidence(card,[self.image],['bars'])['ok'])
        Path(self.image['path']).write_bytes(b'different chart')
        self.assertFalse(validate_chart_evidence(self.card,[self.image],['bars'])['ok'])

    def test_wrong_measurement_is_held(self):
        self.image['semantics']['measurement']='yield_level'
        result=validate_chart_evidence(self.card,[self.image],['bars'])
        self.assertIn('CHART_MEASUREMENT_MISMATCH',[x['code'] for x in result['errors']])


if __name__=='__main__':
    unittest.main()
