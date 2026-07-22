"""Phase 2 offline tests: chrome assembly, PLAN validation, the cell-article
integrity gate, and the full PLAN->WRITE->assemble->gate loop with canned LLM
transports (no network, no LLM, no publishing)."""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import angle_chrome
import angle_engine as ae
import angle_prompts
import angle_writer
from angle_prompts import PlanError
from integrity_gate import validate_cell_article

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "aapl_2026-07-21.json"
ANCHOR = "2026-07-21"


def live_card():
    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        fixture = json.load(fh)
    return ae.run_angle_engine("2", "AAPL", ANCHOR, fixture=fixture)


CANNED_PLAN = {
    "feasible": True, "veto_reason": "",
    "thesis": "AAPL's 90-day window from Jul 21 has closed higher in 16 of 20 "
              "years, and the streak is the story.",
    "beats": [
        {"purpose": "cold open on the record", "carries": ["quotable:record"], "chart": None},
        {"purpose": "year-by-year texture", "carries": ["quotable:best_worst"], "chart": "bars"},
        {"purpose": "shape of the window", "carries": ["quotable:median"], "chart": "trend"},
        {"purpose": "mechanism hypothesis", "carries": [], "chart": None},
        {"purpose": "what to watch", "carries": [], "chart": None},
    ],
    "h2s": ["How strong is this 90-day record?",
            "What did the strongest and weakest years look like?",
            "When do the gains usually arrive?",
            "Why might Apple (AAPL) follow this seasonal pattern?",
            "What should traders watch as the window opens?"],
    "charts": ["trend", "bars"],
    "bridge_after_beat": 0,
    "word_budget": 850,
    "headlines": ["Apple (AAPL) Has Closed Higher in 16 of 20 Years in This Window",
                  "Apple (AAPL) Enters a Historically Strong 90-Day Stretch"],
    "source_ids": [],
}

GOOD_PROSE = """<h1>Apple (AAPL) Has Closed Higher in 16 of 20 Years in This Window</h1>
<p class="dek">Apple heads into a 90-day stretch that has favored the stock in most of the last two decades.</p>
<section id="key-takeaways"><h2>What is the seasonal pattern for Apple (AAPL)?</h2>
<p class="direct-answer">Apple has closed higher in 16 of the last 20 years over the 90-day window beginning Jul 21, 2026, with a median gain of 11.1% across 20 years.</p>
<div class="key-takeaways-box"><ul>
<li>Closed higher in 16 of 20 years in this window</li>
<li>A median gain of 11.1% across 20 years</li>
<li>The best year gained 25.3% (2009); the worst lost 40.8% (2008)</li>
</ul></div></section>
{{HERO}}
<h2>How strong is this 90-day record?</h2>
<p>Apple has closed higher in 16 of the last 20 years over this window. The run covers the 90-day window beginning Jul 21, 2026, measured in calendar days.</p>
<p id="transition_to_tradewave" class="chart-bridge">That record is not a hunch. It comes from the seasonal database at TradeWave.ai, which tracks this exact window across two decades of history.</p>
{{META_STRIP}}
{{KEY_STATS}}
<h2>What did the strongest and weakest years look like?</h2>
<p>The best year gained 25.3% (2009); the worst lost 40.8% (2008). A shorter 30-day slice leans the same way, closing higher in 9 of the last 10 years.</p>
{{FIG:bars}}
<h2>When do the gains usually arrive?</h2>
<p>A median gain of 11.1% across 20 years puts this stretch among Apple's friendlier calendar spans.</p>
{{FIG:trend}}
<h2>Why might Apple (AAPL) follow this seasonal pattern?</h2>
<p>One likely driver is the product cycle that clusters announcements in early fall. It may also reflect institutional positioning ahead of those events. Neither is certain.</p>
<h2>What should traders watch as the window opens?</h2>
<p>Watch whether early strength holds through August, and treat the record as history, not a promise.</p>
{{SOURCES}}
{{METHODOLOGY}}"""

BAD_PROSE = GOOD_PROSE.replace(
    "measured in calendar days", "measured in trading days")


