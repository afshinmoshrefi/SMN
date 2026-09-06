"""Fixed-panel regressions from saved cases and deliberately incomplete inputs."""
import copy
from datetime import date
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cohort_policy import (build_selection_evidence, baseline_cell, comparison_cells,
                           validate_selection_evidence, instrument_key)
from article_evidence import build_cell_evidence, rounded_median, rounded_mean, round_percent
from angle_engine import derive_cell, _cell_public
from integrity_gate import validate_card_evidence

FIXTURE = json.loads((Path(__file__).parent/'fixtures/cohort_policy_cases.json').read_text())


def case(symbol='JPM'):
    return {k: copy.deepcopy(v) for k, v in FIXTURE[symbol].items() if k != 'snapshot_sha256'}


def codes(evidence):
    return {i['code'] for i in evidence['issues']}


def panel_case(start=1986, end=2025, net=1):
    return {'observations': [{'year': y, 'net': net, 'mfe': 2, 'mae': -1} for y in range(start, end+1)],
            'instrument': {'resource_id': '2', 'symbol': 'FIXTURE'}, 'anchor_date': '2026-08-21',
            'days': 90, 'as_of': '2026-08-21',
            'coverage': {'start_year': start, 'end_year': end, 'source_ref': 'synthetic-test-panel'}}


class SavedCohortTests(unittest.TestCase):
    def test_archived_findings_and_disjoint_views(self):
        expected = {'JPM': ('era_sensitive', -2.65, 6.12, -10.34),
                    'WMT': ('era_sensitive', -0.38, 1.71, -2.16),
                    'JNJ': ('era_sensitive', 6.58, 5.16, 8),
                    'F': ('genuine_contrast', -4.825, -2.38, -5.71)}
        for symbol, values in expected.items():
            with self.subTest(symbol=symbol):
                e = build_selection_evidence(**case(symbol))
                self.assertEqual(e['classification'], values[0])
                self.assertEqual(e['cycle']['full']['summary']['median_net'], values[1])
                self.assertEqual(e['cycle']['within_baseline']['summary']['median_net'], values[2])
                self.assertEqual(e['cycle']['earlier']['summary']['median_net'], values[3])
                self.assertEqual(e['overlap']['distinct_years_in_baseline_cycle_union'], 25)
                a = set(e['cycle']['within_baseline']['summary']['years'])
                b = set(e['cycle']['noncycle_within_baseline']['summary']['years'])
                self.assertFalse(a & b)
                self.assertEqual(a | b, set(range(2006, 2026)))
                self.assertTrue(e['pilot_eligibility']['annual_lead'])
                self.assertTrue(validate_selection_evidence(e)['ok'])

    def test_jnj_preserves_annual_recency_disagreement(self):
        e = build_selection_evidence(**case('JNJ'))
        self.assertEqual(e['comparisons']['same_span_cycle_vs_noncycle'], 'consistent')
        self.assertEqual(e['recent']['5']['recent']['median_net'], -3.4)
        self.assertEqual(e['recent']['5']['preceding']['median_net'], 4.01)
        self.assertIn('cohort.annual_recency_5', {q['id'] for q in e['writer_brief']['required_qualifications']})

    def test_ford_preserves_flat_and_no_majority(self):
        e = build_selection_evidence(**case('F'))
        s = e['cycle']['full']['summary']
        self.assertEqual((s['up_years'], s['down_years'], s['flat_years']), (0, 9, 1))
        self.assertEqual(e['baseline']['summary']['strict_majority'], 'none')
        self.assertIn('baseline.no_strict_majority', {q['id'] for q in e['writer_brief']['required_qualifications']})

    def test_cache_union_cannot_invent_full_annual_comparator(self):
        e = build_selection_evidence(**case())
        comparator = e['cycle']['full_span_comparator']
        self.assertEqual(comparator['status'], 'unavailable')
        self.assertEqual(len(comparator['missing_annual_years']), 15)
        self.assertIsNone(comparator['noncycle_summary'])

    def test_baseline_and_comparison_cells_are_fact_licensed_only(self):
        e = build_selection_evidence(**case('WMT'))
        primary = baseline_cell(e)
        context = comparison_cells(e)
        self.assertEqual((primary['years'], primary['days'], primary['n']), ('20', 30, 20))
        self.assertEqual(primary['end_date'], '2026-09-19')
        self.assertEqual(primary['median_net'], 2.23)
        self.assertTrue(all(c['eligible'] is False and c['role'] == 'comparison' for c in context))
        card = {'schema_version': 2, 'story_cell': primary, 'auxiliary_cells': context}
        result = validate_card_evidence(card)
        self.assertTrue(result['ok'], result)

    def test_mutated_summary_fails_recomputation(self):
        e = build_selection_evidence(**case())
        e['baseline']['summary']['median_net'] = 91.1
        self.assertFalse(validate_selection_evidence(e)['ok'])
        self.assertIsNone(baseline_cell(e))

    def test_other_horizon_cannot_change_fixed_baseline(self):
        args = case()
        wrong = copy.deepcopy(args['cells'][0]); wrong['days'] = 30
        for row in wrong['per_year']: row['net'] = 900
        args['cells'].insert(0, wrong)
        e = build_selection_evidence(**args)
        self.assertEqual(e['baseline']['summary']['median_net'], 6.47)
        self.assertEqual(e['window']['calendar_days'], 90)
        self.assertTrue(e['pilot_eligibility']['annual_lead'])

    def test_legacy_card_cannot_establish_missing_twenty_year_request(self):
        args = case(); args['cells'][0]['years'] = '10'
        args['cells'][0]['per_year'] = [r for r in args['cells'][0]['per_year'] if int(r['year']) >= 2016]
        e = build_selection_evidence(**args)
        self.assertIn('BASELINE_COVERAGE_NOT_CHECKED', codes(e))
        self.assertIsNone(baseline_cell(e))


