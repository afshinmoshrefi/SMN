"""Opt-in private editorial contracts; no calls, publication or access changes.

Evidence references describe supplied facts, not a semantic proof. A separate
editor must explain support using passages from the final article. Image reviews
are supplied by a trusted reviewer and bound to local asset/screenshot bytes.
This module never turns a writer's ``passed: true`` into visual approval.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import struct
from urllib.parse import urlparse


POLICY = "private_v2"
_MATERIAL = {"era_sensitive", "genuine_contrast", "mixed", "insufficient", "incomparable"}


def fingerprint(value) -> str:
    raw = value if isinstance(value, str) else json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pointer(value, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("Evidence reference must be a JSON pointer")
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def build_reader_brief(card: dict | None = None, research: dict | None = None,
                       selection_evidence: dict | None = None, *,
                       article_type: str = "seasonal") -> dict:
    """Preserve the selected identity and all policy obligations, without ranking.

    ``selection_evidence.writer_brief`` is emitted by cohort_policy. Its JSON
    pointers are namespaced as ``selection:/...``. Research references use URL
    hashes so rendered citation renumbering cannot change their identity. A
    news evidence packet may be passed as ``research`` with article_type='news'.
    """
    card, research = card or {}, research or {}
    selected = selection_evidence if selection_evidence is not None else card.get("selection_evidence")
    selected = deepcopy(selected or {})
    policy = selected.get("writer_brief") or {}
    story = card.get("story_cell") or {}
    index, holds, qualifications = {}, list(policy.get("hold_reasons") or []), []

    def add(ref, value):
        if value is not None and value != {} and value != [] and value != "":
            index[ref] = deepcopy(value)
            return ref
        return None

    for pointer in policy.get("permitted_summary_refs") or []:
        try:
            if not add("selection:" + pointer, _pointer(selected, pointer)):
                holds.append("empty_selection_evidence:" + pointer)
        except (KeyError, IndexError, TypeError, ValueError):
            holds.append("invalid_selection_evidence:" + str(pointer))
    for item in policy.get("required_qualifications") or []:
        if not isinstance(item, dict) or not _text(item.get("id")) or not _text(item.get("text")):
            holds.append("malformed_required_qualification")
            continue
        refs = []
        for pointer in item.get("evidence_refs") or []:
            try:
                ref = add("selection:" + pointer, _pointer(selected, pointer))
                if ref:
                    refs.append(ref)
            except (KeyError, IndexError, TypeError, ValueError):
                holds.append("invalid_qualification_evidence:" + str(pointer))
        if not refs:
            holds.append("unsupported_required_qualification:" + item["id"])
        prominent = item["id"] in {"cohort.era_sensitive", "cohort.genuine_contrast", "cohort.mixed",
                                   "cohort.annual_recency_5", "cohort.annual_recency_10",
                                   "baseline.no_strict_majority"}
        qualifications.append({"id": item["id"], "text": item["text"],
                               "evidence_refs": refs,
                               "placement": "preview_or_opening" if prominent else "body"})
    classification = policy.get("classification") or selected.get("classification")
    if article_type == "seasonal":
        if classification not in {"consistent", "era_sensitive", "genuine_contrast", "mixed", "insufficient_context", "insufficient", "incomparable"}:
            holds.append("selection_classification_missing_or_unknown")
        if classification in {"insufficient", "incomparable"}:
            holds.append("historical_premise_" + classification)
        if classification in _MATERIAL and not qualifications:
            holds.append("material_comparison_has_no_qualification")
        if "selection:/baseline/summary" not in index:
            holds.append("baseline_summary_missing")
    if len({q["id"] for q in qualifications}) != len(qualifications):
        holds.append("duplicate_required_qualification")

    for key in ("window", "cohort", "returns", "risk", "giveback", "sensitivity"):
        add("story:/evidence/" + key, (story.get("evidence") or {}).get(key))
    if story.get("per_year"):
        add("story:/per_year", story["per_year"])
    if story.get("worst_year") is not None and story.get("worst_net") is not None:
        add("story:/worst_outcome", {k: story.get(k) for k in ("worst_year", "worst_net", "n")})

    source_refs = {}
    for source in research.get("sources") or []:
        if not isinstance(source, dict) or not _text(source.get("url")):
            continue
        excerpt = next((_text(source.get(k)) for k in ("excerpt", "content", "snippet", "raw_content")
                        if _text(source.get(k))), "")
        if not excerpt:
            continue
        ref = "research:" + fingerprint({"url": source["url"], "excerpt": excerpt})[:20]
        add(ref, {**{k: source[k] for k in ("url", "title", "subject", "symbol", "date",
                                          "event_date", "published_at", "fresh", "role") if k in source},
                  "excerpt": excerpt})
        source_refs[str(source.get("id"))] = ref
    for claim in research.get("claims") or []:
        if not isinstance(claim, dict) or not _text(claim.get("text")):
            continue
        refs = [source_refs.get(str(r)) for r in claim.get("source_ids") or []]
        if refs and all(refs):
            add("news_claim:" + str(claim.get("id")), {"text": claim["text"],
                "source_evidence_refs": refs, "event_time": claim.get("event_time")})
    event = research.get("event") or {}
    event_refs = ["news_claim:" + str(r) for r in event.get("claim_ids") or []
                  if "news_claim:" + str(r) in index]
    if event and event_refs:
        add("news:/event", {**{k: event[k] for k in ("event_id", "development_id", "event_time",
                                                    "event_date", "headline") if k in event},
                            "claim_refs": event_refs})

    risk_refs = [r for r in index if r in {"story:/worst_outcome", "story:/evidence/risk",
                                         "story:/evidence/sensitivity", "selection:/baseline/summary"}]
    if article_type == "news":
        risk_refs = [r for r in index if r.startswith("news_claim:") or r.startswith("research:")]
        if not event_refs:
            holds.append("supported_news_event_missing")
    why_refs = [r for r in index if r in {"story:/evidence/window", "news:/event"}
                or (r.startswith("research:") and index[r].get("fresh") is True
                    and index[r].get("event_date"))]
    return {"schema_version": 1, "policy": POLICY, "article_type": article_type,
            "story_identity": {"resource": card.get("resource_id", card.get("resource")),
                               "symbol": card.get("symbol", story.get("symbol")),
                               "anchor_date": story.get("anchor_date", card.get("anchor_date")),
                               "days": story.get("days"), "years": story.get("years")},
            "classification": classification, "proposed_reader_question": policy.get("proposed_reader_question"),
            "required_qualifications": qualifications, "evidence_index": index,
            "risk_evidence_refs": risk_refs, "why_now_evidence_refs": why_refs,
            "forbidden_inferences": deepcopy(policy.get("forbidden_inferences") or []),
            "hold_reasons": list(dict.fromkeys(holds))}


def build_selected_reader_brief(card: dict, research: dict | None = None) -> dict:
    """Rebuild the private seasonal brief from evidence and a selected question.

    Ignore any caller-supplied reader_brief. The selected question is an upstream
    editorial choice; its ID binds later planning/review, while the existing
    independent editor still judges whether the evidence answers its meaning.
    """
    brief = build_reader_brief(card, research)
    selected = card.get("selected_question")
    selected = selected if isinstance(selected, dict) else {}
    question_id, question = _text(selected.get("question_id")), _text(selected.get("text"))
    if not question_id or not question:
        brief["hold_reasons"].append("selected_reader_question_missing")
        return brief
    brief.update(selected_question=question, selected_question_id=question_id,
                 proposed_reader_question=question)
    return brief


def promise_plan_instructions(brief: dict | None) -> str:
    if not brief or brief.get("policy") != POLICY:
        return ""
    return """
