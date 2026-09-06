# Private market and lineup policy

`market_policy.py` is a pure standard-library module. It imports no application configuration, provider, selector, publisher, or service. Nothing runs on import and no function performs I/O. Its outputs always carry `publishable:false` and private/inactive selection policy. No access, probability, scheduling, or publication control is activated.

## Entry points

* `instrument_key(instrument) -> str`: SHA-256 of compact sorted ASCII JSON containing exactly `resource_id`, `provider`, `exchange`, `symbol`, `series_id`. Values are stripped strings; absent/None values are empty strings. Case is preserved and no identity is inferred. A key can identify incomplete legacy metadata without certifying it. This matches the cohort-evidence key contract.
* `metadata_digest(instrument) -> str`: binds the five-field identity, `asset_class`, and complete `semantics` object. It excludes the provenance object itself.
* `instrument_from_audit(identity, audit, *, asset_class=None, semantics=None, provenance=None) -> dict`: copies allowlisted observed CSV metadata into `audit_observations`. It does not manufacture a quality gate, infer asset kind from a ticker/resource, or certify semantics from OHLC columns, a hash, or row count.
* `evaluate_market(instrument, history=None, *, as_of) -> dict`: seasonal measurement/history eligibility, reasons, identity, and provisional core preference. It does not calculate or certify the cohort sample.
* `evaluate_lineup(candidates, *, coverage=(), as_of, max_articles=6, selection_policy=None) -> dict`: selected private jobs, one decision per input candidate, all held material-news omissions, and explicit inactive policy. `as_of` is a timezone-aware datetime/ISO string or a date (midnight UTC). Supply the historical replay clock for archived evidence, not today's clock. The six-article value is a ceiling, not an obligation. An explicit policy must have `private:true` and cannot have `activated:true`.

The exact synthetic eligible equity input is `tests/fixtures/private_equity_candidate.json`. Evaluate it at `as_of='2026-08-21T12:00:00Z'`. Its source references and quality assertions are clearly marked fixtures and must not be used to certify actual market data.

## Trust boundary and metadata

The caller is the **trusted evidence adapter**, not an LLM's JSON output. Source retrieval/entailment, dataset quality, canonical event/question identity, and semantic novelty must already have been checked there. This module validates that those explicit records match, applies deterministic eligibility, and records decisions. It cannot authenticate an external source or determine financial semantics without that reviewed evidence. The metadata hash detects accidental cross-series reuse; it is not a signature and cannot turn untrusted assertions into proof.

Instrument identity requires the five fields above. `series_id` must distinguish provider series variants, including currency/return variants. `asset_class` is one of `equity`, `equity_index`, `etf`, `bond`, `rate`, `commodity`, `fx`, `crypto`, `volatility`. Permanent TradeWave resource IDs are preserved; resource names and ticker labels do not determine asset class.

Required `semantics` fields are `measurement`, `units`, `return_basis`, `adjustment`, `currency`, `calendar_id`, `session_model`, and `timezone`. A reviewed source manifest contains `method:'reviewed_source_manifest'`, `source_uri`, `evidence_id`, `reviewed_at` (aware ISO timestamp no later than `as_of`), and `metadata_sha256` matching `metadata_digest(instrument)`. An LLM-supplied `verified:true` is insufficient. Keep the actual cited code/provider source and review receipt available under the evidence ID.

| Measurement | Units | Return basis / additional metadata |
|---|---|---|
| `adjusted_price_return` | `percent_return` | Equity-only `provider_adjusted_price`; actual review batch uses `adjustment:'provider_adjusted_close_ratio'`. This is not a total-return certification. |
| `adjusted_price` | `percent_return` | `price_return` or `dividend_adjusted_return`; use only when the supplied source certifies that specific interpretation. |
| `price_index` | `percent_return` | `price_return`, plus `index_methodology`. |
| `total_return_index` / `fund_total_return` | `percent_return` | `total_return`; index methodology required for equity indices. |
| `bond_price` | `percent_return` | `price_return`, without implying coupon-inclusive investor return. |
| `fx_spot` | `percent_return` | `spot_change_excluding_carry`; `base_currency`, `quote_currency`, `close_convention`. |
| `crypto_spot` | `percent_return` | `spot_change`; daily UTC close, `session_model:'seven_day'`. |
| `futures_contract` | `percent_return` | `contract_price_change`; contract definition, settlement, multiplier, denominator method. |
| `continuous_futures` | `percent_return` | `continuous_series_change`; contract metadata plus roll rule and back-adjustment. |
| `yield_level`, `volatility_index`, `excess_return_index` | Appropriate declared units | Held for a specialized analysis path. They are not fed into generic seasonal-return interpretation. |

