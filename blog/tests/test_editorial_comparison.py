"""Keep secondary history inspectable when the narrative is shortened."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from seasonal_edition import comparison_html, comparison_rows


def sample(years, up, down, flat=0):
    return {'years':years, 'n':len(years), 'up_years':up,
            'down_years':down, 'flat_years':flat}


class ComparisonDisclosureTests(unittest.TestCase):
    def setUp(self):
        self.data={'card':{'selection_evidence':{
            'baseline':{'summary':sample([2022,2023,2024,2025],2,1,1)},
            'cycle':{'phase':2,
                'within_baseline':{'summary':sample([2022],1,0)},
                'noncycle_within_baseline':{'summary':sample([2023,2024,2025],1,1,1)},
                'full':{'summary':sample([2018,2022],1,1)}}}}}

    def test_literal_cycle_years_and_flat_outcomes_are_preserved(self):
        rows=comparison_rows(self.data)
        self.assertEqual(rows[-1]['years'],[2018,2022])
        self.assertEqual(rows[-2]['flat_years'],1)
        html=comparison_html(self.data)
        self.assertIn('2018, 2022',html)
        self.assertIn('Midterm-year',html)
        self.assertIn('Unchanged',html)
        self.assertIn('not independent confirmation',html)
        self.assertIn('forecast probabilities',html)
        self.assertNotIn(' open',html)

    def test_missing_cycle_is_not_rendered_as_a_zero_result(self):
        del self.data['card']['selection_evidence']['cycle']
        self.assertEqual(comparison_html(self.data),'')

    def test_inconsistent_counts_or_duplicate_years_are_rejected(self):
        for kind in ['count','duplicate','markup']:
            data=deepcopy(self.data)
            s=data['card']['selection_evidence']['cycle']['full']['summary']
            if kind=='count':s['up_years']=9
            elif kind=='duplicate':s['years']=[2022,2022]
            else:s['years']=[2018,'<script>']
            with self.assertRaises(ValueError):comparison_html(data)

    def test_other_cycle_phase_is_not_mislabeled_midterm(self):
        self.data['card']['selection_evidence']['cycle']['phase']=0
        self.assertIn('Election-year',comparison_html(self.data))
        self.assertNotIn('Midterm-year',comparison_html(self.data))

if __name__=='__main__':unittest.main()
