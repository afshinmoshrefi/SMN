"""Private-policy integration and adversarial contract tests; no paid calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import angle_prompts
import editorial_review
import reader_promise as rp


BASE = "/baseline/summary"
FULL = "/cycle/full/summary"
RECENT = "/cycle/within_baseline/summary"
EARLIER = "/cycle/earlier/summary"


def case():
    # A synthetic ACME case whose packet, not model memory, supplies every fact.
    selected = {
        "classification": "era_sensitive",
        "baseline": {"summary": {"n": 20, "years": list(range(2006, 2026)), "median_net": 2.0}},
        "cycle": {"full": {"summary": {"n": 10, "median_net": -1.0}},
                  "within_baseline": {"summary": {"n": 5, "median_net": 3.0}},
                  "earlier": {"summary": {"n": 5, "median_net": -4.0}}},
        "writer_brief": {
            "classification": "era_sensitive", "proposed_reader_question": "Why do the two histories disagree?",
            "permitted_summary_refs": [BASE, FULL, RECENT, EARLIER],
            "required_qualifications": [
                {"id": "cohort.era_sensitive", "text": "Older cycle observations change the direction; the recent cycle subset is positive.",
                 "evidence_refs": [BASE, FULL, RECENT, EARLIER]},
                {"id": "cohort.overlap", "text": "Five recent cycle observations belong to the annual baseline.",
                 "evidence_refs": [BASE, RECENT]}],
            "forbidden_inferences": ["Do not attribute the difference to election causality."], "hold_reasons": []}}
    card = {"resource_id": "2", "symbol": "ACME", "anchor_date": "2026-08-21",
            "angle": {"name": "SEASONAL_CONTEXT"}, "auxiliary_cells": [],
            "story_cell": {"symbol": "ACME", "anchor_date": "2026-08-21", "days": 90,
                "years": "20", "n": 20, "worst_year": 2011, "worst_net": -8.0,
                "evidence": {"window": {"start_date": "2026-08-21", "end_date": "2026-11-18",
                                          "calendar_days": 90, "inclusive": True}}},
            "selection_evidence": selected}
    card["reader_brief"] = rp.build_reader_brief(card)
    return card


def plan_for(card):
    brief = card["reader_brief"]
    return {"schema_version": 2, "feasible": True, "veto_reason": "",
            "reader_question": "Why do the two histories disagree?",
            "thesis": "Older observations explain the historical contrast while the recent samples both have positive medians.",
            "claim_support": [], "source_ids": [], "headlines": ["ACME's mixed history depends on which years are included"],
            "beats": [{"purpose": "Explain the period sensitivity", "carries": ["selection:"+BASE]},
                      {"purpose": "Explain a consequential loss", "carries": ["story:/worst_outcome"]}],
            "h2s": [], "charts": [], "bridge_after_beat": 0, "word_budget": 500,
            "reader_promise": {
                "answer_support": ["selection:"+BASE, "selection:"+FULL, "selection:"+RECENT],
                "why_now": {"text": "The Aug 21 research window is the reference.", "evidence_refs": ["story:/evidence/window"]},
                "risk": {"text": "An observation ended 8% below entry.", "evidence_refs": ["story:/worst_outcome"]},
                "qualifications": deepcopy(brief["required_qualifications"]),
                "headline_support": [{"headline": "ACME's mixed history depends on which years are included",
                                      "evidence_refs": ["selection:"+BASE, "selection:"+FULL]}],
                "hero_brief": {"role": "none", "concept": "No image in this private text draft.",
                               "evidence_refs": [], "factual_implications": []}}}


ARTICLE = """<article><h1>ACME's mixed history depends on which years are included</h1>
<p class="dek">Older cycle observations change the picture; the recent annual and cycle histories both have positive medians.</p>
<p class="direct-answer">The annual record has a positive median across 20 observations, while the full cycle sample has a negative median across 10.</p>
<p>The reference runs Aug 21 through Nov 18, 2026, covering 90 calendar days inclusive of entry.</p>
<p>Five recent cycle observations are already included in the annual baseline. They add context without independent confirmation.</p>
<p>The 2011 observation ended 8% below entry. A favorable median did not eliminate substantial losses.</p>
</article>"""


def independent_review(html, brief):
    # Canned editorial observations tied to actual visible passages.
    rows = [
        ("title", "ACME's mixed history depends on which years are included", ["selection:"+BASE, "selection:"+FULL]),
        ("dek", "Older cycle observations change the picture; the recent annual and cycle histories both have positive medians.", ["selection:"+RECENT, "selection:"+EARLIER]),
        ("answer", "The annual record has a positive median across 20 observations, while the full cycle sample has a negative median across 10.", ["selection:"+BASE, "selection:"+FULL]),
        ("why_now", "The reference runs Aug 21 through Nov 18, 2026, covering 90 calendar days inclusive of entry.", ["story:/evidence/window"]),
        ("risk", "The 2011 observation ended 8% below entry. A favorable median did not eliminate substantial losses.", ["story:/worst_outcome"]),
        ("reader_value", "Older cycle observations change the picture; the recent annual and cycle histories both have positive medians.", ["selection:"+BASE, "selection:"+FULL]),
        ("qualification:cohort.era_sensitive", "Older cycle observations change the picture; the recent annual and cycle histories both have positive medians.", ["selection:"+p for p in [BASE, FULL, RECENT, EARLIER]]),
        ("qualification:cohort.overlap", "Five recent cycle observations are already included in the annual baseline. They add context without independent confirmation.", ["selection:"+BASE, "selection:"+RECENT])]
    return {"article_sha256": rp.fingerprint(html), "brief_sha256": rp.fingerprint(brief),
            "expected_question": "Why do the samples disagree?", "delivered_answer": "The comparison depends on the included years.",
            "checks": [{"check_id": key, "judgment": "supported", "quote": quote, "evidence_refs": refs,
                        "reason": "The quoted passage explains the exact compared sample and its supported consequence."}
                       for key, quote, refs in rows]}


class ReaderPromiseTests(unittest.TestCase):
    def test_selected_brief_rebuilds_facts_and_qualifications_ignoring_supplied_brief(self):
        card = case()
        card["selected_question"] = {"question_id": "acme-window", "text": "Does the historical window justify a strong directional view?"}
        card["reader_brief"] = {"policy": "legacy", "required_qualifications": []}
        before = deepcopy(card)
        brief = rp.build_selected_reader_brief(card)
        self.assertEqual(brief["policy"], "private_v2")
        self.assertEqual(brief["selected_question_id"], "acme-window")
        self.assertEqual(brief["proposed_reader_question"], card["selected_question"]["text"])
        self.assertEqual(len(brief["required_qualifications"]), 2)
        self.assertEqual(brief["evidence_index"]["selection:"+EARLIER]["median_net"], -4.0)
        self.assertEqual(card, before)
        mutated = deepcopy(brief)
        mutated["required_qualifications"].pop()
        self.assertNotEqual(mutated, rp.build_selected_reader_brief(card))

    def test_selected_brief_cannot_fallback_to_generic_question_when_selection_missing(self):
        for question in (None, {}, {"question_id": "selected", "text": " "}, {"text": "What does history say?"}):
            card = case()
            card["selected_question"] = question
            brief = rp.build_selected_reader_brief(card)
            self.assertIn("selected_reader_question_missing", brief["hold_reasons"])

    def test_selected_question_identity_survives_planning_and_independent_review(self):
        card = case()
        card["selected_question"] = {"question_id": "acme-window", "text": "Why do the two histories disagree?"}
        card["reader_brief"] = rp.build_selected_reader_brief(card)
        brief, plan = card["reader_brief"], plan_for(card)
        review = independent_review(ARTICLE, brief)
        for value in (None, "different-question"):
            plan["reader_promise"]["selected_question_id"] = value
            self.assertIn("PROMISE_QUESTION_MISMATCH", [i["code"] for i in rp.validate_promise_plan(plan, brief)])
            review["expected_question_id"] = value
            self.assertIn("PROMISE_QUESTION_MISMATCH", [i["code"] for i in rp.review_reader_promise(ARTICLE, brief, review=review)["issues"]])
        plan["reader_promise"]["selected_question_id"] = "acme-window"
        review["expected_question_id"] = "acme-window"
        self.assertFalse(rp.validate_promise_plan(plan, brief))
        checked = rp.review_reader_promise(ARTICLE, brief, plan=plan, review=review)
        self.assertTrue(checked["text_ready"])
        self.assertIn("already selected question", rp.promise_plan_instructions(brief))
        self.assertIn("matching ID alone cannot establish", rp.build_promise_review_prompt(ARTICLE, brief, plan))
        # Same-ID semantic drift must still use the existing editor failure path.
        review["checks"][2].update(judgment="needs_revision", reason="The writer substituted a method question for the selected reader concern.")
        self.assertFalse(rp.review_reader_promise(ARTICLE, brief, plan=plan, review=review)["text_ready"])

    def test_evidence_to_plan_writer_and_independent_editor(self):
        card = case()
        brief, plan = card["reader_brief"], plan_for(card)
        before = deepcopy(card)
        prompt = angle_prompts.build_plan_prompt(card, available_charts=[])
        self.assertIn('"median_net": -4.0', prompt)
        self.assertIn("cohort.era_sensitive", prompt)
        self.assertIn("Verified selection summary medians/counts are licensed", prompt)
        parsed = angle_prompts.parse_plan(json.dumps(plan), "SEASONAL_CONTEXT", [], reader_brief=brief)
        for p in [angle_prompts.build_write_prompt(card, parsed, available_figs=[]),
                  angle_prompts.build_revision_prompt(ARTICLE, [], card, parsed)]:
            self.assertIn("cohort.era_sensitive", p)
            self.assertIn("material contrasts marked", p)
            self.assertNotIn("Auxiliary-cell numbers (corroborating/conflicting cells)", p)
        observed = []
        def send(p):
            observed.append(p)
            return json.dumps({"decision": "publish", "hard_issues": [], "soft_issues": [],
                "reader_promise_review": independent_review(ARTICLE, brief)})
        result = editorial_review.review_article(ARTICLE, {"reader_brief": brief, "editorial_plan": parsed}, send=send)
        self.assertEqual(len(observed), 1)
        self.assertIn(rp.fingerprint(ARTICLE), observed[0])
        self.assertEqual(result["decision"], "publish")
        self.assertTrue(result["reader_promise_validation"]["text_ready"])
        self.assertFalse(result["reader_promise_validation"]["visual_ready"])
        self.assertEqual(card, before)

    def test_legacy_plan_and_prompt_remain_opt_in(self):
        card = case()
        card.pop("reader_brief")
        card["angle"]["name"] = "CLOCKWORK"
        plan = plan_for(case())
        plan.pop("reader_promise")
        angle_prompts.parse_plan(json.dumps(plan), "CLOCKWORK", [])
        self.assertNotIn("PRIVATE READER", angle_prompts.build_plan_prompt(card, available_charts=[]))
        self.assertNotIn("selection_evidence", angle_prompts._card_digest(card))
        result = editorial_review.review_article("<p>Legacy</p>", {}, send=lambda _: '{"decision":"publish"}')
        self.assertNotIn("reader_promise_validation", result)

    def test_material_qualification_cannot_be_dropped_or_hidden_in_body(self):
        card = case()
        for mutation in ("missing", "placement", "support"):
            plan = plan_for(card)
            q = plan["reader_promise"]["qualifications"]
            if mutation == "missing": q.pop(0)
            if mutation == "placement": q[0]["placement"] = "body"
            if mutation == "support": q[0]["evidence_refs"] = ["selection:"+BASE]
            with self.subTest(mutation=mutation), self.assertRaises(angle_prompts.PlanError):
                angle_prompts.parse_plan(json.dumps(plan), "SEASONAL_CONTEXT", [], reader_brief=card["reader_brief"])

    def test_cannot_choose_cycle_as_answer_and_discard_fixed_baseline(self):
        card = case()
        plan = plan_for(card)
        plan["reader_promise"]["answer_support"] = ["selection:"+FULL]
        self.assertIn("PROMISE_BASELINE_OMITTED", [i["code"] for i in rp.validate_promise_plan(plan, card["reader_brief"])])

    def test_unavailable_required_evidence_holds_instead_of_omitting(self):
        card = case()
        del card["selection_evidence"]["cycle"]["earlier"]
        brief = rp.build_reader_brief(card)
        self.assertTrue(any("invalid_qualification_evidence" in x for x in brief["hold_reasons"]))
        self.assertEqual(len(brief["required_qualifications"]), 2)

    def test_incomparable_and_unclassified_inputs_hold(self):
        for classification in ("incomparable", "insufficient", None):
            card = case()
            card["selection_evidence"]["writer_brief"]["classification"] = classification
            card["selection_evidence"]["classification"] = classification
            self.assertTrue(rp.build_reader_brief(card)["hold_reasons"])

    def test_missing_cycle_context_does_not_veto_eligible_annual_premise(self):
        card = case()
        policy = card["selection_evidence"]["writer_brief"]
        policy["classification"] = "insufficient_context"
        policy["required_qualifications"] = [{"id": "cohort.insufficient_context",
            "text": "The annual history is available; the cycle subset is too small for a separate claim.",
            "evidence_refs": [BASE]}]
        brief = rp.build_reader_brief(card)
        self.assertFalse(brief["hold_reasons"])
        self.assertEqual(brief["required_qualifications"][0]["id"], "cohort.insufficient_context")

    def test_reviewer_cannot_approve_missing_text_with_boolean_or_stale_quotes(self):
        brief = case()["reader_brief"]
        for supplied in ({"passed": True}, [], {"checks": [{"check_id": {}}]}):
            self.assertFalse(rp.review_reader_promise(ARTICLE, brief, review=supplied)["text_ready"])
        changed = ARTICLE.replace("Older cycle observations change the picture; the recent annual and cycle histories both have positive medians.", "The recent history is positive.")
        review = independent_review(changed, brief)
        checked = rp.review_reader_promise(changed, brief, review=review)
        self.assertIn("PROMISE_PASSAGE_MISSING", [i["code"] for i in checked["issues"]])

    def test_hidden_qualification_does_not_satisfy_visible_answer(self):
        brief = case()["reader_brief"]
        changed = ARTICLE.replace('<p class="dek">', '<p class="dek" hidden>')
        review = independent_review(changed, brief)
        self.assertFalse(rp.review_reader_promise(changed, brief, review=review)["text_ready"])

    def test_final_title_change_invalidates_prior_editorial_review(self):
        brief = case()["reader_brief"]
        review = independent_review(ARTICLE, brief)
        changed = ARTICLE.replace("ACME's mixed history depends on which years are included", "ACME will rally")
        checked = rp.review_reader_promise(changed, brief, review=review)
        self.assertIn("PROMISE_REVIEW_PENDING", [i["code"] for i in checked["issues"]])

    def test_inline_emphasis_preserves_visible_quote_spacing(self):
        brief = case()["reader_brief"]
        html = ARTICLE.replace("substantial losses.", "<strong>substantial losses</strong>.")
        self.assertTrue(rp.review_reader_promise(html, brief, review=independent_review(html, brief))["text_ready"])

    def test_material_weak_answer_consumes_existing_review_failure_path(self):
        card = case()
        review = independent_review(ARTICLE, card["reader_brief"])
        review["checks"][5].update(judgment="needs_revision", reason="Only repeats a methodological caveat; does not explain the consequence.")
        result = editorial_review.review_article(ARTICLE, {"reader_brief": card["reader_brief"]},
            send=lambda _: json.dumps({"decision": "publish", "reader_promise_review": review}))
        self.assertEqual(result["decision"], "repair")
        self.assertIn("PROMISE_NOT_DELIVERED", [i["code"] for i in result["hard_issues"]])

    def test_research_reference_identity_survives_citation_renumbering(self):
        source = {"id": 9, "url": "https://example.test/results", "title": "ACME results",
                  "excerpt": "ACME reported slower sales on August 20.", "event_date": "2026-08-20", "fresh": True}
        a = rp.build_reader_brief(case(), {"sources": [source]})
        b = rp.build_reader_brief(case(), {"sources": [dict(source, id=1)]})
        self.assertEqual(a["evidence_index"], b["evidence_index"])
        with_two_passages = rp.build_reader_brief(case(), {"sources": [source,
            dict(source, id=10, excerpt="ACME lowered its outlook in the same report.")]})
        self.assertEqual(sum(k.startswith("research:") for k in with_two_passages["evidence_index"]), 2)

    def test_news_hero_optional_for_text_draft_but_never_visually_ready(self):
        news = {"event": {"event_id": "jobs", "event_time": "2026-09-04T12:30:00Z", "claim_ids": ["jobs"]},
                "claims": [{"id": "jobs", "text": "Hiring increased.", "source_ids": ["bls"]}],
                "sources": [{"id": "bls", "url": "https://example.test/jobs", "excerpt": "Hiring increased; gains were concentrated."}]}
        brief = rp.build_reader_brief(research=news, article_type="news")
        self.assertFalse(brief["hold_reasons"])
        self.assertIn("news:/event", brief["why_now_evidence_refs"])
        result = rp.review_hero("<article><h1>Hiring increased</h1></article>")
        self.assertFalse(result["ready"])
        self.assertEqual(result["status"], "pending")


def png(path, width, height):
    """Small valid image fixtures, not generated publication assets."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
    raw += chunk(b"IDAT", zlib.compress((b"\0" + b"\xff" * width) * height)) + chunk(b"IEND", b"")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


class VisualReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.asset_hash = png(self.root/"asset.png", 1200, 600)
        self.html = '<article><h1>ACME context</h1><figure class="hero"><img src="https://example.test/asset.png"><figcaption>Illustration of a manufacturing line.</figcaption></figure></article>'
        self.asset = {"path": str(self.root/"asset.png"), "url": "https://example.test/asset.png", "provenance": {"kind": "unknown"}}
        self.review = {"asset_sha256": self.asset_hash, "article_sha256": rp.fingerprint(self.html),
                       "reviewer_id": "test-editor", "method": "human_visual", "reviewed_at": "2026-09-06T16:00:00Z",
                       "checks": [{"check": name, "verdict": "pass", "observation": detail} for name, detail in [
                           ("lettering", "No text or lettering appears in the inspected fixture."),
                           ("identity", "The generic image makes no visible company identity claim."),
                           ("provenance", "The visible caption identifies the scene as an illustration."),
                           ("crop", "The relevant scene remains visible in desktop and mobile captures."),
                           ("factual_implication", "The illustration adds context and claims no actual event or price path.")]],
                       "views": []}
        for kind, width, height in [("mobile", 390, 844), ("desktop", 1360, 960)]:
            path = self.root/(kind+".png")
            digest = png(path, width, height)
            self.review["views"].append({"kind": kind, "path": str(path), "sha256": digest})
    def tearDown(self):
        self.temp.cleanup()
    def check(self):
        return rp.review_hero(self.html, self.asset, self.review, trusted_reviewers={"test-editor"})
    def test_full_observed_review_bound_to_bytes_and_article(self):
        self.assertTrue(self.check()["ready"])
    def test_boolean_self_attestation_never_approves(self):
        self.review = {"passed": True, "asset_sha256": self.asset_hash}
        self.assertFalse(self.check()["ready"])
    def test_untrusted_writer_identity_does_not_approve(self):
        self.review["reviewer_id"] = "writer"
        self.assertIn("trusted_visual_reviewer_required", self.check()["pending"])
    def test_changed_image_title_or_crop_invalidates_review(self):
        with self.subTest(change="image"):
            png(self.root/"asset.png", 1200, 601)
            self.assertIn("hero_review_hash_missing_or_stale", self.check()["pending"])
        png(self.root/"asset.png", 1200, 600)
        with self.subTest(change="title"):
            self.html = self.html.replace("ACME context", "ACME announces a new factory")
            self.assertIn("visual_review_article_binding_missing_or_stale", self.check()["pending"])
        with self.subTest(change="crop"):
            (self.root/"mobile.png").write_bytes(b"not a screenshot")
            self.assertIn("actual_mobile_and_desktop_crop_evidence_required", self.check()["pending"])
    def test_observed_garbled_lettering_is_a_hold(self):
        self.review["checks"][0].update(verdict="fail", observation="Entrance banners contain visibly nonsensical lettering.")
        self.assertEqual(self.check()["status"], "hold")
    def test_unknown_provenance_requires_visible_illustration_label(self):
        self.html = self.html.replace("Illustration of a manufacturing line.", "ACME manufacturing plant.")
        self.review["article_sha256"] = rp.fingerprint(self.html)
        self.assertIn("unknown_or_illustrative_provenance_needs_visible_hero_label", self.check()["issues"])
    def test_hero_review_cannot_approve_an_unrelated_body_image(self):
        self.html = self.html.replace('class="hero"', 'class="chart"')
        self.review["article_sha256"] = rp.fingerprint(self.html)
        self.assertIn("reviewed_hero_not_in_rendered_article", self.check()["issues"])
    def test_documentary_manifest_without_source_does_not_pass(self):
        self.asset["provenance"] = {"kind": "documentary"}
        self.assertIn("documentary_provenance_unverified", self.check()["pending"])


if __name__ == "__main__":
    unittest.main()
