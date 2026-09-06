# Private fixed-cohort module contract

`cohort_policy.py` is pure Python. It imports only stdlib and `article_evidence`; it does not fetch data, load configuration, choose a horizon, call a model or publish. This module implements proposed editorial defaults for a private pilot, not a validated trading method or optimal thresholds.

```python
evidence = build_selection_evidence(
    card=None,                         # optional legacy story + auxiliary cells
    cells=matrix_cells_or_raw_responses,
    observations=None,                 # optional common panel rows: year/net/mfe/mae
    instrument={"resource_id": "2", "symbol": "JPM", ...},
    anchor_date="2026-08-21", days=90, # fixed by caller before comparisons
    as_of="2026-08-21",               # decision cutoff never exceeds entry date
    coverage=None, policy=None,
)
assert validate_selection_evidence(evidence)["ok"]
primary = baseline_cell(evidence)       # None when the annual lead must hold
contexts = comparison_cells(evidence)   # explicit subsets for fact licensing only
```

`cells` accepts public cell dictionaries/dataclasses (`resource_id`, `symbol`, `anchor_date`, `days`, string `years`, `per_year`) and raw ChartData4 responses with their saved `request`. Raw prices and archived derived claims are omitted. Multiple horizons may be supplied: only the caller's fixed horizon is used. Wrong resource, symbol or anchor, conflicting observations and internal duplicates hold. The instrument key matches `market_policy`'s SHA-256 of compact, sorted ASCII JSON for resource/provider/exchange/symbol/series_id; empty legacy identity fields remain empty. Market certification remains the separate market policy's responsibility.

The baseline is the declared twenty-year calendar span ending with the latest completed annual window, never the best available lookback. Outcomes learned after entry cannot enter it. A matching `years="20"` or longer annual request establishes that the annual span was checked. Ten-year/PE cells alone do not. A raw common panel can declare coverage with `coverage={start_year, end_year, source_ref}`. Missing years are not replaced with older observations.

Short history requires `series_start_year` plus `series_start_evidence_ref`, or per-year `missing_year_reasons={"2017": {"reason": "source_data_unavailable", "evidence_ref": "..."}}`. Supported reasons are `before_series_start`, `source_data_unavailable`, `corporate_action_unresolved`, `contract_unavailable`, `calendar_unavailable`. Unexplained baseline gaps hold. A cycle request not supplied/covered also holds. A completed cycle request with fewer than six observations yields `insufficient_context`; a valid annual lead can proceed while its limitation remains visible. Unexplained internal cycle gaps hold.

The cycle targets ten matching phase observations, derived from the entry year's calendar phase. It cannot substitute a different phase or shorter, stronger slice. Full-history versus annual-baseline comparisons retain overlap; same-span PE/non-PE and recent/earlier comparisons use disjoint rows. The complete annual comparator over the full cycle-history span remains `unavailable` when the supplied annual panel cannot establish it. Unioning twenty annual plus ten PE observations does not manufacture forty annual observations.

## Output paths

- `identity`, `window`, `as_of`, `decision_cutoff`, `last_completed_year` fix the comparison identity.
- `annual_panel.rows`: canonical merged percent-only `year/net/mfe/mae` rows; optional excursions remain null. `source_ids_by_year`, `confirmed_annual_spans`, `excluded` record provenance and excluded windows.
- `baseline`: `years_code="20"`, requested years, actual `summary`, `per_year`, coverage and missing-year reasons.
- `recent["5"]` and `recent["10"]`: disjoint `recent` and `preceding` summaries, requested recent calendar years, `comparison`, `overlap_n=0`. Gaps never shift the calendar boundary.
- `cycle.full.summary`, `cycle.within_baseline.summary`, `cycle.noncycle_within_baseline.summary`, `cycle.earlier.summary`: actual observations, counts and median directions.
- `cycle.full_span_comparator`: availability, required period, missing annual years and complementary summaries only when the panel supports them.
- `overlap`: exact shared years/n, distinct union size, disjoint comparator overlap.
- `comparisons`: full-cycle/baseline, same-span cycle/noncycle and recent/earlier-cycle relations.
- `classification`: `consistent`, `era_sensitive`, `genuine_contrast`, `mixed`, `insufficient_context`, `insufficient`, or `incomparable`. “Genuine contrast” means an observed disjoint same-span median-sign contrast, not causality, statistical significance or validated prediction. Annual recency contrasts can make the overall result `era_sensitive` even when the cycle views agree.
- `pilot_eligibility`: `eligible`, `annual_lead`, `cycle_lead`, provisional n floors (8/6), `provisional=True`, `optimal_thresholds_validated=False`. Overall eligibility follows annual-lead suitability; the parent still enforces market/reader/evidence checks.
- `issues`: deterministic codes/severity/details; severity `hold` blocks the private annual lead.
- `writer_brief`: `classification`, `proposed_reader_question`, `required_qualifications=[{id,text,evidence_refs}]`, `permitted_summary_refs`, `forbidden_inferences`, `hold_reasons`. References are JSON pointers within this evidence object.
- `replay_input`: sanitized percent-only input record. `validate_selection_evidence` reruns it and checks exact equality; this validates internal calculations, not the external truth of a source.

Summary fields are `n`, `years`, first/last year, up/down/flat counts and rates, exact decimal-based median/mean, a two-decimal half-up median display, median direction, strict majority and best/worst outcome. A strict majority requires more than half of all n, with flat returns still in n. Missing statistics remain null.

## Cells for parent integration

`baseline_cell` returns fixed `years="20"`, `mode="cons"`, same instrument/window, actual n/counts, normalized nullable per-year excursions, `stats_raw={}`, canonical `evidence`, MFE/MAE medians, labels, and `selection_evidence_ref="/baseline/summary"`. It returns `None` when validation or annual eligibility fails. No fallback occurs.

`comparison_cells` returns available full-cycle/recent-cycle/noncycle/older-cycle and disjoint recent annual subsets. Every cell carries its exact `selection_evidence_ref` and `role="comparison"`, with `eligible=False`; they cannot become alternative selected leads. Subset lookback labels are descriptive strings, not new data requests.

Both cell helpers use shared `article_evidence` display statistics so deterministic article gates can recompute them. This change centralizes decimal half-up mean/median/percentage display rounding in `article_evidence` and uses those helpers in `angle_engine.derive_cell`; WMT's exact 2.225% median therefore displays as 2.23% on both surfaces. Paired giveback subtraction also uses decimal operands before rounding. `selection_evidence` retains exact decimal-based medians as well as their displayed values.

JPM/WMT frozen examples are era-sensitive. Ford retains a descriptive same-span contrast and has a flat year. JNJ's cycle views are consistent, but its recent five annual observations have median −3.40% versus +4.01% for preceding fifteen; its overall classification preserves that recency sensitivity.

All outputs remain `mode="private_preview"`, `publishable=False`. No access enforcement or calibrated-probability feature is included.
