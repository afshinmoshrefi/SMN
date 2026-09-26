# SMN daily edition script (Dev)

`smn_daily.py` processes the day's six production subjects in a fixed order.
Passing articles can publish while an article that fails a quality gate stays held.
No AI agent directs the run. Only primary-source discovery has web tools; other
model jobs receive bounded evidence or attached images and have no tools.

```
python smn_daily.py --root /var/lib/tradewave/smn-daily/YYYY-MM-DD --date YYYY-MM-DD --publish
```

| Step | Who |
|---|---|
| Capture production picks and TradeWave studies (read only) | code |
| Discover fresh primary sources beyond production's news | `research` model with web tools; code fetches and records pages |
| Research brief + chart data | `research` model; code checks URLs, quotes, numbers, signs and primary-source citations |
| Write article | `write` model (the only high-end step) |
| Word caps, schema, TradeWave values | code; one `write` repair if they fail |
| Independent review | `review` model; one repair + re-review if it fails |
| Layout (desktop/mobile) | code (Playwright) |
| Screenshot check, hero text check (report only) | `visual` / `hero_check` model |
| Publish to primary Dev, live check, landing check | code + `visual` model |

**Switch providers:** use `--profile claude` (default) or `--profile chatgpt`.
The ChatGPT profile uses Astra at high effort for writing, Sol at medium effort
for research and review, and Luna at low effort for image checks. `--models PATH`
selects a custom role file instead. Resume each edition with the same profile
or custom settings; the saved roles prevent an unnoticed mid-edition change.
Each article's `generation` metadata records who wrote and reviewed it.

**Resume:** rerun the same command. Finished steps are skipped from files in
the root. A problem that bounded retries cannot fix writes `HOLD.json` (exit 2)
with the step and reason. Budget: `--max-jobs` (default 40) counts every model
job, including research, visual checks and retries.

**Persistent provider selection:** `python smn_daily_control.py profile chatgpt`
or `python smn_daily_control.py profile claude`. Omitting the provider displays
the selection. `python smn_daily_control.py run` uses the New York date, publishes
to Dev, and refuses overlapping or already completed runs. Selection affects new
editions; an incomplete edition resumes its recorded profile. Settings and
receipts live under `/var/lib/tradewave/smn-daily` (`--state-root` overrides it).
Use `run --no-publish` for a private verification run. Both CLIs must have their
own supported subscription login on the executing host. Set `SMN_CODEX` and
`SMN_CLAUDE` to the approved CLI executables; never copy authentication files.

Primary evidence is stored in `primary/SYM.txt` with a hash-bound receipt. Source
discovery searches beyond the production bundle, and research must cite two
captured primary pages. Missing, changed or unusable evidence holds the run.
Historical editions with already approved `sources.json` reuse it on resume.

Hero images are reused from production. `hero-check.json` reports misspelled
text but does not block; regeneration is a later step (TW-TASK-0009).
