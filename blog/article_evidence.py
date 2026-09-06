"""Pure, reproducible article facts computed from matched historical rows.

All returns and excursions use the underlying security's entry-price basis.
Missing observations remain missing; no synthetic median-year path is built.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import math
import re
from statistics import median
from typing import Any


def finite_number(value: Any) -> float | None:
    """Parse a numeric observation; absent/nonfinite values are not zero."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def inclusive_window(start_date: str, calendar_days: int) -> dict:
    start = date.fromisoformat(str(start_date))
    days = int(calendar_days)
    if isinstance(calendar_days, bool) or days < 1 or days != float(calendar_days):
        raise ValueError("calendar_days must be a positive whole number")
    return {"start_date": start.isoformat(),
            "end_date": (start + timedelta(days=days - 1)).isoformat(),
            "calendar_days": days, "inclusive": True}


def clean_observations(rows: list[dict]) -> tuple[list[dict], dict]:
    """Keep finite net returns; separately retain available excursions.

    Duplicate years are excluded entirely rather than arbitrarily choosing
    one conflicting row or inflating the sample with duplicate observations.
    """
    parsed = []
    rejected = 0
    for row in rows:
        if not isinstance(row, dict):
            rejected += 1
            continue
        year = finite_number(row.get("year"))
        net = finite_number(row.get("net"))
        if year is None or not year.is_integer() or not 1 <= year <= 9999 or net is None:
            rejected += 1
            continue
        parsed.append({"year": int(year), "net": net,
                       "mfe": finite_number(row.get("mfe")),
                       "mae": finite_number(row.get("mae"))})
    counts = Counter(row["year"] for row in parsed)
    duplicates = sorted(year for year, count in counts.items() if count > 1)
    valid = sorted((r for r in parsed if counts[r["year"]] == 1), key=lambda r: r["year"])
    return valid, {"rejected_rows": rejected, "duplicate_years": duplicates,
                   "missing_mfe": sum(r["mfe"] is None for r in valid),
                   "missing_mae": sum(r["mae"] is None for r in valid)}


def round_percent(value) -> float:
    """Round decimal percentage facts half-up, avoiding binary-float tie drift."""
    return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def rounded_median(values: list[float]) -> float | None:
    return round_percent(median([Decimal(str(v)) for v in values])) if values else None


def rounded_mean(values: list[float]) -> float | None:
    return round_percent(sum(Decimal(str(v)) for v in values) / len(values)) if values else None


_median = rounded_median


def summarize_returns(rows: list[dict]) -> dict:
    years = [r["year"] for r in rows]
    nets = [r["net"] for r in rows]
    best = max(rows, key=lambda r: r['net']) if rows else {}
    worst = min(rows, key=lambda r: r['net']) if rows else {}
    return {"n": len(rows), "years": years,
            "first_year": min(years) if years else None,
            "last_year": max(years) if years else None,
            "up_years": sum(n > 0 for n in nets),
            "down_years": sum(n < 0 for n in nets),
            "flat_years": sum(n == 0 for n in nets),
            "up_rate_pct": round_percent(Decimal(100 * sum(n > 0 for n in nets)) / len(nets)) if nets else None,
            "down_rate_pct": round_percent(Decimal(100 * sum(n < 0 for n in nets)) / len(nets)) if nets else None,
            "median_net": _median(nets),
            "avg_net": rounded_mean(nets),
            "best_year": best.get('year'), "best_net": best.get('net'),
            "worst_year": worst.get('year'), "worst_net": worst.get('net')}


def build_cell_evidence(cell: dict) -> dict:
    """Canonical evidence shared by planning, writing and deterministic gates."""
    rows, quality = clean_observations(cell.get("per_year") or [])
    window = inclusive_window(cell["anchor_date"], cell["days"])
    summary = summarize_returns(rows)
    years = summary["years"]
    code = str(cell.get("years", ""))
    phase = re.fullmatch(r"pe([0-3])-\d+", code.lower())
    phase_names = {"0": "presidential election", "1": "post-election",
                   "2": "midterm election", "3": "pre-election"}
    consecutive = bool(years) and years == list(range(years[0], years[-1] + 1))
    descriptor = (phase_names[phase.group(1)] + " observations" if phase else
                  "consecutive annual observations" if consecutive else "annual observations")
    label = (f"{len(rows)} {descriptor} spanning {years[0]}–{years[-1]}"
             if rows else "no usable historical observations")
    cohort = {"years_code": code, "years": years, "n": len(rows),
              "first_year": summary["first_year"], "last_year": summary["last_year"],
              "calendar_span_years": years[-1] - years[0] + 1 if years else 0,
              "is_consecutive": consecutive, "label": label}

    # Median(MFE - net) preserves the within-year relationship. The two
    # separate medians generally do not describe the same observation.
    paired = [r for r in rows if r["mfe"] is not None and r["mfe"] >= r["net"]]
    quality['invalid_giveback_pairs'] = sum(r['mfe'] is not None and r['mfe'] < r['net'] for r in rows)
    givebacks = [Decimal(str(r["mfe"])) - Decimal(str(r["net"])) for r in paired]
    giveback = {"median_pp": _median(givebacks), "n": len(paired),
                "years": [r["year"] for r in paired],
                "definition": "Median of each observation's highest return minus its ending return; "
                              "percentage points of entry price, not percentage decline from the peak."}
    mfe = [r["mfe"] for r in rows if r["mfe"] is not None]
    mae = [r["mae"] for r in rows if r["mae"] is not None]
    risk = {"median_favorable_from_entry": {"value_pct": _median(mfe), "n": len(mfe)},
            "median_adverse_from_entry": {"value_pct": _median(mae), "n": len(mae)},
            "worst_adverse_from_entry": {"value_pct": min(mae) if mae else None,
                                         "n": len(mae)},
            "extrema_timing_known": False,
            "definition": "MFE and MAE measure movement from entry; extrema do not establish "
                          "when the high or low occurred or peak-to-trough drawdown."}

    # Compare disjoint observations within the SAME window and cohort. A
    # PE cohort's recent five observations can span two decades, not five years.
    comparison = None
    if len(rows) >= 10:
        comparison = {"recent": summarize_returns(rows[-5:]),
                      "earlier": summarize_returns(rows[:-5]), "overlap": False,
                      "definition": "Most recent five sampled observations versus all earlier "
                                    "sampled observations; descriptive, not an independent validation."}
    sensitivity = None
    if len(rows) >= 3:
        extreme = max(rows, key=lambda r: abs(r["net"]))
        remaining = [r for r in rows if r["year"] != extreme["year"]]
        sensitivity = {"excluded_year": extreme["year"], "excluded_net": extreme["net"],
                       "without_largest_absolute_move": summarize_returns(remaining),
                       "definition": "Descriptive sensitivity to removing the largest absolute "
                                     "ending return; not a validated trading result."}
    return {"schema_version": 1, "window": window, "cohort": cohort,
            "returns": summary, "giveback": giveback, "risk": risk,
            "recent_vs_earlier": comparison, "sensitivity": sensitivity,
            "quality": quality,
            "scope": "Selected historical window; gross returns before costs. Does not establish "
                     "future probability, causality, or outperformance of a matched benchmark."}
