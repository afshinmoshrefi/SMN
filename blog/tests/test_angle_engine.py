"""angle_engine is deterministic and fully testable offline: synthetic
ChartData4 responses in, cells/scores/angle decisions/Angle Cards out.
No network, no config, no LLM."""
import datetime
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import angle_engine as ae


TODAY = datetime.date(2026, 7, 20)
ANCHOR = "2026-07-21"


def raw_response(nets, start_year=None, stats=None, mfe=5.0, mae=-5.0):
    """Synthetic ChartData4 payload. nets is a list of per-year net returns,
    oldest first; years count back from 2025 so the incomplete current year
    never appears unless a test adds it explicitly."""
    if start_year is None:
        start_year = 2025 - len(nets) + 1
    rows = [{"year": start_year + i, "pct": f"{net},{mfe},{mae}", "price": "10,11"}
            for i, net in enumerate(nets)]
    return {"ChartData4": rows, "stats": stats or {}}


def derive(nets, years="10", days=60, tag=None, stats=None, anchor=ANCHOR):
    cell = ae.derive_cell(
        raw_response(nets, stats=stats), resource_id="2", symbol="TEST",
        anchor=anchor, days=days, years=years,
        horizon_tag=tag or f"{days}d", today=TODAY)
    assert cell is not None
    return cell


def score_and_select(cells, ctx_dir=0, ctx_source="none", news_fresh=False):
    ae.score_cells(cells, ctx_dir)
    return ae.select_angle(cells, ctx_dir=ctx_dir, ctx_source=ctx_source,
                           news_fresh=news_fresh)


class TestBinomTail(unittest.TestCase):
    def test_exact_values(self):
        self.assertAlmostEqual(ae.binom_tail(10, 8), 56 / 1024)          # 0.0547
        self.assertAlmostEqual(ae.binom_tail(10, 9), 11 / 1024)          # 0.0107
        self.assertAlmostEqual(ae.binom_tail(10, 10), 1 / 1024)
        self.assertAlmostEqual(ae.binom_tail(14, 11), 470 / 16384)       # 0.0287
        self.assertEqual(ae.binom_tail(10, 0), 1.0)
        self.assertEqual(ae.binom_tail(10, 11), 0.0)

    def test_surprise_scale(self):
        self.assertAlmostEqual(ae.surprise(0.01), 2.0)
        self.assertGreater(ae.surprise(0.001), ae.surprise(0.01))


class TestDeriveCell(unittest.TestCase):
    def test_counts_direction_median(self):
        cell = derive([2.0, -1.0, 3.0, 4.0, -2.0, 5.0, 6.0, 1.0, 2.5, 3.5])
        self.assertEqual((cell.n, cell.up_years, cell.down_years), (10, 8, 2))
        self.assertEqual(cell.direction, "bullish")
        self.assertAlmostEqual(cell.median_net, 2.75)
        self.assertEqual(cell.best_net, 6.0)
        self.assertEqual(cell.worst_net, -2.0)

    def test_zero_rows_dropped(self):
        raw = raw_response([1.0, 2.0, 3.0])
        raw["ChartData4"].append({"year": 2026, "pct": "0,0,0", "price": "0,0"})
        cell = ae.derive_cell(raw, resource_id="2", symbol="TEST", anchor=ANCHOR,
                              days=60, years="10", horizon_tag="60d", today=TODAY)
        self.assertEqual(cell.n, 3)

    def test_incomplete_current_year_dropped(self):
        # A 2026 row with nonzero values must still be excluded while the
        # anchored window has not completed (anchor 2026-07-21 + 60d > today).
        raw = raw_response([1.0, 2.0, 3.0])
        raw["ChartData4"].append({"year": 2026, "pct": "9,9,-1", "price": "1,2"})
        cell = ae.derive_cell(raw, resource_id="2", symbol="TEST", anchor=ANCHOR,
                              days=60, years="10", horizon_tag="60d", today=TODAY)
        self.assertEqual(cell.n, 3)
        # ...but kept once the window is historical (anchor far in the past).
        cell2 = ae.derive_cell(raw, resource_id="2", symbol="TEST",
                               anchor="2026-01-02", days=60, years="10",
                               horizon_tag="60d", today=TODAY)
        self.assertEqual(cell2.n, 4)

    def test_stats_mismatch_flag(self):
        stats = {"Num Winners": "9", "Num Losers": "1", "Trade Dir": "Long"}
        cell = derive([1, 2, 3, 4, 5, 6, 7, 8, -1, -2], stats=stats)   # 8 up 2 dn
        self.assertTrue(cell.stats_mismatch)
        ok = derive([1, 2, 3, 4, 5, 6, 7, 8, 9, -2],
                    stats={"Num Winners": "9", "Num Losers": "1", "Trade Dir": "Long"})
        self.assertFalse(ok.stats_mismatch)

    def test_short_direction_stats_agree(self):
        # For a short-labeled pattern the API counts down years as winners.
        stats = {"Num Winners": "8", "Num Losers": "2", "Trade Dir": "Short"}
        cell = derive([-1, -2, -3, -4, -5, -6, -7, -8, 1, 2], stats=stats)
        self.assertEqual(cell.direction, "bearish")
        self.assertFalse(cell.stats_mismatch)


