# Lean SMN Dev daily edition

`lean_daily.py` replaces the Astra Extra High heartbeat agent. A plain scheduled
script now runs the day. Models run only where judgment or writing is needed:

| Step | Who | Model |
|---|---|---|
| Capture production subjects and TradeWave engine results | Python | none |
| Research: find current sources, commission, business chart | one job per subject, web search | Terra high |
| Check research: excerpts on the live page, chart values in excerpts, units, dates | Python | none |
| Write the article | one job per subject | Astra high |
| Structure, source word caps, numbers-in-evidence check | Python | none |
| Editorial review (7 checks) | one job per subject | Terra medium |
| One repair if the review or code checks fail, then a second review | only when needed | Astra high, Terra medium |
| Layout at desktop and mobile: contract, overflow, clipped text, stretched images, overlaps | Playwright | none |
| Stage, activate, live verification on SMN Dev (`--publish`) | existing publisher | none |

Model and effort settings are `WRITER`, `REVIEWER`, `REPAIR` in
`engine_edition_workflow.py` and `MODEL, EFFORT` in `lean_research.py`.
TradeWave calculation authority, exact study identity, immutable job receipts,
the no-API-fallback rule and the Dev-only publication guards are unchanged.

## Run

```powershell
$Date = '2026-09-23'
$Root = "C:\...\smn-lean-daily\editions\$Date"
python .\lean_daily.py --root $Root --date $Date --codex $CodexExe --repo $Repo --publish
```

Omit `--publish` to stop after local layout checks (`ready_to_publish`).
A rerun resumes: finished captures, research, job receipts and reviews are
reused, never regenerated. A date with `dev-publication-receipt.json` is done.

Exit 0 prints the status. Exit 2 writes `HOLD.json` with the step and reason;
nothing is retried automatically. Inspect it before rerunning. Research
problems are listed per subject in `research/`; review failures and code
checks in `results/SYMBOL/repair-issues.json`.

`usage-summary.json` gives tokens per model, effort and stage, and the account
meter before the first job and after the last.

## Schedule

Windows Task Scheduler, daily at 07:00 America/New_York, "run whether user is
logged on or not", working directory = this `blog` folder. Remove the Codex
heartbeat automation once a lean day has passed side-by-side review.

## What code does not see

The layout checks cannot see text clipped inside a chart image. The chart
renderer is deterministic, so this changes only when chart code changes; look
at the saved `qa-*-*.png` screenshots after any chart code change.