class PanelQualityTests(unittest.TestCase):
    def test_full_common_panel_establishes_disjoint_full_span_comparator(self):
        e = build_selection_evidence(**panel_case())
        self.assertEqual(e['classification'], 'consistent')
        comparator = e['cycle']['full_span_comparator']
        self.assertEqual(comparator['status'], 'available')
        self.assertEqual(comparator['cycle_summary']['n'], 10)
        self.assertEqual(comparator['noncycle_summary']['n'], 30)
        self.assertTrue(validate_selection_evidence(e)['ok'])

    def test_raw_panel_requires_coverage_evidence(self):
        args = panel_case(); args.pop('coverage')
        e = build_selection_evidence(**args)
        self.assertIn('BASELINE_COVERAGE_NOT_CHECKED', codes(e))
        self.assertIn('CYCLE_CONTEXT_NOT_CHECKED', codes(e))

    def test_current_or_future_nonzero_rows_are_never_lookahead(self):
        args = panel_case()
        args['observations'] += [{'year': 2026, 'net': 99}, {'year': 2027, 'net': 99}]
        args['as_of'] = '2027-01-01'
        e = build_selection_evidence(**args)
        self.assertEqual(e['decision_cutoff'], '2026-08-21')
        self.assertEqual(e['last_completed_year'], 2025)
        self.assertEqual(e['baseline']['summary']['median_net'], 1)
        self.assertNotIn(2026, e['baseline']['summary']['years'])

    def test_historical_flat_is_not_discarded(self):
        args = panel_case(); args['observations'][-1] = {'year': 2025, 'net': 0, 'mfe': 0, 'mae': 0}
        e = build_selection_evidence(**args)
        self.assertEqual(e['baseline']['summary']['n'], 20)
        self.assertEqual(e['baseline']['summary']['flat_years'], 1)

    def test_unexplained_internal_gap_holds_without_backfilling(self):
        args = panel_case(); args['observations'] = [r for r in args['observations'] if r['year'] != 2017]
        e = build_selection_evidence(**args)
        self.assertEqual(e['baseline']['summary']['n'], 19)
        self.assertIn('BASELINE_UNEXPLAINED_MISSING_YEARS', codes(e))
        self.assertIsNone(baseline_cell(e))

    def test_documented_gap_is_visible_and_does_not_move_recent_boundary(self):
        args = panel_case(); args['observations'] = [r for r in args['observations'] if r['year'] != 2023]
        args['coverage']['missing_year_reasons'] = {'2023': {'reason': 'source_data_unavailable', 'evidence_ref': 'provider-gap-record'}}
        e = build_selection_evidence(**args)
        self.assertTrue(e['pilot_eligibility']['annual_lead'])
        self.assertEqual(e['recent']['5']['recent']['n'], 4)
        self.assertEqual(e['recent']['5']['preceding']['n'], 15)
        self.assertEqual(e['recent']['5']['requested_recent_years'], list(range(2021, 2026)))

    def test_unfavorable_result_is_not_a_missing_data_reason(self):
        args = panel_case(); args['observations'] = [r for r in args['observations'] if r['year'] != 2017]
        args['coverage']['missing_year_reasons'] = {'2017': {'reason': 'bad_return', 'evidence_ref': 'do-not-accept'}}
        self.assertIn('BASELINE_UNEXPLAINED_MISSING_YEARS', codes(build_selection_evidence(**args)))

    def test_checked_short_cycle_allows_valid_annual_lead(self):
        args = case(); args['instrument']['symbol'] = 'SHORT'
        for c in args['cells']:
            c['symbol'] = 'SHORT'; c['per_year'] = [r for r in c['per_year'] if int(r['year']) >= 2012]
        args['coverage'] = {'series_start_year': 2012, 'series_start_evidence_ref': 'provider-history-start'}
        e = build_selection_evidence(**args)
        self.assertEqual(e['classification'], 'insufficient_context')
        self.assertEqual(e['baseline']['summary']['n'], 14)
        self.assertEqual(e['cycle']['full']['summary']['n'], 3)
        self.assertTrue(e['pilot_eligibility']['annual_lead'])
        self.assertFalse(e['pilot_eligibility']['cycle_lead'])

    def test_unchecked_cycle_request_holds(self):
        args = case(); args['cells'] = args['cells'][:1]
        e = build_selection_evidence(**args)
        self.assertIn('CYCLE_CONTEXT_NOT_CHECKED', codes(e))
        self.assertIsNone(baseline_cell(e))

    def test_malformed_missing_nonfinite_net_never_becomes_zero(self):
        for value in (None, 'NaN', float('inf'), True):
            with self.subTest(value=value):
                args = panel_case(); args['observations'][-1]['net'] = value
                e = build_selection_evidence(**args)
                self.assertIn('INVALID_NET_OBSERVATION', codes(e))
                self.assertEqual(e['baseline']['summary']['n'], 19)
                self.assertEqual(e['baseline']['summary']['flat_years'], 0)
                json.dumps(e, allow_nan=False)

    def test_missing_excursions_remain_nullable(self):
        args = panel_case()
        for r in args['observations']: r.pop('mfe'); r['mae'] = 'NaN'
        e = build_selection_evidence(**args); cell = baseline_cell(e)
        self.assertIsNone(cell['median_mfe']); self.assertIsNone(cell['median_mae'])
        self.assertEqual(cell['evidence']['giveback']['n'], 0)

    def test_conflicting_overlap_is_quarantined_not_averaged(self):
        args = case(); args['cells'][1]['per_year'][-2]['net'] = 999
        e = build_selection_evidence(**args)
        self.assertEqual(e['classification'], 'incomparable')
        self.assertIn('CONFLICTING_OBSERVATIONS', codes(e))
        self.assertNotIn(2022, e['baseline']['summary']['years'])

    def test_duplicate_year_within_source_holds(self):
        args = panel_case(); args['observations'].append(copy.deepcopy(args['observations'][-1]))
        e = build_selection_evidence(**args)
        self.assertIn('DUPLICATE_YEAR_WITHIN_SOURCE', codes(e))
        self.assertNotIn(2025, e['baseline']['summary']['years'])

    def test_wrong_identity_or_anchor_holds(self):
        for key, value in [('symbol', 'OTHER'), ('resource_id', '7'), ('anchor_date', '2026-08-22')]:
            with self.subTest(key=key):
                args = case(); args['cells'][1][key] = value
                self.assertEqual(build_selection_evidence(**args)['classification'], 'incomparable')

    def test_invalid_year_code_and_calendar_fail_explicitly(self):
        args = case(); args['cells'][0]['years'] = 20
        self.assertIn('YEARS_CODE_MUST_BE_STRING', codes(build_selection_evidence(**args)))
        for days in (True, 0, 1.5, 'NaN'):
            args = case(); args['days'] = days
            self.assertEqual(build_selection_evidence(**args)['classification'], 'incomparable')

    def test_window_counts_calendar_dates_including_leap_day(self):
        args = panel_case(); args.update(anchor_date='2024-02-28', days=2, as_of='2024-02-28')
        args['observations'] = [r for r in args['observations'] if r['year'] <= 2023]
        args['coverage'].update(start_year=1986, end_year=2023)
        e = build_selection_evidence(**args)
        self.assertEqual(e['window']['end_date'], '2024-02-29')

    def test_mixed_flat_comparison_requires_visible_qualification(self):
        args = panel_case()
        for r in args['observations']:
            if r['year'] % 4 == 2: r['net'] = 0
        e = build_selection_evidence(**args)
        self.assertEqual(e['classification'], 'mixed')
        self.assertIn('cohort.mixed', {q['id'] for q in e['writer_brief']['required_qualifications']})

    def test_no_raw_prices_in_serializable_output(self):
        args = case(); args['cells'][0]['price'] = 12345
        args['cells'][0]['stats_raw'] = {'52W High': 12345}
        def keys(value):
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(v) for v in value.values()))
            if isinstance(value, list): return set().union(*(keys(v) for v in value)) if value else set()
            return set()
        output = build_selection_evidence(**args)
        self.assertNotIn('price', keys(output)); self.assertNotIn('stats_raw', keys(output))
        self.assertFalse(output['publishable'])

    def test_instrument_key_does_not_guess_missing_metadata(self):
        self.assertEqual(instrument_key({'resource_id': '2', 'symbol': 'JPM'}),
                         instrument_key({'resource_id': 2, 'symbol': 'JPM', 'provider': None}))
        self.assertNotEqual(instrument_key({'resource_id': '2', 'symbol': 'JPM'}),
                            instrument_key({'resource_id': '2', 'symbol': 'JPM', 'provider': 'verified'}))


