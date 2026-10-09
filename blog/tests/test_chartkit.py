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
from unittest.mock import patch

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

    def test_verified_flat_completed_year_is_kept(self):
        # AAPL 1986: a real 0.0% year. Keep it in the ten-year chart and
        # display the retained engine's reported count, not a renderer count.
        years = [1986, 1990, 1994, 1998, 2002, 2006, 2010, 2014, 2018, 2022]
        nets = [0.0, 10.78, 8.97, 0.46, 10.78, 10.72, 2.99, 8.21, 2.06, 10.33]
        meta = dict(BARS_META, verified_completed=True, engine_winners=9, engine_losers=1)
        s = ck.record_bars(years, nets, meta, self._p("b.png"))
        self.assertEqual(s["n"], 10)
        self.assertIn("TradeWave reports 9 winning years among 10 completed years", s["title"])

        # If the engine reports a different classification for a flat year,
        # that supplied result controls the label without changing the row.
        meta.update(engine_winners=10, engine_losers=0)
        s = ck.record_bars(years, nets, meta, self._p("engine-count.png"))
        self.assertEqual(s["n"], 10)
        self.assertIn("TradeWave reports 10 winning years among 10 completed years", s["title"])
        meta['engine_losers'] = 1
        with self.assertRaisesRegex(ValueError, 'differs from completed chart sample'):
            ck.record_bars(years, nets, meta, self._p("mismatch.png"))

    def test_zeroed_row_dropped_with_excursion(self):
        s = ck.record_bars(YEARS + [2026], NETS + [0.0], dict(BARS_META),
                           self._p("b.png"), mfe=MFE + [0.0], mae=MAE + [0.0])
        self.assertEqual(s["n"], 20)

    def test_alt_composition(self):
        s = ck.record_bars(YEARS, NETS, BARS_META, self._p("b.png"))
        self.assertEqual(s["alt"], f"{s['title']}. {s['spec']}. {s['source']}")

    def test_reference_series_keeps_qualification_with_exported_chart(self):
        meta=dict(BARS_META,symbol='GC',measurement='provider_reference_price_change')
        for mobile in (False,True):
            s=ck.record_bars(YEARS,NETS,meta,self._p('reference-'+str(mobile)+'.png'),
                             mfe=MFE,mae=MAE,mobile=mobile)
            self.assertEqual(s['n'],20)
            self.assertIn('final reference-price change',s['spec'])
            self.assertIn('session/roll validation pending',s['source'])
            self.assertIn('session/roll validation pending',s['alt'])

    def test_median_and_source_from_arrays(self):
        s = ck.record_bars(YEARS, NETS, BARS_META, self._p("b.png"))
        # n and year span derived from the drawn arrays
        self.assertIn("n=20 completed years", s["source"])
        self.assertIn("2006–2025", s["source"])

    def test_excursion_spec_variants(self):
        both = ck.record_bars(YEARS, NETS, dict(BARS_META), self._p("a.png"),
                              mfe=MFE, mae=MAE)
        self.assertIn("lowest to highest change from entry", both["spec"])
        self.assertIn("not peak-to-trough drawdown", both["spec"])
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

    def test_record_bars_median_label_keeps_last_bar_and_caps_clear(self):
        for count in (6, 7):
            for median in (2.75, -2.75):
                with self.subTest(count=count, median=median):
                    years = list(range(2019, 2019 + count))
                    nets = [1.0, -3.0, 4.0, -2.0, 5.0, -4.0, 6.0][:count]
                    engine = dict(price_median=median, title='Recorded outcomes',
                                  spec='Net return by year', source='TradeWave',
                                  caption='Recorded outcomes', mobile_title='Recorded outcomes')
                    meta = dict(BARS_META, engine_presentation=engine, range_caps=True)
                    captured = []
                    with patch.object(ck, '_save', side_effect=lambda fig, path: captured.append(fig)):
                        ck.record_bars(years, nets, meta, self._p('geometry.png'),
                                       mfe=[v + 2 for v in nets], mae=[v - 2 for v in nets])
                    fig = captured[0]
                    try:
                        ax = fig.axes[0]
                        fig.canvas.draw()
                        renderer = fig.canvas.get_renderer()
                        label = next(t for t in ax.texts if t.get_text() == f'median {median:+.2f}%')
                        box = label.get_window_extent(renderer)
                        plot = ax.get_window_extent(renderer)
                        last_cap = ax.transData.transform((count - 1 + .15, median))[0]
                        last_bar = ax.transData.transform((count - 1 + .31, median))[0]
                        self.assertGreater(box.x0, max(last_bar, last_cap) + 6)
                        self.assertLessEqual(box.x1, plot.x1 - 6)
                        self.assertEqual(label.get_fontsize(), 11)
                        self.assertEqual([bar.get_height() for bar in ax.patches], nets)
                    finally:
                        ck.plt.close(fig)

    def test_record_bars_long_median_uses_key_below_subtitle(self):
        engine = dict(price_median=123456789.12, title='Recorded outcomes',
                      spec='Net return by year', source='TradeWave',
                      caption='Recorded outcomes', mobile_title='Recorded outcomes')
        captured = []
        with patch.object(ck, '_save', side_effect=lambda fig, path: captured.append(fig)):
            ck.record_bars(list(range(2019, 2025)),
                           [123456780, 123456770, 123456790, 123456800, 123456785, 123456795],
                           dict(BARS_META, engine_presentation=engine), self._p('long.png'), w=500)
        fig = captured[0]
        try:
            ax = fig.axes[0]
            label = 'median +123456789.12%'
            self.assertFalse(any(t.get_text() == label for t in ax.texts))
            key = next(t for t in fig.texts if t.get_text() == label)
            self.assertEqual(key.get_fontsize(), 11)
            self.assertGreater(key.get_position()[1], ax.get_position().y1)
            self.assertLess(key.get_position()[1], .842)
            self.assertTrue(any(isinstance(line, ck.Line2D) for line in fig.artists))
        finally:
            ck.plt.close(fig)

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
        # The curve is rebased to 0 at index 0, which is TREND_MARGIN_DAYS
        # BEFORE the window opens. The spec must say why it starts there as
        # well as when: a bare "rebased to 0 at Jul 7" beside a Jul 21 window
        # reads as a contradiction and got XOM held on 2026-08-21.
        self.assertIn("two weeks before the window opens", s["spec"])
        self.assertIn("Jul 7", s["spec"])

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


