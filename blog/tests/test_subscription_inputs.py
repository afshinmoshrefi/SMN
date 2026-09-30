"""Offline selected-input custody and literal engine-response fidelity."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import subscription_inputs as inputs
import engine_seasonal as engine

FIXTURES=json.loads((Path(__file__).parent/'fixtures/engine-edition-20260910.json').read_text())


def package():
    return {'date':'2026-09-30','csv_base64':base64.b64encode(b'ticker,score\nVIX,8\n').decode(),
            'rows':[{'ticker':'VIX','company':'Cboe Volatility Index','score':8,
                     'pat_resource_id':'5','pat_start_date':'2026-10-10','pat_days':'129',
                     'pat_years':'pe2-8','pat_mode':'pe','pat_direction':'short','featured_score':2}],
            'market_families':{'5':'INDX'},'selection':{'module_sha256':'fixture','dynamic_count_enabled':True,'config':{'USERID':22}}}


class SelectedInputsTests(unittest.TestCase):
    def test_plain_pe_lookback_and_frozen_other_phases(self):
        for edition,expected in [('2026-09-30','pe2-8'),('2027-09-30','pe3-8'),
                                 ('2028-09-30','pe0-8'),('2029-09-30','pe1-8')]:
            p=package(); p['date']=edition; p['rows'][0]['pat_years']='8'
            self.assertEqual(inputs.selection_posts(p,edition)[0]['lookback_years'],expected)
        self.assertEqual(inputs.selection_posts(package(),'2026-09-30')[0]['lookback_years'],'pe2-8')
        p=package(); p['rows'][0].update(pat_years='8',pat_mode='consecutive')
        self.assertEqual(inputs.selection_posts(p,'2026-09-30')[0]['lookback_years'],'8')

    def test_inconsistent_pe_metadata_holds(self):
        for years,mode in [('pe1-8','pe'),('pe2-8','consecutive'),('8','unsupported')]:
            p=package(); p['rows'][0].update(pat_years=years,pat_mode=mode)
            with self.assertRaises(inputs.Held):
                inputs.selection_posts(p,'2026-09-30')

    def test_local_transport_uses_candidate_source_without_self_ssh(self):
        result=SimpleNamespace(returncode=0,stdout='{}')
        with patch.dict(inputs.os.environ,{'SMN_CAPTURE_LOCAL':'1'}),patch.object(inputs.subprocess,'run',return_value=result) as run:
            inputs.fetch_selection('2026-09-30')
        args,kwargs=run.call_args
        self.assertEqual(args[0][0],sys.executable)
        self.assertEqual(kwargs['env']['SMN_INPUT_SOURCE_BLOG'],str(Path(inputs.__file__).resolve().parent))
        self.assertNotIn('ssh',args[0])

    def test_default_hero_wrapper_preserves_call_identity_without_api_execution(self):
        p=inputs.selection_posts(package(),'2026-09-30')[0]
        with patch.object(inputs,'_remote',return_value={'fixture':True}) as remote:
            self.assertEqual(inputs.generate_hero(p,'2026-09-30'),{'fixture':True})
        self.assertEqual(json.loads(remote.call_args.kwargs['payload']),{'post':p,'date':'2026-09-30'})
        self.assertIn("config.news_root_folder=str(directory)",remote.call_args.args[0])
        self.assertIn("sys.path.insert(0,str(blog)); import article_hero_image",remote.call_args.args[0])
        self.assertIn("hero.hero_image_workflow(resource_id=post['resource_id']",remote.call_args.args[0])
        self.assertIn("image=Path(output.get('image_path')",remote.call_args.args[0])

    def test_capture_needs_no_published_article_and_reuses_without_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); fetch=Mock(return_value=package())
            first=inputs.capture(root,'2026-09-30',fetch)
            self.assertEqual(first['symbols'],['VIX'])
            posts=json.loads((root/'production/posts.json').read_bytes())
            self.assertEqual(posts[0]['lookback_years'],'pe2-8')
            self.assertEqual(posts[0]['source_mode'],'selected_inputs')
            self.assertEqual(posts[0]['author_id'],'22')
            self.assertFalse((root/'production/VIX/article.html').exists())
            self.assertTrue(inputs.capture(root,'2026-09-30',fetch)['idempotent'])
            self.assertEqual(fetch.call_count,1)
            (root/'production/selection.csv').write_bytes(b'changed')
            with self.assertRaisesRegex(inputs.Held,'changed'):
                inputs.capture(root,'2026-09-30',fetch)

    def test_wrong_date_and_duplicate_studies_hold_missing_day_waits(self):
        p=package(); p['date']='2026-09-29'
        with self.assertRaisesRegex(inputs.Held,'edition'):
            inputs.selection_posts(p,'2026-09-30')
        p=package(); p['rows']*=2
        with self.assertRaisesRegex(inputs.Held,'duplicate'):
            inputs.selection_posts(p,'2026-09-30')
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(inputs.capture(Path(directory),'2026-09-30',lambda _:None)['status'],'waiting_for_selection')
            self.assertFalse((Path(directory)/'production').exists())

    def test_publication_metadata_preserves_featured_last_order(self):
        p=package(); row=deepcopy(p['rows'][0]); row['ticker']='SPY'; row['pat_resource_id']='11'
        p['rows'].append(row); p['market_families']['11']='ETF'
        posts=inputs.selection_posts(p,'2026-09-30')
        self.assertEqual([post['published_date'] for post in posts],['2026-09-30T00:00:00Z','2026-09-30T00:00:01Z'])

    def test_explicit_hero_callback_once_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); inputs.capture(root,'2026-09-30',lambda _:package())
            blob=b'offline hero'; callback=Mock(return_value={'image_base64':base64.b64encode(blob).decode(),
                'sha256':hashlib.sha256(blob).hexdigest(),'provider':'fixture','api_cost_stage':True,'cost_usd':None})
            inputs.prepare_heroes(root,'2026-09-30',callback)
            receipt=json.loads((root/'input-heroes.json').read_bytes())
            self.assertEqual(receipt['heroes']['VIX']['sha256'],hashlib.sha256(blob).hexdigest())
            self.assertTrue(inputs.prepare_heroes(root,'2026-09-30',callback)['idempotent'])
            self.assertEqual(callback.call_count,1)
            (root/receipt['heroes']['VIX']['path']).write_bytes(b'changed')
            with self.assertRaisesRegex(inputs.Held,'changed'):
                inputs.prepare_heroes(root,'2026-09-30',callback)

    def test_retained_hero_has_no_api_cost_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); inputs.capture(root,'2026-09-30',lambda _:package())
            blob=b'retained hero'; callback=Mock(return_value={'image_base64':base64.b64encode(blob).decode(),
                'sha256':hashlib.sha256(blob).hexdigest(),'provider':'retained fixture','api_cost_stage':False})
            self.assertFalse(inputs.prepare_heroes(root,'2026-09-30',callback)['api_cost_stage'])
            self.assertFalse(json.loads((root/'input-heroes.json').read_bytes())['api_cost_stage'])
            self.assertFalse(inputs.prepare_heroes(root,'2026-09-30',callback)['api_cost_stage'])
            self.assertEqual(callback.call_count,1)

    def test_all_selected_cards_copy_literal_full_engine_evidence(self):
        for fixture in FIXTURES:
            with self.subTest(symbol=fixture['original']['symbol']):
                p={**fixture['original'],'source_mode':'selected_inputs'}
                before=deepcopy(fixture['export'])
                card=engine.make_selected_card(p,fixture['export'],fixture['owner'],fixture['captured_at'],
                    {'selection_sha256':'a'*64,'selected_row_sha256':'b'*64},{'name':'TEST','reason':'Fixture'})
                primary=next(q['response'] for q in fixture['export']['responses'] if q['request']['years']==p['lookback_years'])
                self.assertEqual(card['engine_results']['stats'],primary['stats'])
                self.assertEqual(card['story_cell']['per_year'],engine.decode_rows(primary,p['pattern_start_date']))
                self.assertEqual(card['price_path'],fixture['export']['price_path'])
                self.assertEqual(fixture['export'],before)
                self.assertNotIn('primary_chart_values_match_production',card['provenance'])

    def test_engine_identity_and_selection_binding_cannot_change(self):
        f=deepcopy(FIXTURES[0]); p={**f['original'],'source_mode':'selected_inputs'}
        f['export']['responses'][0]['request']['days_out']='1'
        with self.assertRaisesRegex(ValueError,'query identity'):
            engine.make_selected_card(p,f['export'],f['owner'],f['captured_at'],{'selection_sha256':'a'*64},{'name':'TEST'})
        f=FIXTURES[0]
        with self.assertRaisesRegex(ValueError,'binding'):
            engine.make_selected_card(p,f['export'],f['owner'],f['captured_at'],{}, {'name':'TEST'})


if __name__=='__main__':
    unittest.main()