class CanonicalRoundingTests(unittest.TestCase):
    def test_raw_zero_placeholder_is_not_a_completed_flat_return(self):
        a=panel_case()
        a['cells']=[{'request':{'market':'2','symbol':'FIXTURE','entry_date':'2026-08-21','days_out':90,'years':20,'pe_cycle':'cons'},
                     'ChartData4':[{'year':2017,'pct':'0,0,0','price':'0,0'}]}]
        e=build_selection_evidence(**a)
        self.assertIn('AMBIGUOUS_HISTORICAL_ZERO_PLACEHOLDER',codes(e))
        self.assertFalse(e['pilot_eligibility']['annual_lead'])
        self.assertTrue(validate_selection_evidence(e)['ok'])

    def test_half_up_decimal_ties_match_policy_cells_and_integrity(self):
        for symbol, expected in [('WMT', 2.23), ('JNJ', 3.65)]:
            e = build_selection_evidence(**case(symbol)); c = baseline_cell(e)
            self.assertEqual(e['baseline']['summary']['median_net_display'], expected)
            self.assertEqual(c['median_net'], expected)
            self.assertTrue(validate_card_evidence({'schema_version': 2, 'story_cell': c, 'auxiliary_cells': comparison_cells(e)})['ok'])

    def test_mean_and_median_use_decimal_half_up(self):
        self.assertEqual(rounded_median([2.22, 2.23]), 2.23)
        self.assertEqual(rounded_mean([3.64, 3.65]), 3.65)
        self.assertEqual(round_percent('-4.825'), -4.83)
        raw = {'ChartData4': [{'year': 2024, 'pct': '2.22,3.64,-4.82'}, {'year': 2025, 'pct': '2.23,3.65,-4.83'}]}
        c = derive_cell(raw, resource_id='2', symbol='TEST', anchor='2026-08-21', days=90, years='20', horizon_tag='90d', today=date(2026,8,21))
        self.assertEqual((c.median_net, c.median_mfe, c.median_mae), (2.23, 3.65, -4.83))
        self.assertTrue(validate_card_evidence({'schema_version': 2, 'story_cell': _cell_public(c), 'auxiliary_cells': []})['ok'])

    def test_giveback_subtracts_decimal_pairs_before_rounding(self):
        e = build_cell_evidence({'anchor_date': '2026-08-21', 'days': 30, 'years': '1',
                                'per_year': [{'year': 2025, 'net': 1.875, 'mfe': 4.1, 'mae': -1}]})
        self.assertEqual(e['giveback']['median_pp'], 2.23)


