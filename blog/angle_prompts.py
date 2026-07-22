"""angle_prompts.py — PLAN and WRITE prompt builders (Phase 2).

Two calls replace the old single mega-prompt:

  PLAN  — small JSON: the writer decides the controlling idea FIRST (thesis,
          beats, H2s, chart set, bridge position, derived word budget,
          headline candidates), or vetoes an infeasible angle (once).
  WRITE — prose-only HTML fragment with slot tokens; all furniture is
          server-rendered by angle_chrome.py, so the invariants here are a
          fraction of the old prompt: facts discipline, not layout defense.

Variety is a consequence of the Angle Card, never a target: nothing here
randomizes, rotates, or asks for variety.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

# Word-budget bands per angle (design §3): a clamp, not a quota — the PLAN
# derives its budget from beat count (~150-200 words/beat) inside the band.
ANGLE_BANDS = {
    "COLLISION": (900, 1200),
    "TAILWIND": (800, 1100),
    "CLOCKWORK": (700, 1000),
    "FORK": (900, 1200),
    "REGIME": (900, 1200),
    "QUIET_EDGE": (500, 800),
}

ALLOWED_CHARTS = ("price", "trend", "bars", "bars_mae_mfe", "cumulative")

# ============================================================
# Per-angle guidance — principles and bad examples, never model
# paragraphs to imitate (imitation is how new templates are born).
# ============================================================
ANGLE_GUIDANCE = {
    "COLLISION": """The news and the calendar disagree; the collision IS the story.
- Open cold: both facts inside the first two sentences — what just happened, what history says. No warm-up.
- Cover the news briefly and cite it; the reader already saw the headline. Your value is the half they haven't seen.
- The bridge is the pivot: place it where the piece turns from the news to the history. Make the turn feel like a reveal, not a section change.
- Deep-dive the history: when inside the window the weakness/strength tends to hit, what the worst and best years looked like, what the adverse excursions did even in years that ended well.
- End on reconciliation: what would confirm the news side, what would confirm the calendar side, concretely and by date where possible.
- Do not resolve the tension. You are not predicting the winner; you are showing the reader a fight they didn't know was scheduled.
BAD: "While the news is bullish, seasonality suggests caution." (mush — name the numbers and the dates)
BAD: burying the historical record below three paragraphs of news recap. (the news is the setup, not the story)""",

    "TAILWIND": """News and history point the same way. The job is shape and size, not cheerleading.
- Lead with the news, then let history do the multiplying: how often this window rewarded the same setup, and by how much.
- Spend the middle on the SHAPE of the window: when gains typically cluster, how large the adverse excursions ran even in winning years, which year broke the run and what that year looked like.
- The risk note is load-bearing here, not boilerplate: agreement between news and history is exactly when readers over-commit.
- Close with what would have to happen for this year to underperform the record.
BAD: stacking three superlatives on the same stat. (state it once, plainly; the number is the drama)
BAD: "history says this can only go higher." (history says nothing of the sort and the gate will hold the piece)""",

    "CLOCKWORK": """The streak is the story; news is seasoning.
- Open with the record itself, stated plainly in one sentence. The reader should stop scrolling because of the number, not the adjectives.
- The spine of the piece is the year-by-year texture: the streak's closest call, its biggest year, the year that broke it (if one did) and what that year had in common with today (or didn't).
- Bridge early — right after the cold open. The reader will immediately ask "says who?"; answer it.
- One short section on today's context, then the mechanism hypothesis: why might this repeat (rebalancing, earnings clustering, fiscal calendar), always as hypothesis, never as fact.
- Close short. A streak piece that trails off undoes its own punch.
BAD: opening with price action and saving the streak for paragraph three. (the streak is the lede)
BAD: "this pattern guarantees..." (nothing guarantees; the gate holds predictions)""",

    "FORK": """Two horizons of the same calendar disagree. The disagreement is the insight.
- Name both cells plainly and early: the near window and its record, the far window and its record. Exactly two — a third clock makes noise, not insight.
- The middle explains how both can be true at once: what sits inside the longer window (an early soft stretch inside a longer climb, or the reverse). Use the windows' dates; give the reader the calendar of when the regime typically turns.
- The near-term cell is the actionable one and owns the charts; the far cell lives in prose with exact counts.
- Close with the dates that decide it: when the near window ends, what the far window implies after.
- State counts for the far cell as counts ("closed higher in 16 of 20"), never as percentages — the stats box belongs to the near cell alone.
BAD: presenting the two cells as a contradiction that discredits the data. (both are true; explain the composition)
BAD: hedging every sentence because two answers exist. (the piece is confident ABOUT the disagreement)""",

    "REGIME": """The election-cycle slice is the story.