PRIVATE READER-PROMISE POLICY (overrides angle framing, not factual constraints):
Use the unchanged main story cell. The selection evidence has already fixed the
window and baseline. Do not replace it with the strongest lookback or discard a
material contradiction. Verified selection summary medians/counts are licensed
for prose comparisons with their actual dates and n; they are not chart data.
When the brief has selected_question and selected_question_id, answer that
already selected question; do not substitute a different question because its
historical pattern is stronger. Put its exact ID in reader_promise.selected_question_id.
Otherwise choose one useful reader question. Give a supported answer, not a
catalogue of warnings. Explain why the question matters using a dated event or the research
window; a calendar reference need not imply breaking news. Include one
consequential supported risk. Explain required qualifications once, naturally;
those marked preview_or_opening must qualify the title/dek or opening answer.
For every qualification, retain ALL of its supplied evidence_refs in the plan;
do not shorten that internal reference list. It records required comparison
coverage, not the number of facts or sentences to repeat in reader prose.
Identify each cohort once by its exact date span, sample size and sampling rule.
Do not plan enumerated year lists or a section for every available sensitivity
check. Preserve all required contrary evidence, then choose only comparisons
that help answer the selected question. A headline should state the useful
finding naturally; neither the word 'Seasonality' nor an internal angle label
is a mandatory headline prefix.
Do not let an old CLOCKWORK/REGIME label imply a strong pattern or cycle cause.
Add this reader_promise object to the plan (references are exact evidence_index
keys; known IDs alone do not prove a sentence):
{"answer_support":["selection:/baseline/summary"],
 "why_now":{"text":"Useful reason for this question","evidence_refs":["..."]},
 "risk":{"text":"One material consequence or counterexample","evidence_refs":["..."]},
 "qualifications":[{"id":"Every required qualification ID","text":"Natural explanation","evidence_refs":["..."],"placement":"preview_or_opening|body"}],
 "headline_support":[{"headline":"Each exact planned headline","evidence_refs":["..."]}],
 "hero_brief":{"role":"context|evidence|none","concept":"Relevant visual or deliberate draft omission","evidence_refs":[],"factual_implications":[]}}