class TestChromeAssembly(unittest.TestCase):
    def setUp(self):
        self.card = live_card()
        self.chrome = angle_chrome.build_chrome(self.card, company="Apple Inc")

    def test_meta_strip_shows_derived_bias(self):
        strip = self.chrome["META_STRIP"]
        self.assertIn("Historical bias: bullish (16 of 20 years closed higher)", strip)
        self.assertIn("90 calendar days", strip)
        self.assertNotIn("Trade Direction", strip)

    def test_key_stats_verbatim(self):
        ks = self.chrome["KEY_STATS"]
        self.assertIn("<span>Percent Profitable</span><span>80%</span>", ks)
        self.assertIn("Sample Size", ks)
        # The footnote states the convention; it no longer claims "long"
        # unconditionally, because ChartData4 can return a short-accounted
        # cell and that claim was then false (see angle_chrome.render_key_stats).
        self.assertIn("close-to-close accounting", ks)
        self.assertIn("winners are years the window closed higher", ks)

    def test_assemble_good_prose(self):
        out = angle_chrome.assemble_article(GOOD_PROSE, self.chrome,
                                            planned_figs=["trend", "bars"])
        html = out["html"]
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertEqual(html.count('class="pattern-meta"'), 1)
        self.assertEqual(html.count('class="key-stats"'), 1)
        self.assertNotIn("{{", html)
        self.assertIn("application/ld+json", html)

    def test_missing_tokens_get_default_placement(self):
        prose = GOOD_PROSE.replace("{{META_STRIP}}\n", "").replace("{{KEY_STATS}}\n", "")
        out = angle_chrome.assemble_article(prose, self.chrome)
        self.assertEqual(out["html"].count('class="pattern-meta"'), 1)
        self.assertEqual(out["html"].count('class="key-stats"'), 1)
        self.assertTrue(any("default placement" in w for w in out["warnings"]))
        # Insertion must never truncate: everything after the anchor survives
        # (2026-07-21 live-run bug: _insert_after dropped the document tail).
        self.assertIn("transition_to_tradewave", out["html"])
        self.assertIn("What should traders watch", out["html"])

    def test_byline_insertion_preserves_document(self):
        out = angle_chrome.assemble_article(GOOD_PROSE, self.chrome,
                                            byline="Powered by TradeWave.AI")
        self.assertIn("Powered by TradeWave.AI", out["html"])
        self.assertIn("transition_to_tradewave", out["html"])
        self.assertIn("What should traders watch", out["html"])
        art = out["html"][out["html"].index("<article>"):]     # skip head/CSS
        self.assertLess(art.index('class="dek"'), art.index('class="meta"'))
        self.assertLess(art.index('class="meta"'), art.index('id="key-takeaways"'))

    def test_duplicate_and_unknown_tokens(self):
        prose = GOOD_PROSE.replace("{{META_STRIP}}", "{{META_STRIP}}\n{{META_STRIP}}\n{{BOGUS_TOKEN}}")
        out = angle_chrome.assemble_article(prose, self.chrome)
        self.assertEqual(out["html"].count('class="pattern-meta"'), 1)
        self.assertNotIn("BOGUS_TOKEN", out["html"])

    def test_citation_renumbering_cited_only(self):
        research = {"sources": [
            {"id": 4, "publisher": "CNBC", "title": "A", "url": "https://x/a"},
            {"id": 9, "publisher": "Reuters", "title": "B", "url": "https://x/b"},
            {"id": 2, "publisher": "AP", "title": "C", "url": "https://x/c"},
        ]}
        prose = ("<p>one<sup>[9]</sup> two<sup>[4]</sup> again<sup>[9]</sup> "
                 "dead<sup>[7]</sup></p>")
        renum, order = angle_chrome.renumber_citations(prose, research)
        self.assertEqual(order, [9, 4])
        self.assertIn("one<sup>[1]</sup>", renum)
        self.assertIn("two<sup>[2]</sup>", renum)
        self.assertIn("again<sup>[1]</sup>", renum)
        self.assertNotIn("[7]", renum)
        sources_html = angle_chrome.render_sources(order, research)
        self.assertEqual(sources_html.count("<li>"), 2)
        self.assertLess(sources_html.index("Reuters"), sources_html.index("CNBC"))