- One crisp paragraph up front on why grouping by cycle phase is legitimate: same phase, same policy calendar, same institutional rhythms. Assume a smart reader who has never heard of cycle analysis. No civics lecture.
- Then the phase record for this window: counts, median, the outlier years, stated with the same discipline as any other cell.
- Overlay this year: where the current year sits inside the phase, what has tracked the phase norm so far and what hasn't.
- Anchor the close to the policy calendar: the dated events inside the window (FOMC meetings, fiscal deadlines, the election itself where relevant).
- Phases are spelled out in plain English everywhere ("midterm election years"), never PE shorthand.
BAD: partisan framing of any kind. (the cycle is a calendar, not a candidate)
BAD: implying the cycle causes the returns. (association is the claim; causation is a hypothesis, labeled as one)""",

    "QUIET_EDGE": """No fresh news. That absence is the angle: the reader is early, not late.
- Open with the window and its record, and say plainly that nothing happened today — no catalyst, no headline. The value is advance notice.
- NO drivers-news section. No "today", "this week", "recently" anywhere. Any dated fact gets its actual date.
- This is the short piece: the record, the shape (best case, worst case, where in the window it moves), the mechanism hypothesis, what to watch when the window opens. Then stop.
- Bridge in the second or third paragraph; there is no news block to wait behind.
BAD: manufacturing urgency from stale headlines. (the temporal gate will hold the piece — and it reads as desperation)
BAD: padding to sound substantial. (thin story, short piece; that is the design working)""",
}

# ============================================================
# Locked invariants — the facts discipline (shared by both calls)
# ============================================================
INVARIANTS = """Non-negotiable facts discipline:
- Every TradeWave number you write must come verbatim from the Angle Card: the story cell's stats, its per-year rows, or a provided quotable string. Nothing else. Do not recompute, round further, or extrapolate.
- Every rate or record must carry its sample size in the same sentence. The provided quotables embed it correctly — but use each quotable IN FULL at most once in the direct-answer box and at most once in the body. Every later reference is shortened ("16 of 20", "that 11.1% median"); shortened count forms like "16 of 20" always remain valid. Never open two sections with the same fact or the same sentence shape.
- Windows are measured in calendar days. Never write "trading days".
- Auxiliary-cell numbers (corroborating/conflicting cells) appear as counts only ("closed higher in 12 of 15"), never as percentages, and never with the labels used in the key-stats box (Percent Profitable, Avg Profit, Num Winners, Num Losers, Median Profit, Std Dev, Sharpe Ratio, TradeWave Ratio).
- Every figure depicts the STORY cell and only the story cell: charts exist for the story cell's window and lookback, and for no other cell. Never place a figure inside a beat that discusses an auxiliary (corroborating/conflicting) cell, and never caption, label, or describe a figure as showing an auxiliary window. Auxiliary cells live in prose with exact counts and carry no figure.
- Causal claims: either cite a provided research source with <sup>[id]</sup>, or frame explicitly as hypothesis ("one likely driver", "may reflect"). Never state a mechanism as fact.
- External facts (news, prices, analyst views) come only from the Research JSON; cite with <sup>[id]</sup> using the source's id. No research entry, no claim. Never invent sources or URLs.
- Do not use any source whose "fresh" flag is false for the headline, dek, or opening; if you mention a non-fresh source at all, date it explicitly and avoid "recently/today/this week/now" in that sentence.
- "TradeWave" is first mentioned inside the bridge paragraph (id="transition_to_tradewave") and never before it. The bridge contains no statistics.
- The projection/average-path language: any mention of projected or expected path must be described as the average historical trend across the analyzed years — it is not a forecast.
- If you mention drawdowns, MAE, MFE, or intraperiod downside anywhere, the chart set must include bars_mae_mfe (the plan enforces this; do not mention excursions if that chart is absent).
- Cumulative figures must state whether they are a sum or compounded.
- No investment advice, no predictions, no guarantees. Historical tendencies only.
- No em dashes. Use the % symbol, not "percent". One date format in prose: "Sep 18, 2026"."""

STYLE = """Voice: senior markets reporter who found something the reader does not know.
Short paragraphs. Sentences under 25 words. State facts directly — no "it is worth noting".
When a number is striking, let it carry the sentence; do not stack adjectives on it.
A number stated once is stated; repeating it verbatim in another section is filler.
All <h2> headings are natural questions a reader might search."""


# ============================================================
# PLAN
# ============================================================

_STORY_FIELDS = ("symbol", "anchor_date", "days", "years", "mode", "horizon_tag",
                 "n", "up_years", "down_years", "flat_years", "direction",
                 "median_net", "avg_net", "best_year", "best_net", "worst_year",
                 "worst_net", "median_mfe", "median_mae", "per_year", "quotables")


def _card_digest(card: Dict[str, Any]) -> Dict[str, Any]:
    """The Angle Card as the WRITER may see it: reader-quotable facts only.
    Engine scoring internals (tail_p, conviction, story_score, eligibility)
    are stripped by whitelist — the 2026-07-21 smoke run showed the writer
    dressing tail_p up as "a p-value in TradeWave's test". stats_raw is also
    excluded: the key-stats box is chrome; prose draws on quotables/per-year."""
    slim = {k: card[k] for k in ("symbol", "anchor_date", "context",
                                 "tension_descriptor") if k in card}
    slim["angle"] = card["angle"]
    story = {k: card["story_cell"].get(k) for k in _STORY_FIELDS
             if k in card["story_cell"]}
    slim["story_cell"] = story
    slim["auxiliary_cells"] = [
        {k: c.get(k) for k in ("role", "days", "years", "mode", "n", "up_years",
                               "down_years", "direction", "median_net", "quotables")}
        for c in card.get("auxiliary_cells", [])]
    return slim


def _research_digest(research: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(research, dict):
        return {"available": False}
    sources = [{"id": s.get("id"), "publisher": s.get("publisher"),
                "title": s.get("title"), "date": s.get("date"),
                "fresh": s.get("fresh")}
               for s in research.get("sources", []) if isinstance(s, dict)]
    return {"available": True, "sources": sources,
            "has_special_signals": bool(research.get("special_signals")),
            "temporal": research.get("temporal", {})}


def build_plan_prompt(card: Dict[str, Any],
                      research: Optional[Dict[str, Any]] = None,
                      available_charts: Optional[List[str]] = None) -> str:
    angle = card["angle"]["name"]
    lo, hi = ANGLE_BANDS[angle]
    fallbacks = ", ".join(r["name"] for r in card["angle"].get("runner_up", [])) or "none"
    charts_universe = (sorted(set(available_charts) & set(ALLOWED_CHARTS))
                      if available_charts is not None else list(ALLOWED_CHARTS))
    if charts_universe:
        charts_rule = (f'- "charts": 1-3 picks from {charts_universe} that serve the '
                       'thesis. If you plan to mention drawdowns/excursions in any '
                       'beat, "charts" MUST include "bars_mae_mfe"'
                       + ("" if "bars_mae_mfe" in charts_universe else
                          ' — it is NOT available for this article, so plan no '
                          'excursion/drawdown discussion at all') + ".")
    else:
        charts_rule = ('- "charts": [] — NO charts are available for this article. '
                       'Plan no chart beats and no excursion/drawdown discussion '
                       '(the worst-year and best-year quotables are still allowed).')
    return f"""You are the planning editor for Seasonal Market News. Decide how ONE article will be built, then return ONLY a JSON object (no prose, no fences).

