"""Cross-surface numeric agreement. Each case is a defect that shipped."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from consistency_gate import check_article


def _codes(result):
    return {e["code"] for e in result["errors"]}


KEYSTATS = (
    '<aside class="key-stats"><div class="row">Sample Size<span>10 years</span></div>'
    '<div class="row">Percent Profitable<span>0%</span></div>'
    '<div class="row">Num Winners<span>0</span></div>'
    '<div class="row">Num Losers<span>9</span></div></aside>'
)
SOURCES = ('<section class="sources"><ol><li><a href="https://a.test">A</a></li>'
           '<li><a href="https://b.test">B</a></li></ol></section>')


def _doc(body="", captions=(), keystats=KEYSTATS, sources=SOURCES):
    figs = "".join(f"<figure><figcaption>{c}</figcaption></figure>" for c in captions)
    return f"<article><p>{body}</p>{keystats}{figs}{sources}</article>"


class CaptionSampleTests(unittest.TestCase):
    def test_two_captions_disagreeing_on_sample_is_hard(self):
        # Ford 2026-08-17: "9 of the past 9" beside "10 of the past 10".
        doc = _doc(captions=[
            "F has closed lower in 9 of the past 9 years. n=9 completed years",
            "F has closed lower in 10 of the past 10 years. n=10 completed years"])

        self.assertIn("CAPTION_SAMPLE_DISAGREEMENT", _codes(check_article(doc)))

    def test_captions_agreeing_with_keystats_pass(self):
        doc = _doc(captions=["F has closed lower in 9 of the past 10 years. n=10 completed years"])

        self.assertNotIn("CAPTION_SAMPLE_DISAGREEMENT", _codes(check_article(doc)))
        self.assertNotIn("CAPTION_SAMPLE_NOT_KEYSTATS", _codes(check_article(doc)))

    def test_prose_may_cite_a_second_sample_for_an_auxiliary_cell(self):
        # The FORK/REGIME comparison is the point of the angle engine: a
        # 10-year midterm cell against 20 years of unconditioned history.
        doc = _doc(body="Closed lower in 9 of 10 midterm years, while 14 of 20 "
                        "years in the full history closed higher.",
                   captions=["n=10 completed years"])

        self.assertEqual(_codes(check_article(doc)), set())


class CumulativeSignTests(unittest.TestCase):
    def test_positive_cumulative_on_a_falling_pattern_must_say_short(self):
        doc = _doc(captions=["Stacking the Aug 17 - Sep 15 window compounds to "
                             "+61.0% over 10 years"])

        self.assertIn("CUMULATIVE_SIGN_UNEXPLAINED", _codes(check_article(doc)))

    def test_saying_short_clears_it(self):
        doc = _doc(captions=["Shorting the Aug 17 - Sep 15 window compounds to "
                             "+61.0% over 10 years"])

        self.assertNotIn("CUMULATIVE_SIGN_UNEXPLAINED", _codes(check_article(doc)))

    def test_positive_cumulative_on_a_rising_pattern_is_fine(self):
        rising = KEYSTATS.replace("Num Winners<span>0", "Num Winners<span>9").replace(
            "Num Losers<span>9", "Num Losers<span>1")
        doc = _doc(keystats=rising,
                   captions=["Stacking the window compounds to +61.0% over 10 years"])

        self.assertNotIn("CUMULATIVE_SIGN_UNEXPLAINED", _codes(check_article(doc)))

    def test_explicit_direction_overrides_the_win_loss_heuristic(self):
        doc = _doc(captions=["The window compounds to +61.0% over 10 years"])

        self.assertIn("CUMULATIVE_SIGN_UNEXPLAINED",
                      _codes(check_article(doc, direction="short")))


class StatsArithmeticTests(unittest.TestCase):
    def test_winners_plus_losers_over_sample_is_hard(self):
        bad = KEYSTATS.replace("Num Winners<span>0", "Num Winners<span>4")

        self.assertIn("STATS_SUM_EXCEEDS_SAMPLE", _codes(check_article(_doc(keystats=bad))))

    def test_a_flat_year_is_a_warning_not_an_error(self):
        # 10 years, 0 winners, 9 losers: one genuinely flat year.
        result = check_article(_doc(captions=["n=10 completed years"]))

        self.assertNotIn("STATS_SUM_EXCEEDS_SAMPLE", _codes(result))
        self.assertIn("STATS_SUM_BELOW_SAMPLE", {w["code"] for w in result["warnings"]})


class CitationTests(unittest.TestCase):
    def test_marker_beyond_the_sources_list(self):
        doc = _doc(body='Revenue rose<sup>[5]</sup>.', captions=["n=10 completed years"])

        self.assertIn("CITATION_ORPHAN", _codes(check_article(doc)))

    def test_marker_inside_the_list_is_fine(self):
        doc = _doc(body='Revenue rose<sup>[2]</sup>.', captions=["n=10 completed years"])

        self.assertNotIn("CITATION_ORPHAN", _codes(check_article(doc)))


class EmptyInputTests(unittest.TestCase):
    def test_empty_article_does_not_raise(self):
        self.assertTrue(check_article("")["ok"])


class CaptionCountTests(unittest.TestCase):
    def test_caption_folding_a_flat_year_into_losses_is_hard(self):
        # 10 years, 0 winners, 9 losers, one flat: "10 of the past 10" is wrong.
        doc = _doc(captions=["F has closed lower in 10 of the past 10 years"])

        self.assertIn("CAPTION_COUNT_MISMATCH", _codes(check_article(doc)))

    def test_caption_matching_the_counted_losers_passes(self):
        doc = _doc(captions=["F has closed lower in 9 of the past 10 years"])

        self.assertNotIn("CAPTION_COUNT_MISMATCH", _codes(check_article(doc)))


if __name__ == "__main__":
    unittest.main()
