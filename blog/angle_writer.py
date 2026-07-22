"""angle_writer.py — PLAN -> WRITE -> assemble -> gate orchestrator (Phase 2).

The bounded loop from ANGLE_ENGINE_DESIGN.md §4, generation side:

    PLAN (LLM, JSON; one re-ask on invalid JSON; may veto once)
      -> WRITE (LLM, prose fragment with slot tokens)
      -> assemble (deterministic chrome substitution — angle_chrome)
      -> integrity gate (deterministic — integrity_gate.validate_cell_article)
      -> on hard issues: ONE targeted revision, re-assemble, re-gate
      -> publish-ready | hold (never publishes anything itself)

LLM transports are injected callables (same pattern as editorial_review), so
tests run fully offline with canned responses. The default live transports
use AI_tools.send_openai_prompt.

CLI (dev harness — writes artifacts to a directory, never publishes):
  python3 angle_writer.py --card card.json [--research research.json]
      [--out DIR] [--dry-run] [--editorial]
  --dry-run prints the PLAN and WRITE prompts without any LLM call.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, '/home/flask')

import angle_chrome
import angle_prompts
from angle_prompts import PlanError
from integrity_gate import validate_cell_article


def _default_plan_send(prompt: str) -> str:
    from AI_tools import send_openai_prompt
    return send_openai_prompt(prompt, system="Return only a JSON object.",
                              stream=False, temperature=0.0)


def _default_write_send(prompt: str) -> str:
    from AI_tools import send_openai_prompt
    return send_openai_prompt(
        prompt,
        system=("Return only one HTML fragment - no JSON, no code fences, "
                "no commentary."),
        stream=False, temperature=0.2)


def _strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:html)?\s*|\s*```$", "", cleaned, flags=re.M)
    return cleaned.strip()


def generate_angle_article(card: Dict[str, Any], *,
                           research: Optional[Dict[str, Any]] = None,
                           images: Optional[List[Dict[str, str]]] = None,
                           hero_url: str = "",
                           company: str = "",
                           byline: str = "",
                           cta_link: str = "",
                           methodology_url: str = "",
                           book_url: str = "",
                           send_plan: Callable[[str], str] | None = None,
                           send_write: Callable[[str], str] | None = None,
                           editorial_send: Callable[[str], str] | None = None,
                           run_editorial: bool = False) -> Dict[str, Any]:
    """Full generation for one Angle Card. Returns an artifacts dict:

    status: 'ready' (all gates clean) | 'hold' (hard issues survived the one
            revision) | 'vetoed' (plan declared the angle infeasible — caller
            falls back to the card's runner-up angle, once) | 'plan_failed'
    plus plan/prose/html/gate results for the audit trail. Never publishes.
    """
    send_plan = send_plan or _default_plan_send
    send_write = send_write or _default_write_send
    if card.get("angle") is None:
        return {"status": "no_story", "detail": card.get("no_story", "")}
    angle = card["angle"]["name"]
    artifacts: Dict[str, Any] = {"angle": angle, "symbol": card.get("symbol")}

    # ---- PLAN (one re-ask on invalid output; may veto once) ----
    # The chart universe is fixed BEFORE planning: a plan can only choose
    # charts that actually exist for this article (2026-07-21 live-run lesson:
    # a plan that promises bars_mae_mfe no assembler can render strands the
    # writer in an unfixable CHART_SEMANTICS_MISMATCH).
    available_charts = ([i.get("variant") for i in images] if images is not None
                        else [])
    plan_prompt = angle_prompts.build_plan_prompt(card, research, available_charts)
    artifacts["plan_prompt"] = plan_prompt
    raw = send_plan(plan_prompt)
    try:
        plan = angle_prompts.parse_plan(raw, angle, available_charts)
    except PlanError as first_err:
        retry_prompt = (plan_prompt +
                        f"\n\nYour previous response was invalid ({first_err}). "
                        "Return ONLY the corrected JSON object.")
        try:
            plan = angle_prompts.parse_plan(send_plan(retry_prompt), angle,
                                            available_charts)
        except PlanError as second_err:
            return {**artifacts, "status": "plan_failed",
                    "detail": f"{first_err} / retry: {second_err}"}
    if plan.get("feasible") is False:
        return {**artifacts, "status": "vetoed",
                "detail": plan.get("veto_reason", ""),
                "fallbacks": card["angle"].get("runner_up", [])}
    artifacts["plan"] = plan

    # ---- WRITE ----
    available_figs = ([i.get("variant") for i in images] if images else None)
    write_prompt = angle_prompts.build_write_prompt(card, plan, research,
                                                    available_figs)
    artifacts["write_prompt"] = write_prompt
    prose = _strip_fences(send_write(write_prompt))
    artifacts["prose"] = prose

    def _assemble(p: str) -> Dict[str, Any]:
        chrome = angle_chrome.build_chrome(
            card, images=images, hero_url=hero_url, research=research,
            company=company, cta_link=cta_link,
            methodology_url=methodology_url, book_url=book_url)
        return angle_chrome.assemble_article(
            p, chrome, research=research, planned_figs=plan.get("charts"),
            byline=byline)

    # ---- Gate pass 1: deterministic integrity + independent editorial ----
    # (design §4: both gates score the same pass; hard issues from either
    # feed ONE combined revision; a second pass then publishes or holds)
    def _editorial(html: str) -> Optional[Dict[str, Any]]:
        if not run_editorial:
            return None
        from editorial_review import review_article
        story = card.get("story_cell") or {}
        facts = {"symbol": card.get("symbol"),
                 "start_date": story.get("anchor_date"),
                 "days": story.get("days"), "years": story.get("years"),
                 "direction": story.get("direction"),
                 # The reviewer must be able to verify the server-rendered
                 # key-stats box and every licensed prose number.
                 "story_stats_raw": story.get("stats_raw") or {},
                 # Auxiliary cells are quotable as COUNTS ONLY. Passing their
                 # stats_raw let the reviewer mistake aux numbers for the story
                 # cell's and fail correct articles, so those fields are removed
                 # here rather than left to be misattributed.
                 "auxiliary_cells": [
                     {k: v for k, v in (a or {}).items()
                      if k in ("days", "years", "lookback_label", "role",
                               "up_years", "down_years", "n", "median_net",
                               "direction", "anchor_date")}
                     for a in (card.get("auxiliary_cells") or [])
                 ],
                 "_stats_scope": (
                     "story_stats_raw is the ONLY source for the key-stats box, "
                     "pattern-meta strip and figure captions. Auxiliary cells "
                     "carry NO key-stats fields and must never be compared "
                     "against the key-stats box; they are quotable as counts "
                     "only (e.g. 'closed higher in 15 of 20')."
                 ),
                 "angle_card": angle_prompts._card_digest(card)}
        return review_article(html, facts, research, send=editorial_send)

    def _gate(html: str, tag: str) -> Tuple[List[Dict[str, str]], bool]:
        """Returns (hard issues from both gates, editorial_says_hold)."""
        integrity = validate_cell_article(html, card,
                                          word_budget=int(plan.get("word_budget") or 0))
        result: Dict[str, Any] = dict(integrity)
        hold = False
        try:
            review = _editorial(html)
        except Exception as exc:                 # review infra failure = hold
            review = {"decision": "hold",
                      "hard_issues": [{"code": "EDITORIAL_UNAVAILABLE",
                                       "detail": str(exc)}]}
        if review is not None:
            result["editorial"] = review
            hold = review.get("decision") == "hold"
        artifacts[tag] = result
        hard = list(integrity["errors"])
        if review is not None:
            for issue in review.get("hard_issues") or []:
                if isinstance(issue, dict):
                    hard.append({"code": str(issue.get("code", "EDITORIAL")),
                                 "detail": str(issue.get("detail", ""))})
        return hard, hold

    assembled = _assemble(prose)
    artifacts["assembly_warnings"] = assembled["warnings"]
    html = assembled["html"]
    hard1, hold1 = _gate(html, "gate1")
    if hold1:                                    # unfixable per the reviewer
        artifacts.update(status="hold", html=html)
        return artifacts
    if not hard1:
        artifacts.update(status="ready", html=html)
        return artifacts

    # ---- ONE bounded revision on the union of hard issues ----
    revision_prompt = angle_prompts.build_revision_prompt(
        prose, hard1, card, plan, research)
    artifacts["revision_prompt"] = revision_prompt
    prose2 = _strip_fences(send_write(revision_prompt))
    artifacts["prose_revised"] = prose2
    assembled2 = _assemble(prose2)
    artifacts["assembly_warnings_2"] = assembled2["warnings"]
    html = assembled2["html"]
    hard2, hold2 = _gate(html, "gate2")
    if hard2 or hold2:
        artifacts.update(status="hold", html=html)
        return artifacts
    artifacts.update(status="ready", html=html)
    return artifacts


# ============================================================
# CLI harness (dev): artifacts to disk, no publishing, no Redis
# ============================================================

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="SMN angle writer harness (Phase 2).")
    p.add_argument("--card", required=True, help="Angle Card JSON (angle_engine --json)")
    p.add_argument("--research", default="", help="optional research JSON file")
    p.add_argument("--out", default="", help="directory for artifacts")
    p.add_argument("--dry-run", action="store_true",
                   help="print prompts only; no LLM calls")
    p.add_argument("--editorial", action="store_true",
                   help="also run the independent editorial review (LLM)")
    args = p.parse_args(argv)

    with open(args.card, encoding="utf-8") as fh:
        card = json.load(fh)
    research = None
    if args.research:
        with open(args.research, encoding="utf-8") as fh:
            research = json.load(fh)

    if args.dry_run:
        print("========== PLAN PROMPT ==========")
        print(angle_prompts.build_plan_prompt(card, research))
        print("\n(dry run: WRITE prompt requires a plan; run live to continue)")
        return 0

    result = generate_angle_article(card, research=research,
                                    run_editorial=args.editorial)
    print(f"status: {result['status']}")
    if result.get("gate1"):
        print(f"gate1: ok={result['gate1']['ok']} "
              f"errors={[e['code'] for e in result['gate1']['errors']]}")
    if result.get("gate2"):
        print(f"gate2: ok={result['gate2']['ok']} "
              f"errors={[e['code'] for e in result['gate2']['errors']]}")
    if result.get("status") == "vetoed":
        print(f"veto: {result.get('detail')}")

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        base = os.path.join(args.out,
                            f"{card.get('symbol', 'UNKNOWN')}_{stamp}")
        for name, key in (("plan.json", "plan"), ("article.html", "html"),
                          ("prose.html", "prose"),
                          ("prose_revised.html", "prose_revised")):
            if result.get(key) is not None:
                payload = result[key]
                mode = "w"
                with open(f"{base}_{name}", mode, encoding="utf-8") as fh:
                    fh.write(payload if isinstance(payload, str)
                             else json.dumps(payload, indent=2, ensure_ascii=False))
        with open(f"{base}_result.json", "w", encoding="utf-8") as fh:
            slim = {k: v for k, v in result.items()
                    if k not in ("html", "prose", "prose_revised",
                                 "plan_prompt", "write_prompt", "revision_prompt")}
            fh.write(json.dumps(slim, indent=2, ensure_ascii=False, default=str))
        print(f"artifacts: {base}_*")
    return 0 if result.get("status") in ("ready",) else 1


if __name__ == "__main__":
    raise SystemExit(main())