The angle engine assigned this piece the {angle} angle. Your job is to commit to one controlling idea and structure everything around it. You are deciding the article's spine, not writing it.

ANGLE GUIDANCE ({angle}):
{ANGLE_GUIDANCE[angle]}

{INVARIANTS}

ANGLE CARD (authoritative data):
{json.dumps(_card_digest(card), ensure_ascii=False)}

RESEARCH AVAILABILITY:
{json.dumps(_research_digest(research), ensure_ascii=False)}

Return exactly this JSON shape:
{{
  "feasible": true,
  "veto_reason": "",
  "thesis": "ONE sentence, max 25 words: the controlling idea of THIS article.",
  "beats": [
    {{"purpose": "what this beat accomplishes for the thesis",
      "carries": ["quotable:record", "stat:Percent Profitable", "research:3"],
      "chart": null}}
  ],
  "h2s": ["Question-format headings, one per body section beat"],
  "charts": ["subset of {list(ALLOWED_CHARTS)} that serves the thesis, 2 or 3"],
  "bridge_after_beat": 1,
  "word_budget": 0,
  "headlines": ["two candidates, each under 16 words, seasonality-first, company name + ticker"],
  "source_ids": []
}}

Planning rules:
- 3 to 8 beats. Each beat names what it carries; a beat with nothing to carry does not exist.
- word_budget = beats x 150-200 words, clamped to {lo}-{hi} for {angle}. Thin story, low budget — never pad.
{charts_rule}
- "source_ids": the research sources you will actually cite (aim for 8+ distinct when available; fewer only if research is thin). Empty list when research is unavailable — then the article makes NO external claims.
- bridge_after_beat: index (0-based) of the beat after which the TradeWave bridge lands, per the angle guidance.
- Set "feasible": false with a one-sentence veto_reason ONLY if the research cannot support this angle at all (fallback angles available: {fallbacks}). Vetoing on preference is not allowed."""


class PlanError(ValueError):
    pass


def parse_plan(raw: str, angle: str,
               available_charts: Optional[List[str]] = None) -> Dict[str, Any]:
    """Parse + validate the PLAN JSON. Raises PlanError with a specific,
    feed-back-able message (the orchestrator allows exactly one retry).
    When available_charts is given, planned charts must be a subset of it
    (empty available -> charts must be [])."""
    cleaned = (raw or "").strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.M).strip()
    try:
        plan = json.loads(cleaned)
    except Exception:
        m = re.search(r"\{.*\}", cleaned, re.S)
        if not m:
            raise PlanError("plan is not a JSON object")
        try:
            plan = json.loads(m.group(0))
        except Exception as exc:
            raise PlanError(f"plan JSON does not parse: {exc}")
    if not isinstance(plan, dict):
        raise PlanError("plan is not a JSON object")

    if plan.get("feasible") is False:
        if not str(plan.get("veto_reason", "")).strip():
            raise PlanError("veto without veto_reason")
        return plan

    problems: List[str] = []
    thesis = str(plan.get("thesis", "")).strip()
    if not thesis:
        problems.append("missing thesis")
    elif len(thesis.split()) > 30:
        problems.append("thesis exceeds 25 words")
    beats = plan.get("beats")
    if not isinstance(beats, list) or not (3 <= len(beats) <= 8):
        problems.append("beats must be a list of 3-8 items")
    charts = plan.get("charts")
    universe = (sorted(set(available_charts) & set(ALLOWED_CHARTS))
                if available_charts is not None else list(ALLOWED_CHARTS))
    min_charts = 1 if universe else 0
    if (not isinstance(charts, list) or not (min_charts <= len(charts) <= 3)
            or any(c not in universe for c in charts)):
        problems.append(f"charts must be {min_charts}-3 of {universe}")
    lo, hi = ANGLE_BANDS[angle]
    try:
        budget = int(plan.get("word_budget", 0))
    except (TypeError, ValueError):
        budget = 0
    if budget:
        plan["word_budget"] = max(lo, min(hi, budget))
    else:
        problems.append("missing word_budget")
    headlines = plan.get("headlines")
    if not isinstance(headlines, list) or not headlines:
        problems.append("missing headlines")
    else:
        for h in headlines:
            if len(str(h).split()) > 16:
                problems.append(f"headline over 16 words: {str(h)[:60]!r}")
    if isinstance(beats, list):
        n_beats = len(beats)
        try:
            bab = int(plan.get("bridge_after_beat", -1))
        except (TypeError, ValueError):
            bab = -1
        if not (0 <= bab < n_beats):
            problems.append("bridge_after_beat out of range")
    if not isinstance(plan.get("source_ids", []), list):
        problems.append("source_ids must be a list")
    if problems:
        raise PlanError("; ".join(problems))
    return plan


# ============================================================
# WRITE
# ============================================================

def build_write_prompt(card: Dict[str, Any], plan: Dict[str, Any],
                       research: Optional[Dict[str, Any]] = None,
                       available_figs: Optional[List[str]] = None) -> str:
    angle = card["angle"]["name"]
    figs = [c for c in plan.get("charts", []) if not available_figs
            or c in available_figs]
    fig_tokens = " ".join(f"{{{{FIG:{c}}}}}" for c in figs)
    if figs and "bars_mae_mfe" not in figs:
        excursion_rule = ("\n- The bars_mae_mfe chart is not part of this article: "
                          "do NOT discuss MAE, MFE, drawdowns, or adverse/favorable "
                          "excursions anywhere. The best-year and worst-year "
                          "quotables are still allowed.")
    elif not figs:
        excursion_rule = ("\n- NO charts are available: do not reference any chart "
                          "or figure, and do NOT discuss MAE, MFE, drawdowns, or "
                          "excursions. The best-year and worst-year quotables are "
                          "still allowed.")
    else:
        excursion_rule = ""
    research_block = (json.dumps(research, ensure_ascii=False)
                      if isinstance(research, dict) else
                      '{"available": false} — research is unavailable: make NO external claims, cite nothing, use no news, prices, or analyst views.')
    return f"""You are a financial journalist for Seasonal Market News. Write ONE article as an HTML FRAGMENT (no <!doctype>, <html>, <head>, <body>, <style>, no markdown, no code fences). Follow the plan exactly — it is your own editing decision, already made.

