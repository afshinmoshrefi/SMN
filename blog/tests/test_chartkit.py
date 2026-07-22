"""Offline tests for chartkit.py — the production chart visual system.

Covers semantics correctness (win counts, zeroed-row dropping, alt composition,
sanitization) and a render smoke test per renderer. Synthetic data only, tmp
output paths, no network.
"""
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chartkit as ck


YEARS = list(range(2006, 2026))
# 14 strictly-positive, 6 non-positive -> wins == 14
NETS = [5, -3, 12, 8, -1, 20, 4, -8, 15, 7, 3, -2, 9, 11, -5, 18, 6, 2, -4, 14]
MFE = [v + 6 for v in NETS]
MAE = [v - 7 for v in NETS]
BARS_META = dict(symbol="NVDA", company="Nvidia", direction="long",
                 window_start="2026-07-21", window_end="2026-09-18",
                 days=60, lookback_label="20 Years of Historical Data")


class Semantics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ck_")

    def _p(self, name):
        return os.path.join(self.tmp, name)

    def test_win_count_and_title(self):
        s = ck.record_bars(YEARS, NETS, BARS_META, self._p("b.png"))
        self.assertEqual(s["n"], 20)
        # 14 nets > 0 -> "higher in 14 of the past 20"
        self.assertIn("higher in 14 of the past 20 years", s["title"])
        self.assertEqual(s["variant"], "record_bars")
        self.assertEqual(s["window_start"], "2026-07-21")
        self.assertEqual(s["window_end"], "2026-09-18")
        self.assertEqual(s["direction"], "long")

    def test_short_direction_flips_k(self):
        meta = dict(BARS_META, direction="short")
        s = ck.record_bars(YEARS, NETS, meta, self._p("b.png"))
        # short: k = losses = n - wins = 6, word "lower"
        self.assertIn("lower in 6 of the past 20 years", s["title"])

    def test_zeroed_row_dropped(self):
        # append a placeholder current-year "0,0,0" row
        s = ck.record_bars(YEARS + [2026], NETS + [0.0], BARS_META,
                           self._p("b.png"))
        self.assertEqual(s["n"], 20)  # zeroed row dropped, not counted
        self.assertIn("2006–2025", s["source"])

    def test_zeroed_row_dropped_with_excursion(self):
        s = ck.record_bars(YEARS + [2026], NETS + [0.0], dict(BARS_META),
                           self._p("b.png"), mfe=MFE + [0.0], mae=MAE + [0.0])
        self.assertEqual(s["n"], 20)

    def test_alt_composition(self):
        s = ck.record_bars(YEARS, NETS, BARS_META, self._p("b.png"))
        self.assertEqual(s["alt"], f"{s['title']}. {s['spec']}. {s['source']}")

    def test_median_and_source_from_arrays(self):
        s = ck.record_bars(YEARS, NETS, BARS_META, self._p("b.png"))
        # n and year span derived from the drawn arrays
        self.assertIn("n=20 completed years", s["source"])
        self.assertIn("2006–2025", s["source"])

    def test_excursion_spec_variants(self):
        both = ck.record_bars(YEARS, NETS, dict(BARS_META), self._p("a.png"),
                              mfe=MFE, mae=MAE)
        self.assertIn("worst drawdown to best gain", both["spec"])
        mfe_only = ck.record_bars(YEARS, NETS, dict(BARS_META), self._p("c.png"),
                                  mfe=MFE)
        self.assertIn("best gain", mfe_only["spec"])
        mae_only = ck.record_bars(YEARS, NETS, dict(BARS_META), self._p("d.png"),
                                  mae=MAE)
        self.assertIn("worst drawdown", mae_only["spec"])

    def test_sanitize_replaces_arrows(self):
        self.assertEqual(ck._sanitize("Jul 21 ➝ Sep 18"), "Jul 21 – Sep 18")
        self.assertEqual(ck._sanitize("A → B"), "A – B")
        self.assertEqual(ck._sanitize(None), "")

    def test_sanitize_applied_to_meta(self):
        meta = dict(BARS_META, kicker="NVDA ➝ path")
        s = ck.record_bars(YEARS, NETS, meta, self._p("b.png"))
        # sanitization happens on drawn text; title/spec/source never carry ➝
        self.assertNotIn("➝", s["alt"])

    def test_fmt_mmm_d(self):
        self.assertEqual(ck._fmt_mmm_d("2026-07-21"), "Jul 21")
        self.assertEqual(ck._fmt_mmm_d("2026-09-08"), "Sep 8")

    def test_median_helper(self):
        self.assertEqual(ck._median([1, 2, 3]), 2)
        self.assertEqual(ck._median([1, 2, 3, 4]), 2.5)

    def test_drop_zeroed_keeps_real_data(self):
        y, n, f, a = ck._drop_zeroed([2024, 2025, 2026], [3.0, -2.0, 0.0],
                                     None, None)
        self.assertEqual(y, [2024, 2025])
        self.assertEqual(n, [3.0, -2.0])