The private futures implementation supports only a reviewed `denominator_method:'positive_entry_price'`, checked positive historical values, and `history.denominator_checks_passed:true`. That upstream check must hold unsupported zero, negative, or unstable near-zero bases; this module does not manufacture a financial threshold. It describes a series/contract price change, not financed, rolled trade P&L. More sophisticated denominator methods remain unimplemented. ETF eligibility additionally requires `fund_structure:'unlevered_plain'`. A crypto future uses its exchange calendar; seven-day spot treatment does not automatically apply to it.

The `history` record contains:

```
instrument_key, calendar_id,
last_observation_date, expected_latest_date, calendar_checked_as_of,
dataset_sha256, evidence_ids,
unexplained_gaps: [], invalid_values: [], nonpositive_values: false,
window_checks_passed: true
```

Date fields use ISO calendar dates. `calendar_checked_as_of` must equal the replay/current UTC date supplied to the policy. For exchange instruments, the upstream calendar review supplies the expected completed session, including holidays and close timing; the latest stored date must match it. For supported UTC crypto spot, expected latest is deterministically yesterday, including weekends. A missing status is unknown, never an implicit pass. These checks do not calculate a sample from first/last CSV dates.

## Candidate and gate contract

Every candidate supplies `candidate_id`, `kind` (`seasonal` or `news`), instrument metadata where applicable, `question:{question_id,text,answer_evidence_ids}`, explicit `exposure_ids`, and `priority:{consequence,timeliness,relevance,completeness,reason,evidence_ids}`. The four editorial dimensions are integers 0-3. They are review inputs with cited reasons, not statistical reliability or predicted engagement. Material verified news orders before ordinary candidates; equities/plain ETFs receive only a final tie preference. No market slots are reserved.

A seasonal candidate supplies reviewed `history` and `cohort_gate:{eligible:true,issues:[],evidence_ids:[...]}`. The adapter derives that gate from the cohort-evidence result. `evaluate_lineup` calls `evaluate_market` itself and requires **both** gates. It ignores any supplied `seasonal_eligible` flag. Answer and priority evidence IDs must belong to the passed cohort gate.

A news candidate supplies:

```
event: {
  event_id, development_id, occurred_at,
  fact_ids: [...], evidence_ids: [...],
  supersedes_development_ids: [...]  # optional explicit reviewed update relation
}
news_gate: {
  eligible: true, issues: [], valid_until,
  event_id, development_id, occurred_at, fact_ids,
  evidence_ids: [...]
}
```

The gate binds the exact event/development/date/fact IDs, and event/answer/priority evidence IDs must be contained in its evidence set. The event cannot be future-dated and the gate must remain valid at `as_of`. The upstream news validator determines source freshness and validity; this module does not invent a separate news age cutoff. A source-page refresh alone must never receive a new event date. `material_news:true` signals a reviewed consequential event, not a model forecast. News is independently eligible even if the historical claim is held. A macro story may omit the instrument; if an instrument is supplied, its full identity is required.

`fact_ids`, `event_id`, and `question_id` are canonical evidence-review identities, not free labels regenerated by each writer. Different keywords, shorter extracts, or a new hash by themselves do not prove a new fact. The adapter must retain stable identities across extraction changes and require a documented semantic novelty review for new facts.

## Coverage and decisions

Each selected job includes a `coverage_record` suitable for a private saved ledger. Preserve its `event_id`, `development_id`, `occurred_at`, `fact_ids`, `evidence_ids`, `question_id`, `answer_evidence_ids`, `exposure_ids`, and `instrument_key`; add the private `article_id` when created. Pass the current record for each article/event-question, with older revisions retained separately for audit. No state is persisted by this module. Budgets are 500 candidates and 2,000 current coverage records per evaluation.

* Same event and question: consolidate unless new fact AND new evidence IDs, explicit supersession, and a non-older event time support `update_draft`. Legacy same-event coverage lacking prior fact metadata is conservatively consolidated.
* Same event, renamed question, same answer evidence: consolidate. A genuinely separate question needs distinct reviewed answer evidence.
* Same exposure and question: consolidate repeated seasonal material. Independently verified new news events can qualify even when their instrument/exposure appeared recently.
* A newer eligible development replaces an earlier same-event job within the current private slate rather than consuming a second slot.
* Every selection/consolidation/hold records reasons. Material news held by evidence or the daily ceiling appears in `omitted_material_events` for review. Consolidation records the target instead of presenting it as unexplained absence.

No reader-facing probabilities, politician/party/policy scores, raw price output, publisher hooks, schedules, or access changes are added. Calendar cohorts remain a separate descriptive evidence policy. Tests use frozen synthetic manifests and no network or paid API.
