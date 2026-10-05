# Publication continuity qualification

Status: Dev implementation in progress; production retains the October 5 verified release. Owner: Codex SMN publication session 01a0fcf4-37f1-7d33-bced-ac46999fd9fa. Canonical claim and authorization: TradeWave TW-BUG-0027. This policy supersedes the historical all-six hold only when explicitly enabled.

## Reader behavior

Research begins at 05:30 America/New_York. Publish the full edition when ready. At the configured 07:00 target, expose reviewed available subjects and clearly list pending coverage. If none are ready, show a dated status notice and retain prior articles with their original dates. Never label a subset complete or a status notice as fresh research.

Keep the frozen expected selection unchanged. Subject receipts remain bound to factual evidence, engine output and independent review. Presentation fallback has its own explicit deterministic proof; it must never counterfeit a successful model review or alter financial content. An unsupported claim requires new validated content or withholding the subject.

Recovery continues with bounded backoff and the existing cumulative allowance. Budget exhaustion and missing authentication remain visible needs-attention conditions; they do not spend extra jobs or remove already published coverage. Retain failed attempts and successful evidence. Publish repaired subjects through idempotent revisions with stable article URLs and preserved rollback history.

Newsletter eligibility initially remains complete-edition-only. A verified partial revision is not newsletter eligibility. Existing one-campaign-per-date protection remains. Job completion proves neither provider acceptance nor delivery.

## Ownership and interfaces

- Publication worker: package, installer, staged/live validation and immutable revision adapter `publication_continuity.publish_available`.
- Scheduling worker: deadline tick, resumable controller, bounded retries and newsletter completeness checks.
- Presentation worker: separately proved deterministic readable fallback, substantive review separation.
- Integration owner: compatibility review, observer freshness, regression runs, Dev activation and browser qualification.
- Existing independent verifier: acceptance review and external evidence; no competing writes.

## Required acceptance

Use retained evidence and mocked provider calls; no new model or image generation for qualification.

1. A held subject permits qualified available subjects to publish with truthful pending coverage.
2. No qualified subject produces a dated honest notice; yesterday's articles keep their dates.
3. Wrong selection, changed source/review bytes, altered engine values and unsupported claims are rejected.
4. Optional presentation failure can use an independently bound readable fallback; failed original proof remains intact.
5. Deadline publication is not blocked by the research controller's long-running lock.
6. Budget exhaustion starts no model job. Transient recovery has backoff and preserves successful receipts.
7. Partial-to-complete revisions are monotonic and repeat calls are idempotent; newsletter sends once only after completeness.
8. Interrupted activation and failed verification retain evidence and restore the previous verified revision.
9. Real Linux/browser rehearsal covers notice, partial and complete rendering on desktop/mobile. No synthetic research is published as genuine fresh production coverage.
10. Observer rechecks public state at least every five minutes. Missing delivery credentials/recipients remain visible and incidents remain retryable after provisioning.

## Existing incident regressions

Reuse `tests/test_editorial_gate.py` for source-bound quote markers, reversed quote evidence, exact numerics, style advisories and last-recorded-price anchor versus future-price claims. Reuse `tests/test_smn_primary_sources.py` for padded dates, genuine wrong dates, source timeout caching and bounded fetch retry. Do not rerun writing to repair these representation/transport failures.

`tests/test_smn_operational_alerts.py` covers freshness-cache expiry, unavailable transport, recipient-specific retries and stable delivery identity. Installing the observer, configuring recipients/thresholds and proving actual transport delivery remain distinct operational prerequisites; passing mocked transport tests does not establish them.

## Deployment boundary

Integrate only task-owned commits, run focused Linux checks, acquire the shared Dev activation lock, preserve pointer/catalog/pins and rollback evidence, activate the exact candidate and verify actual rendered behavior. Production requires a separately scoped release of the qualified policy. Do not infer production activation, paid budget increases or credential provisioning from this Dev qualification.
