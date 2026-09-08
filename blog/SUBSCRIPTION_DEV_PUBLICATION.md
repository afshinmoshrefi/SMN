# Subscription Edition: Dev Publication

The September 8, 2026 owner request authorizes recreating the six production
subjects through the saved ChatGPT subscription and publishing those articles
on SMN Dev only. This is a manually initiated edition, not permission to start
a scheduler, change production, or enable a paid API fallback.

The source continues accepted private SMN commit `672eeb0`. The older SMN main
and TW2 SMN mirror are not the authority for the private editorial generator.
The live Dev application is preserved; only reviewed static article artifacts
and the relevant Dev catalog/homepage entries are installed.

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