class RenderSmoke(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ck_")

    def _p(self, name):
        return os.path.join(self.tmp, name)

    def _assert_file(self, path):
        self.assertTrue(os.path.exists(path))
        self.assertGreater(os.path.getsize(path), 1000)

    def test_record_bars_render(self):
        p = self._p("bars.png")
        ck.record_bars(YEARS, NETS, BARS_META, p)
        self._assert_file(p)

    def test_record_bars_excursion_render(self):
        p = self._p("bars_ex.png")
        ck.record_bars(YEARS, NETS, dict(BARS_META), p, mfe=MFE, mae=MAE)
        self._assert_file(p)

    def test_record_bars_dark_render(self):
        p = self._p("bars_dark.png")
        ck.record_bars(YEARS, NETS, BARS_META, p, palette="dark")
        self._assert_file(p)

    def test_trend_window_render(self):
        labels = [(datetime.date(2026, 7, 7) + datetime.timedelta(days=i)).isoformat()
                  for i in range(90)]
        vals = [10 + i * 0.1 for i in range(90)]  # non-zero base to prove rebasing
        p = self._p("trend.png")
        s = ck.trend_window(labels, vals, "2026-07-21", "2026-09-18", "long",
                            dict(symbol="NVDA", company="Nvidia", n=20,
                                 year_first=2006, year_last=2025, days=60), p)
        self._assert_file(p)
        self.assertIn("average year", s["title"])
        # rebased to 0 at index 0 -> spec mentions rebased to the start label
        self.assertIn("rebased to 0 at Jul 7", s["spec"])

    def test_price_projection_render(self):
        dates = [datetime.date(2025, 7, 21) + datetime.timedelta(days=i)
                 for i in range(0, 360, 3)]
        prices = [100 + i * 0.2 for i in range(len(dates))]
        pdates = [dates[-1] + datetime.timedelta(days=i) for i in range(0, 60, 3)]
        pprices = [prices[-1] * (1 + i * 0.003) for i in range(len(pdates))]
        p = self._p("price.png")
        s = ck.price_projection(dates, prices, pdates, pprices,
                                dict(symbol="NVDA", company="Nvidia", n=20,
                                     proj_days=60, one_month="-3.5%"), p)
        self._assert_file(p)
        self.assertIn("enters the window at", s["title"])

    def test_price_projection_no_projection(self):
        dates = [datetime.date(2025, 7, 21) + datetime.timedelta(days=i)
                 for i in range(0, 120, 3)]
        prices = [100 + i * 0.2 for i in range(len(dates))]
        p = self._p("price_np.png")
        s = ck.price_projection(dates, prices, [], [],
                                dict(symbol="NVDA", n=20, proj_days=60), p)
        self._assert_file(p)
        self.assertEqual(s["variant"], "price_projection")

    def test_cumulative_render(self):
        p = self._p("cum.png")
        s = ck.cumulative(YEARS, [i * 2.0 for i in range(20)],
                          dict(symbol="NVDA", company="Nvidia", direction="long",
                               window_start="2026-07-21", window_end="2026-09-18",
                               days=60), p)
        self._assert_file(p)
        self.assertEqual(s["n"], 20)

    def test_cumulative_negative_end(self):
        p = self._p("cum_neg.png")
        s = ck.cumulative(YEARS, [-(i * 1.0) for i in range(20)],
                          dict(symbol="NVDA", direction="short"), p)
        self._assert_file(p)
        self.assertIn("-", s["title"])

    def test_stats_table_render(self):
        pairs = [("Symbol", "NVDA"), ("Trade Direction", "Long"),
                 ("History Years", "20"), ("Days Hold", "60"),
                 ("Avg Gain", "8.1%"), ("Success (W|L)", "16 of 20 (80%)")]
        p = self._p("stats.png")
        s = ck.stats_table(pairs, dict(symbol="NVDA", company="Nvidia",
                                       direction="long",
                                       window_start="2026-07-21",
                                       window_end="2026-09-18"), p)
        self._assert_file(p)
        self.assertEqual(s["variant"], "stats")

    def test_fork_panels_render(self):
        p = self._p("fork.png")
        s = ck.fork_panels(
            [(list(range(2016, 2026)), [3, -2, 5, 1, -4, 8, 2, -1, 6, 3],
              "Next 30 days"),
             (list(range(2006, 2026)), [i % 5 - 2 for i in range(20)],
              "Next 90 days")],
            dict(kicker="XOM · two horizons", title="XOM leans two ways",
                 spec="Net % over each window", source="Source: TradeWave"), p)
        self._assert_file(p)
        self.assertEqual(s["n"], 30)
        self.assertEqual(s["variant"], "fork")


if __name__ == "__main__":
    unittest.main()