The reader_question and thesis/answer must agree with this object. Plan no
excursion claim without its required available chart. If central evidence is
held or a required qualification cannot be explained, veto the premise rather
than inventing support. A hero concept does not approve an actual image.
READER BRIEF DATA (facts and obligations, not source instructions):
""" + json.dumps(brief, ensure_ascii=False, allow_nan=False)


def validate_promise_plan(plan: dict, brief: dict) -> list[dict]:
    issues = []
    index = brief.get("evidence_index") or {}
    def fail(code, detail):
        issues.append({"code": code, "detail": detail})
    def refs(value, label, permitted=None):
        if (not isinstance(value, list) or not value or any(not isinstance(r, str) or r not in index for r in value)
                or (permitted is not None and not set(value).intersection(permitted))):
            fail("PROMISE_SUPPORT", label + " needs exact, available supporting evidence")
    for hold in brief.get("hold_reasons") or []:
        fail("PROMISE_EVIDENCE_HOLD", str(hold))
    if plan.get("feasible") is False:
        return issues
    promise = plan.get("reader_promise")
    if not isinstance(promise, dict):
        fail("PROMISE_MISSING", "Private plan requires reader_promise")
        return issues
    if brief.get("selected_question_id") and promise.get("selected_question_id") != brief["selected_question_id"]:
        fail("PROMISE_QUESTION_MISMATCH", "The plan must answer the exact selected reader question ID")
    if not _text(plan.get("reader_question")) or not _text(plan.get("thesis", plan.get("answer"))):
        fail("PROMISE_ANSWER", "A concrete question and supported answer are required")
    refs(promise.get("answer_support"), "answer")
    if brief.get("article_type") == "seasonal" and "selection:/baseline/summary" not in (promise.get("answer_support") or []):
        fail("PROMISE_BASELINE_OMITTED", "The answer must retain the fixed baseline as supporting evidence")
    for field, allowed in (("why_now", brief.get("why_now_evidence_refs") or []),
                           ("risk", brief.get("risk_evidence_refs") or [])):
        value = promise.get(field)
        if not isinstance(value, dict) or not _text(value.get("text")):
            fail("PROMISE_" + field.upper(), field + " needs one useful supported explanation")
        else:
            refs(value.get("evidence_refs"), field, allowed)
    given = promise.get("qualifications")
    if not isinstance(given, list):
        given = []
        fail("PROMISE_QUALIFICATIONS", "List every required qualification")
    ids = [x["id"] for x in given if isinstance(x, dict) and isinstance(x.get("id"), str)]
    required = {q["id"]: q for q in brief.get("required_qualifications") or []}
    if len(ids) != len(set(ids)) or set(ids) != set(required) or len(ids) != len(given):
        fail("PROMISE_QUALIFICATIONS", "Required qualification IDs must appear exactly once")
    for item in given:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in required:
            continue
        q = required[item["id"]]
        if not _text(item.get("text")) or item.get("placement") != q["placement"]:
            fail("PROMISE_QUALIFICATION_PLACEMENT", q["id"] + " must retain its required prominence")
        refs(item.get("evidence_refs"), q["id"])
        supplied = item.get("evidence_refs")
        supplied = [r for r in supplied if isinstance(r, str)] if isinstance(supplied, list) else []
        if not set(q["evidence_refs"]).issubset(supplied):
            fail("PROMISE_QUALIFICATION_SUPPORT", q["id"] + " omitted material comparison evidence")
    headlines = plan.get("headlines") or []
    supports = promise.get("headline_support") or []
    if not isinstance(supports, list) or [s.get("headline") for s in supports if isinstance(s, dict)] != headlines:
        fail("PROMISE_HEADLINES", "Support each exact planned headline in order")
    else:
        for item in supports:
            refs(item.get("evidence_refs"), "headline")
    hero = promise.get("hero_brief")
    if not isinstance(hero, dict) or hero.get("role") not in {"context", "evidence", "none"} or not _text(hero.get("concept")):
        fail("PROMISE_HERO", "Describe a relevant hero or deliberate draft omission")
    else:
        if hero.get("role") == "evidence" or hero.get("factual_implications"):
            refs(hero.get("evidence_refs"), "hero implications")
    return issues


class _Article(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.stack, self.blocks, self.images, self.hero_text = [], [], [], []
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hidden = any(f[2] for f in self.stack) or tag in {"head", "script", "style", "template"} or "hidden" in a or a.get("aria-hidden") == "true" or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", a.get("style", ""), re.I))
        if tag == 'picture':
            a['_sources'] = []
        picture = next((f for f in reversed(self.stack) if f[0] == 'picture'), None)
        if tag == 'source' and picture is not None and not hidden:
            picture[1]['_sources'].append(a)
        if tag == "img" and not hidden:
            self.images.append({**a, '_picture': picture is not None,
                                '_picture_sources': list(picture[1]['_sources']) if picture else [],
                                "_hero": any(f[0] == "figure" and "hero" in f[1].get("class", "").split()
                                                 for f in self.stack)})
        if tag not in {"img", "br", "hr", "meta", "link", "input", "source", "wbr"}:
            self.stack.append([tag, a, hidden, []])
    def handle_data(self, data):
        if self.stack and not self.stack[-1][2]:
            for frame in self.stack:
                if not frame[2]:
                    frame[3].append(data)
    def handle_endtag(self, tag):
        match = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i][0] == tag), None)
        if match is None:
            return
        for name, attrs, hidden, parts in self.stack[match:]:
            # Inline tags do not create spaces: <strong>word</strong>. is word.
            # Preserve source whitespace, then normalize it for quote matching.
            value = " ".join("".join(parts).split())
            if not hidden and name in {"h1", "h2", "p", "li", "figcaption"} and value:
                self.blocks.append({"tag": name, "class": attrs.get("class", ""), "text": value})
            if not hidden and name == "figure" and "hero" in attrs.get("class", "").split():
                self.hero_text.append(value)
        del self.stack[match:]


def _requirements(brief):
    return ["title", "dek", "answer", "why_now", "risk", "reader_value"] + [
        "qualification:" + q["id"] for q in brief.get("required_qualifications") or []]


def build_promise_review_prompt(article_html: str, brief: dict, plan: dict | None = None) -> str:
    """Append to the existing independent review call; never runs another call."""
    return """
