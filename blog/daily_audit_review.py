#!/usr/bin/env python3
"""
daily_audit_review.py
=====================

Periodic, observer-only review of the article audit trail written by
blog/article_audit.py. Runs from a timer AFTER articles are published and
records what it finds — it can never touch generation or publication:
it is a separate process that only reads published artifacts and only
writes `review.json` files back into the audit runs.

For each un-reviewed run of the target date it records:
  * citation_gate  — deterministic citation check of the final HTML
  * editorial      — one independent LLM review (decision + hard/soft issues);
                     opt-out via config.audit_review_llm_enabled = False

Findings are DATA for the periodic audit/improvement cycle, never actions.
Every per-run failure is caught and recorded; the job always exits 0 unless
the audit root itself is unusable.

Usage:
    daily_audit_review.py [--date YYYY-MM-DD] [--root DIR] [--no-llm]
    (default date: yesterday, so the 03:00 UTC batch is fully settled)
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/home/flask")

REVIEW_FILENAME = "review.json"
SUMMARY_LOG = Path(__file__).resolve().parent / "logs" / "audit_reviews.jsonl"


def _load_config():
    try:
        import config
        return config
    except Exception:
        return None


def _read(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def review_run(run_dir: Path, *, llm_enabled: bool) -> dict:
    """Review one audit run directory. Never raises; failures become fields."""
    result = {
        "schema_version": 1,
        "reviewed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "run": run_dir.name,
        "errors": [],
    }
    manifest = _read_json(run_dir / "manifest.json") or {}
    identity = manifest.get("identity") or {}
    result["identity"] = identity

    html = _read(run_dir / "final.html") or _read(run_dir / "article_llm_raw.html")
    if not html:
        result["errors"].append("no final.html or article_llm_raw.html in run")
        return result
    research = _read_json(run_dir / "research.json")

    # 1) Deterministic citation check (pure stdlib+regex, no network).
    try:
        from citation_gate import validate_citations
        result["citation_gate"] = validate_citations(
            html, research=research, symbol=identity.get("symbol"),
            strict=False, check_liveness=False)
    except Exception as exc:
        result["errors"].append(f"citation_gate failed: {exc}")

    # 2) Independent editorial review (LLM) — observer only, no repair pass.
    if llm_enabled:
        try:
            from editorial_review import review_article
            facts = {key: identity.get(key) for key in
                     ("symbol", "resource_id", "start_date", "days", "years",
                      "direction", "article_publish_date") if identity.get(key)}
            result["editorial"] = review_article(html, facts, research)
        except Exception as exc:
            result["errors"].append(f"editorial review failed: {exc}")
    else:
        result["editorial"] = {"skipped": "llm disabled"}
    return result


def find_runs(root: Path, date: datetime.date) -> list:
    day_dir = root / f"{date.year:04d}" / f"{date.month:02d}" / f"{date.day:02d}"
    if not day_dir.is_dir():
        return []
    return sorted(p for p in day_dir.iterdir() if p.is_dir() and (p / "manifest.json").exists())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Observer-only daily review of the article audit trail.")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: yesterday).")
    parser.add_argument("--root", default=None, help="Audit root (default: config.article_audit_dir).")
    parser.add_argument("--no-llm", action="store_true", help="Skip the LLM editorial review.")
    args = parser.parse_args(argv)

    cfg = _load_config()
    root = Path(args.root or getattr(cfg, "article_audit_dir", "") or
                Path(__file__).resolve().parent / "audit")
    if not root.is_dir():
        print(f"[AUDIT-REVIEW] no audit root at {root}; nothing to review.")
        return 0
    date = (datetime.date.fromisoformat(args.date) if args.date
            else datetime.date.today() - datetime.timedelta(days=1))
    llm_enabled = (not args.no_llm) and bool(getattr(cfg, "audit_review_llm_enabled", True))

    runs = find_runs(root, date)
    reviewed = skipped = failed = 0
    for run_dir in runs:
        target = run_dir / REVIEW_FILENAME
        if target.exists():
            skipped += 1
            continue
        outcome = review_run(run_dir, llm_enabled=llm_enabled)
        try:
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(outcome, ensure_ascii=False, indent=2,
                                      default=str) + "\n", encoding="utf-8")
            tmp.replace(target)
            reviewed += 1
            if outcome["errors"]:
                failed += 1
        except Exception as exc:
            failed += 1
            print(f"[AUDIT-REVIEW] could not write review for {run_dir.name}: {exc}")

    summary = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "date": date.isoformat(), "runs": len(runs), "reviewed": reviewed,
               "already_reviewed": skipped, "with_errors": failed,
               "llm_enabled": llm_enabled}
    print(f"[AUDIT-REVIEW] {json.dumps(summary)}")
    try:
        SUMMARY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SUMMARY_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
