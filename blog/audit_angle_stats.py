#!/usr/bin/env python3
"""
audit_angle_stats.py
====================

Observer-only, per-angle rollup of the article audit trail written by
blog/article_audit.py and reviewed by blog/daily_audit_review.py.

For every run filed under the audit tree in the target date window it reads:
  * manifest.json -> identity  (engine / angle, when the run is an angle-engine
                                piece; runs with no angle field are "legacy")
  * review.json   -> editorial (decision + hard/soft issue codes, as recorded by
                                daily_audit_review.py)

and prints a per-angle table of run counts, editorial decision counts, and
hard/soft issue code frequencies, with the design's TEMPLATE_CADENCE soft code
called out on its own line so template drift is visible per angle.

Everything here is DATA for the periodic audit/improvement cycle. This tool is a
pure stdlib observer: it only reads the audit tree and prints — it never writes,
never touches generation or publication, and never calls the network or an LLM.

Usage:
    audit_angle_stats.py [--days N] [--end YYYY-MM-DD] [--root DIR] [--json]
    (default: the last 7 days ending today; root from config.article_audit_dir)
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/home/flask")

LEGACY_KEY = "legacy"
TEMPLATE_CADENCE_CODE = "TEMPLATE_CADENCE"


def _load_config():
    try:
        import config
        return config
    except Exception:
        return None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _issue_codes(issues) -> list:
    """Pull the 'code' out of a hard/soft issue list. Tolerates strings, dicts
    with a missing code, and non-list junk (all -> best-effort code strings)."""
    codes = []
    if not isinstance(issues, list):
        return codes
    for item in issues:
        if isinstance(item, dict):
            codes.append(str(item.get("code", "UNKNOWN")))
        elif item is not None:
            codes.append(str(item))
    return codes


def _decision_of(review: dict) -> str:
    """Classify a run's editorial outcome from its review.json (or lack of one)."""
    if review is None:
        return "unreviewed"          # daily_audit_review has not run on it yet
    editorial = review.get("editorial")
    if not isinstance(editorial, dict):
        return "unknown"
    if "decision" in editorial:
        return str(editorial["decision"])
    if "skipped" in editorial:
        return "skipped"             # review ran with --no-llm
    return "error"                   # editorial attempted but produced no decision


def find_runs(root: Path, date: datetime.date) -> list:
    """Run directories filed under root/YYYY/MM/DD that carry a manifest."""
    day_dir = root / f"{date.year:04d}" / f"{date.month:02d}" / f"{date.day:02d}"
    if not day_dir.is_dir():
        return []
    return sorted(p for p in day_dir.iterdir()
                  if p.is_dir() and (p / "manifest.json").exists())


def _blank_stat() -> dict:
    return {
        "runs": 0,
        "decisions": Counter(),
        "hard_issues": Counter(),
        "soft_issues": Counter(),
        "template_cadence": 0,
    }


def collect_stats(root: Path, dates) -> dict:
    """Aggregate per-angle stats over the given dates. Pure: reads only, and
    returns {angle_name: stat_dict}. Missing/broken files degrade gracefully."""
    stats: dict = {}
    for date in dates:
        for run_dir in find_runs(root, date):
            manifest = _read_json(run_dir / "manifest.json") or {}
            identity = manifest.get("identity") or {}
            angle = identity.get("angle")
            key = angle if angle else LEGACY_KEY

            stat = stats.setdefault(key, _blank_stat())
            stat["runs"] += 1

            review = _read_json(run_dir / "review.json")
            stat["decisions"][_decision_of(review)] += 1
            if review is not None:
                editorial = review.get("editorial")
                if isinstance(editorial, dict):
                    for code in _issue_codes(editorial.get("hard_issues")):
                        stat["hard_issues"][code] += 1
                    for code in _issue_codes(editorial.get("soft_issues")):
                        stat["soft_issues"][code] += 1
            stat["template_cadence"] = stat["soft_issues"][TEMPLATE_CADENCE_CODE]
    return stats


def _sorted_angles(stats: dict) -> list:
    """Angles by descending run count, 'legacy' always last."""
    return sorted(stats, key=lambda k: (k == LEGACY_KEY, -stats[k]["runs"], k))


def _fmt_counter(counter: Counter) -> str:
    if not counter:
        return "-"
    return "  ".join(f"{code}={n}" for code, n in counter.most_common())


def format_report(stats: dict, root: Path, dates) -> str:
    span = (f"{dates[-1].isoformat()} .. {dates[0].isoformat()}"
            if dates else "(no dates)")
    total = sum(s["runs"] for s in stats.values())
    lines = [
        "=" * 70,
        f"Per-angle audit stats   root={root}",
        f"window: {span}  ({len(dates)} day(s))   runs: {total}   "
        f"angles: {len([k for k in stats if k != LEGACY_KEY])}"
        + (" (+legacy)" if LEGACY_KEY in stats else ""),
        "=" * 70,
    ]
    if not stats:
        lines.append("(no audit runs found in this window)")
        return "\n".join(lines)

    for angle in _sorted_angles(stats):
        stat = stats[angle]
        lines.append("")
        lines.append(f"ANGLE: {angle}   runs={stat['runs']}")
        lines.append(f"  decisions      : {_fmt_counter(stat['decisions'])}")
        lines.append(f"  TEMPLATE_CADENCE: {stat['template_cadence']}")
        lines.append(f"  hard issues    : {_fmt_counter(stat['hard_issues'])}")
        lines.append(f"  soft issues    : {_fmt_counter(stat['soft_issues'])}")
    return "\n".join(lines)


def _jsonable(stats: dict) -> dict:
    return {angle: {"runs": s["runs"],
                    "decisions": dict(s["decisions"]),
                    "hard_issues": dict(s["hard_issues"]),
                    "soft_issues": dict(s["soft_issues"]),
                    "template_cadence": s["template_cadence"]}
            for angle, s in stats.items()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Observer-only per-angle rollup of the article audit trail.")
    parser.add_argument("--days", type=int, default=7,
                        help="Number of days to scan, ending on --end (default: 7).")
    parser.add_argument("--end", default=None,
                        help="Last day of the window, YYYY-MM-DD (default: today).")
    parser.add_argument("--root", default=None,
                        help="Audit root (default: config.article_audit_dir).")
    parser.add_argument("--json", action="store_true",
                        help="Also emit the machine-readable rollup as JSON.")
    args = parser.parse_args(argv)

    cfg = _load_config()
    root = Path(args.root or getattr(cfg, "article_audit_dir", "") or
                Path(__file__).resolve().parent / "audit")
    end = (datetime.date.fromisoformat(args.end) if args.end
           else datetime.date.today())
    days = max(1, args.days)
    dates = [end - datetime.timedelta(days=i) for i in range(days)]

    if not root.is_dir():
        print(f"[ANGLE-STATS] no audit root at {root}; nothing to scan.")
        return 0

    stats = collect_stats(root, dates)
    print(format_report(stats, root, dates))
    if args.json:
        print("\n[ANGLE-STATS-JSON] " + json.dumps(_jsonable(stats), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
