"""Offline integration regressions for the first SMN development pass.

SMN_TEST_BLOG can point at an integration worktree while these tests are
developed separately. Heavy application modules are parsed to execute their
actual helper functions without importing configuration or network clients.
"""
from __future__ import annotations

import ast
import copy
import datetime
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
import types
import typing
import unittest
from unittest.mock import patch


BLOG = Path(os.environ.get("SMN_TEST_BLOG", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(BLOG))


def load_pure_module(name):
    spec = importlib.util.spec_from_file_location("_first_pass_" + name, BLOG / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def actual_functions(filename, names, namespace=None):
    """Compile selected production definitions; never duplicate their code."""
    tree = ast.parse((BLOG / filename).read_text(encoding="utf-8"))
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in names]
    if {node.name for node in selected} != set(names):
        raise AssertionError(f"Missing requested definitions in {filename}")
    env = {"datetime": datetime, "json": json, "re": re,
           "secrets": secrets, "time": time, **vars(typing)}
    env.update(namespace or {})
    compiled = ast.Module(body=[ast.ImportFrom(module="__future__",
                    names=[ast.alias(name="annotations")], level=0)] + selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(compiled), str(BLOG / filename), "exec"), env)
    return env


sources = load_pure_module("article_sources")
chrome = load_pure_module("angle_chrome")


class SourceIdentityTests(unittest.TestCase):
    def test_nested_source_objects_rebind_and_removed_objects_disappear(self):
        research = {
            "sources": [{"id": 3, "title": "Bank earnings"}, {"id": 6, "title": "Europe survey"}],
            "earnings": {"summary": "Bank earnings rose", "sources": [
                {"id": 3, "title": "Bank earnings"}, {"id": 4, "title": "Removed report"}]},
            "claims": [{"id": "claim-3", "source_ids": [3, 4, 6]}],
            "unrelated_record": {"id": 3},
        }
        sources.normalize_research_source_ids(research)
        self.assertEqual(research["earnings"]["sources"], [{"id": 1, "title": "Bank earnings"}])
        self.assertEqual(research["claims"], [{"id": "claim-3", "source_ids": [1, 2]}])
        self.assertEqual(research["unrelated_record"]["id"], 3)
        again = copy.deepcopy(research)
        sources.normalize_research_source_ids(research)
        self.assertEqual(research, again)

    def test_binding_rejects_fabricated_url_and_requires_actual_quote(self):
        research = {"sources": [{"id": 3, "url": "https://issuer.test/results/"},
                                {"id": 4, "url": "https://issuer.test/fabricated"}],
                    "claims": [{"source_ids": [3], "evidence_quote": "Revenue rose by 4%."},
                               {"source_ids": [3], "evidence_quote": "Profit doubled."},
                               {"source_ids": [4], "evidence_quote": "Revenue rose by 4%."}]}
        retrieved = [{"url": "https://issuer.test/results", "content": "A short snippet.",
                      "raw_content": "Published results. Revenue rose by 4%. The full statement follows."}]
        bound = sources.attach_retrieved_evidence(research, retrieved, "2026-09-05T12:00:00Z")
        self.assertEqual([s["id"] for s in bound["sources"]], [3])
        self.assertEqual(bound["sources"][0]["excerpt"], retrieved[0]["raw_content"])
        self.assertTrue(bound["sources"][0]["retrieval_supported"])
        self.assertEqual([c["evidence_available"] for c in bound["claims"]], [True, False, False])


class TitleIdentityTests(unittest.TestCase):
    def test_replacement_escapes_visible_title_and_updates_article_schema_only(self):
        replace = actual_functions("article_workflow.py", ["_replace_title_in_html"])["_replace_title_in_html"]
        title = 'Apple & Partners: "What <changed>?" \\1 </script>'
        graph = {"@graph": [{"@type": ["NewsArticle"], "headline": "Old headline"},
                            {"@type": "Organization", "name": "SMN", "headline": "Keep organization data"}]}
        original = ('<html><head><title>Old headline</title><script type="application/ld+json">'
                    + json.dumps(graph) + '</script></head><body><h1 class="title">Old headline</h1>'
                    '<p>Approved body &amp; citations stay intact.</p></body></html>')
        updated = replace(original, title)
        for tag in ("h1", "title"):
            self.assertIn(f"<{tag}>{html.escape(title)}</{tag}>", updated)
        schema = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', updated, re.S).group(1))
        self.assertEqual(schema["@graph"][0]["headline"], title)
        self.assertEqual(schema["@graph"][1]["headline"], "Keep organization data")
        self.assertEqual(updated.count("</script>"), 1)
        self.assertIn('<p>Approved body &amp; citations stay intact.</p>', updated)

    def test_invalid_schema_is_preserved_without_blocking_visible_title(self):
        replace = actual_functions("article_workflow.py", ["_replace_title_in_html"])["_replace_title_in_html"]
        original = '<title>Old</title><h1>Old</h1><script type="application/ld+json">invalid</script>'
        updated = replace(original, "New & supported")
        self.assertIn("<h1>New &amp; supported</h1>", updated)
        self.assertIn('type="application/ld+json">invalid</script>', updated)