class TestPlanValidation(unittest.TestCase):
    def test_good_plan_passes(self):
        plan = angle_prompts.parse_plan(json.dumps(CANNED_PLAN), "CLOCKWORK")
        self.assertEqual(plan["word_budget"], 850)

    def test_budget_clamped_to_band(self):
        fat = dict(CANNED_PLAN, word_budget=2000)
        plan = angle_prompts.parse_plan(json.dumps(fat), "CLOCKWORK")
        self.assertEqual(plan["word_budget"], 1000)

    def test_headline_too_long(self):
        bad = dict(CANNED_PLAN, headlines=[
            "This headline definitely runs far past the sixteen word ceiling "
            "that the design imposes on every candidate"])
        with self.assertRaises(PlanError):
            angle_prompts.parse_plan(json.dumps(bad), "CLOCKWORK")

    def test_bad_chart_and_bridge(self):
        bad = dict(CANNED_PLAN, charts=["pie"], bridge_after_beat=99)
        with self.assertRaises(PlanError) as ctx:
            angle_prompts.parse_plan(json.dumps(bad), "CLOCKWORK")
        self.assertIn("charts", str(ctx.exception))
        self.assertIn("bridge_after_beat", str(ctx.exception))

    def test_veto_needs_reason(self):
        with self.assertRaises(PlanError):
            angle_prompts.parse_plan('{"feasible": false, "veto_reason": ""}', "CLOCKWORK")
        veto = angle_prompts.parse_plan(
            '{"feasible": false, "veto_reason": "research is empty"}', "CLOCKWORK")
        self.assertFalse(veto["feasible"])

    def test_chart_universe_constraint(self):
        with self.assertRaises(PlanError):
            angle_prompts.parse_plan(json.dumps(CANNED_PLAN), "CLOCKWORK",
                                     available_charts=["price"])
        ok = angle_prompts.parse_plan(json.dumps(CANNED_PLAN), "CLOCKWORK",
                                      available_charts=["trend", "bars", "price"])
        self.assertEqual(ok["charts"], ["trend", "bars"])
        empty_ok = angle_prompts.parse_plan(
            json.dumps(dict(CANNED_PLAN, charts=[])), "CLOCKWORK",
            available_charts=[])
        self.assertEqual(empty_ok["charts"], [])

    def test_gate_licenses_card_percents_only(self):
        # Regression for the 2026-07-21 live run: per-year values quoted in a
        # winners clause are legitimate; invented rates are not.
        card = live_card()
        chrome = angle_chrome.build_chrome(card, company="Apple Inc")
        legit = GOOD_PROSE.replace(
            "puts this stretch among Apple's friendlier calendar spans",
            "and winning years averaged 14.84%, the card's own figure")
        out = angle_chrome.assemble_article(legit, chrome)
        self.assertEqual(validate_cell_article(out["html"], card)["errors"], [])
        invented = GOOD_PROSE.replace(
            "puts this stretch among Apple's friendlier calendar spans",
            "meaning the window has been profitable 85% of the time")
        out2 = angle_chrome.assemble_article(invented, chrome)
        codes = [e["code"] for e in validate_cell_article(out2["html"], card)["errors"]]
        self.assertIn("PCT_MISMATCH", codes)


class TestCellGate(unittest.TestCase):
    def setUp(self):
        self.card = live_card()
        self.chrome = angle_chrome.build_chrome(self.card, company="Apple Inc")

    def _gate(self, prose):
        out = angle_chrome.assemble_article(prose, self.chrome)
        return validate_cell_article(out["html"], self.card, word_budget=850)

    def test_good_article_passes(self):
        result = self._gate(GOOD_PROSE)
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["ok"])

    def test_trading_days_flagged(self):
        codes = [e["code"] for e in self._gate(BAD_PROSE)["errors"]]
        self.assertIn("TRADING_DAYS_LABEL", codes)

    def test_pair_mismatch_flagged(self):
        target = "closing higher in 9 of the last 10 years"
        self.assertIn(target, GOOD_PROSE)          # guard against silent no-op
        prose = GOOD_PROSE.replace(target, "closing higher in 14 of the last 20 years")
        codes = [e["code"] for e in self._gate(prose)["errors"]]
        self.assertIn("PAIR_MISMATCH", codes)

    def test_tradewave_before_bridge_flagged(self):
        prose = GOOD_PROSE.replace(
            "The run covers", "TradeWave data shows the run covers")
        codes = [e["code"] for e in self._gate(prose)["errors"]]
        self.assertIn("TW_BEFORE_BRIDGE", codes)

    def test_bridge_with_stats_flagged(self):
        prose = GOOD_PROSE.replace(
            "which tracks this exact window across two decades of history",
            "which shows an 80% hit rate in this window")
        codes = [e["code"] for e in self._gate(prose)["errors"]]
        self.assertIn("BRIDGE_MALFORMED", codes)

    def test_excursions_require_mae_mfe_chart(self):
        prose = GOOD_PROSE.replace(
            "treat the record as history, not a promise",
            "drawdowns inside the window have run deep even in winning years")
        codes = [e["code"] for e in self._gate(prose)["errors"]]
        self.assertIn("CHART_SEMANTICS_MISMATCH", codes)