class QualificationScopeTests(unittest.TestCase):
    @staticmethod
    def qualifications(e):
        return {q['id']: q for q in e['writer_brief']['required_qualifications']}

    def test_jpm_and_wmt_cycle_reversal_require_only_the_cycle_pair(self):
        for symbol in ('JPM','WMT'):
            with self.subTest(symbol=symbol):
                e=build_selection_evidence(**case(symbol));q=self.qualifications(e)
                self.assertEqual(q['cohort.era_sensitive']['evidence_refs'],
                                 ['/cycle/within_baseline/summary','/cycle/earlier/summary'])
                self.assertTrue(all(v['comparison']=='consistent' for v in e['recent'].values()))
                self.assertNotIn('cohort.annual_recency_5',q)
                self.assertNotIn('cohort.annual_recency_10',q)

    def test_jnj_annual_reversal_does_not_require_irrelevant_cycle_refs(self):
        e=build_selection_evidence(**case('JNJ'));q=self.qualifications(e)
        self.assertEqual(q['cohort.annual_recency_5']['evidence_refs'],['/recent/5'])
        self.assertNotIn('cohort.annual_recency_10',q)
        self.assertNotIn('cohort.era_sensitive',q)

    def test_corrected_f_keeps_both_annual_reversals_and_matched_contrast(self):
        a=json.loads((Path(__file__).parent/'fixtures/cohort_policy_corrected_f.json').read_text())
        e=build_selection_evidence(**a);q=self.qualifications(e)
        self.assertEqual(e['classification'],'genuine_contrast')
        self.assertEqual(e['baseline']['summary']['median_net_display'],-0.67)
        self.assertNotIn('cohort.era_sensitive',q)
        self.assertEqual(q['cohort.annual_recency_5']['evidence_refs'],['/recent/5'])
        self.assertEqual(q['cohort.annual_recency_10']['evidence_refs'],['/recent/10'])
        self.assertEqual(q['cohort.genuine_contrast']['evidence_refs'],
                         ['/cycle/within_baseline/summary','/cycle/noncycle_within_baseline/summary'])
        self.assertTrue(validate_selection_evidence(e)['ok'])

    def test_simultaneous_cycle_and_annual_reversals_are_all_retained(self):
        a=panel_case()
        for r in a['observations']:
            if r['year']<2006 and r['year']%4==2:r['net']=-5
            elif r['year']>=2021:r['net']=-2
        e=build_selection_evidence(**a);q=self.qualifications(e)
        self.assertTrue({'cohort.era_sensitive','cohort.annual_recency_5','cohort.annual_recency_10'}<=set(q))
        self.assertEqual(len(q),len(e['writer_brief']['required_qualifications']))

    def test_annual_reversal_survives_insufficient_cycle_context(self):
        a=panel_case(start=2012)
        a['coverage'].update(start_year=1986,series_start_year=2012,series_start_evidence_ref='synthetic-inception')
        for r in a['observations']:
            if r['year']>=2021:r['net']=-2
        e=build_selection_evidence(**a);q=self.qualifications(e)
        self.assertEqual(e['classification'],'insufficient_context')
        self.assertIn('cohort.annual_recency_5',q)
        self.assertIn('cohort.insufficient_context',q)


if __name__ == '__main__': unittest.main()
