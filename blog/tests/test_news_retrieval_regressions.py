"""Regressions found by the September 6 isolated live news scan."""
import copy
from dataclasses import replace
import hashlib
import sys
from pathlib import Path
import unittest

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))

from news_discovery import DiscoveryPolicy, ground_discovered_events, prepare_retrieved_sources
from news_selection import select_news_events
from test_news_discovery import extraction, saved_search
from test_news_pipeline import NOW


class NewsRetrievalRegressionTests(unittest.TestCase):
    def result(self, host, number=1):
        item = copy.deepcopy(saved_search()[0]["results"][0])
        item["title"] = "Fictional company publishes a revised sales outlook"
        item["url"] = f"https://{host}/news/fixture-{number}"
        item["raw_content"] = ("# Site navigation\n\n" + "[Portfolio](https://example.test/menu)\n" * 400
                               + "\n# " + item["title"] + "\n\n" + item["raw_content"]
                               + "\n\n## Recommended Stories\n\nUnrelated recommendations and site furniture.")
        return item

    def test_navigation_removed_and_exact_body_offsets_and_hashes_preserved(self):
        item = self.result("finance.yahoo.com")
        source = prepare_retrieved_sources([{"results": [item]}], now=NOW)["sources"][0]
        provenance = source["provenance"]
        self.assertGreater(provenance["body_start"], 13000)
        self.assertNotIn("Portfolio", source["excerpt"])
        self.assertNotIn("Recommended Stories", source["excerpt"])
        self.assertEqual(item["raw_content"][provenance["inspected_start"]:provenance["inspected_end"]], source["excerpt"])
        self.assertEqual(hashlib.sha256(source["excerpt"].encode()).hexdigest(), provenance["excerpt_sha256"])
        self.assertEqual(hashlib.sha256(item["raw_content"].encode()).hexdigest(), provenance["raw_content_sha256"])

    def test_bodyless_photo_shell_is_rejected_even_when_raw_content_is_long(self):
        item = self.result("reuters.com")
        item["raw_content"] = ("# " + item["title"] + "\n\n" + "![Photo](https://example.test/photo.jpg)\n" * 100
                               + "\nOur Standards: Reuters\n\nA long journalist biography.\n\n## Read Next\n\n"
                               + saved_search()[0]["results"][0]["raw_content"])
        result = prepare_retrieved_sources([{"results": [item]}], now=NOW)
        self.assertFalse(result["sources"])
        self.assertEqual(result["rejected"][0]["reason"], "substantive_article_body_missing")

    def test_shared_domain_cannot_exhaust_budget_before_distinct_source(self):
        items = [self.result("finance.yahoo.com", number) for number in range(3)] + [self.result("cnbc.com")]
        # Original first-three-prefix allocation consumed the entire budget.
        policy = replace(DiscoveryPolicy(), max_total_characters=4000)
        result = prepare_retrieved_sources([{"results": items}], now=NOW, policy=policy)
        domains = [source["url"].split("/")[2] for source in result["sources"]]
        self.assertEqual(domains[:2], ["finance.yahoo.com", "cnbc.com"])
        self.assertEqual(len(result["sources"]), 4)
        self.assertLessEqual(sum(len(s["excerpt"]) for s in result["sources"]), 4000)

    def test_too_small_allocation_does_not_claim_reporting_was_inspected(self):
        result = prepare_retrieved_sources([{"results": [self.result("cnbc.com")]}], now=NOW,
                                          policy=replace(DiscoveryPolicy(), max_total_characters=100))
        self.assertFalse(result["sources"])
        self.assertEqual(result["rejected"][0]["reason"], "bounded_budget_cannot_include_substantive_body")

    def test_full_reporting_flattened_into_one_paragraph_is_preserved(self):
        item = self.result("cnbc.com")
        prose = saved_search()[0]["results"][0]["raw_content"].replace("\n", " ")
        item["raw_content"] = "# " + item["title"] + "\n\n" + prose + " " + prose
        result = prepare_retrieved_sources([{"results": [item]}], now=NOW)
        self.assertEqual(len(result["sources"]), 1)


