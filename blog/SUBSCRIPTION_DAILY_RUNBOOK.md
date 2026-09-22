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
Preserve partial attempts and immutable receipts. `daily-state.json` and `.daily-lock`
are the checkpoint and concurrency boundary. A held status requires diagnosis,
not routine owner intervention. The owner authorizes bounded autonomous recovery
under the rules below; never restart writer jobs blindly.

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

## Cumulative homepage

The recovery site now serves its production wire-style homepage at `/`, rather
than redirecting to the latest six-article edition. Publication merges the new
entries into the retained `posts.json` catalog by article URL. It preserves all
earlier edition files and keeps older coverage accessible through `search.html`.
The home uses production's 14-day/50-article display limits; those limits never
delete catalog entries or articles. Both the homepage and archive are noindex.

`home-manifest.json` binds the generated catalog/home/search files and retained
article hashes to the source release. Live verification must exercise older
article discovery as well as the current six. A missing catalog with retained
articles requires an explicit hash-bound `archive-seed.json` migration; never
silently start an empty archive. The September21 migration is a presentation
replay of already approved articles, with no new model calls or study changes.

## Autonomous recovery - September 22 owner instruction

Publication continuity is the goal. Inspect the failure, repair recoverable
operational problems, and continue within this Dev-only authorization. Do not
ask the owner to approve routine recovery or regenerate good articles.

- SHA256 hexadecimal is case-insensitive. Accept valid 64-character upper,
  lower or mixed-case fingerprints only when the actual file bytes match.
  Write new proof records in lowercase; never approve a changed file by simply
  replacing its expected hash. Reinspect changed visual output.
- Validate all six reviews before creating the publication package. An empty
  package left before staging can be reused only without any stage, activation
  or completion record. Preserve nonempty/committed packages. For other partial
  publications create a fresh publication-only recovery directory referencing
  the original reviewed results and successful immutable job receipts.
- Retry read-only network/copy checks up to three times with short backoff.
  Before retrying a write whose outcome is uncertain, inspect remote receipts,
  locks and active pointers. Finish an already successful step rather than
  duplicating it. Never steal a live lock or change production.
- Reuse successful model receipts. Do not repeat writing/review for formatting,
  transport or packaging errors. Preserve the 12-new-job daily cap and no paid
  API fallback. Actual article revisions need newly bound checks/review.
- For source drift, integrate current main under the deployment policy, rerun
  affected checks and replay publication. Preserve failed evidence and record
  the new source and recovery relation. Roll back failed live activation before
  repair; verify restoration. Never publish failed evidence or bypass TradeWave
  calculation authority, exact study identity, actual image inspection or Dev
  host guards just to complete the day.
- Escalate only after bounded recovery cannot resolve the problem or a genuine
  authority/access/product decision is needed. Keep the last verified edition
  available and report the exact blocker. A missing six-subject production
  batch still waits quietly; never substitute a previous date.

After recovery, write a recovery resolution beside the original hold, and a
live_verified completion receipt in the canonical date directory referencing
the verified recovery artifact. Keep original hold/failed attempt evidence.
The next heartbeat checks completion first and must not regenerate that date.
