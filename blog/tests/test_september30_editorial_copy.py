"""Retained final article copy; no live reviewer calls or recalculated finance."""
import json
import unittest
from pathlib import Path
import editorial_gate as gate

FIXTURES=json.loads((Path(__file__).parent/'fixtures/september30_editorial_copy.json').read_text(encoding='utf-8'))

class RetainedCopyTests(unittest.TestCase):
    def audit(self,symbol):
        f=FIXTURES[symbol];reqs=gate.requirements(f['article'],f['card'])
        engine=f['card']['engine_results']
        ctx={'edition':'2026-09-30','primary_sources':{},'material_context':[],
             'requirements':reqs,'primary_years':engine['cohort']['years'],
             'comparison_years':{str(c['request']['years']):[r['year'] for r in c['per_year']] for c in engine['comparisons']}}
        claims=[]
        for req in reqs:
            claims.append({'unit_id':req['id'],'status':'qualified_analysis' if req['kind']=='analysis' else 'supported',
                'source_url':'','source_quote':'','event_date':'',
                'cohorts':[{'sample':k,'shared_years':sorted(set(ctx['primary_years']) & set(ctx['comparison_years'][k]))} for k in req.get('samples',[])]})
        review={'issues':[],'editorial_audit':{'coverage':[],'claims':claims}}
        return gate.problems(f['article'],f['bundle'],ctx,review),reqs

    def test_real_vix_copy_holds_instrument_and_stale_future_claim(self):
        errors,_=self.audit('VIX')
        self.assertTrue(any('not attainable' in e for e in errors))
        self.assertTrue(any('future date' in e for e in errors))
        from seasonal_edition import check_temporal_instrument_copy
        f=FIXTURES['VIX'];entry=[u['text'] for u in gate.units(f['article']) if 'The VIX starts near' in u['text']]
        self.assertTrue(entry)
        with self.assertRaisesRegex(ValueError,'before its start'):
            check_temporal_instrument_copy(entry,f['bundle'])

    def test_real_amd_overlap_and_ibm_secondary_causality_hold(self):
        errors,_=self.audit('AMD')
        self.assertTrue(any('overlap count is not true for 10' in e for e in errors))
        errors,_=self.audit('IBM')
        self.assertTrue(any('causal explanation lacks' in e for e in errors))

    def test_real_spy_qqq_have_no_new_deterministic_copy_failures(self):
        # This proves the copy-check boundary, not live semantic reviewer approval.
        for symbol in ('SPY','QQQ'):
            with self.subTest(symbol=symbol):self.assertEqual(self.audit(symbol)[0],[])

if __name__=='__main__':unittest.main()
