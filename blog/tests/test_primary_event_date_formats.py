import unittest
from datetime import date
from editorial_gate import quoted_event_date

class PrimaryEventDates(unittest.TestCase):
    def setUp(self):self.event=date(2026,10,21)
    def test_iso_date(self):self.assertTrue(quoted_event_date('Release on 2026-10-21.',self.event))
    def test_full_month_date(self):self.assertTrue(quoted_event_date('Release on October 21, 2026.',self.event))
    def test_abbreviated_month(self):self.assertTrue(quoted_event_date('Release on Oct 21, 2026.',self.event))
    def test_dotted_abbreviation(self):self.assertTrue(quoted_event_date('Release on Oct. 21, 2026.',self.event))
    def test_concatenated_dom_text(self):self.assertTrue(quoted_event_date('Release on Oct.21,2026.',self.event))
    def test_full_month_concatenated_dom_text(self):self.assertTrue(quoted_event_date('Release on October21,2026.',self.event))
    def test_case_and_nonbreaking_space(self):self.assertTrue(quoted_event_date('Release on OCT.\u00a021, 2026.',self.event))
    def test_yearless_requires_source_year_context(self):
        self.assertFalse(quoted_event_date('Release on Oct. 21.',self.event))
        self.assertTrue(quoted_event_date('Release on Oct. 21.',self.event,allow_yearless=True))
    def test_explicit_wrong_year_does_not_use_yearless_exception(self):
        self.assertFalse(quoted_event_date('Release on Oct. 21, 2025.',self.event,allow_yearless=True))
        self.assertFalse(quoted_event_date('Release on October 21, 2025.',self.event,allow_yearless=True))
    def test_wrong_day(self):self.assertFalse(quoted_event_date('Release on Oct. 22, 2026.',self.event,allow_yearless=True))
    def test_day_prefix_is_not_a_date(self):self.assertFalse(quoted_event_date('Release on Oct. 210, 2026.',self.event,allow_yearless=True))
    def test_wrong_month(self):self.assertFalse(quoted_event_date('Release on November 21, 2026.',self.event,allow_yearless=True))
    def test_missing_date(self):self.assertFalse(quoted_event_date('Earnings will be announced shortly.',self.event,allow_yearless=True))
    def test_malformed_year_is_not_yearless(self):self.assertFalse(quoted_event_date('Release on Oct. 21, 20260.',self.event,allow_yearless=True))
    def test_slash_wrong_year_is_explicit(self):self.assertFalse(quoted_event_date('Release on Oct. 21 / 2025.',self.event,allow_yearless=True))
    def test_short_wrong_year_is_explicit(self):
        self.assertFalse(quoted_event_date("Release on Oct. 21, '25.",self.event,allow_yearless=True))
        self.assertFalse(quoted_event_date('Release on Oct. 21,\u201925.',self.event,allow_yearless=True))
    def test_slash_current_year(self):self.assertTrue(quoted_event_date('Release on Oct. 21 / 2026.',self.event))
    def test_short_year_requires_confirmed_current_year_context(self):
        self.assertFalse(quoted_event_date("Release on Oct. 21, '26.",self.event))
        self.assertTrue(quoted_event_date("Release on Oct. 21, '26.",self.event,allow_yearless=True))
    def test_time_is_not_a_short_year(self):self.assertTrue(quoted_event_date('Release on Oct. 21 13:00.',self.event,allow_yearless=True))
    def test_wrong_year_across_ascii_and_unicode_punctuation(self):
        separators=['(', ' (', ' [', ' / ', ' - ', '\u2013', ' \u2013 ', '\u2014', ' \u2014 ', "'", '\u2019', ' | ', ' _ ', ' : ', ' , ', ' / (', ' \u00a0[', ' \u2022 ']
        for sep in separators:
            with self.subTest(separator=sep):
                self.assertFalse(quoted_event_date('Release on Oct. 21'+sep+'2025).',self.event,allow_yearless=True))
    def test_correct_year_across_punctuation(self):
        for sep in ['(', ' (', ' / ', '\u2013', '\u2014', ' [', ' | ']:
            with self.subTest(separator=sep):
                self.assertTrue(quoted_event_date('Release on Oct. 21'+sep+'2026).',self.event))
    def test_ambiguous_numeric_suffix_is_not_yearless(self):
        for token in ['2','202','20260','202600']:
            with self.subTest(token=token):
                self.assertFalse(quoted_event_date('Release on Oct. 21('+token+').',self.event,allow_yearless=True))

if __name__=='__main__':unittest.main()