class FreshEventTests(unittest.TestCase):
    def setUp(self):
        self.select = actual_functions("angle_pipeline.py", ["_freshest_source"])["_freshest_source"]
        self.source = {"id": 3, "document_type": "event_report", "retrieval_supported": True,
                       "date": "2026-09-05", "event_date": "2026-09-04"}
        self.claim = {"source_ids": [3], "event_date": "2026-09-04", "evidence_available": True}

    def test_only_supported_linked_same_date_claim_can_create_fresh_peg(self):
        self.assertEqual(self.select({"sources": [self.source], "claims": [self.claim]}, "2026-09-05"), self.source)
        variants = [[], [dict(self.claim, evidence_available=False)],
                    [dict(self.claim, source_ids=[9])], [dict(self.claim, event_date="2026-09-03")]]
        for claims in variants:
            with self.subTest(claims=claims):
                self.assertIsNone(self.select({"sources": [self.source], "claims": claims}, "2026-09-05"))

    def test_background_stale_and_future_events_are_not_fresh_pegs(self):
        for changes in ({"document_type": "background"}, {"retrieval_supported": False},
                        {"event_date": "2026-08-01"}, {"event_date": "2026-09-06"}):
            source = dict(self.source, **changes)
            claim = dict(self.claim, event_date=source["event_date"])
            with self.subTest(changes=changes):
                self.assertIsNone(self.select({"sources": [source], "claims": [claim]}, "2026-09-05"))


class ChromeIntegrationTests(unittest.TestCase):
    def test_hero_moves_above_summary_and_takeaways_are_labeled_once(self):
        hero = chrome.render_hero("https://images.test/hero.png", "AAPL", "Apple")
        prose = ('<h1>Apple seasonal analysis</h1><p class="dek">A useful answer.</p>'
                 '<section id="key-takeaways"><h2>Summary</h2><p class="direct-answer">The historical answer.</p>'
                 '<div class="key-takeaways-box"><ul><li>A useful implication.</li></ul></div></section>'
                 '<h2>Evidence</h2><p>Keep the complete body.</p>{{HERO}}{{HERO}}{{METHODOLOGY}}')
        assembled = chrome.assemble_article(prose, {"HERO": hero, "METHODOLOGY": "<p>Methodology tail.</p>"})["html"]
        body = assembled.split("<article>", 1)[1]
        self.assertEqual(body.count('class="hero"'), 1)
        self.assertLess(body.index('class="dek"'), body.index('class="hero"'))
        self.assertLess(body.index('class="hero"'), body.index('id="key-takeaways"'))
        self.assertEqual(body.count("<h2>Key Takeaways</h2>"), 1)
        self.assertIn("Keep the complete body.", body)
        self.assertIn("Methodology tail.", body)
        labeled = prose.replace('<div class="key-takeaways-box">', '<div class="key-takeaways-box"><h2>Key Takeaways</h2>')
        second = chrome.assemble_article(labeled, {"HERO": hero, "METHODOLOGY": ""})["html"]
        self.assertEqual(second.count("<h2>Key Takeaways</h2>"), 1)

    def test_cumulative_caption_and_alt_preserve_chart_short_accounting(self):
        cell = {"anchor_date": "2026-09-05", "direction": "bullish",
                "evidence": {"cohort": {"label": "10 sampled midterm election years, 1986–2022"}}}
        image = {"variant": "cumulative", "url": "https://images.test/cumulative.png",
                 "caption": "Compounded short-side return", "alt": "Legacy short-side curve",
                 "semantics": {"direction": "short"}}
        result = chrome.render_figure("cumulative", [image], cell)
        self.assertIn("cumulative short-side profit", result)
        self.assertIn("a gain is not a rise in the stock price", result)
        self.assertIn("1986–2022", result)
        self.assertIn("short-side", re.search(r'alt="([^"]+)"', result).group(1))
        image["semantics"]["direction"] = "long"
        cell["direction"] = "bearish"
        long_result = chrome.render_figure("cumulative", [image], cell)
        self.assertNotIn("cumulative short-side profit", long_result)


