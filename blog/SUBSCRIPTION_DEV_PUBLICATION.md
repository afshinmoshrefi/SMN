# Subscription Edition: Dev Publication

The September 8, 2026 owner request authorizes recreating the six production
subjects through the saved ChatGPT subscription and publishing those articles
on SMN Dev only. This is a manually initiated edition, not permission to start
a scheduler, change production, or enable a paid API fallback.

The source continues accepted private SMN commit `672eeb0`. The older SMN main
and TW2 SMN mirror are not the authority for the private editorial generator.
The live Dev application is preserved; only reviewed static article artifacts
and the relevant Dev catalog/homepage entries are installed.

## September 8 Price-Path Format Update

The owner subsequently approved adding the original SMN price/seasonal-path
concept to all six Dev articles, explicitly requiring each article's own
analysis years. This revision continues published source `d5f1f8b` in
`codex/smn-price-path-20260908`; it preserves the six accepted article JSON
files and existing assets. Newly rendered charts use the selected 2006-2025
annual samples, not the production articles' different election-cycle samples.

`seasonal_price_path.derive` consumes the retained daily CSV and its independent
history audit. It first reproduces net/MFE/MAE for every selected annual window.
All years must match; missing years cannot shrink the sample or change the
lookback. The pointwise median is computed from individual normalized close
paths at calendar offsets from each historical window's start, using the last
recorded close on/before each date. The current closing value anchors the
illustration. No average/median is guessed from a production curve; no annual
extrema, invented paths, holiday-as-weekday assumption or cycle-drift extension
supplies daily values. Five exchange histories retain their passed full-session
audits. GC remains an explicit dev-only reference-series exception with its
independent calendar hold unchanged.

The plotted horizon is **60 calendar days including the anchor**, separate from
each article's complete analysis window. The stock/ETF/index snapshots end
September 4; GC's retained reference series ends September 7. Captions date
those anchors, label the exact sample, distinguish historical illustration
from forecast and retain the GC qualification. Expanded methodology explains
the sampled years, date alignment, last-observation convention and median.
Only a derived percentage path is exported as CSV, not the raw daily dataset.

Adapters for this format set `seasonal_contract.price_path_required=True` and
supply `source.price_path_input={csv_bytes, audit, horizon_days, reference_only}`
to `seasonal_edition.prepare`. Preparing/rendering a required path without data
fails. The renderer inserts it after the opening while preserving the native
annual/range charts and editorial visual. Desktop is one connected plot; mobile
uses two readable panels containing the same data. `subscription_publication`
requires the independent review's path evidence hash and verifies the displayed
figure, desktop/mobile asset hashes and percentage export before packaging.

Revision evidence, reviews, tests and local/live screenshots are under
`smn-review-20260908/subscription-price-path/` in the owner's orchestrator
workspace. Numeric drawing uses Python; subscription reviews use the existing
official Codex worker. There is no new paid OpenAI API or image-generation call.
Only the Dev static edition is updated; the live generator checkout and the
TradeWave viewer's separately recorded endpoint conventions are unchanged.

`subscription_writer` owns official Codex execution with ChatGPT authentication,
Astra Extra High, isolated API-key-free child environment, immutable prompt,
schema, evidence and output hashes, and serialized job receipts. The observed
September 8 source adapter lives in the dated local review artifacts. It freezes
production identities, primary-source readings and TradeWave daily history;
it never supplies the old article prose to the new writer. Selected windows are
preserved. Annual charts use a fixed 20-window baseline; overlapping midterm
history is a separate comparison. Deterministic code draws all numeric charts.

`subscription_publication.package` accepts articles only after mechanical,
independent editorial and rendered-page checks bound to their exact outputs.
It exports public HTML/assets and percent-only history evidence. Prompts,
auth files, account metadata, job logs, raw provider prices and private source
manifests are not exported. A six-article landing page links each recreated
article to its production original. New pages carry noindex/nofollow.

`install_subscription_dev.py` refuses every host except SMN at 192.168.1.180,
and every origin/root except https://smn-dev.trxstat.com and /var/www/smn.
An atomic dev-activation lock protects the brief write. Every changed file is
backed up, then static content, posts/search/suggestion catalogs and the
homepage edition section are replaced atomically per file. Exceptions restore
the affected files. The installer does not import the old publisher, invoke
Redis, email/newsletter, IndexNow, RSS distribution, model APIs, generators,
schedulers or service restarts. Original article files remain intact.
Live desktop/mobile content assertions are required after installation; a
successful file copy is not the final delivery check.

Data-specific safeguards:

- A first annual window that begins before retained source history is explicitly
  unavailable, never partly calculated or treated as a later data gap. Missing
  sessions after the source starts still hold the calendar audit.
- SPX is labelled price-index change excluding dividends, not adjusted equity
  total return. Portfolio weights have unsigned percent labels.
- GC's independent historical-session audit is held. An explicitly identified
  Dev reference-series illustration can describe available recorded price
  changes only with visible session/roll-method qualifications. It is not a
  validated futures strategy or futures-account return. The held audit is
  retained and cannot be turned into a pass; production release remains blocked.
  Native GC chart labels and export descriptions retain this reference-series
  qualification; the mobile axis identifies reference-price changes.
- The linked viewer receives the same resource, symbol, selected date, day count
  and lookback. This edition's calculation uses closes inside inclusive dates.
  The existing ChartData4 endpoint can advance its end to the next recorded date
  and adjusts leap crossings. That convention difference is recorded for future
  integration; this article task does not change the TradeWave engine.
- Markdown links emitted inside paragraph strings are rendered only if their
  destination exactly matches the source-bound TradeWave study. Other supplied
  links fail; raw Markdown/long encoded URLs are not exposed in reader copy.

This does not qualify the application for staging or production. A future
automated publishing integration must preserve these content/source bindings,
add an account-wide queue owner, and resolve GC metadata and viewer endpoint
conventions before representing the batch as a fully qualified production run.
