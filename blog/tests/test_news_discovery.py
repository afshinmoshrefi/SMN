"""Offline coverage of the complete search -> event -> draft preview path."""
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))

from news_discovery import (DiscoveryPolicy, discover_news_events, ground_discovered_events,
                           main, prepare_retrieved_sources, run_news_scan)
from test_news_pipeline import NOW, article, packet, passing_review, plan


def saved_search():
    source = packet()["research"]["sources"][0]
    return [{"results": [{"title": "Fictional test release, not real news",
                           "url": "https://bls.gov/news.release/fictional-fixture.htm",
                           "published_date": source["published_at"],
                           "raw_content": ("September 5, 2026. " + source["excerpt"] + "\n\n"
                                           "This fictional release describes the revised company outlook and the investment plan together. "
                                           "The company still expects positive sales growth, but has lowered the range of that expectation. "
                                           "It does not establish a sector-wide decline or quantify the duration of weaker orders."),
                           "content": "Search snippet intentionally differs from retrieved article."}]}]


def extraction(source):
    return {"events": [{"event_key": "fictional-atlas-outlook-september",
                        "development_key": "sales-outlook-reduction",
                        "headline": "Fictional fixture: Atlas lowers its outlook",
                        "date_evidence": {"source_id": source["id"], "date_text": "September 5, 2026",
                                          "quote": source["excerpt"]},
                        "source_ids": [source["id"]],
                        "claims": [{"source_id": source["id"], "quote": source["excerpt"]}],
                        "significance": 4, "significance_reason": "Revised company outlook changes expected growth.",
                        "audience_relevance": 4, "relevance_reason": "Readers learn what changed in the industrial demand outlook.",
                        "material_development": True, "major_event": False}]}


def replay_responses():
    source = prepare_retrieved_sources(saved_search(), now=NOW)["sources"][0]
    discovery_reply = extraction(source)
    grounded = ground_discovered_events(discovery_reply, [source], now=NOW)
    cid = grounded["research"]["claims"][0]["id"]

    def remap(value):
        if isinstance(value, dict):
            return {k: ([cid] if k.endswith("claim_ids") and child else remap(child)) for k, child in value.items()}
        if isinstance(value, list):
            return [remap(child) for child in value]
        return value

    return [discovery_reply, remap(plan()), remap(article()), passing_review()]


