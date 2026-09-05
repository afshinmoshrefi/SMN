"""Regression checks for independently calculated, reader-facing evidence."""
from datetime import date
import json
from pathlib import Path
from statistics import median
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import angle_engine as ae
from article_evidence import inclusive_window
from consistency_gate import check_article
from integrity_gate import validate_card_evidence, validate_evidence_claims, _allowed_pairs


# Recorded JPM 90-day / 15-observation Dev case from August 21, 2026.
# Fixed raw matched rows, not an expectation generated using production code.
JPM = [
    (2011, -7.54, 15.44, -15.91), (2012, 7.49, 15.30, -3.60),
    (2013, 8.76, 9.26, -3.04), (2014, 4.17, 6.58, -6.62),
    (2015, 6.82, 9.32, -21.27), (2016, 18.96, 23.14, -1.05),
    (2017, 9.88, 13.67, -2.81), (2018, -3.22, 3.40, -10.29),
    (2019, 22.36, 22.98, -2.57), (2020, 19.52, 23.31, -6.10),
    (2021, 4.68, 11.04, -3.96), (2022, 15.74, 17.76, -12.41),
    (2023, 3.31, 3.62, -8.89), (2024, 14.88, 16.27, -6.52),
    (2025, 3.22, 11.10, -0.46),
]


def make_cell(rows=JPM, years='15', days=90, anchor='2026-08-21'):
    raw = {'ChartData4': [{'year': y, 'pct': f'{net},{mfe},{mae}'}
                          for y, net, mfe, mae in rows], 'stats': {}}
    return ae.derive_cell(raw, resource_id='2', symbol='JPM', anchor=anchor,
                          days=days, years=years, horizon_tag=f'{days}d',
                          today=date(2026, 8, 21))


def make_card(cell=None):
    return {'schema_version': 2, 'symbol': 'JPM',
            'story_cell': ae._cell_public(cell or make_cell()), 'auxiliary_cells': []}


def codes(result):
    return {e['code'] for e in result['errors']}


class GivebackTests(unittest.TestCase):
    def test_jpm_median_of_matched_differences_is_379_not_618(self):
        cell = make_cell()
        evidence = ae._cell_public(cell)['evidence']
        self.assertEqual(evidence['giveback']['median_pp'], 3.79)
        self.assertEqual(evidence['giveback']['n'], 15)
        self.assertAlmostEqual(median(r[2] for r in JPM) - median(r[1] for r in JPM), 6.18)
        quote = ae.build_quotables(cell)['give_back']
        self.assertIn('3.8 percentage points across 15 paired observations', quote)
        self.assertNotIn('median year', quote)
        self.assertIn('entry price', quote)

    def test_bearish_case_still_measures_underlying_price_not_short_pnl(self):
        cell = make_cell([(2023, -8, 1, -10), (2024, -4, 3, -5), (2025, -2, 2, -3)])
        self.assertEqual(cell.direction, 'bearish')
        fact = ae._cell_public(cell)['evidence']['giveback']
        self.assertEqual(fact['median_pp'], 7.0)  # median([9, 7, 4])
        self.assertEqual(fact['n'], 3)

    def test_missing_excursion_does_not_become_zero_or_remove_valid_net(self):
        cell = make_cell([(2020, 2, '', -4), (2021, 3, 'nan', 'inf'),
                          (2022, 4, 8, -2), (2023, 'nan', 5, -3)])
        self.assertEqual(cell.n, 3)
        self.assertIsNone(cell.per_year[0]['mfe'])
        self.assertIsNone(cell.per_year[1]['mfe'])
        evidence = ae._cell_public(cell)['evidence']
        self.assertEqual(evidence['giveback']['median_pp'], 4.0)
        self.assertEqual(evidence['giveback']['n'], 1)
        self.assertEqual(evidence['risk']['median_adverse_from_entry'], {'value_pct': -3.0, 'n': 2})
        self.assertEqual(evidence['risk']['median_favorable_from_entry'], {'value_pct': 8.0, 'n': 1})
        quote = ae.build_quotables(cell)
        self.assertNotIn('never_green', quote)
        self.assertIn('1 of 1 years with available highs', quote['touched'])
        json.dumps(ae._cell_public(cell), allow_nan=False)

    def test_all_missing_risk_is_explicitly_unavailable(self):
        cell = make_cell([(2023, 2, '', ''), (2024, -1, 'nan', '-inf')])
        evidence = ae._cell_public(cell)['evidence']
        self.assertEqual(evidence['giveback']['median_pp'], None)
        self.assertEqual(evidence['giveback']['n'], 0)
        self.assertIsNone(cell.median_mfe)
        self.assertIsNone(cell.median_mae)
        self.assertNotIn('give_back', ae.build_quotables(cell))
        self.assertNotIn('never_green', ae.build_quotables(cell))

    def test_duplicate_years_are_excluded_instead_of_overweighting(self):
        cell = make_cell([(2023, 2, 3, -1), (2023, 5, 6, -1), (2024, -2, 1, -3)])
        self.assertEqual(cell.n, 1)
        self.assertEqual(cell.worst_year, 2024)
        self.assertTrue(any('duplicate years: [2023]' in note for note in cell.notes))

    def test_invalid_negative_giveback_is_not_a_quotable(self):
        evidence = ae._cell_public(make_cell([(2023, 9, 5, -1)]))['evidence']
        self.assertEqual(evidence['giveback']['n'], 0)
        self.assertIsNone(evidence['giveback']['median_pp'])


