# Subscription daily worker - September 19, 2026 override

This is the short operational override to
[`SUBSCRIPTION_DEV_PUBLICATION.md`](SUBSCRIPTION_DEV_PUBLICATION.md).
Only the local Dev scheduler is authorized. Production remains unchanged: no
production writes, queue activation, email, distribution, staging, or paid API
fallback. Dev `.176` is the recovery target; `.180` remains offline.

## Schedule and boundaries

Run a local Windows Codex heartbeat at approximately 07:00 America/New_York.
It uses the saved ChatGPT subscription with Astra Extra High. The Windows host
must be awake and signed in; there is no paid OpenAI API fallback. The heartbeat
coordinates research and visual review. Python performs only the defined stages
below; it does not perform unattended research or vision judgment.

Use a fresh immutable directory per requested edition date, for example
`$Root = C:\...\smn-daily-worker-YYYYMMDD\editions\YYYY-MM-DD`.
Never reuse a partial attempt. `daily-state.json` and `.daily-lock` are the
checkpoint and concurrency boundary. A failure is held as
`failed_needs_review`; inspect and start an explicit new attempt rather than
retrying automatically.

## Capture and source preparation

Capture read-only production and engine evidence from the clean, current
`origin/main` source:

```powershell
$Blog = 'C:\...\smn-daily-worker-YYYYMMDD\blog'
python "$Blog\subscription_capture.py" production --root $Root --date $Date
python "$Blog\subscription_capture.py" engine --root $Root --date $Date
```

The requested date must have exactly six distinct published subjects. If fewer
than six exist, remain `waiting_for_production`; never substitute a previous
date. If a capture already exists, hashes must match. Do not overwrite or copy
stale news. Prepare a fresh `$Root\sources.json` from the prior schema at
`C:\Users\afshin\Documents\TradeWave Main Orchestrator\smn-review-20260917\engine-editorial\sources.json`:
the top-level object is keyed by symbol, with `company`, `angle`, `category`,
`question`, `brief`, and `sources`; each source has `id`, `title`, `url`,
`date`, `excerpt`, `max_derived_words`, and `excerpt_kind`. `chart` is required:
a two-item array `[chartSpecification, records]`, with source-backed values,
record IDs, units, reporting periods and source locators. `hero_alt` is required.
Verify primary sources; audit research is a lead, not independent verification.
Refresh every dated news/source entry for the requested day;
the prior file is a schema/template, never a stale news cache.

TradeWave's engine is the read-only source authority. Preserve its exact
strings, values, identities, dates, durations, directions, lookback strings,
and price-path/overlay output. Never recalculate, normalize, interpolate, or
repair engine mathematics.

## Daily controller

From the blog directory, run the bounded controller with the official Codex
CLI configuration and at most 12 newly created writer/reviewer jobs:

```powershell
python .\subscription_daily.py --root $Root --date $Date --codex $CodexExe --max-new-model-jobs 12 --source-commit $SourceCommit
```

The stages are `prepare`, `write`, `receive`, `review`, and `finalize` for
each of the six symbols. `subscription_writer` is the official CLI writer and
reviewer; preserve its immutable per-job receipts. Do not publish until all
six final review bindings and mechanical/source checks pass.

## Layout and visual review

After Python completes, run the browser layout checks for all six symbols and
inspect the resulting desktop/mobile images pixel-by-pixel:

```powershell
node .\subscription_layout.cjs $Root $Symbol1 $Symbol2 $Symbol3 $Symbol4 $Symbol5 $Symbol6
```

The script checks 1440x1050 and 390x844 layouts and writes `layout-checks.json`,
but `pixel_inspection_pending` is not approval. A human/heartbeat visual pass
must record the pixel decision before packaging.
For each result write `visual-checks.json` containing `passed: true`, the exact
`article_html_sha256`, and `inspected_images` mapping relative screenshot paths
to their SHA-256 hashes, only after actually viewing those captures. A failure
holds publication; code edits require a newly committed source and review.

## Dev-only package, activation, and finish

Use the clean current source repository and exact pinned commit:

```powershell
python .\subscription_dev_publish.py stage --root $Root --repo $Repo
python .\subscription_dev_publish.py activate --root $Root --repo $Repo --node $Node --playwright $Playwright
python .\subscription_dev_publish.py finish --root $Root --repo $Repo
```

`activate` stages and switches only the `.176` recovery Dev edition, then runs
`subscription_live.cjs` against `https://smn-dev.trxstat.com`; it requires the
main acknowledgement lock and rolls back on changed `origin/main` or failed
live verification. `finish` is allowed only after live desktop/mobile checks,
public hash/provenance checks, preserved prior edition checks, and unchanged
`origin/main`. Before `finish`, view both live landing screenshots and write
`live-landing-visual-checks.json` with `passed: true` and `inspected_images`
mapping their root-relative paths to SHA-256 hashes. Keep the activation window
short: rollback on a visual failure. A `dev-publication-receipt.json` with
`status: live_verified` means the date is complete; never regenerate it on a
heartbeat. Preserve package/stage records and inspect an interrupted operation
before resuming. Use explicit rollback for an interrupted activation:

```powershell
python .\subscription_dev_publish.py rollback --root $Root --repo $Repo
```

No command in this runbook changes TradeWave mathematics or production.