class TestScoring(unittest.TestCase):
    def test_floors(self):
        small = derive([1, 2, 3, 4, 5, 6, 7], years="10")            # n=7 cons
        pe_ok = derive([-1, -2, -3, -4, -5, -6], years="pe2-10")     # n=6 pe
        ae.score_cells([small, pe_ok], ctx_dir=0)
        self.assertFalse(small.eligible)
        self.assertIn("below cons floor", small.ineligible_reason)
        self.assertTrue(pe_ok.eligible)

    def test_coin_flip_ineligible(self):
        flip = derive([1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 1])        # 6 up / 5 dn
        ae.score_cells([flip], ctx_dir=0)
        self.assertFalse(flip.eligible)
        self.assertIn("coin-flip", flip.ineligible_reason)

    def test_flat_tie_ineligible(self):
        tie = derive([1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 1, -1])     # 6/6
        ae.score_cells([tie], ctx_dir=0)
        self.assertFalse(tie.eligible)
        self.assertIn("no directional majority", tie.ineligible_reason)

    def test_magnitude_path(self):
        # 7 of 10 (tail 0.172) fails even the magnitude path...
        seven = derive([4, 5, 6, 7, 8, 9, 10, -4, -5, -6])
        # ...8 of 10 (tail 0.0547) passes the striking path outright.
        eight = derive([4, 5, 6, 7, 8, 9, 10, 11, -4, -5])
        ae.score_cells([seven, eight], ctx_dir=0)
        self.assertFalse(seven.eligible)
        self.assertTrue(eight.eligible)

    def test_tension_symmetric(self):
        bear = derive([-1, -2, -3, -4, -5, -6, -7, -8, -9, 1])       # 9 dn
        ae.score_cells([bear], ctx_dir=1)                            # bullish ctx
        self.assertEqual(bear.tension, 1.0)
        self.assertGreater(bear.story_score, bear.conviction)
        ae.score_cells([bear], ctx_dir=-1)                           # bearish ctx
        self.assertEqual(bear.tension, 0.0)


