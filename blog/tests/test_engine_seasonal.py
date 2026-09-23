"""Fidelity tests against retained TradeWave responses, not a second calculator."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine_seasonal as e
import seasonal_edition as se
from visual_evidence import digest

FIXTURES=json.loads((Path(__file__).parent/'fixtures/engine-edition-20260910.json').read_text(encoding='utf-8'))


def card(f):
    return e.make_card(f['original'],f['retained'],f['export'],f['owner'],f['captured_at'],
        {'retained_file':'September10 production audit'}, {'name':'TEST','reason':'Engine fidelity fixture'},f['dataset'])


class EngineAuthorityTests(unittest.TestCase):
    def test_started_unfinished_window_uses_engine_completion_flag(self):
        f=json.loads((Path(__file__).parent/'fixtures/aph-completion-20260921.json').read_text())
        response=f['response'];before=deepcopy(response)
        rows=e.decode_rows(response,f['anchor'])
        self.assertEqual([r['year'] for r in rows],f['published_years'])
        self.assertEqual(len(rows),8)
        for row,raw in zip(rows,response['ChartData4'][:-1]):
            self.assertEqual([row[k] for k in ('net','mfe','mae')],[float(v) for v in raw['pct'].split(',')])
            self.assertEqual([row[k] for k in ('entry_price','exit_price')],[float(v) for v in raw['price'].split(',')])
        self.assertEqual(response,before)

    def test_completed_flat_rows_and_legacy_placeholder_contract(self):
        flat={'year':2022,'price':'77.55,77.55','pct':'0.0,0.0,0.0','completed':True}
        legacy={'year':2026,'price':'0,0','pct':'0,0,0'}
        self.assertEqual(e.decode_rows({'ChartData4':[flat,legacy]},'2026-09-18')[0]['net'],0)
        self.assertEqual(len(e.decode_rows({'ChartData4':[flat,legacy]},'2026-09-18')),1)
        bad={**flat,'completed':'false'}
        with self.assertRaisesRegex(ValueError,'completion flag'):
            e.decode_rows({'ChartData4':[bad]},'2026-09-18')

    def test_all_six_keep_exact_selected_study_and_original_metrics(self):
        for f in FIXTURES:
            with self.subTest(symbol=f['original']['symbol']):
                c=card(f)
                self.assertEqual(c['production_identity'],e.identity(f['original']))
                self.assertEqual(c['engine_results']['stats'],f['retained']['stats'])
                self.assertEqual(c['price_path']['projection_response'],f['export']['price_path']['projection_response'])

    def test_ibm_recovers_fifteen_rows_not_truncated_ten_row_writer_packet(self):
        f=next(f for f in FIXTURES if f['original']['symbol']=='IBM');c=card(f)
        self.assertEqual(len(f['retained']['per_year']),10)
        self.assertEqual(c['engine_results']['cohort']['years'],[1966,1970,1974,1978,1982,1986,1990,1994,1998,2002,2006,2010,2014,2018,2022])
        self.assertEqual(c['story_cell']['years'],'pe2-15')
        self.assertEqual(c['engine_results']['stats']['Num Winners'],'14')

    def test_source_mismatch_and_silent_lookback_substitution_hold(self):
        f=deepcopy(FIXTURES[0]);f['original']['lookback_years']='20'
        with self.assertRaisesRegex(ValueError,'identity'):card(f)
        f=deepcopy(FIXTURES[0]);f['export']['responses'][0]['response']['ChartData4'][0]['pct']='999,999,999'
        with self.assertRaisesRegex(ValueError,'differ'):card(f)

    def test_authoritative_extrema_are_not_forced_to_zero(self):
        c=card(next(f for f in FIXTURES if f['original']['symbol']=='KDP'))
        row=next(r for r in c['story_cell']['per_year'] if r['year']==2022)
        self.assertEqual(row['mfe'],-0.26)
        self.assertEqual(c['engine_results']['stats']['Median Profit'],'4.15%')
        e.validate_card(c)

    def test_forged_price_sample_and_legacy_calculation_inputs_rejected(self):
        c=card(FIXTURES[0]);c['price_path']['request']['years']='20'
        with self.assertRaisesRegex(ValueError,'different study'):e.validate_card(c)
        with self.assertRaisesRegex(ValueError,'engine export required'):
            se.bind_source({'card':{'story_cell':{},'selection_evidence':{}}},{})

    def test_renderer_preserves_rows_and_never_calls_legacy_median(self):
        f=next(f for f in FIXTURES if f['original']['symbol']=='KDP');c=card(f)
        contract={'card_sha256':digest(c),'angle':'TEST','company':'Keurig Dr Pepper','history_source_id':'history',
            'viewer_url':'https://tradewave.ai/app/','methodology_url':'https://seasonalmarketnews.com/methodology.html'}
        b={'seasonal_contract':contract,'sources':[{'id':'history','payload':c['engine_results']}]}
        with tempfile.TemporaryDirectory() as directory, patch('chartkit._median',side_effect=AssertionError('No duplicate median')):
            data=e.prepare({'card':c},b,directory)
            self.assertTrue(e.verify_assets(data,directory))
            with (Path(directory)/'assets/tradewave-observations.csv').open() as file:
                exported=list(csv.DictReader(file))
            self.assertEqual(exported[6]['mfe'],'-0.26')
            self.assertIn('A positive short result means a bet on falling prices worked',e.stats_html(data))
            self.assertIn('Median full-window short result',e.stats_html(data))
            self.assertEqual(data['images'][0]['values_sha256'],digest(c['story_cell']['per_year']))
            for image in data['images']:
                markup=e.figure_html(data,image['variant'])
                self.assertIn(image['url']+'?v='+image['sha256'][:16],markup)
                self.assertIn(image['mobile_url']+'?v='+image['mobile_sha256'][:16],markup)
            (Path(directory)/data['images'][0]['url']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'Changed'):e.verify_assets(data,directory)

    def test_comparisons_are_engine_outputs_and_describe_overlap(self):
        c=card(next(f for f in FIXTURES if f['original']['symbol']=='KMB'))
        d={'evidence':c['engine_results']}
        rows=e.comparison_rows(d)
        self.assertEqual(rows[0]['stats']['Trade Dir'],'short')
        self.assertEqual(rows[1]['stats']['Trade Dir'],'long')
        self.assertEqual(rows[1]['stats']['Median Profit'],'0.64%')
        rendered=e.comparison_html(d)
        self.assertIn('Some years appear in more than one group',rendered)
        self.assertNotIn('ten-year sample is included in the twenty-year sample',rendered)

    def test_comparison_discloses_actual_years_when_history_is_short(self):
        years_8=list(range(2018,2026))
        years_10=list(range(2016,2026))
        stats={'Trade Dir':'long','Percent Profitable':'50%','Median Profit':'1.00%'}
        data={'evidence':{'cohort':{'label':'8 selected years','years':years_8},'stats':stats,
            'comparisons':[{'label':'Requested 20-year history','stats':stats,
                'per_year':[{'year':year} for year in years_10]}]}}
        rendered=e.comparison_html(data)
        self.assertIn(', '.join(map(str,years_8)),rendered)
        self.assertIn(', '.join(map(str,years_10)),rendered)
        self.assertIn('requested lookback can extend beyond available history',rendered)
        self.assertNotIn('ten-year sample is included in the twenty-year sample',rendered)


if __name__=='__main__':unittest.main()
