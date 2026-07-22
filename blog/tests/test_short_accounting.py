"""Regression: the key-stats box must never contradict the narrative.

ChartData4 can return Trade Dir = short, and its Winners/Losers and profit
figures are then SHORT-accounted - "winners" are years the window closed LOWER.
The narrative is always derived from per-year nets, so a short-accounted box
told the opposite story inside the same article.

Live case that exposed it (CL 30d x 20y, 2026-07-22):
    derived    : bearish, 6 up / 14 down, median -2.85%
    stats_raw  : Winners 14, Losers 6, Median Profit +2.85%
The editorial gate held the article. Every bearish article would have hit this;
it went unseen because every previously generated cell was bullish.
"""
import re
import sys
import unittest

sys.path.insert(0, "/home/flask/blog")
sys.path.insert(0, "/home/flask")

import angle_chrome as ac


def _rows(html):
    return dict(re.findall(r"<span>([^<]+)</span><span>([^<]+)</span>", html))


BEARISH_SHORT_CELL = {
    "n": 20, "up_years": 6, "down_years": 14, "median_net": -2.85,
    "days": 30, "years": "20", "anchor_date": "2026-07-22", "symbol": "CL",
    "stats_raw": {
        "Trade Dir": "short",
        "Num Winners": 14, "Num Losers": 6,
        "Percent Profitable": "70%", "Median Profit": "2.85%",
        "Avg Profit": "6.87%", "Avg Loss": "-3.1%",
        "Sharpe Ratio": "0.9", "Std Dev": "5.0%",
    },
}

BULLISH_LONG_CELL = {
    "n": 20, "up_years": 16, "down_years": 4, "median_net": 2.75,
    "days": 30, "years": "20", "anchor_date": "2026-07-21", "symbol": "DUK",
    "stats_raw": {
        "Trade Dir": "long",
        "Num Winners": 16, "Num Losers": 4,
        "Percent Profitable": "80%", "Median Profit": "2.75%",
        "Avg Profit": "3.75%", "Std Dev": "3.97%",
    },
}


class ShortAccountingTests(unittest.TestCase):
    def test_short_cell_counts_match_the_derived_direction(self):
        rows = _rows(ac.render_key_stats(BEARISH_SHORT_CELL))
        # winners must mean "years closed higher" = up_years, not short winners
        self.assertEqual(rows["Num Winners"], "6")
        self.assertEqual(rows["Num Losers"], "14")
        self.assertEqual(rows["Percent Profitable"], "30%")

    def test_short_cell_median_keeps_the_narrative_sign(self):
        rows = _rows(ac.render_key_stats(BEARISH_SHORT_CELL))
        self.assertTrue(rows["Median Profit"].startswith("-"),
                        "a bearish cell must not show a positive median")

    def test_short_cell_omits_stats_that_cannot_be_resigned(self):
        rows = _rows(ac.render_key_stats(BEARISH_SHORT_CELL))
        for label in ("Avg Profit", "Avg Loss", "Sharpe Ratio"):
            self.assertNotIn(label, rows,
                             "%s is short-accounted and must be omitted, "
                             "not shown with a misleading sign" % label)

    def test_long_cell_is_untouched(self):
        rows = _rows(ac.render_key_stats(BULLISH_LONG_CELL))
        self.assertEqual(rows["Num Winners"], "16")
        self.assertEqual(rows["Num Losers"], "4")
        self.assertEqual(rows["Percent Profitable"], "80%")
        self.assertEqual(rows["Avg Profit"], "3.75%")


if __name__ == "__main__":
    unittest.main()