def _texts(fig):
    """Every visible string on the figure: header/footer, axes, ticks, legend."""
    out = list(fig.texts)
    for ax in fig.axes:
        out += list(ax.texts) + ax.get_xticklabels() + ax.get_yticklabels()
        out += [ax.xaxis.label, ax.yaxis.label]
        if ax.get_legend():
            out += list(ax.get_legend().get_texts())
    return [t for t in out if t.get_visible() and t.get_text().strip()]


ENGINE = dict(price_median=5.36, title='SPY: the selected seasonal record',
              spec='7 selected midterm-election years (1998–2022) · Oct 29 – Dec 2, 2026',
              source='TradeWave engine · 7 selected midterm-election years (1998–2022)',
              caption='Each bar shows the price change.', mobile_title='SPY: seasonal record',
              mobile_spec='7 selected midterm-election years (1998–2022) · Oct 29 – Dec 2, 2026')
PRICE_META = dict(symbol='IWM', company='iShares Russell 2000 ETF (IWM)', kicker_tail='price and seasonal trend',
                  title="IWM: recorded prices and TradeWave's seasonal trend", mobile_title='IWM: price and seasonal trend',
                  spec='Daily closes to Oct 2, 2026 · dashed: TradeWave seasonal trend for the next 60 weekdays',
                  mobile_spec='Closes to Oct 2, 2026 · dashed: 60-weekday seasonal trend',
                  source='Source: TradeWave · trend from 6 selected midterm-election years (2002–2022) · illustration, not a forecast',
                  trend_label='TradeWave seasonal trend (normalized; not a forecast)',
                  mobile_trend_label='TradeWave seasonal trend (not a forecast)',
                  price_label='Share price (USD)', last_date='2026-10-02', last_close=279.02,
                  window_start='2026-10-24', window_end='2026-12-02', direction='long', n=6)