PRIVATE READER-PROMISE REVIEW: Add a reader_promise_review object to your JSON.
The final title, dek and article must deliver one useful answer to the reader's
question. An accurate but generic caution is not enough. Check every requirement
below against the exact evidence, not just recognized reference IDs. One
consequential risk is useful; repeated qualifications without new understanding
are an editorial defect. Material contrast must qualify the preview or opening
when required. Source/cycle references have separate namespaces and remain
stable even when the displayed citation numbers change.
If selected_question is present, assess that question's actual meaning, not a
replacement chosen by the writer. A matching ID alone cannot establish this;
the answer and reader_value checks must explain how the visible answer resolves
the selected question. Mark a change of question needs_revision.
Return reader_promise_review with article_sha256 and brief_sha256 exactly as
given, expected_question, delivered_answer, and checks. When the brief has a
selected_question_id, also return expected_question_id with that exact ID.
Every required check
needs {check_id, judgment: supported|needs_revision|unassessable,
quote: exact visible article passage, evidence_refs: [evidence_index keys],
reason: specific support or mismatch}. For title/dek quote that element; for
answer quote the direct answer; why_now/risk/reader_value quote useful prose.
For each qualification quote its actual explanation, not the policy text. A
missing passage is needs_revision. Do not approve an absent qualification merely
because it is in the brief. Never assess unseen image pixels from a URL, alt
text or a writer's hero concept. Visual readiness is a separate recorded review.
REVIEW BINDING AND REQUIREMENTS:
""" + json.dumps({"article_sha256": fingerprint(article_html), "brief_sha256": fingerprint(brief),
                  "required_checks": _requirements(brief), "reader_brief": brief,
                  "plan": plan or {}}, ensure_ascii=False)


def review_reader_promise(article_html: str, brief: dict, *, plan: dict | None = None,
                          review: dict | None = None, hero_asset: dict | None = None,
                          hero_review: dict | None = None, trusted_reviewers=()) -> dict:
    """Check support coverage/binding; semantic judgments remain editor evidence.

    Text can be ready while images are pending for a private draft. ``ready``
    requires both. No result from this helper triggers publication.
    """
    issues = validate_promise_plan(plan, brief) if plan is not None else [
        {"code": "PROMISE_EVIDENCE_HOLD", "detail": str(x)} for x in brief.get("hold_reasons") or []]
    parsed = _Article(article_html)
    review = review if isinstance(review, dict) else {}
    def fail(code, detail):
        issues.append({"code": code, "detail": detail})
    if review.get("article_sha256") != fingerprint(article_html) or review.get("brief_sha256") != fingerprint(brief):
        fail("PROMISE_REVIEW_PENDING", "Independent review is missing or belongs to different article/evidence bytes")
    if not _text(review.get("expected_question")) or not _text(review.get("delivered_answer")):
        fail("PROMISE_REVIEW_INCOMPLETE", "Reviewer must identify the expected question and delivered answer")
    if brief.get("selected_question_id") and review.get("expected_question_id") != brief["selected_question_id"]:
        fail("PROMISE_QUESTION_MISMATCH", "Reviewer must assess the exact selected reader question ID")
    checks = review.get("checks") or []
    ids = [c["check_id"] for c in checks if isinstance(c, dict) and isinstance(c.get("check_id"), str)] if isinstance(checks, list) else []
    required = _requirements(brief)
    if len(ids) != len(set(ids)) or set(ids) != set(required):
        fail("PROMISE_REVIEW_INCOMPLETE", "Every required check must be reviewed exactly once")
    blocks = parsed.blocks
    all_text = " ".join(b["text"] for b in blocks)
    title = " ".join(b["text"] for b in blocks if b["tag"] == "h1")
    dek = " ".join(b["text"] for b in blocks if "dek" in b["class"].split())
    answer = " ".join(b["text"] for b in blocks if "direct-answer" in b["class"].split())
    opening = " ".join([title, dek, answer] + [b["text"] for b in blocks if b["tag"] == "p"][:2])
    if not answer and brief.get("article_type") == "news":
        answer = dek + " " + " ".join(b["text"] for b in blocks if b["tag"] == "p")
    qualifications = {"qualification:"+q["id"]: q for q in brief.get("required_qualifications") or []}
    for item in checks if isinstance(checks, list) else []:
        if not isinstance(item, dict):
            fail("PROMISE_REVIEW_INCOMPLETE", "Malformed review item")
            continue
        key = _text(item.get("check_id"))
        quote = " ".join(_text(item.get("quote")).split())
        target = {"title": title, "dek": dek, "answer": answer}.get(key, all_text)
        if qualifications.get(key, {}).get("placement") == "preview_or_opening":
            target = opening
        if not quote or quote not in target:
            fail("PROMISE_PASSAGE_MISSING", str(key) + " has no matching visible passage at its required location")
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(r not in brief.get("evidence_index", {}) for r in refs if isinstance(r, str)) or any(not isinstance(r, str) for r in refs):
            fail("PROMISE_REVIEW_SUPPORT", str(key) + " lacks available exact evidence")
        safe_refs = [r for r in refs if isinstance(r, str)] if isinstance(refs, list) else []
        if key in qualifications and not set(qualifications[key]["evidence_refs"]).issubset(safe_refs):
            fail("PROMISE_REVIEW_SUPPORT", str(key) + " dropped material comparison support")
        if item.get("judgment") != "supported" or not _text(item.get("reason")):
            fail("PROMISE_NOT_DELIVERED", str(key) + ": " + _text(item.get("reason")))
    visual = review_hero(article_html, hero_asset, hero_review, trusted_reviewers=trusted_reviewers)
    return {"text_ready": not issues, "visual_ready": visual["ready"],
            "ready": not issues and visual["ready"], "issues": issues,
            "visual_review": visual, "article_sha256": fingerprint(article_html),
            "brief_sha256": fingerprint(brief)}


def _image_file(path):
    data = Path(path).read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
    elif data.startswith(b"\xff\xd8\xff") or (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        width, height = None, None
    else:
        raise ValueError("not a supported raster image")
    return {"sha256": hashlib.sha256(data).hexdigest(), "width": width, "height": height}


def review_hero(article_html: str, asset: dict | None = None, review: dict | None = None, *,
                trusted_reviewers=()) -> dict:
    """Review actual bytes and recorded observations. No network or vision call.

    Asset: {path, url, provenance:{kind:'documentary|illustration|unknown',
    source_url, credit}}. Review: {asset_sha256, reviewer_id, method:
    'human_visual|vision', reviewed_at, article_sha256,
    checks:[{check, verdict, observation}],
    views:[{kind:'mobile|desktop', path, sha256}]}. All five check names below
    are mandatory. Unknown provenance requires a visible hero illustration label.
    Trusted IDs must come from the private caller, not from the draft/LLM output.

    Responsive assets add mobile:{path,url,sha256}. Their review also requires
    mobile_asset_sha256; every check declares variants:['desktop','mobile'];
    each screenshot view binds asset_url and asset_sha256 to its displayed
    variant. Both actual image files and the picture's exact URLs are checked.
    Existing single-image reviews retain their original contract.
    """
    issues, pending = [], []
    asset = asset if isinstance(asset, dict) else {}
    review = review if isinstance(review, dict) else {}
    if not asset:
        return {"ready": False, "status": "pending", "issues": [],
                "pending": ["hero_absent_or_asset_manifest_missing"]}
    try:
        actual = _image_file(asset.get("path"))
    except (OSError, TypeError, ValueError):
        return {"ready": False, "status": "pending", "issues": [],
                "pending": ["local_hero_bytes_unavailable"]}
    parsed = _Article(article_html)
    heroes = [i for i in parsed.images if i["_hero"]]
    if not asset.get("url") or len(heroes) != 1 or heroes[0].get("src") != asset["url"]:
        issues.append("reviewed_hero_not_in_rendered_article")
    responsive = 'mobile' in asset
    mobile = asset.get('mobile') if isinstance(asset.get('mobile'), dict) else {}
    actual_mobile = None
    if responsive:
        try:
            actual_mobile = _image_file(mobile.get('path'))
        except (OSError, TypeError, ValueError):
            pending.append('local_mobile_hero_bytes_unavailable')
        if asset.get('sha256') != actual['sha256']:
            pending.append('desktop_hero_manifest_hash_missing_or_stale')
        if actual_mobile is not None:
            if not actual_mobile.get('width') or not actual_mobile.get('height'):
                pending.append('mobile_hero_must_be_png')
            if mobile.get('sha256') != actual_mobile['sha256']:
                pending.append('mobile_hero_manifest_hash_missing_or_stale')
            if review.get('mobile_asset_sha256') != actual_mobile['sha256']:
                pending.append('mobile_hero_review_hash_missing_or_stale')
        if asset.get('evidence_sha256') and mobile.get('evidence_sha256') != asset['evidence_sha256']:
            issues.append('responsive_hero_evidence_mismatch')
        sources = heroes[0]['_picture_sources'] if len(heroes) == 1 else []
        if (len(heroes) != 1 or not heroes[0]['_picture'] or len(sources) != 1
                or not mobile.get('url') or sources[0].get('srcset') != mobile['url']
                or not re.fullmatch(r'\(max-width:\s*600px\)', sources[0].get('media') or '')
                or sources[0].get('type') != 'image/png'):
            issues.append('reviewed_mobile_hero_not_in_rendered_article')
    elif any(i['_picture_sources'] for i in heroes):
        issues.append('unreviewed_responsive_hero_source')
    if any(i.get('srcset') for i in heroes):
        issues.append('unreviewed_hero_img_srcset')
    provenance = asset.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    if provenance.get("kind") == "documentary":
        if urlparse(str(provenance.get("source_url", ""))).scheme not in {"https", "http"} or not _text(provenance.get("credit")):
            pending.append("documentary_provenance_unverified")
    elif not any(re.search(r"\billustration\b", t, re.I) for t in parsed.hero_text):
        issues.append("unknown_or_illustrative_provenance_needs_visible_hero_label")
    if not review:
        pending.append("hero_not_reviewed")
    if review.get("asset_sha256") != actual["sha256"]:
        pending.append("hero_review_hash_missing_or_stale")
    if review.get("article_sha256") != fingerprint(article_html):
        pending.append("visual_review_article_binding_missing_or_stale")
    if review.get("reviewer_id") not in set(trusted_reviewers) or review.get("method") not in {"human_visual", "vision"}:
        pending.append("trusted_visual_reviewer_required")
    try:
        if not isinstance(review.get("reviewed_at"), str) or datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("review time needs timezone")
    except ValueError:
        pending.append("visual_review_timestamp_required")
    checks = review.get("checks")
    required = {"lettering", "identity", "provenance", "crop", "factual_implication"}
    names = [c["check"] for c in checks if isinstance(c, dict) and isinstance(c.get("check"), str)] if isinstance(checks, list) else []
    if len(names) != len(set(names)) or set(names) != required:
        pending.append("all_visual_observations_required")
    for check in checks if isinstance(checks, list) else []:
        if not isinstance(check, dict):
            pending.append("malformed_visual_check")
            continue
        if responsive and (not isinstance(check.get('variants'), list)
                           or len(check['variants']) != 2
                           or any(not isinstance(v, str) for v in check['variants'])
                           or set(check['variants']) != {'desktop', 'mobile'}):
            pending.append('both_hero_variants_must_be_reviewed:' + str(check.get('check')))
        if check.get("verdict") == "fail":
            issues.append("visual_" + str(check.get("check")) + ": " + _text(check.get("observation")))
        elif check.get("verdict") != "pass" or len(_text(check.get("observation"))) < 20:
            pending.append("observed_evidence_required:" + str(check.get("check")))
    views = review.get("views") or []
    verified_views = set()
    for view in views if isinstance(views, list) else []:
        if not isinstance(view, dict):
            continue
        try:
            image = _image_file(view.get("path"))
            kind, width = view.get("kind"), image["width"]
            if image["sha256"] != view.get("sha256") or width is None:
                continue
            if responsive:
                variant = mobile if kind == 'mobile' else asset
                rendered_asset = actual_mobile if kind == 'mobile' else actual
                if (rendered_asset is None or view.get('asset_url') != variant.get('url')
                        or view.get('asset_sha256') != rendered_asset['sha256']):
                    continue
            if (kind == "mobile" and 300 <= width <= 600) or (kind == "desktop" and width >= 900):
                verified_views.add(kind)
        except (OSError, TypeError, ValueError):
            continue
    if verified_views != {"mobile", "desktop"}:
        pending.append("actual_mobile_and_desktop_crop_evidence_required")
    return {"ready": not issues and not pending, "status": "hold" if issues else "pending" if pending else "ready",
            "issues": issues, "pending": list(dict.fromkeys(pending)), "asset_sha256": actual["sha256"],
            **({'mobile_asset_sha256': actual_mobile['sha256']} if actual_mobile is not None else {})}
