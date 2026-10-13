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
    def test_ordinal_cannot_hide_explicit_wrong_year(self):
        self.assertFalse(quoted_event_date('Release on Oct. 21st, 2025.',self.event,allow_yearless=True))
        self.assertTrue(quoted_event_date('Release on Oct. 21st, 2026.',self.event))
    def test_range_end_is_not_a_short_year(self):
        self.assertFalse(quoted_event_date('Release on Oct. 21\u201326, 2025.',self.event,allow_yearless=True))
        self.assertTrue(quoted_event_date('Conference Oct. 21\u201326, 2026.',self.event))
        self.assertFalse(quoted_event_date('Conference Oct. 21\u201326.',self.event))
    def test_sentence_boundary_stops_numeric_suffix(self):
        for punctuation in ['.','!','?']:
            with self.subTest(punctuation=punctuation):
                self.assertTrue(quoted_event_date('Release on Oct. 21'+punctuation+' 2 speakers will present.',self.event,allow_yearless=True))
    def test_ranges_ordinals_and_years_matrix(self):
        for separator in ['-','\u2013','\u2014',' to ',' through ']:
            for ordinal in ['', 'st']:
                with self.subTest(separator=separator,ordinal=ordinal):
                    self.assertFalse(quoted_event_date('Conference Oct. 21'+ordinal+separator+'26th (2025).',self.event,allow_yearless=True))
                    self.assertTrue(quoted_event_date('Conference Oct. 21'+ordinal+separator+'26th (2026).',self.event))
    def test_invalid_or_reversed_range(self):
        self.assertFalse(quoted_event_date('Conference Oct. 21-36, 2026.',self.event,allow_yearless=True))
        self.assertFalse(quoted_event_date('Conference Oct. 21-20, 2026.',self.event,allow_yearless=True))
    def test_range_does_not_invent_interior_date(self):
        self.assertFalse(quoted_event_date('Conference Oct. 21-26, 2026.',date(2026,10,23)))
    def test_invalid_ordinal_and_word_boundary(self):
        self.assertFalse(quoted_event_date('Release Oct. 21th, 2026.',self.event,allow_yearless=True))
        self.assertFalse(quoted_event_date('Office Oct. 21street, 2025.',self.event,allow_yearless=True))
    def test_explicit_year_stays_in_its_sentence(self):
        self.assertTrue(quoted_event_date('Release Oct. 21st. 2025 results will be discussed.',self.event,allow_yearless=True))
    def test_explicit_year_connector(self):
        for connector in ['in','of','year','of the year']:
            with self.subTest(connector=connector):
                self.assertFalse(quoted_event_date('Release Oct. 21st '+connector+' 2025.',self.event,allow_yearless=True))
                self.assertTrue(quoted_event_date('Release Oct. 21st '+connector+' 2026.',self.event))
    def test_number_in_concatenated_next_sentence(self):
        self.assertTrue(quoted_event_date('Release Oct. 21.2 speakers will present.',self.event,allow_yearless=True))
    def test_iso_date_requires_token_boundaries(self):
        for text in ['x2026-10-21','2026-10-21x','2026-10-210','2026-10-21-01']:
            with self.subTest(text=text):self.assertFalse(quoted_event_date(text,self.event))
    def test_year_requires_token_boundary(self):
        self.assertFalse(quoted_event_date('Release Oct. 21, 2026wrong.',self.event,allow_yearless=True))
    def test_range_endpoints_must_be_calendar_dates(self):
        self.assertFalse(quoted_event_date('Conference April 30-31, 2026.',date(2026,4,30)))
        self.assertFalse(quoted_event_date('Conference April 30-31.',date(2026,4,30),allow_yearless=True))
    def test_in_ordinary_prose_keeps_confirmed_yearless_date(self):
        for rendered in ['October 21','Oct 21','Oct. 21','Oct.21','October21','Oct. 21st','Oct.21st']:
            for prose in ['in New York','in the morning','in person']:
                with self.subTest(rendered=rendered,prose=prose):
                    text='Release on '+rendered+' '+prose+'.'
                    self.assertTrue(quoted_event_date(text,self.event,allow_yearless=True))
                    self.assertFalse(quoted_event_date(text,self.event))
    def test_in_explicit_wrong_or_malformed_year_is_not_prose(self):
        for suffix in ['in 2025','in 20260','in 202','in (2025)',"in '25",'in \u201925','in / 25','in the year 2025','in year 20260','in year unspecified']:
            with self.subTest(suffix=suffix):
                self.assertFalse(quoted_event_date('Release Oct.21 '+suffix+'.',self.event,allow_yearless=True))
    def test_in_explicit_current_year_still_matches(self):
        for suffix in ['in 2026','in (2026)',"in '26",'in the year 2026']:
            with self.subTest(suffix=suffix):
                self.assertTrue(quoted_event_date('Release Oct.21 '+suffix+'.',self.event,allow_yearless=True))

if __name__=='__main__':unittest.main()