class NewsDiscoveryTests(unittest.TestCase):
    def test_retrieved_passage_not_model_assertion_establishes_source_provenance(self):
        prepared = prepare_retrieved_sources(saved_search(), now=NOW)
        source = prepared["sources"][0]
        self.assertTrue(source["verified"])
        self.assertEqual(source["verification_method"], "retrieved_passage_and_exact_span_check")
        self.assertEqual(source["source_type"], "primary")
        self.assertNotIn("Search snippet intentionally", source["excerpt"])
        self.assertEqual(len(source["provenance"]["raw_content_sha256"]), 64)

    def test_snippet_only_result_is_rejected_before_model_call(self):
        data = saved_search()
        del data[0]["results"][0]["raw_content"]
        result = discover_news_events(saved_search=data, now=NOW, send=lambda prompt: self.fail("no source evidence"))
        self.assertFalse(result["events"])
        self.assertEqual(result["extraction_calls"], 0)
        self.assertIn("snippet_insufficient", result["retrieval"]["rejected"][0]["reason"])

    def test_allowed_domain_matching_rejects_lookalike(self):
        data = saved_search()
        data[0]["results"][0]["url"] = "https://bls.gov.attacker.test/release"
        self.assertFalse(prepare_retrieved_sources(data, now=NOW)["sources"])

    def test_exact_claim_passage_is_required(self):
        source = prepare_retrieved_sources(saved_search(), now=NOW)["sources"][0]
        reply = extraction(source)
        reply["events"][0]["claims"][0]["quote"] = "This invented forecast is not in the retrieved source passage."
        grounded = ground_discovered_events(reply, [source], now=NOW)
        self.assertFalse(grounded["events"])
        self.assertIn("absent", grounded["rejected"][0]["reason"])

    def test_extractor_cannot_invent_source_or_verification(self):
        source = prepare_retrieved_sources(saved_search(), now=NOW)["sources"][0]
        reply = extraction(source)
        reply["events"][0]["claims"][0].update(source_id="invented", verified=True,
                                                url="https://bls.gov/invented", raw_content="invented")
        self.assertFalse(ground_discovered_events(reply, [source], now=NOW)["events"])

    def test_date_must_be_in_a_retrieved_event_passage(self):
        source = prepare_retrieved_sources(saved_search(), now=NOW)["sources"][0]
        reply = extraction(source)
        reply["events"][0]["date_evidence"]["date_text"] = "September 4, 2026"
        grounded = ground_discovered_events(reply, [source], now=NOW)
        self.assertFalse(grounded["events"])
        self.assertIn("date text absent", grounded["rejected"][0]["reason"])

    def test_old_event_is_not_refreshed_by_current_publication_metadata(self):
        data = saved_search()
        data[0]["results"][0]["raw_content"] = data[0]["results"][0]["raw_content"].replace("September 5, 2026", "August 5, 2026")
        source = prepare_retrieved_sources(data, now=NOW)["sources"][0]
        reply = extraction(source)
        reply["events"][0]["date_evidence"]["date_text"] = "August 5, 2026"
        grounded = ground_discovered_events(reply, [source], now=NOW)
        self.assertFalse(grounded["events"])
        self.assertIn("stale", grounded["rejected"][0]["reason"])

    def test_model_event_label_changes_do_not_change_document_identity(self):
        source = prepare_retrieved_sources(saved_search(), now=NOW)["sources"][0]
        reply = extraction(source)
        first = ground_discovered_events(reply, [source], now=NOW)["events"][0]
        reply["events"][0].update(event_key="another name for exactly this release", development_key="different editorial label")
        second = ground_discovered_events(reply, [source], now=NOW)["events"][0]
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(first["development_id"], second["development_id"])

    def test_search_has_finite_budget_and_requests_raw_content(self):
        calls = []

        def search(query, **kwargs):
            calls.append(kwargs)
            return {"results": []}

        result = discover_news_events(search=search, send=lambda p: self.fail("no sources"), now=NOW)
        self.assertEqual(result["search_calls"], 2)
        self.assertTrue(all(c["include_raw_content"] is True and c["days"] == 1 and c["max_results"] == 6 for c in calls))
        with self.assertRaises(ValueError):
            discover_news_events(search=search, queries=["one", "two", "three"], now=NOW)

    def test_truncated_evidence_is_labeled_and_cannot_ground_removed_text(self):
        policy = replace(DiscoveryPolicy(), max_source_characters=900)
        data = saved_search()
        data[0]["results"][0]["raw_content"] += "\n\n" + data[0]["results"][0]["raw_content"]
        source = prepare_retrieved_sources(data, now=NOW, policy=policy)["sources"][0]
        self.assertTrue(source["provenance"]["truncated_for_discovery"])
        self.assertEqual(len(source["excerpt"]), 900)

    def test_complete_replay_generates_private_article_and_never_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            replies = iter(replay_responses())
            result = run_news_scan(output_dir=directory, saved_search=saved_search(), now=NOW,
                                   send=lambda prompt: next(replies))
            self.assertFalse(result["publishable"])
            self.assertFalse(result["activation"]["publication_enabled"])
            self.assertFalse(result["activation"]["schedule_installed"])
            self.assertEqual(result["discovery"]["search_calls"], 0)
            self.assertEqual(result["discovery"]["extraction_calls"], 1)
            self.assertEqual(len(result["drafts"]), 1)
            draft = result["drafts"][0]["result"]
            self.assertEqual(draft["status"], "draft_ready", draft)
            self.assertTrue(Path(draft["artifact_paths"]["preview_html"]).exists())
            self.assertTrue(Path(result["artifact_path"]).exists())

    def test_cli_saved_search_and_response_fixture_is_fully_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "search.json").write_text(json.dumps(saved_search()), encoding="utf-8")
            (path / "responses.json").write_text(json.dumps(replay_responses()), encoding="utf-8")
            with redirect_stdout(io.StringIO()) as printed:
                code = main(["--out", str(path / "preview"), "--saved-search", str(path / "search.json"),
                             "--responses", str(path / "responses.json"), "--now", NOW])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(printed.getvalue())["drafts"], 1)

    def test_provider_errors_are_sanitized(self):
        def send(prompt):
            raise RuntimeError("api-key-secret")
        result = discover_news_events(saved_search=saved_search(), send=send, now=NOW)
        self.assertEqual(result["errors"], ["discovery_failed:RuntimeError"])
        self.assertNotIn("api-key-secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