class TestAngleSelection(unittest.TestCase):
    def test_collision_beats_clockwork_when_tension(self):
        colliding = derive([-1, -2, -3, -4, -5, -6, -7, -8, -9, -10, -11, 1, 2, 3, -4],
                           days=60)                                  # 12 dn of 15
        decision, ranked = score_and_select(
            [colliding], ctx_dir=1, ctx_source="news", news_fresh=True)
        self.assertEqual(decision.angle, "COLLISION")
        self.assertIn("news bullish vs bearish history", decision.rationale)

    def test_tailwind_when_aligned_and_striking(self):
        # 8 of 10: striking but NOT extreme — the news peg carries the piece.
        aligned = derive([1, 2, 3, 4, 5, 6, 7, 8, -1, -2], days=60)
        decision, _ = score_and_select(
            [aligned], ctx_dir=1, ctx_source="news", news_fresh=True)
        self.assertEqual(decision.angle, "TAILWIND")

    def test_extreme_aligned_is_clockwork_not_tailwind(self):
        # 9 of 10 is extreme: the streak is the story "regardless of peg"
        # (design: news becomes seasoning). TAILWIND remains the fallback.
        aligned = derive([1, 2, 3, 4, 5, 6, 7, 8, 9, -1], days=60)
        decision, ranked = score_and_select(
            [aligned], ctx_dir=1, ctx_source="news", news_fresh=True)
        self.assertEqual(decision.angle, "CLOCKWORK")
        self.assertIn("TAILWIND", [d.angle for d in ranked])

    def test_clockwork_without_peg(self):
        streak = derive([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], days=30)    # 10 of 10
        decision, _ = score_and_select([streak])
        self.assertEqual(decision.angle, "CLOCKWORK")

    def test_quiet_edge_fallback(self):
        good = derive([4, 5, 6, 7, 8, 9, 10, 11, -4, -5], days=60)   # 8 of 10
        decision, _ = score_and_select([good])
        self.assertEqual(decision.angle, "QUIET_EDGE")

    def test_fork_prefers_balanced_pair(self):
        near_bear = derive([-1, -2, -3, -4, -5, -6, -7, -8, -9, 1], days=30)
        far_bull = derive([1, 2, 3, 4, 5, 6, 7, 8, 9, -1], days=90)
        decision, _ = score_and_select([near_bear, far_bull])
        self.assertEqual(decision.angle, "FORK")
        self.assertEqual(decision.story_cell_key, near_bear.key())   # shorter horizon
        self.assertEqual(decision.counter_cell_key, far_bull.key())

    def test_fork_excluded_when_news_collides(self):
        near_bear = derive([-1, -2, -3, -4, -5, -6, -7, -8, -9, -10, -11, 1, 2, 3, -4],
                           days=30)                                  # 12 dn of 15
        far_bull = derive([1, 2, 3, 4, 5, 6, 7, 8, 9, -1], days=90)
        decision, _ = score_and_select(
            [near_bear, far_bull], ctx_dir=1, ctx_source="news", news_fresh=True)
        self.assertEqual(decision.angle, "COLLISION")                # tension outranks

    def test_regime_dominance_and_pe_not_clockwork(self):
        pe_streak = derive([-1, -2, -3, -4, -5, -6, -7, -8, -9], years="pe2-10")
        weak_cons = derive([4, 5, 6, 7, 8, 9, 10, 11, -4, -5], days=90)  # 8 of 10
        decision, ranked = score_and_select([pe_streak, weak_cons])
        self.assertEqual(decision.angle, "REGIME")
        self.assertNotIn("CLOCKWORK",
                         [d.angle for d in ranked if d.story_cell_key == pe_streak.key()])

    def test_streak_on_the_line_flavor(self):
        # 9 of 10 up but the most recent completed year broke the run.
        broke = derive([1, 2, 3, 4, 5, 6, 7, 8, 9, -1], days=30)
        decision, _ = score_and_select([broke])
        self.assertIn("streak_on_the_line", decision.flavors)

    def test_no_story(self):
        flip = derive([1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 1, -1])
        decision, ranked = score_and_select([flip])
        self.assertIsNone(decision)
        self.assertEqual(ranked, [])