THE PLAN (yours; follow it):
{json.dumps(plan, ensure_ascii=False)}

ANGLE GUIDANCE ({angle}):
{ANGLE_GUIDANCE[angle]}

{INVARIANTS}

{STYLE}

Output contract (exact):
- Start with <h1> (pick the stronger of your two planned headlines), then <p class="dek"> (one sentence, no TradeWave mention).
- Immediately after the dek: <section id="key-takeaways"> with an <h2> question, one <p class="direct-answer"> sentence that answers it from the story cell's data (self-contained, snippet-ready), and a <div class="key-takeaways-box"> with 3-5 <li> bullets, data first.
- Then the hero slot token {{{{HERO}}}} on its own line.
- Body sections follow YOUR beats, one <h2> question per section, prose in <p>.
- The bridge: a single short paragraph <p id="transition_to_tradewave" class="chart-bridge"> placed after beat {plan.get('bridge_after_beat')} exactly as planned. First mention of TradeWave.ai happens here, no statistics in it, and its wording must turn THIS article's thesis — do not reuse stock phrasing.
- Place these slot tokens where the plan's beats call for them (each exactly once, on its own line): {{{{META_STRIP}}}} {{{{KEY_STATS}}}} {fig_tokens}
  They render server-side; put {{{{META_STRIP}}}} and {{{{KEY_STATS}}}} inside your seasonal-record section, and each figure token where its beat discusses that chart, with a one-sentence lead-in before it.{excursion_rule}
