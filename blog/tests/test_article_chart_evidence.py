"""Chart identity must be checked before canonical captions replace labels."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from article_chart_evidence import chart_accounting_basis, validate_chart_evidence


CARD = {'story_cell': {'n': 10, 'anchor_date': '2026-08-21', 'days': 30,
                       'years': 'pe2-10', 'direction': 'bearish'}}
IMAGE = {'variant': 'bars', 'caption': 'Historical observations',
         'semantics': {'n': 10, 'window_start': '2026-08-21',
                       'window_end': '2026-09-19', 'direction': 'long'}}


def codes(result):
    return {item['code'] for item in result['errors']}


class ChartEvidenceTests(unittest.TestCase):
    def test_matching_explicit_metadata_passes(self):
        self.assertTrue(validate_chart_evidence(CARD, [IMAGE], ['bars'])['ok'])

    def test_actual_count_disagreement_is_not_hidden_by_cohort_caption(self):
        image = deepcopy(IMAGE)
        image['caption'] = '10 midterm election observations spanning 1986–2022'
        image['semantics']['n'] = 11
        self.assertIn('CHART_SAMPLE_MISMATCH', codes(validate_chart_evidence(CARD, [image], ['bars'])))

    def test_end_date_off_by_one_and_wrong_anchor_are_rejected(self):
        for field, bad in [('window_end', '2026-09-20'), ('window_start', '2026-08-22')]:
            image = deepcopy(IMAGE)
            image['semantics'][field] = bad
            self.assertIn('CHART_WINDOW_MISMATCH', codes(validate_chart_evidence(CARD, [image])))

    def test_unselected_and_price_context_charts_are_exempt(self):
        image = deepcopy(IMAGE)
        image['semantics']['n'] = 15
        self.assertTrue(validate_chart_evidence(CARD, [image], ['trend'])['ok'])
        self.assertTrue(validate_chart_evidence(CARD, [image], [])['ok'])
        for variant in ('price', 'price_proj_30', 'price_proj_90'):
            image['variant'] = variant
            self.assertTrue(validate_chart_evidence(CARD, [image])['ok'])

    def test_absent_legacy_semantics_remain_compatible(self):
        for image in ({'variant': 'bars'}, {'variant': 'bars', 'semantics': {}},
                      {'variant': 'bars', 'semantics': {'direction': 'long'}}):
            self.assertTrue(validate_chart_evidence(CARD, [image])['ok'])

    def test_present_nonfinite_fractional_or_malformed_fields_fail(self):
        for field, invalid in [('n', 'nan'), ('n', 10.5), ('n', -1),
                               ('window_start', 'not a date'), ('window_end', '2026-09-31')]:
            image = deepcopy(IMAGE)
            image['semantics'][field] = invalid
            self.assertIn('CHART_EVIDENCE_INVALID', codes(validate_chart_evidence(CARD, [image])))

    def test_row_count_recomputed_instead_of_trusting_stale_card_count(self):
        card = deepcopy(CARD)
        card['story_cell']['per_year'] = [
            {'year': 2023, 'net': 1, 'mfe': 2, 'mae': -1},
            {'year': 2024, 'net': 1, 'mfe': 2, 'mae': -1},
            {'year': 2025, 'net': 'nan', 'mfe': 2, 'mae': -1}]
        image = deepcopy(IMAGE)
        image['semantics']['n'] = 2
        self.assertTrue(validate_chart_evidence(card, [image])['ok'])
        image['semantics']['n'] = 3
        self.assertIn('CHART_SAMPLE_MISMATCH', codes(validate_chart_evidence(card, [image])))


class ChartAccountingTests(unittest.TestCase):
    def test_direction_is_renderer_accounting_not_seasonal_bias(self):
        self.assertEqual(chart_accounting_basis(IMAGE), 'long')
        self.assertEqual(chart_accounting_basis({'semantics': {'direction': 'short'}}), 'short')
        self.assertEqual(chart_accounting_basis({'semantics': {'direction': 'bearish'}}), 'unknown')

    def test_explicit_metadata_precedes_legacy_caption_heuristic(self):
        image = dict(IMAGE, caption='Shorting the window compounds to 20%.')
        self.assertEqual(chart_accounting_basis(image), 'long')

    def test_legacy_caption_requires_explicit_short_side_wording(self):
        self.assertEqual(chart_accounting_basis({'caption': 'Cumulative short-side return'}), 'short')
        self.assertEqual(chart_accounting_basis({'alt': 'Shorting the window compounds to 20%.'}), 'short')
        self.assertEqual(chart_accounting_basis({'caption': 'A short window with bearish history'}), 'unknown')


if __name__ == '__main__':
    unittest.main()
