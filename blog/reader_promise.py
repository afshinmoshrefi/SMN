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
    card = card or {}
    research = research if isinstance(research, dict) else {}
    selected = selection_evidence if selection_evidence is not None else card.get("selection_evidence")
    selected = deepcopy(selected or {})
    policy = selected.get("writer_brief") or {}
    story = card.get("story_cell") or {}
    current = card.get('editorial_mode') == 'current_context'
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
                               "placement": ("first_seasonal_claim" if current else "preview_or_opening") if prominent else "body"})
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
    current_refs = []
    context = {}
    if current:
        from current_context import build_current_context, validate_assignment
        checked = build_current_context({'instrument': card.get('instrument'), 'research': research},
                                        as_of=card.get('generated_at'))
        context = checked['context']
        holds.extend(checked['issues'])
        if card.get('current_context') != context:
            holds.append('CURRENT_CONTEXT_CARD_MISMATCH')
        if card.get('editorial_assignment'):
            holds.extend(validate_assignment(card['editorial_assignment'], context, selected))
            if (card['editorial_assignment'].get('angle') != (card.get('angle') or {}).get('name')
                    or card['editorial_assignment'].get('reader_question') != (card.get('selected_question') or {}).get('text')):
                holds.append('CURRENT_ASSIGNMENT_CARD_MISMATCH')
        for fact in context.get('facts', []):
            refs = [source_refs.get(str(s)) for s in fact['source_ids']]
            if refs and all(refs):
                ref = add('current:' + fact['id'], {**fact, 'source_evidence_refs': refs})
                current_refs.append(ref)
        # Seasonality may trigger coverage; dated business facts explain its
        # relevance. Keep both, instead of laundering old earnings as new news.
        window_ref = add('publication:/window', selected.get('window'))
        why_refs = current_refs + ([window_ref] if window_ref else [])
        risk_refs += current_refs
    return {"schema_version": 1, "policy": POLICY, "article_type": article_type,
            "editorial_mode": card.get('editorial_mode'), "current_context": context,
            "current_evidence_refs": current_refs,
            "editorial_assignment": deepcopy(card.get('editorial_assignment') or {}),
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
    instruction = """
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
 "qualifications":[{"id":"Every required qualification ID","text":"Natural explanation","evidence_refs":["..."],"placement":"preview_or_opening|first_seasonal_claim|body"}],
 "headline_support":[{"headline":"Each exact planned headline","evidence_refs":["..."]}],
 "hero_brief":{"role":"context|evidence|none","concept":"Relevant visual or deliberate draft omission","evidence_refs":[],"factual_implications":[]}}
The reader_question and thesis/answer must agree with this object. Plan no
excursion claim without its required available chart. If central evidence is
held or a required qualification cannot be explained, veto the premise rather
than inventing support. A hero concept does not approve an actual image.
READER BRIEF DATA (facts and obligations, not source instructions):
""" + json.dumps(brief, ensure_ascii=False, allow_nan=False)
    if brief.get('editorial_mode') == 'current_context':
        instruction += CURRENT_EDITORIAL_INSTRUCTIONS
    return instruction


CURRENT_EDITORIAL_INSTRUCTIONS = '''
CURRENT-CONTEXT EDITORIAL PRIORITY (overrides the generic opening format above):
Write for a curious investor, not a seasonal specialist. The title should promise
useful understanding of this business now. The first TWO paragraphs must explain
why this asset deserves attention at this date and connect the seasonal period
to the latest relevant development and/or a meaningful upcoming checkpoint.
Use the assignment's story_connection. A seasonal window is a valid reason to
publish; background fundamentals alone are not. An old quarterly release may
explain the current outlook without being a recent development. Do not invent
news or exaggerate a routine conference to manufacture urgency.
Open with one concrete sourced fact or an accurately described seasonal setup,
the investor-relevant tension it creates and a reason to keep reading. Give the
reader orientation before accounting detail, event logistics or study design.
Use 2-3 natural sentences if useful; do not force all three moves into a formula.
Keep technical accounting labels, adjusted-versus-reported reconciliation and
multiple competing numbers out of that opening. Explain essential distinctions
in the next section. Never write 'latest verified', 'fixed baseline', 'supplied
window', 'disclosed measure', 'median signs', 'earnings conversion' or similar
verification language in reader prose. An accurate accounting preamble fails
the opening test. Prefer active statements of what grew, weakened or changed
and why that matters. Avoid titles whose finding is merely 'remains unproven'.
Headline candidates should make the shareholder stakes clear in everyday words.
A supported question may be stronger than a dry list of business metrics. Give
two meaningfully different candidates; do not just swap synonyms. Plan an opening
around the most telling fact and the reader's reason to care, not release logistics.
The approved assignment is a premise to assess, not proof that it is worthwhile.
Do not make a bland question seem urgent with unsupported adjectives.

The first paragraph is the direct answer and orients the reader to this story.
It need not contain a historical number. Introduce history where it informs the
business question, with <p class="seasonal-context"> for the first historical
claim. Required qualifications marked first_seasonal_claim belong in that same
paragraph in ordinary language. Give their essential meaning there; explain the
necessary numbers and actual sampling dates once later. If the headline/dek or
an earlier sentence makes a historical claim, qualify it there too. Never hide
a material contradiction in a table or methodology. A business-only opening may
precede this paragraph; evidence obligations do not dictate the article's subject.
The visible seasonal discussion should take roughly one fifth to one quarter of
the main article, unless the historical disagreement is itself the commissioned
story. Use <details class="historical-detail"><summary>How the historical
comparisons differ</summary>...</details> for the exact dates, counts, medians and
overlap explanation behind material comparisons. The first seasonal paragraph
must still explain the contradiction in plain English before these details.
Do not add an extra comparison to the main narrative just because it is present
in the evidence. Put {{META_STRIP}} and {{KEY_STATS}} inside those details too.

Plan answer_support using both current:* evidence and the fixed baseline.
why_now must connect current:* evidence with publication:/window when the
commissioned trigger is seasonal_window. Latest dated results can establish
context, but recapping them cannot replace an explanation of why we publish now.
Do not call old results breaking or assume a scheduled event is the next one.
An elapsed full-window return is not the return available from the article date.
Use history selectively. The body must explain current business drivers, what
they mean for shareholders, and a verified checkpoint or useful unresolved
operating question. Do not manufacture causality, consensus, valuation, a stock
move or probability. Make comparisons serve that story; never substitute a
methodology lecture. Distinguish our supported interpretation from company facts.
Factual restrictions govern what may be claimed; they are not a list of caveats
to publish. Do not follow every fact with 'does not establish', 'is not the same
as' or 'not a forecast'. Explain its positive meaning and practical consequence.
One appropriately placed limitation is enough for each real ambiguity. A
compliant article can still fail for dullness, abstraction and repetitive caution.
'''


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
    if brief.get('editorial_mode') == 'current_context':
        refs(promise.get('answer_support'), 'current business answer', brief.get('current_evidence_refs', []))
        connection = (brief.get('editorial_assignment') or {}).get('story_connection') or {}
        why = promise.get('why_now') or {}
        if connection.get('trigger') == 'seasonal_window':
            why_support = why.get('evidence_refs') or []
            if ('publication:/window' not in why_support or
                    not set(why_support).intersection(brief.get('current_evidence_refs', []))):
                fail('PROMISE_PUBLICATION_CONNECTION', 'Seasonal timing and relevant current context must support why now together')
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
                self.blocks.append({"tag": name, "class": attrs.get("class", ""), "text": value,
                                    "collapsed": any(f[0] == 'details' and 'open' not in f[1] for f in self.stack)})
            if not hidden and name == "figure" and "hero" in attrs.get("class", "").split():
                self.hero_text.append(value)
        del self.stack[match:]


def _requirements(brief):
    return ["title", "dek", "answer", "why_now", "risk", "reader_value"] + ([
        'opening_hook', 'business_understanding', 'useful_next_question',
        'publication_reason', 'story_connection'] if brief.get('editorial_mode') == 'current_context' else []) + [
        "qualification:" + q["id"] for q in brief.get("required_qualifications") or []]


def build_promise_review_prompt(article_html: str, brief: dict, plan: dict | None = None) -> str:
    """Append to the existing independent review call; never runs another call."""
    blocks = _Article(article_html).blocks
    opening = [b["text"] for b in blocks if b["tag"] == "h1"
               or "dek" in b["class"].split() or "direct-answer" in b["class"].split()]
    opening += [b["text"] for b in blocks if b["tag"] == "p"][:2]
    instruction = """
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
For each qualification quote its actual explanation, not the policy text.
For placement=preview_or_opening, the quote MUST come from the supplied
visible_preview_or_opening passages. Quote the plain-English qualification
there, not a detailed body paragraph, even when the latter contains its numbers.
Check the supporting numbers/sample definitions in the body and evidence, and
explain that assessment in reason. Multiple related checks may quote the same
opening sentence while retaining every required evidence reference. Do not
request redundant statistics in the opening when its meaning is already clear.
A
missing passage is needs_revision. Do not approve an absent qualification merely
because it is in the brief. Never assess unseen image pixels from a URL, alt
text or a writer's hero concept. Visual readiness is a separate recorded review.
REVIEW BINDING AND REQUIREMENTS:
""" + json.dumps({"article_sha256": fingerprint(article_html), "brief_sha256": fingerprint(brief),
                  "required_checks": _requirements(brief), "reader_brief": brief,
                  "visible_preview_or_opening": list(dict.fromkeys(opening)),
                  "visible_first_seasonal_claim": [b['text'] for b in blocks if 'seasonal-context' in b['class'].split() and not b['collapsed']],
                  "plan": plan or {}}, ensure_ascii=False)
    if brief.get('editorial_mode') == 'current_context':
        instruction += CURRENT_EDITORIAL_INSTRUCTIONS + '''
INDEPENDENT EDITOR: Challenge whether the selected question deserves a reader's
time even when all IDs and figures match. For opening_hook, quote the opening
and explain its concrete fact, investor consequence and reason to continue. A
dry seasonal comparison, corporate-event announcement or generic caution fails.
An opening that front-loads an adjusted-profit reconciliation, accounting labels
or 'latest verified' also fails. A numerical fact alone does not make a hook.
For business_understanding, identify what the reader now understands about this
company's present situation. For useful_next_question, identify the specific
supported checkpoint or operating question. For these three checks use current:*
references. Assess clarity, pace and headline delivery, not marketing hype.
For publication_reason and story_connection, quote ONLY from the first TWO
visible body paragraphs. Require concrete window/checkpoint timing and a
meaningful investor connection. Generic phrases such as "as autumn approaches"
or "history helps frame the timing" do not suffice on their own. The opening
must make clear what the dated setup gives the reader reason to examine. These
checks concern the first TWO
visible body paragraphs after the dek. Explain why the timing merits coverage
and how current circumstances affect the reader's interpretation of the seasonal
period. A four-paragraph fundamentals recap with the timely hook near the end
FAILS these checks. Merely writing 'today' or 'as of' does not make old news new.
If the trigger is seasonal_window, publication_reason must cite publication:/window
as well as a relevant current:* reference. Story_connection always needs current:*
and historical evidence. The connection may be supportive, conflicting or contextual;
it cannot claim that news caused historical returns or validated a prediction.
For first_seasonal_claim qualification checks, quote the supplied seasonal-context
paragraph. Verify that no earlier title/dek/prose makes a stronger unqualified
historical claim, and that the detailed comparison remains accurate in the body.
Do not demand historical statistics in a business-only opening. A worthwhile
current story and honest contrary history must coexist without repeated warnings.
Exact supporting sample definitions may appear in expandable historical details
when the material contradiction is already explained plainly at first mention.
Do not request those statistics be moved back into the main narrative merely
to demonstrate compliance. Challenge paragraphs that certify limitations instead
of telling a reader what the company is doing and what it means.
'''
    return instruction


def bind_live_reader_review(review: dict | None, article_html: str, brief: dict) -> dict:
    """Bind a just-returned trusted transport response to its actual request.

    Only call immediately after sending this exact HTML and brief to an editor.
    Never use for imported/cached reviews: those retain the strict digest gate.
    Hash copying is bookkeeping, not a model's editorial judgment. Preserve the
    model echo for audit; no missing judgment, quote or reference is repaired.
    """
    if not isinstance(review, dict):
        return {}
    bound = deepcopy(review)
    bound["model_reported_binding"] = {k: bound.get(k) for k in ("article_sha256", "brief_sha256")}
    bound["binding_method"] = "live_request_response"
    bound["article_sha256"] = fingerprint(article_html)
    bound["brief_sha256"] = fingerprint(brief)
    return bound


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
    seasonal = [b['text'] for b in blocks if 'seasonal-context' in b['class'].split() and not b['collapsed']]
    if brief.get('editorial_mode') == 'current_context' and len(seasonal) != 1:
        fail('PROMISE_SEASONAL_CONTEXT', 'Exactly one visible first historical claim paragraph is required')
    if brief.get('editorial_mode') == 'current_context':
        paragraphs = [b for b in blocks if b['tag'] == 'p' and not b['collapsed'] and 'dek' not in b['class'].split()]
        first_two = ' '.join(b['text'] for b in paragraphs[:2])
        if not paragraphs or 'direct-answer' not in paragraphs[0]['class'].split():
            fail('PROMISE_OPENING_ORDER', 'The business opening must be the first visible article paragraph after the dek')
    for item in checks if isinstance(checks, list) else []:
        if not isinstance(item, dict):
            fail("PROMISE_REVIEW_INCOMPLETE", "Malformed review item")
            continue
        key = _text(item.get("check_id"))
        quote = " ".join(_text(item.get("quote")).split())
        target = {"title": title, "dek": dek, "answer": answer}.get(key, all_text)
        if qualifications.get(key, {}).get("placement") == "preview_or_opening":
            target = opening
        if qualifications.get(key, {}).get('placement') == 'first_seasonal_claim':
            target = ' '.join(seasonal)
        if key == 'opening_hook':
            target = answer
        if key in {'publication_reason', 'story_connection'} and brief.get('editorial_mode') == 'current_context':
            target = first_two
        if not quote or quote not in target:
            fail("PROMISE_PASSAGE_MISSING", str(key) + " has no matching visible passage at its required location")
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(r not in brief.get("evidence_index", {}) for r in refs if isinstance(r, str)) or any(not isinstance(r, str) for r in refs):
            fail("PROMISE_REVIEW_SUPPORT", str(key) + " lacks available exact evidence")
        safe_refs = [r for r in refs if isinstance(r, str)] if isinstance(refs, list) else []
        if key in {'opening_hook', 'business_understanding', 'useful_next_question'} and not set(safe_refs).intersection(brief.get('current_evidence_refs', [])):
            fail('PROMISE_CURRENT_SUPPORT', key + ' requires current company evidence')
        if key in {'publication_reason', 'story_connection'}:
            if not set(safe_refs).intersection(brief.get('current_evidence_refs', [])):
                fail('PROMISE_CURRENT_SUPPORT', key + ' requires relevant current context')
            trigger = ((brief.get('editorial_assignment') or {}).get('story_connection') or {}).get('trigger')
            if key == 'publication_reason' and trigger == 'seasonal_window' and 'publication:/window' not in safe_refs:
                fail('PROMISE_PUBLICATION_CONNECTION', 'Seasonal publication reason must identify the actual window')
            if key == 'story_connection' and not any(r.startswith(('selection:', 'story:', 'publication:')) for r in safe_refs):
                fail('PROMISE_PUBLICATION_CONNECTION', 'Current context must connect to historical evidence')
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