class WindowAndCohortTests(unittest.TestCase):
    def test_inclusive_calendar_dates(self):
        for start, days, expected in [('2026-08-21', 30, '2026-09-19'),
                                      ('2026-07-01', 31, '2026-07-31'),
                                      ('2026-12-20', 30, '2027-01-18'),
                                      ('2024-02-28', 3, '2024-03-01'),
                                      ('2026-09-05', 1, '2026-09-05')]:
            self.assertEqual(inclusive_window(start, days)['end_date'], expected)
        for invalid in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                inclusive_window('2026-08-21', invalid)

    def test_current_year_not_complete_until_after_inclusive_final_date(self):
        raw = {'ChartData4': [{'year': 2025, 'pct': '1,2,-1'}, {'year': 2026, 'pct': '2,3,-1'}]}
        args = dict(resource_id='2', symbol='JPM', anchor='2026-08-21', days=30,
                    years='10', horizon_tag='30d')
        self.assertEqual(ae.derive_cell(raw, today=date(2026, 9, 19), **args).n, 1)
        self.assertEqual(ae.derive_cell(raw, today=date(2026, 9, 20), **args).n, 2)

    def test_midterm_sample_has_actual_decades_not_ten_consecutive_years(self):
        rows = [(year, 1, 3, -2) for year in range(1986, 2023, 4)]
        cell = make_cell(rows, years='pe2-10')
        evidence = ae._cell_public(cell)['evidence']
        cohort = evidence['cohort']
        self.assertEqual(cohort['years_code'], 'pe2-10')
        self.assertEqual((cohort['n'], cohort['first_year'], cohort['last_year']), (10, 1986, 2022))
        self.assertEqual(cohort['calendar_span_years'], 37)
        self.assertFalse(cohort['is_consecutive'])
        self.assertIn('midterm election observations spanning 1986–2022', cohort['label'])
        self.assertIn('10 of 10 sampled midterm election years', ae.build_quotables(cell)['record'])

    def test_recent_comparison_uses_disjoint_five_observations(self):
        evidence = ae._cell_public(make_cell())['evidence']
        comparison = evidence['recent_vs_earlier']
        recent, earlier = comparison['recent'], comparison['earlier']
        self.assertEqual(recent['years'], [2021, 2022, 2023, 2024, 2025])
        self.assertEqual(earlier['years'], list(range(2011, 2021)))
        self.assertEqual((recent['n'], earlier['n']), (5, 10))
        self.assertFalse(set(recent['years']) & set(earlier['years']))
        self.assertEqual(recent['median_net'], 4.68)
        self.assertEqual(earlier['median_net'], 8.12)
        self.assertIn((5, 5), _allowed_pairs(make_card()))

    def test_small_samples_omit_comparison_and_risk_timing_is_unknown(self):
        evidence = ae._cell_public(make_cell(JPM[:9]))['evidence']
        self.assertIsNone(evidence['recent_vs_earlier'])
        self.assertFalse(evidence['risk']['extrema_timing_known'])
        self.assertEqual(evidence['risk']['worst_adverse_from_entry']['value_pct'], -21.27)
        self.assertEqual(evidence['sensitivity']['excluded_year'], 2019)
        self.assertEqual(evidence['sensitivity']['without_largest_absolute_move']['n'], 8)


