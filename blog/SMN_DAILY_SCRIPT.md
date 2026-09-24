# SMN daily edition script (Dev)

`smn_daily.py` makes one six-article Dev edition in a fixed order. No AI agent
directs the run; each AI call is one short, tool-less job with a receipt.

```
python smn_daily.py --root /var/lib/tradewave/smn-daily/YYYY-MM-DD --date YYYY-MM-DD --publish
```

| Step | Who |
|---|---|
| Capture production picks and TradeWave studies (read only) | code |
| Research brief + chart data from production's saved news | `research` model; code checks URLs, quotes, numbers, signs |
| Write article | `write` model (the only high-end step) |
| Word caps, schema, TradeWave values | code; one `write` repair if they fail |
| Independent review | `review` model; one repair + re-review if it fails |
| Layout (desktop/mobile) | code (Playwright) |
| Screenshot check, hero text check (report only) | `visual` / `hero_check` model |
| Publish to primary Dev, live check, landing check | code + `visual` model |

**Switch models:** edit one line in `smn_models.json` (provider `claude` or
`codex`). Each article's `generation` metadata records who wrote and reviewed it.

**Resume:** rerun the same command. Finished steps are skipped from files in
the root. A problem that bounded retries cannot fix writes `HOLD.json` (exit 2)
with the step and reason. Budget: `--max-jobs` (default 30) counts every model
job, including research, visual checks and retries.

Hero images are reused from production. `hero-check.json` reports misspelled
text but does not block; regeneration is a later step (TW-TASK-0009).