FAKE_IMAGES = [
    {"variant": "trend", "url": "https://x/t.png",
     "caption": "AAPL Seasonal Trend | 20-year average", "alt": "trend chart"},
    {"variant": "bars", "url": "https://x/b.png",
     "caption": "AAPL Return Bars | Per-Year Net", "alt": "bars chart"},
]


class TestWriterLoop(unittest.TestCase):
    def setUp(self):
        self.card = live_card()

    def test_clean_first_pass(self):
        result = angle_writer.generate_angle_article(
            self.card, company="Apple Inc", images=FAKE_IMAGES,
            send_plan=lambda p: json.dumps(CANNED_PLAN),
            send_write=lambda p: GOOD_PROSE)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["gate1"]["ok"])
        self.assertNotIn("gate2", result)
        self.assertIn("<!doctype html>", result["html"])
        self.assertIn("https://x/t.png", result["html"])   # figures rendered

    def test_one_revision_then_ready(self):
        writes = []

        def send_write(prompt):
            writes.append(prompt)
            return BAD_PROSE if len(writes) == 1 else GOOD_PROSE

        result = angle_writer.generate_angle_article(
            self.card, company="Apple Inc", images=FAKE_IMAGES,
            send_plan=lambda p: json.dumps(CANNED_PLAN), send_write=send_write)
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["gate1"]["ok"])
        self.assertTrue(result["gate2"]["ok"])
        self.assertEqual(len(writes), 2)
        self.assertIn("TRADING_DAYS_LABEL", writes[1])   # codes fed back verbatim

    def test_revision_cap_holds(self):
        result = angle_writer.generate_angle_article(
            self.card, company="Apple Inc", images=FAKE_IMAGES,
            send_plan=lambda p: json.dumps(CANNED_PLAN),
            send_write=lambda p: BAD_PROSE)               # never fixes it
        self.assertEqual(result["status"], "hold")
        self.assertFalse(result["gate2"]["ok"])

    def test_plan_retry_then_success(self):
        plans = []

        def send_plan(prompt):
            plans.append(prompt)
            return "not json at all" if len(plans) == 1 else json.dumps(CANNED_PLAN)

        result = angle_writer.generate_angle_article(
            self.card, company="Apple Inc", images=FAKE_IMAGES,
            send_plan=send_plan, send_write=lambda p: GOOD_PROSE)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(plans), 2)
        self.assertIn("previous response was invalid", plans[1])

    def test_veto_returns_fallbacks(self):
        result = angle_writer.generate_angle_article(
            self.card, images=FAKE_IMAGES,
            send_plan=lambda p: '{"feasible": false, "veto_reason": "no research"}',
            send_write=lambda p: GOOD_PROSE)
        self.assertEqual(result["status"], "vetoed")
        self.assertTrue(result["fallbacks"])              # QUIET_EDGE available

    def test_plan_cannot_choose_unavailable_chart(self):
        # No images -> chart universe is empty -> the canned plan (trend/bars)
        # is invalid twice -> plan_failed, never a doomed WRITE call.
        result = angle_writer.generate_angle_article(
            self.card, company="Apple Inc", images=None,
            send_plan=lambda p: json.dumps(CANNED_PLAN),
            send_write=lambda p: GOOD_PROSE)
        self.assertEqual(result["status"], "plan_failed")
        self.assertIn("charts", result["detail"])

    def test_chartless_plan_and_excursion_ban(self):
        chartless = dict(CANNED_PLAN, charts=[])
        prompt_out = {}

        def send_write(prompt):
            prompt_out["write"] = prompt
            return GOOD_PROSE.replace("{{FIG:bars}}\n", "").replace("{{FIG:trend}}\n", "")

        result = angle_writer.generate_angle_article(
            self.card, company="Apple Inc", images=[],
            send_plan=lambda p: json.dumps(chartless), send_write=send_write)
        self.assertEqual(result["status"], "ready")
        self.assertIn("NO charts are available", prompt_out["write"])

    def test_prompts_carry_the_craft(self):
        plan_prompt = angle_prompts.build_plan_prompt(self.card)
        self.assertIn("CLOCKWORK", plan_prompt)
        self.assertIn("The streak is the story", plan_prompt)
        self.assertIn("veto", plan_prompt.lower())
        write_prompt = angle_prompts.build_write_prompt(self.card, CANNED_PLAN)
        self.assertIn("transition_to_tradewave", write_prompt)
        self.assertIn("{{META_STRIP}}", write_prompt)
        self.assertIn("calendar days", write_prompt)
        self.assertIn("has closed higher in 16 of the last 20 years", write_prompt)

    def test_engine_internals_never_reach_the_writer(self):
        # 2026-07-21 smoke run: the writer quoted tail_p as "TradeWave's test".
        for prompt in (angle_prompts.build_plan_prompt(self.card),
                       angle_prompts.build_write_prompt(self.card, CANNED_PLAN)):
            for leak in ("tail_p", "conviction", "story_score", "ineligible",
                         "stats_raw"):
                self.assertNotIn(leak, prompt)

    def test_internal_metric_leak_flagged(self):
        card = live_card()
        chrome = angle_chrome.build_chrome(card, company="Apple Inc")
        prose = GOOD_PROSE.replace(
            "treat the record as history, not a promise",
            "the skew carries a tail p-value of 0.0059 in this test")
        out = angle_chrome.assemble_article(prose, chrome)
        codes = [e["code"] for e in validate_cell_article(out["html"], card)["errors"]]
        self.assertIn("INTERNAL_METRIC_LEAK", codes)

    def test_editorial_facts_include_authoritative_stats(self):
        captured = {}

        def ed_send(prompt):
            captured["prompt"] = prompt
            return ED_PUBLISH

        result = angle_writer.generate_angle_article(
            live_card(), company="Apple Inc", images=FAKE_IMAGES,
            send_plan=lambda p: json.dumps(CANNED_PLAN),
            send_write=lambda p: GOOD_PROSE,
            run_editorial=True, editorial_send=ed_send)
        self.assertEqual(result["status"], "ready")
        self.assertIn("story_stats_raw", captured["prompt"])
        self.assertIn("Percent Profitable", captured["prompt"])