class FallbackIdentityTests(unittest.TestCase):
    def run_pipeline(self, *, moved, chart_failure=False):
        original_cell = {"symbol": "AAPL", "anchor_date": "2026-09-05", "days": 30,
                         "years": "20", "direction": "bullish", "stats_raw": {}}
        next_cell = dict(original_cell, days=60 if moved else 30)
        original = {"symbol": "AAPL", "story_cell": original_cell, "angle": {"name": "CLOCKWORK"}}
        fallback = {"symbol": "AAPL", "story_cell": next_cell, "angle": {"name": "FORK"}}
        analysis = {"card": original}
        charts, drafts, links = [], [], []

        def render_charts(resource_id, cell):
            charts.append(copy.deepcopy(cell))
            if chart_failure and len(charts) == 2:
                raise ValueError("Chart fixture unavailable")
            return [{"variant": "bars", "url": f"https://images.test/{cell['days']}.png"}]

        def write(card, **kwargs):
            drafts.append((copy.deepcopy(card), copy.deepcopy(kwargs)))
            return ({"status": "vetoed", "detail": "Try the next supported angle"} if len(drafts) == 1
                    else {"status": "ready", "html": "<html><body>Approved article</body></html>"})

        def encode(resource_id, symbol, date, days, years):
            links.append((resource_id, symbol, date, days, years))
            return f"window-{days}"

        audit = types.SimpleNamespace(begin=lambda _: {}, record=lambda *args: None, finish=lambda *args: None)
        env = actual_functions("angle_pipeline.py", ["generate_angle_news_article", "_story_identity",
                            "_story_cta", "_direction_label", "_approve_seo_title"], {
            "angle_engine": types.SimpleNamespace(analyze=lambda *args, **kwargs: analysis,
                                                 fallback_card=lambda *args: fallback),
            "angle_writer": types.SimpleNamespace(generate_angle_article=write),
            "_chart_images": render_charts, "_prepare_research": lambda *args: None,
            "article_audit": audit, "collect_model_usage": lambda **kwargs: {},
            "config": types.SimpleNamespace(domain_root="https://tradewave.test/", tw_viewer_path="app/",
                                           angle_publish_enabled=False),
        })
        blog_tools = types.ModuleType("blog_tools")
        blog_tools.get_company_name = lambda *args: "Apple"
        blog_tools.convert_param_base64 = encode
        workflow = types.ModuleType("article_workflow")
        workflow.generate_hero_image = lambda **kwargs: ("", kwargs["img_paths"] + [
            {"variant": "hero", "url": "https://images.test/hero.png"}])
        workflow._replace_title_in_html = lambda doc, title: doc
        titles = types.ModuleType("article_title")
        titles.generate_unique_seo_title = lambda *args, **kwargs: ""
        with patch.dict(sys.modules, {"blog_tools": blog_tools, "article_workflow": workflow, "article_title": titles}):
            result = env["generate_angle_news_article"]("2", "AAPL", anchor="2026-09-05",
                    send_plan=lambda _: "", send_write=lambda _: "", editorial_send=lambda _: "",
                    publish=False)
        return result, charts, drafts, links

    def test_changed_fallback_cell_rebuilds_charts_and_cta_before_writing(self):
        result, charts, drafts, links = self.run_pipeline(moved=True)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([c["days"] for c in charts], [30, 60])
        self.assertEqual([link[3] for link in links], [30, 60])
        self.assertEqual(drafts[1][0]["story_cell"]["days"], 60)
        self.assertEqual(drafts[1][1]["images"][0]["url"], "https://images.test/60.png")
        self.assertTrue(drafts[1][1]["cta_link"].endswith("window-60"))
        self.assertNotIn("publish_result", result)

    def test_same_cell_keeps_existing_charts_and_rebuild_failure_holds(self):
        result, charts, drafts, links = self.run_pipeline(moved=False)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(charts), 1)
        self.assertEqual(len(links), 1)
        self.assertEqual(len(drafts), 2)
        result, charts, drafts, _ = self.run_pipeline(moved=True, chart_failure=True)
        self.assertEqual(result["status"], "hold")
        self.assertEqual(len(charts), 2)
        self.assertEqual(len(drafts), 1)


if __name__ == "__main__":
    unittest.main()