class TestQuotables(unittest.TestCase):
    def test_strings_embed_n_and_avoid_reserved_labels(self):
        cell = derive([-2, -3, -4, -5, -6, -7, -8, -9, -10, -11, 1, 2, -3, -4],
                      days=60)                                       # 12 dn of 14
        q = ae.build_quotables(cell)
        self.assertEqual(q["record"], "has closed lower in 12 of the last 14 years")
        self.assertIn("across 14 years", q["median"])
        self.assertIn("60-day window beginning Jul 21, 2026", q["window"])
        joined = " ".join(q.values()).lower()
        for label in ae.RESERVED_LABELS:
            self.assertNotIn(label.lower(), joined)

    def test_streak_quotable(self):
        cell = derive([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        self.assertEqual(ae.build_quotables(cell)["streak"], "10 for 10 in this window")


class TestEndToEndFixture(unittest.TestCase):
    def _fixture(self):
        fx = {}
        # 30d/10y: 8-down (striking, deliberately below the extreme cutoff so
        # CLOCKWORK stays out of the way); 90d/20y coin flip must be skipped
        # as ineligible; missing grid cells must be skipped silently.
        fx[ae._cell_key("2", "TEST", ANCHOR, 30, "10")] = raw_response(
            [-4, -5, -6, -7, -8, -9, -10, -11, 4, 5],
            stats={"1M Return": "6.2", "Num Winners": "2", "Num Losers": "8",
                   "Trade Dir": "Long"})
        fx[ae._cell_key("2", "TEST", ANCHOR, 90, "20")] = raw_response(
            [1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5])
        return fx

    def test_card_collision_with_news(self):
        card = ae.run_angle_engine(
            "2", "TEST", ANCHOR, fixture=self._fixture(),
            news_headline="TEST pops on earnings beat",
            news_date="2026-07-19", news_direction="bullish")
        self.assertEqual(card["angle"]["name"], "COLLISION")
        self.assertEqual(card["tension_descriptor"], "contradicts_news")
        self.assertEqual(card["story_cell"]["quotables"]["record"],
                         "has closed lower in 8 of the last 10 years")
        self.assertEqual(len(card["matrix_summary"]), 2)
        self.assertTrue(card["context"]["news_fresh"])
        json.dumps(card)                                   # card must serialize

    def test_card_stale_news_goes_quiet(self):
        card = ae.run_angle_engine(
            "2", "TEST", ANCHOR, fixture=self._fixture(),
            news_headline="Old story", news_date="2026-05-01",
            news_direction="bullish")
        self.assertFalse(card["context"]["news_fresh"])
        self.assertEqual(card["angle"]["name"], "QUIET_EDGE")

    def test_card_no_story(self):
        fx = {ae._cell_key("2", "TEST", ANCHOR, 30, "10"):
              raw_response([1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 1, -1])}
        card = ae.run_angle_engine("2", "TEST", ANCHOR, fixture=fx)
        self.assertIsNone(card["angle"])
        self.assertIn("no eligible cell", card["no_story"])


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "aapl_2026-07-21.json"


@unittest.skipUnless(FIXTURE_PATH.is_file(), "live fixture not present")
class TestLiveFixture(unittest.TestCase):
    """Recorded prod ChartData4 responses (2026-07-20, workflow auth path).
    Locks in the verified arbitrary-window semantics: full stats block,
    Trade Dir 'long' on every cell, current year present as a zero row,
    derived counts equal to the API's W/L."""

    def setUp(self):
        with open(FIXTURE_PATH, encoding="utf-8") as fh:
            self.fixture = json.load(fh)

    def test_semantics_lock(self):
        for key, raw in self.fixture.items():
            stats = raw["stats"]
            self.assertEqual(stats["Trade Dir"], "long", key)
            for needed in ("Num Winners", "Num Losers", "Percent Profitable",
                           "Avg Profit", "Median Profit", "Std Dev", "Sharpe Ratio"):
                self.assertIn(needed, stats, key)

    def test_derived_counts_match_api(self):
        for key, raw in self.fixture.items():
            _, sym, anchor, days, years = key.split("_")
            cell = ae.derive_cell(raw, resource_id="2", symbol=sym, anchor=anchor,
                                  days=int(days), years=years,
                                  horizon_tag=f"{days}d", today=TODAY)
            self.assertIsNotNone(cell, key)
            self.assertFalse(cell.stats_mismatch,
                             f"{key}: {cell.notes}")

    def test_card_is_clockwork_without_news(self):
        card = ae.run_angle_engine("2", "AAPL", ANCHOR, fixture=self.fixture)
        self.assertEqual(card["angle"]["name"], "CLOCKWORK")
        story = card["story_cell"]
        self.assertEqual((story["days"], story["years"]), (90, "20"))
        self.assertEqual(story["quotables"]["record"],
                         "has closed higher in 16 of the last 20 years")

    def test_analyze_and_fallback_card(self):
        analysis = ae.analyze("2", "AAPL", ANCHOR, fixture=self.fixture)
        self.assertEqual(analysis["card"]["angle"]["name"], "CLOCKWORK")
        self.assertTrue(analysis["cells"])
        self.assertGreaterEqual(len(analysis["candidates"]), 2)
        fb = ae.fallback_card(analysis)
        self.assertEqual(fb["angle"]["name"], "QUIET_EDGE")
        self.assertEqual(fb["story_cell"]["days"], 90)      # same best cell
        self.assertIsNone(ae.fallback_card(analysis, index=99))

    def test_card_collides_with_bearish_news(self):
        card = ae.run_angle_engine(
            "2", "AAPL", ANCHOR, fixture=self.fixture,
            news_direction="bearish", news_date="2026-07-19",
            news_headline="hypothetical guidance cut")
        self.assertEqual(card["angle"]["name"], "COLLISION")
        self.assertEqual(card["tension_descriptor"], "contradicts_news")


if __name__ == "__main__":
    unittest.main()