class IdentityAndReadability(unittest.TestCase):
    """Afshin, 2026-10-09: charts must name the security and be readable."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ck_")

    def _render(self, fn, *args, **kwargs):
        captured = []
        with patch.object(ck, '_save', side_effect=lambda fig, path: captured.append(fig)):
            sem = fn(*args, **kwargs)
        return captured[0], sem

    def _assert_readable(self, fig, min_px):
        try:
            for t in _texts(fig):
                self.assertGreaterEqual(t.get_fontsize() * fig.dpi / 72, min_px, t.get_text())
        finally:
            ck.plt.close(fig)

    def test_identity_kicker_names_symbol_and_company_once(self):
        self.assertEqual(ck.identity_kicker('IWM', 'iShares Russell 2000 ETF (IWM)', 'Seasonal record'),
                         'IWM · iShares Russell 2000 ETF · Seasonal record')
        self.assertEqual(ck.identity_kicker('SPY', 'SPDR S&P 500 ETF Trust'), 'SPY · SPDR S&P 500 ETF Trust')
        self.assertEqual(ck.identity_kicker('VIX', ''), 'VIX')

    def test_every_bar_variant_names_the_security(self):
        for mobile in (False, True):
            with self.subTest(mobile=mobile):
                meta = dict(BARS_META, symbol='SPY', company='SPDR S&P 500 ETF Trust', engine_presentation=ENGINE)
                fig, _ = self._render(ck.record_bars, YEARS, NETS, meta, os.path.join(self.tmp, 'b.png'),
                                      mfe=MFE, mae=MAE, mobile=mobile)
                # Desktop kickers are letter-tracked ('S  P  Y'); undo that to read them.
                header = [(s[::3] if s[1:3] == '  ' else s).upper() for s in (t.get_text() for t in fig.texts)]
                self.assertTrue(any(s.startswith('SPY · SPDR S&P 500 ETF TRUST') for s in header), header)
                # Desktop shows at ~0.58x and phones at ~0.46x; 18/24 image px
                # keeps every label at or above ~10.5-11 CSS px.
                self._assert_readable(fig, 22 if mobile else 18)

    def test_header_never_overlaps_plot_or_footer(self):
        for mobile in (False, True):
            meta = dict(BARS_META, engine_presentation=ENGINE)
            fig, _ = self._render(ck.record_bars, YEARS, NETS, meta, os.path.join(self.tmp, 'b.png'), mobile=mobile)
            try:
                fig.canvas.draw()
                r = fig.canvas.get_renderer()
                boxes = [t.get_window_extent(r) for t in fig.texts]
                plot = fig.axes[0].get_window_extent(r)
                header = [b for b in boxes if b.y0 > plot.y1 - 1]
                self.assertGreaterEqual(len(header), 3)
                for i, a in enumerate(boxes):
                    for b in boxes[i + 1:]:
                        self.assertFalse(a.overlaps(b), (a, b))
                for b in header:
                    self.assertGreater(b.y0, plot.y1)
            finally:
                ck.plt.close(fig)

    def test_engine_price_trend_identity_window_and_no_derived_values(self):
        dates = [datetime.datetime(2025, 10, 6) + datetime.timedelta(days=i) for i in range(0, 360, 2)]
        closes = [250 + (i % 17) * 0.8 for i in range(len(dates))]
        tdates = [dates[-1] + datetime.timedelta(days=i) for i in range(0, 84, 2)]
        trend = [closes[-1] + i * 0.9 for i in range(len(tdates))]
        for mobile, size in ((False, (1280, 720)), (True, (780, 1100))):
            with self.subTest(mobile=mobile):
                path = os.path.join(self.tmp, f'price-{mobile}.png')
                sem = ck.engine_price_trend(dates, closes, tdates, trend, PRICE_META, path, mobile=mobile)
                from PIL import Image
                with Image.open(path) as image:
                    self.assertEqual(image.size, size)
                self.assertTrue(sem['kicker'].startswith('IWM · iShares Russell 2000 ETF · '))
                fig, _ = self._render(ck.engine_price_trend, dates, closes, tdates, trend, PRICE_META, path, mobile=mobile)
                strings = [t.get_text() for t in _texts(fig)]
                legend = ' '.join(t.get_text() for t in fig.axes[0].get_legend().get_texts())
                self.assertIn('$279.02', legend)
                self.assertIn('Seasonal window Oct 24 – Dec 2', legend)
                self.assertIn('not a forecast', legend)
                # Pure presentation: no return, target or endpoint is printed.
                self.assertFalse([s for s in strings if '%' in s], strings)
                self._assert_readable(fig, 22 if mobile else 18)

    def test_engine_price_trend_window_after_displayed_dates(self):
        dates = [datetime.datetime(2025, 10, 6) + datetime.timedelta(days=i) for i in range(0, 120, 2)]
        closes = [50 + i * 0.1 for i in range(len(dates))]
        tdates = [dates[-1] + datetime.timedelta(days=i) for i in range(0, 30, 2)]
        meta = dict(PRICE_META, window_start='2027-03-01', window_end='2027-04-01')
        fig, _ = self._render(ck.engine_price_trend, dates, closes, tdates, closes[-len(tdates):], meta,
                              os.path.join(self.tmp, 'late.png'))
        try:
            self.assertTrue(any('opens Mar 1' in t.get_text() for t in fig.axes[0].texts))
        finally:
            ck.plt.close(fig)

    def test_long_titles_wrap_instead_of_shrinking_on_phones(self):
        title = 'Data Center dominated NVIDIA’s reported second-quarter revenue'
        fig, _ = ck.new_frame('NVDA · NVIDIA Corporation', title, 'Fiscal 2027 second quarter', 'Source: nvidia.com',
                              w=780, h=900, sizes=(13, 20, 13), track_kicker=False, header_frac=.89,
                              wrap_lines=(1, 2, 2))
        try:
            t = next(t for t in fig.texts if 'Data Center' in t.get_text())
            self.assertEqual(t.get_fontsize(), 20)
            self.assertIn('\n', t.get_text())
            fig.canvas.draw()
            self.assertLessEqual(t.get_window_extent(fig.canvas.get_renderer()).x1, 0.955 * 780)
        finally:
            ck.plt.close(fig)


if __name__ == "__main__":
    unittest.main()