class DeterministicEvidenceGateTests(unittest.TestCase):
    def test_valid_new_card_passes_and_legacy_card_remains_readable(self):
        self.assertTrue(validate_card_evidence(make_card())['ok'])
        self.assertTrue(validate_card_evidence({'schema_version': 1, 'story_cell': {'n': 10}})['ok'])

    def test_new_card_cannot_omit_evidence(self):
        card = make_card()
        del card['story_cell']['evidence']
        self.assertIn('EVIDENCE_MISSING', codes(validate_card_evidence(card)))

    def test_malformed_cached_evidence_is_rejected_without_crashing_claim_check(self):
        card = make_card(make_cell(days=30))
        card['story_cell']['evidence'] = {'window': {}}
        self.assertIn('EVIDENCE_DERIVATION_MISMATCH', codes(validate_card_evidence(card)))
        bad = '<p>The 30-day window runs from Aug 21 to Sep 20.</p>'
        self.assertIn('WINDOW_RANGE_MISMATCH', codes(validate_evidence_claims(bad, card)))

    def test_recomputes_paired_math_not_trust_stale_quotable(self):
        card = make_card()
        card['story_cell']['evidence']['giveback']['median_pp'] = 6.18
        card['story_cell']['quotables']['give_back'] = 'the median year handed back 6.2 points'
        result = validate_card_evidence(card)
        self.assertIn('EVIDENCE_DERIVATION_MISMATCH', codes(result))
        self.assertIn('GIVEBACK_DERIVATION_MISMATCH', codes(result))

    def test_count_and_end_date_mutations_fail(self):
        card = make_card()
        card['story_cell']['n'] += 1
        card['story_cell']['end_date'] = '2026-11-20'
        self.assertIn('EVIDENCE_SUMMARY_MISMATCH', codes(validate_card_evidence(card)))
        self.assertIn('EVIDENCE_WINDOW_MISMATCH', codes(validate_card_evidence(card)))

    def test_stale_api_count_is_not_published_as_a_second_sample(self):
        card = make_card()
        card['story_cell']['stats_raw']['Num Winners'] = 14  # actual 13
        self.assertIn('EVIDENCE_STATS_MISMATCH', codes(validate_card_evidence(card)))

    def test_explicit_window_range_catches_off_by_one_in_prose(self):
        card = make_card(make_cell(days=30))
        good = '<p>The 30-day window runs from Aug 21 to Sep 19.</p>'
        bad = '<p>The 30-day window runs from Aug 21 to Sep 20.</p>'
        self.assertTrue(validate_evidence_claims(good, card)['ok'])
        self.assertIn('WINDOW_RANGE_MISMATCH', codes(validate_evidence_claims(bad, card)))
        iso = '<p>The window runs from 2026-08-21 through 2026-09-20.</p>'
        self.assertIn('WINDOW_RANGE_MISMATCH', codes(validate_evidence_claims(iso, card)))

    def test_unrelated_news_dates_and_licensed_auxiliary_windows_are_allowed(self):
        card = make_card(make_cell(days=30))
        card['auxiliary_cells'] = [ae._cell_public(make_cell(days=60))]
        article = ('<p>The company scheduled a conference from Aug 21 to Sep 20.</p>'
                   '<p>The 60-day window runs from Aug 21 through Oct 19.</p>')
        self.assertTrue(validate_evidence_claims(article, card)['ok'])

    def test_explicit_historical_median_cannot_be_invented(self):
        card = make_card()
        bad = '<p>The historical window had a median gain of 91.1%.</p>'
        good = '<p>The historical window had a median gain of 7.5% across 15 years.</p>'
        self.assertIn('DERIVED_RETURN_MISMATCH', codes(validate_evidence_claims(bad, card)))
        self.assertTrue(validate_evidence_claims(good, card)['ok'])
        self.assertIn('DERIVED_RETURN_MISMATCH', codes(validate_evidence_claims(
            '<p>The window had a median gain of 7.44%.</p>', card)))
        subgroup = '<p>The most recent five sampled observations had a median gain of 4.7%.</p>'
        self.assertTrue(validate_evidence_claims(subgroup, card)['ok'])
        news = '<p>The median return was 91.1% in the analyst survey.<sup>[1]</sup></p>'
        self.assertTrue(validate_evidence_claims(news, card)['ok'])

    def test_explicit_historical_return_keeps_its_sign(self):
        card = make_card(make_cell([(2023, -8, 1, -10), (2024, -4, 3, -5), (2025, -2, 2, -3)]))
        self.assertTrue(validate_evidence_claims('<p>The window had a median loss of 4.0%.</p>', card)['ok'])
        self.assertIn('DERIVED_RETURN_MISMATCH', codes(validate_evidence_claims(
            '<p>The window had a median gain of 4.0%.</p>', card)))

    def test_explicit_giveback_claim_must_match_paired_metric_and_sample(self):
        card = make_card()
        self.assertIn('GIVEBACK_CLAIM_MISMATCH', codes(validate_evidence_claims(
            '<p>The window had a median giveback of 6.2 percentage points.</p>', card)))
        self.assertIn('GIVEBACK_CLAIM_MISMATCH', codes(validate_evidence_claims(
            '<p>The decline had a median of 3.8 percentage points across 14 paired observations.</p>', card)))
        correct = '<p>' + card['story_cell']['quotables']['give_back'] + '.</p>'
        self.assertTrue(validate_evidence_claims(correct, card)['ok'])

    def test_primary_chart_cycle_sample_cannot_be_called_last_ten_years(self):
        card = make_card(make_cell([(year, 1, 3, -2) for year in range(1986, 2023, 4)], years='pe2-10'))
        bad = '<figcaption>Closed higher in 10 of the past 10 years.</figcaption>'
        good = '<figcaption>Closed higher in 10 of the past 10 midterm election years.</figcaption>'
        self.assertIn('COHORT_PERIOD_MISLABELED', codes(validate_evidence_claims(bad, card)))
        self.assertTrue(validate_evidence_claims(good, card)['ok'])
        self.assertIn('COHORT_PERIOD_MISLABELED', codes(check_article(bad, card=card)))


if __name__ == '__main__':
    unittest.main()
