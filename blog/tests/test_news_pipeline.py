"""Offline tests for event selection, source contracts and the draft-only loop."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))

from news_pipeline import run_news_article
from news_selection import NewsPolicy, select_news_events, validate_event


NOW = "2026-09-05T16:00:00Z"


def packet():
    """Explicitly fictional fixture, never a purported real news example."""
    return {
        "event": {"event_id": "fictional-atlas-outlook-september", "development_id": "release-1",
                  "headline": "Fictional fixture: Atlas lowers its sales outlook",
                  "event_time": "2026-09-05T14:00:00Z", "event_time_basis": "official_release",
                  "source_ids": [1], "claim_ids": ["c1", "c2", "c3"],
                  "significance": 4, "significance_reason": "The updated outlook changes expected sales growth.",
                  "audience_relevance": 4, "relevance_reason": "Readers following industrial demand would learn what changed.",
                  "material_development": True, "major_event": False, "tickers": ["FICTIONAL"]},
        "research": {
            "sources": [{"id": 1, "title": "Fictional Atlas Manufacturing outlook release",
                         "url": "https://example.test/atlas/outlook-september",
                         "published_at": "2026-09-05T14:00:00Z", "verified_at": "2026-09-05T15:00:00Z",
                         "verified": True, "source_type": "primary", "role": "event", "page_type": "release",
                         "excerpt": "Fictional fixture. Atlas Manufacturing now expects full-year sales growth of 2% to 4%, compared with its previous 5% to 7% range. Incoming orders have softened in its industrial division. Management left its investment plan unchanged and did not quantify the likely duration of the slowdown."}],
            "claims": [{"id": "c1", "text": "Atlas lowered expected sales growth to 2% to 4%, from 5% to 7%.", "source_ids": [1]},
                       {"id": "c2", "text": "Incoming orders softened in its industrial division.", "source_ids": [1]},
                       {"id": "c3", "text": "The investment plan is unchanged; management did not quantify the likely duration of the slowdown.", "source_ids": [1]}]
        }}


def plan():
    return {"feasible": True, "reader_question": "What changed in this outlook and what remains uncertain?",
            "answer": "Sales are expected to grow more slowly, while the investment plan is unchanged.",
            "claim_ids": ["c1", "c2", "c3"], "word_budget": 500,
            "sections": [{"heading": "A lower growth expectation", "purpose": "Explain the revision", "claim_ids": ["c1", "c2"]},
                         {"heading": "The unresolved duration", "purpose": "Explain what the release cannot establish", "claim_ids": ["c3"]}],
            "unknowns": ["How long softer orders will persist"]}


def article():
    return {"title": "Fictional fixture: Atlas cuts its sales outlook as orders soften", "title_claim_ids": ["c1", "c2"],
            "dek": "The revised forecast points to slower growth, while the company keeps its investment plan intact.", "dek_claim_ids": ["c1", "c3"],
            "takeaways": [{"text": "The company still expects sales growth; a lower forecast does not mean a projected sales decline.", "claim_ids": ["c1"]},
                          {"text": "Its unchanged investment plan provides a different signal from the weaker near-term orders.", "claim_ids": ["c2", "c3"]}],
            "sections": [{"heading": "A smaller increase in sales", "paragraphs": [
                {"text": "Atlas Manufacturing lowered its full-year sales-growth forecast on September 5 to a range of 2% to 4%, compared with its earlier expectation of 5% to 7%. The new range remains positive. Readers should distinguish the deterioration in the forecast from an outright contraction in expected annual sales.", "kind": "fact", "claim_ids": ["c1"]},
                {"text": "Management reported softer incoming orders in the industrial division. That identifies a pressure point within the company, but the release does not establish a broad decline across the industrial sector. It would take evidence from other businesses to make that wider assessment.", "kind": "analysis", "claim_ids": ["c2"]}]},
                {"heading": "What the unchanged plan leaves open", "paragraphs": [
                    {"text": "The company left its investment plan unchanged and did not quantify how long the order slowdown might persist. Those decisions and omissions matter together: the current spending plan remains in place, while the timing of a recovery cannot be inferred from this release.", "kind": "analysis", "claim_ids": ["c3"]},
                    {"text": "The useful next question is whether subsequent orders stabilize enough to support the revised outlook. A further update to the investment plan would also help readers assess whether the weaker demand has changed management's longer-term decisions.", "kind": "unknown", "claim_ids": []}]}]}


def passing_review():
    return {"passed": True, "issues": []}


class NewsSelectionTests(unittest.TestCase):
    def test_primary_release_needs_no_seasonality_or_ticker(self):
        data = packet()
        del data["event"]["tickers"]
        result = select_news_events([data["event"]], data["research"], now=NOW)
        self.assertEqual(result["selected"][0]["slot"], "lead")
        self.assertFalse(result["publishable"])
        self.assertFalse(result["policy"]["enabled"])
        self.assertFalse(result["policy"]["automatic_publication"])

    def test_old_event_does_not_become_fresh_when_page_is_retrieved(self):
        data = packet()
        data["event"]["event_time"] = "2026-08-01T14:00:00Z"
        checked = validate_event(**data, now=NOW)
        self.assertIn("stale_event", checked["issues"])

    def test_generic_refreshed_page_is_rejected(self):
        data = packet()
        data["research"]["sources"][0]["page_type"] = "estimates"
        checked = validate_event(**data, now=NOW)
        self.assertFalse(checked["valid"])
        self.assertIn("generic_page_is_not_event_evidence", checked["source_issues"][0]["issues"])

    def test_event_timestamp_is_required_and_timezone_aware(self):
        for stamp in (None, "2026-09-05T14:00:00"):
            with self.subTest(stamp=stamp):
                data = packet()
                data["event"]["event_time"] = stamp
                self.assertFalse(validate_event(**data, now=NOW)["valid"])

    def test_verification_and_excerpts_are_required(self):
        for field, value in (("verified", False), ("excerpt", ""), ("verified_at", "2026-08-01T14:00:00Z")):
            with self.subTest(field=field):
                data = packet()
                data["research"]["sources"][0][field] = value
                self.assertFalse(validate_event(**data, now=NOW)["valid"])

    def test_stale_context_source_cannot_establish_new_event(self):
        data = packet()
        data["research"]["sources"][0].update(role="context", published_at="2020-01-01T14:00:00Z")
        self.assertIn("no_fresh_event_source", validate_event(**data, now=NOW)["issues"])

    def test_single_secondary_report_is_not_enough(self):
        data = packet()
        data["research"]["sources"][0]["source_type"] = "reporting"
        self.assertIn("event_requires_primary_source_or_two_reporting_domains", validate_event(**data, now=NOW)["issues"])
        second = {**data["research"]["sources"][0], "id": 2, "url": "https://independent.test/report"}
        data["research"]["sources"].append(second)
        data["event"]["source_ids"].append(2)
        self.assertTrue(validate_event(**data, now=NOW)["valid"])

    def test_already_covered_development_is_skipped(self):
        data = packet()
        result = select_news_events([data["event"]], data["research"], now=NOW,
                                    coverage=[{"event_id": data["event"]["event_id"],
                                               "development_ids": ["release-1"], "article_id": "article-a"}])
        self.assertFalse(result["selected"])
        self.assertEqual(result["decisions"][0]["action"], "skip")

    def test_same_event_new_development_updates_existing_article(self):
        data = packet()
        result = select_news_events([data["event"]], data["research"], now=NOW,
                                    coverage=[{"event_id": data["event"]["event_id"],
                                               "development_ids": ["initial-report"], "article_id": "article-a",
                                               "last_event_time": "2026-09-05T12:00:00Z"}])
        self.assertEqual(result["selected"][0]["action"], "update_draft")
        self.assertEqual(result["selected"][0]["update_article_id"], "article-a")

    def test_different_event_same_ticker_remains_eligible(self):
        data = packet()
        result = select_news_events([data["event"]], data["research"], now=NOW,
                                    coverage=[{"event_id": "another-event", "tickers": ["FICTIONAL"],
                                               "development_ids": ["release-1"], "article_id": "article-a"}])
        self.assertEqual(result["selected"][0]["action"], "new_draft")

    def test_latest_development_deduplicates_event_within_batch(self):
        data = packet()
        earlier = {**data["event"], "development_id": "initial-report", "event_time": "2026-09-05T13:00:00Z"}
        result = select_news_events([earlier, data["event"]], data["research"], now=NOW)
        self.assertEqual(len(result["selected"]), 1)
        self.assertEqual(result["selected"][0]["development_id"], "release-1")
        self.assertEqual(result["decisions"][0]["reasons"], ["superseded_by_newer_event_development"])

    def test_major_news_can_exceed_baseline_but_is_bounded(self):
        data = packet()
        events = [{**data["event"], "event_id": f"event-{i}", "major_event": True} for i in range(6)]
        result = select_news_events(events, data["research"], now=NOW)
        self.assertEqual(len(result["selected"]), 4)
        self.assertEqual(result["selected"][2]["slot"], "major_event_overflow")
        self.assertEqual(len(result["decisions"]), 2)

    def test_second_story_is_optional_when_value_is_low(self):
        data = packet()
        weaker = {**data["event"], "event_id": "weaker", "significance": 2, "audience_relevance": 2}
        result = select_news_events([data["event"], weaker], data["research"], now=NOW)
        self.assertEqual(len(result["selected"]), 1)
        self.assertEqual(result["decisions"][0]["action"], "defer")


class NewsPipelineTests(unittest.TestCase):
    def run_with(self, replies, **kwargs):
        responses = iter(replies)
        prompts = []

        def send(prompt):
            prompts.append(prompt)
            return json.dumps(next(responses))

        result = run_news_article(**packet(), send=send, now=NOW, **kwargs)
        return result, prompts

    def test_success_is_a_source_linked_preview_never_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            result, prompts = self.run_with([plan(), article(), passing_review()], output_dir=directory)
            self.assertEqual(result["status"], "draft_ready", result)
            self.assertFalse(result["publishable"])
            self.assertEqual(result["provider_calls"], 3)
            self.assertIn('href="#source-1"', result["article_html"])
            self.assertIn("https://example.test/atlas/outlook-september", result["article_html"])
            self.assertIn("As of September 05, 2026", result["article_html"])
            self.assertTrue(Path(result["artifact_paths"]["preview_html"]).exists())
            self.assertIn("Incoming orders have softened", prompts[0])
            self.assertIn("Incoming orders have softened", prompts[1])

    def test_bad_evidence_prevents_any_provider_call(self):
        data = packet()
        data["research"]["sources"][0]["verified"] = False
        result = run_news_article(**data, send=lambda prompt: self.fail("provider must not run"), now=NOW)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["provider_calls"], 0)

    def test_bad_citations_hold_even_when_model_reviewer_passes(self):
        bad = article()
        bad["sections"][0]["paragraphs"][0]["claim_ids"] = ["invented"]
        result, _ = self.run_with([plan(), bad, passing_review()], max_revisions=0)
        self.assertEqual(result["status"], "hold")
        self.assertNotIn("article_html", result)
        self.assertTrue(any("invalid_claim_references" in x for x in result["validation"]["article"]["issues"]))

    def test_material_editorial_issue_triggers_one_revision(self):
        fail = {"passed": False, "issues": [{"severity": "editorial", "location": "dek", "problem": "Repetition", "fix": "Remove repeated detail"}]}
        result, prompts = self.run_with([plan(), article(), fail, article(), passing_review()])
        self.assertEqual(result["status"], "draft_ready")
        self.assertEqual(result["revisions"], 1)
        self.assertEqual(result["provider_calls"], 5)
        self.assertIn("Repetition", prompts[3])

    def test_persistent_semantic_issue_holds_after_one_revision(self):
        fail = {"passed": False, "issues": [{"severity": "hard", "location": "title", "problem": "Overstates evidence", "fix": "Narrow claim"}]}
        result, _ = self.run_with([plan(), article(), fail, article(), fail])
        self.assertEqual(result["status"], "hold")
        self.assertFalse(result["publishable"])
        self.assertEqual(result["provider_calls"], 5)

    def test_final_title_is_reviewed_and_never_regenerated_after_gate(self):
        result, prompts = self.run_with([plan(), article(), passing_review()])
        self.assertIn(article()["title"], prompts[-1])
        self.assertEqual(result["article"]["title"], article()["title"])

    def test_provider_failure_does_not_leak_error_text_or_retry(self):
        def bad_send(prompt):
            raise RuntimeError("secret-key-in-provider-error")
        result = run_news_article(**packet(), send=bad_send, now=NOW)
        self.assertEqual(result["status"], "hold")
        self.assertEqual(result["provider_calls"], 1)
        self.assertNotIn("secret-key", json.dumps(result))

    def test_malformed_review_cannot_claim_success(self):
        result, _ = self.run_with([plan(), article(), {"passed": True, "issues": [{"severity": "hard"}]}])
        self.assertEqual(result["status"], "hold")
        self.assertTrue(result["errors"])

    def test_markup_and_calibrated_probability_are_rejected(self):
        bad = article()
        bad["dek"] = '<script>alert("x")</script> calibrated probability'
        result, _ = self.run_with([plan(), bad, passing_review()], max_revisions=0)
        self.assertEqual(result["status"], "hold")
        self.assertNotIn("article_html", result)

    def test_refuse_publication_directory_before_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "posts.json").write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "publication indexes"):
                run_news_article(**packet(), send=lambda prompt: self.fail("must not call"),
                                 output_dir=Path(directory, "child"), now=NOW)
            self.assertFalse(Path(directory, "child").exists())

    def test_live_modules_are_not_imported_by_offline_workflow(self):
        before = set(sys.modules)
        self.run_with([plan(), article(), passing_review()])
        self.assertFalse({"config", "AI_tools", "publish_article", "article_processor", "daily_article_queue"}
                         & (set(sys.modules) - before))


if __name__ == "__main__":
    unittest.main()