class DateOnlyUpdateRegressionTests(unittest.TestCase):
    def grounded(self):
        source = prepare_retrieved_sources(saved_search(), now=NOW)["sources"][0]
        grounded = ground_discovered_events(extraction(source), [source], now=NOW)
        event = grounded["events"][0]
        coverage = [{"event_id": event["event_id"], "article_id": "previous-draft",
                     "development_ids": ["older-grounded-development"], "last_event_time": event["event_time"],
                     "source_body_hashes": {source["url"]: "0" * 64},
                     "covered_claim_texts": ["The older fictional release left the sales outlook unchanged."]}]
        return event, grounded["research"], coverage

    def test_material_grounded_same_day_development_proposes_update(self):
        event, research, coverage = self.grounded()
        result = select_news_events([event], research, coverage=coverage, now=NOW)
        self.assertEqual(result["selected"][0]["action"], "update_draft")
        self.assertEqual(result["selected"][0]["update_article_id"], "previous-draft")

    def test_repeated_grounded_development_still_skips(self):
        event, research, coverage = self.grounded()
        coverage[0]["development_ids"].append(event["development_id"])
        result = select_news_events([event], research, coverage=coverage, now=NOW)
        self.assertFalse(result["selected"])
        self.assertEqual(result["decisions"][0]["reasons"], ["development_already_covered"])

    def test_new_label_without_grounded_fingerprint_does_not_unlock_update(self):
        event, research, coverage = self.grounded()
        event["development_id"] = "invented-new-label"
        result = select_news_events([event], research, coverage=coverage, now=NOW)
        self.assertFalse(result["selected"])
        self.assertEqual(result["decisions"][0]["reasons"], ["older_than_existing_coverage"])

    def test_mismatched_source_span_does_not_unlock_update(self):
        event, research, coverage = self.grounded()
        research["claims"][0]["support"][0]["quote"] = "This invented evidence does not match the source."
        result = select_news_events([event], research, coverage=coverage, now=NOW)
        self.assertFalse(result["selected"])

    def test_older_day_still_rejected_even_with_grounded_development(self):
        event, research, coverage = self.grounded()
        event["event_time"] = "2026-09-04T00:00:00Z"
        result = select_news_events([event], research, coverage=coverage, now=NOW)
        self.assertFalse(result["selected"])
        self.assertEqual(result["decisions"][0]["reasons"], ["older_than_existing_coverage"])

    def test_unchanged_body_different_quote_is_not_a_new_development(self):
        source = prepare_retrieved_sources(saved_search(), now=NOW)["sources"][0]
        original = ground_discovered_events(extraction(source), [source], now=NOW)
        original_event = original["events"][0]
        coverage = [{"event_id": original_event["event_id"], "article_id": "already-covered",
                     "development_ids": [original_event["development_id"]],
                     "last_event_time": original_event["event_time"],
                     "source_body_hashes": original_event["source_body_hashes"],
                     "covered_claim_texts": [c["text"] for c in original["research"]["claims"]]}]
        proposal = extraction(source)
        proposal["events"][0]["claims"][0]["quote"] = source["excerpt"].split("\n\n")[0]
        shorter = ground_discovered_events(proposal, [source], now=NOW)
        self.assertNotEqual(original_event["development_id"], shorter["events"][0]["development_id"])
        result = select_news_events(shorter["events"], shorter["research"], coverage=coverage, now=NOW)
        self.assertFalse(result["selected"])

    def test_changed_body_does_not_make_already_covered_quote_new(self):
        event, research, coverage = self.grounded()
        coverage[0]["covered_claim_texts"] = [research["claims"][0]["text"]]
        result = select_news_events([event], research, coverage=coverage, now=NOW)
        self.assertFalse(result["selected"])

    def test_legacy_coverage_without_prior_evidence_snapshot_skips_equal_date(self):
        for field in ("source_body_hashes", "covered_claim_texts"):
            with self.subTest(field=field):
                event, research, coverage = self.grounded()
                del coverage[0][field]
                result = select_news_events([event], research, coverage=coverage, now=NOW)
                self.assertFalse(result["selected"])


if __name__ == "__main__":
    unittest.main()