- Do NOT write your own <figure>, <aside>, <table>, or stats boxes; do not restate the key-stats box row-by-row in prose.
- End the final section with what-to-watch items, then the tokens {{{{SOURCES}}}} and {{{{METHODOLOGY}}}} on their own lines. Nothing after them.
- Citations: <sup>[id]</sup> where id is the research source's own id. Cite only planned source_ids; every external claim carries one.
- Total length: at most word_budget + 10% = {int(int(plan.get('word_budget') or 0) * 1.1)} words; under budget is always fine. Cut the weakest beat before padding any other.

ANGLE CARD (authoritative TradeWave data — quote numbers exactly):
{json.dumps(_card_digest(card), ensure_ascii=False)}

RESEARCH JSON (only permitted external context):
{research_block}

Return only the HTML fragment."""


def build_revision_prompt(prose: str, issues: List[Dict[str, str]],
                          card: Dict[str, Any], plan: Dict[str, Any],
                          research: Optional[Dict[str, Any]] = None) -> str:
    """One targeted revision (the bounded loop's single model retry): change
    only what the issue codes name, preserve everything else verbatim."""
    return f"""You are revising your own SMN article draft. Fix ONLY the issues listed below; leave every other sentence, token, and citation exactly as it is. Return the corrected HTML fragment only (same output contract as before: fragment, slot tokens intact, no fences).

ISSUES (each names what to change):
{json.dumps(issues, ensure_ascii=False, indent=1)}

Rules for fixing:
- A number that disagrees with the Angle Card is corrected to the card's value or the sentence is deleted. Never invent a replacement.
- An unsupported claim is deleted, not softened.
- A missing sample size is added from the card's quotables.
- "trading days" becomes "calendar days".
- Stale-framing issues: date the fact explicitly or delete the sentence.
- TW_BEFORE_BRIDGE: delete or rephrase EVERY TradeWave mention that appears before the bridge paragraph — the bridge must be the first mention in the prose. Statistics stay; the attribution moves.
- INTERNAL_METRIC_LEAK: delete any mention of p-values, tail probabilities, scores, or engine internals entirely; they are not TradeWave statistics.

{INVARIANTS}

ANGLE CARD:
{json.dumps(_card_digest(card), ensure_ascii=False)}

THE PLAN:
{json.dumps(plan, ensure_ascii=False)}

RESEARCH JSON:
{json.dumps(research, ensure_ascii=False) if isinstance(research, dict) else '{"available": false}'}

DRAFT TO REVISE:
{prose}"""