ED_PUBLISH = '{"decision":"publish","hard_issues":[],"soft_issues":[]}'
ED_REPAIR = ('{"decision":"repair","hard_issues":[{"code":"EVENT_STALE_FRAMING",'
             '"detail":"2025 event framed as current"}],"soft_issues":[]}')
ED_HOLD = '{"decision":"hold","hard_issues":[],"soft_issues":[]}'


class TestEditorialInLoop(unittest.TestCase):
    def setUp(self):
        self.card = live_card()

    def _gen(self, editorial_send, send_write=None):
        return angle_writer.generate_angle_article(
            self.card, company="Apple Inc", images=FAKE_IMAGES,
            send_plan=lambda p: json.dumps(CANNED_PLAN),
            send_write=send_write or (lambda p: GOOD_PROSE),
            run_editorial=True, editorial_send=editorial_send)

    def test_editorial_publish_first_pass(self):
        result = self._gen(lambda p: ED_PUBLISH)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["gate1"]["editorial"]["decision"], "publish")
        self.assertNotIn("gate2", result)

    def test_editorial_repair_feeds_shared_revision(self):
        reviews, writes = [], []

        def ed_send(prompt):
            reviews.append(prompt)
            return ED_REPAIR if len(reviews) == 1 else ED_PUBLISH

        def send_write(prompt):
            writes.append(prompt)
            return GOOD_PROSE

        result = self._gen(ed_send, send_write)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(writes), 2)                     # write + one revision
        self.assertIn("EVENT_STALE_FRAMING", result["revision_prompt"])
        self.assertEqual(result["gate2"]["editorial"]["decision"], "publish")

    def test_editorial_hold_is_terminal(self):
        writes = []

        def send_write(prompt):
            writes.append(prompt)
            return GOOD_PROSE

        result = self._gen(lambda p: ED_HOLD, send_write)
        self.assertEqual(result["status"], "hold")
        self.assertEqual(len(writes), 1)                     # no revision spent

    def test_editorial_infra_failure_holds(self):
        def broken(prompt):
            raise RuntimeError("reviewer down")

        result = self._gen(broken)
        self.assertEqual(result["status"], "hold")


