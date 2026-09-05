"""Offline regressions for the evidence-to-editorial boundary and bounded edits."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import angle_prompts
import angle_writer
from editorial_review import review_article, run_review_cycle
from test_angle_writer import CANNED_PLAN, FAKE_IMAGES, GOOD_PROSE, live_card


def review(soft=None, hard=None, decision="publish"):
    return json.dumps({"decision": decision, "hard_issues": hard or [],
                       "soft_issues": soft or []})


MATERIAL = {"code": "REPETITION", "severity": "material",
            "detail": "The body repeats the opening record without interpretation."}
RESEARCH = {
    "symbol": "AAPL", "company": "Apple",
    "sources": [{"id": 9, "title": "Apple results", "publisher": "Issuer",
                 "url": "https://example.test/results", "date": "2026-07-20",
                 "event_date": "2026-07-19", "subject": "Apple",
                 "excerpt": "Apple reported an increase in services revenue.", "fresh": True},
                {"id": 4, "title": "Sector demand", "publisher": "Agency",
                 "url": "https://example.test/sector", "date": "2026-07-18"}],
    "catalysts": [{"summary": "Services revenue rose", "subject": "Apple",
                   "date": "2026-07-19", "source_id": 9},
                  {"summary": "A filtered-out claim must not survive", "source_id": 33}],
    "analyst": {"summary": "Mixed source attribution is unavailable", "source_ids": [4, 33]},
}


class TestEvidencePlanning(unittest.TestCase):
    def test_substantive_evidence_keeps_subject_event_date_and_attribution(self):
        digest = angle_prompts._research_digest(RESEARCH)
        self.assertEqual(digest["sources"][0]["excerpt"], RESEARCH["sources"][0]["excerpt"])
        self.assertEqual(digest["sources"][0]["subject"], "Apple")
        self.assertEqual(digest["sources"][0]["event_date"], "2026-07-19")
        self.assertEqual(digest["claims"], [{
            "evidence_id": "catalysts[0]", "source_ids": [9],
            "content": {"summary": "Services revenue rose", "subject": "Apple", "date": "2026-07-19"}}])
        self.assertEqual(digest["omitted_unattributed_claims"], 2)

    def test_writer_and_planner_receive_same_substantive_brief(self):
        card = live_card()
        plan = dict(CANNED_PLAN, source_ids=[9])
        for prompt in (angle_prompts.build_plan_prompt(card, RESEARCH),
                       angle_prompts.build_write_prompt(card, plan, RESEARCH)):
            self.assertIn(RESEARCH["sources"][0]["excerpt"], prompt)
            self.assertNotIn("A filtered-out claim must not survive", prompt)

    def test_missing_research_does_not_manufacture_an_event(self):
        digest = angle_prompts._research_digest({"sources": [], "catalysts": RESEARCH["catalysts"]})
        self.assertFalse(digest["available"])
        self.assertEqual(digest["claims"], [])

    def test_computed_window_and_cohort_reach_writer_and_reviewer(self):
        card = live_card()
        evidence = {"window": {"end_date": "2026-10-18", "inclusive": True},
                    "cohort": {"years": [2011, 2015, 2019], "n": 3},
                    "risk": {"extrema_timing_known": False}}
        card["story_cell"]["evidence"] = evidence
        self.assertEqual(angle_prompts._card_digest(card)["story_cell"]["evidence"], evidence)
        self.assertEqual(angle_writer.build_editorial_facts(card)["evidence"], evidence)

    def version2(self):
        plan = copy.deepcopy(CANNED_PLAN)
        plan.update(schema_version=2, reader_question="What does this window's record add to the news?",
                    source_ids=[9], claim_support=[{"claim": "Services revenue rose",
                    "source_ids": [9], "support": "Apple results report higher services revenue"}])
        plan["beats"] = plan["beats"][:3]
        return plan

    def test_new_plan_requires_question_and_claim_support(self):
        plan = self.version2()
        angle_prompts.parse_plan(json.dumps(plan), "CLOCKWORK", research=RESEARCH)
        del plan["reader_question"]
        with self.assertRaisesRegex(angle_prompts.PlanError, "reader_question"):
            angle_prompts.parse_plan(json.dumps(plan), "CLOCKWORK", research=RESEARCH)
        plan = self.version2()
        plan["claim_support"] = []
        with self.assertRaisesRegex(angle_prompts.PlanError, "support a planned claim"):
            angle_prompts.parse_plan(json.dumps(plan), "CLOCKWORK", research=RESEARCH)

    def test_new_plan_rejects_source_absent_from_research(self):
        with self.assertRaisesRegex(angle_prompts.PlanError, "missing from research"):
            angle_prompts.parse_plan(json.dumps(self.version2()), "CLOCKWORK", research=None)


class TestBoundedEditorialEdit(unittest.TestCase):
    def generate(self, write_results, review_results=None, **kwargs):
        writes, reviews = [], []
        write_iter = iter(write_results)
        review_iter = iter(review_results or [])

        def send_write(prompt):
            writes.append(prompt)
            return next(write_iter)

        def send_review(prompt):
            reviews.append(prompt)
            return next(review_iter)

        plan = kwargs.pop("plan", CANNED_PLAN)
        result = angle_writer.generate_angle_article(
            live_card(), images=FAKE_IMAGES, send_plan=lambda _: json.dumps(plan),
            send_write=send_write, editorial_send=send_review,
            run_editorial=review_results is not None, **kwargs)
        return result, writes, reviews

    def test_material_soft_issue_edits_even_if_reviewer_says_publish(self):
        revised = GOOD_PROSE.replace("The run covers", "The evidence covers")
        result, writes, reviews = self.generate(
            [GOOD_PROSE, revised], [review([MATERIAL]), review()])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(writes), 2)
        self.assertEqual(len(reviews), 2)
        self.assertIn("REPETITION", writes[1])
        self.assertIn("The evidence covers", result["html"])
        self.assertIn("16 of the last 20", result["html"])
        self.assertIn("https://x/t.png", result["html"])

    def test_new_factual_error_after_soft_edit_is_held(self):
        broken = GOOD_PROSE.replace("closing higher in 9 of the last 10 years",
                                    "closing higher in 14 of the last 20 years")
        result, writes, reviews = self.generate(
            [GOOD_PROSE, broken], [review([MATERIAL]), review()])
        self.assertEqual(result["status"], "hold")
        self.assertIn("PAIR_MISMATCH", [i["code"] for i in result["gate2"]["revision_issues"]])
        self.assertEqual(len(writes), 2)
        self.assertEqual(len(reviews), 2)

    def test_persistent_material_issue_stops_after_one_edit(self):
        result, writes, reviews = self.generate(
            [GOOD_PROSE, GOOD_PROSE], [review([MATERIAL]), review([MATERIAL])])
        self.assertEqual(result["status"], "hold")
        self.assertEqual(len(writes), 2)
        self.assertEqual(len(reviews), 2)

    def test_minor_style_does_not_spend_an_edit(self):
        minor = dict(MATERIAL, severity="minor")
        result, writes, _ = self.generate([GOOD_PROSE], [review([minor])])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(writes), 1)

    def test_budget_enforced_on_prose_and_rechecked_after_cut(self):
        long_prose = GOOD_PROSE.replace("{{SOURCES}}", "<p>" + "padding " * 1000 + "</p>{{SOURCES}}")
        result, writes, _ = self.generate([long_prose, GOOD_PROSE], plan=dict(CANNED_PLAN, word_budget=450))
        self.assertEqual(result["status"], "ready")
        self.assertIn("LENGTH_OVER_BUDGET", writes[1])
        self.assertGreater(result["gate1"]["prose"]["word_count"], result["gate1"]["prose"]["word_limit"])
        self.assertLessEqual(result["gate2"]["prose"]["word_count"], result["gate2"]["prose"]["word_limit"])

    def test_citation_removed_by_assembler_still_requires_repair(self):
        bad = GOOD_PROSE.replace("{{SOURCES}}", "<p>An unsupported fact.<sup>[99]</sup></p>{{SOURCES}}")
        result, writes, _ = self.generate([bad, GOOD_PROSE])
        self.assertEqual(result["status"], "ready")
        self.assertIn("CITATION_UNPLANNED_OR_MISSING", writes[1])

    def test_citations_survive_edit_and_each_review_uses_current_display_order(self):
        first = GOOD_PROSE.replace("{{SOURCES}}", "<p>Services revenue rose.<sup>[9]</sup> "
                                   "Sector demand changed.<sup>[4]</sup></p>{{SOURCES}}")
        second = GOOD_PROSE.replace("{{SOURCES}}", "<p>Sector demand changed.<sup>[4]</sup> "
                                    "Services revenue rose.<sup>[9]</sup></p>{{SOURCES}}")
        result, _, prompts = self.generate([first, second], [review([MATERIAL]), review()],
                                          research=RESEARCH, plan=dict(CANNED_PLAN, source_ids=[9, 4]))
        self.assertEqual(result["status"], "ready")
        self.assertIn("Sector demand changed.<sup>[1]</sup>", result["html"])
        self.assertIn("Services revenue rose.<sup>[2]</sup>", result["html"])
        for prompt, expected_title in zip(prompts, ["Apple results", "Sector demand"]):
            packet = json.loads(prompt.split("RESEARCH DATA:\n", 1)[1].split("\n\nARTICLE HTML", 1)[0])
            self.assertEqual(packet["sources"][0]["id"], 1)
            self.assertEqual(packet["sources"][0]["title"], expected_title)


class TestCitationAndReviewIdentity(unittest.TestCase):
    def test_reviewer_source_order_matches_rendered_list_without_mutating_input(self):
        original = copy.deepcopy(RESEARCH)
        aligned = angle_writer.research_for_rendered_citations(RESEARCH, [4, 9])
        self.assertEqual([s["id"] for s in aligned["sources"]], [1, 2])
        self.assertEqual(aligned["sources"][0]["title"], "Sector demand")
        self.assertEqual(aligned["catalysts"][0]["source_id"], 2)
        self.assertIsNone(aligned["catalysts"][1]["source_id"])
        self.assertEqual(RESEARCH, original)

    def test_review_states_inclusive_dates_and_loss_semantics(self):
        prompts = []
        review_article("<p>The stock lost 11.2%.</p>", {}, send=lambda p: prompts.append(p) or review())
        self.assertIn("Sep 19, 2026", prompts[0])
        self.assertIn("start + (days - 1)", prompts[0])
        self.assertIn("'lost 11.2%' is the same result as -11.2%", prompts[0])

    def test_legacy_editorial_cycle_also_repairs_material_repetition(self):
        html = "<html><body>Original</body></html>"
        responses = iter([review([MATERIAL]), "<html><body>Edited</body></html>", review([MATERIAL])])
        result_html, report = run_review_cycle(html, {}, send=lambda _: next(responses))
        self.assertIn("Edited", result_html)
        self.assertTrue(report["repaired"])
        self.assertEqual(report["decision"], "hold")


if __name__ == "__main__":
    unittest.main()