class TestPipeline(unittest.TestCase):
    """Offline orchestration test: analysis from the recorded fixture, chart /
    research / hero / SEO / publish collaborators stubbed."""

    def setUp(self):
        import types
        from unittest.mock import patch
        import angle_engine as ae_mod
        import angle_pipeline

        self.angle_pipeline = angle_pipeline
        with open(FIXTURE_PATH, encoding="utf-8") as fh:
            fixture = json.load(fh)
        self.analysis = ae_mod.analyze("2", "AAPL", ANCHOR, fixture=fixture)
        self.recorded = []

        class AuditStub:
            @staticmethod
            def begin(identity):
                return {"identity": identity}

            @staticmethod
            def record(name, value):
                self.recorded.append(name)

            @staticmethod
            def finish(trail, tracking):
                pass

        fake_aw = types.ModuleType("article_workflow")
        fake_aw.generate_hero_image = lambda **kw: (
            '<figure class="hero"></figure>',
            list(kw["img_paths"]) + [{"variant": "hero", "url": "https://x/hero.png"}])
        fake_at = types.ModuleType("article_title")

        def _no_title(*a, **k):
            raise RuntimeError("stubbed out")
        fake_at.generate_unique_seo_title = _no_title

        self.patches = [
            patch.object(angle_pipeline, "article_audit", AuditStub),
            patch.object(angle_pipeline.angle_engine, "analyze",
                         lambda *a, **k: self.analysis),
            patch.object(angle_pipeline, "_chart_images",
                         lambda rid, cell: list(FAKE_IMAGES)),
            patch.object(angle_pipeline, "_prepare_research",
                         lambda *a, **k: None),
            patch.dict(sys.modules, {"article_workflow": fake_aw,
                                     "article_title": fake_at}),
            patch.object(angle_pipeline, "config",
                         types.SimpleNamespace(angle_publish_enabled=False)),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])

    def _run(self, send_plan=None, **kw):
        return self.angle_pipeline.generate_angle_news_article(
            "2", "AAPL", anchor=ANCHOR, publish=True, run_editorial=False,
            send_plan=send_plan or (lambda p: json.dumps(CANNED_PLAN)),
            send_write=lambda p: GOOD_PROSE, **kw)

    def test_ready_with_publish_double_gated(self):
        result = self._run()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["publish_skipped"],
                         "config.angle_publish_enabled is off")
        self.assertNotIn("publish_result", result)
        self.assertEqual(result["hero_url"], "https://x/hero.png")
        self.assertFalse(result["research_used"])
        for name in ("angle_card.json", "plan.json", "final.html"):
            self.assertIn(name, self.recorded)

    def test_veto_falls_back_once(self):
        calls = []

        def send_plan(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return '{"feasible": false, "veto_reason": "no research for a streak piece"}'
            return json.dumps(CANNED_PLAN)

        result = self._run(send_plan=send_plan)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["angle"], "QUIET_EDGE")      # fallback card used
        self.assertIn("angle_card_fallback.json", self.recorded)


class TestResearchGuard(unittest.TestCase):
    def test_empty_sources_research_treated_as_none(self):
        # 2026-07-21 XLK batch: Tavily returned 0 usable sources and the piece
        # was held for uncitable claims. Empty sources must mean no research.
        import types
        from unittest.mock import patch
        import angle_pipeline
        fake_aw = types.ModuleType("article_workflow")
        fake_aw.research_tavily = lambda **kw: {"sources": [], "price": {"last": 1}}
        fake_ap = types.ModuleType("article_prompt")
        fake_ap._filter_research_sources = lambda r, **kw: r
        fake_ap._annotate_research_temporal = lambda r, **kw: r
        fake_gp = types.ModuleType("get_price_eod")
        fake_gp.get_quote_details = lambda *a, **k: None
        with patch.dict(sys.modules, {"article_workflow": fake_aw,
                                      "article_prompt": fake_ap,
                                      "get_price_eod": fake_gp}):
            self.assertIsNone(
                angle_pipeline._prepare_research("2", "XLK", "XLK", {}))
            fake_aw.research_tavily = lambda **kw: {
                "sources": [{"id": 1, "url": "https://x", "title": "t"}]}
            self.assertIsNotNone(
                angle_pipeline._prepare_research("2", "XLK", "XLK", {}))


if __name__ == "__main__":
    unittest.main()
