# Subscription Edition: Engine-backed Dev Publication

## Owner-approved projection design — September 12, 2026

Afshin clarified that production is correct: the projection is a normalized
section of the TradeWave trend chart superimposed on the actual price chart.
Preserve that behavior. It is not a newly calculated mean/median return study.
The earlier agent diagnosis of a "projection units defect" and resulting
actual-return replacement were based on a mistaken interpretation of the
product. They are retracted, not pending fixes or an approved roadmap.

TradeWave Dev and main were restored at
`fe0c271b4f6184c5f6069d585a49879498697230`. Its application sources match the
pre-experiment `ffb5137cd4111fe0063e833c782e96e5de634c50` exactly; the original
frontend artifact was reused only after proving its React source tree matches.
A live browser verified that the anchor plus 60 overlay points equal the
existing TradeWave helper output. Production was never modified.

The six original September 10 articles' authoritative exports, study identity,
statistics, and price-path values are restored. Rejected actual-return outputs
are archived locally and must not enter this edition. SMN consumes the existing
TradeWave helper's points unchanged. The remaining editorial change describes
the illustration accurately as a normalized trend overlay. Explicit copyedit
receipts preserve the subscription-written articles, original figures/data,
and exact before/after wording. A fresh independent review binds the final
caption and article; primary-observation and price-path CSV hashes must remain
identical to the pre-experiment edition.

Original production SMN also has a normalized-overlay implementation in
`blog/article_images.py::_build_projection`. This is not evidence that its
seasonal statistics are wrong. Do not replace the TradeWave methodology or
start a production migration while completing this Dev comparison edition.
The new subscription workflow calls the existing TradeWave owner instead of
building another SMN projection calculator.

Dev publication still requires all six final reviews, desktop/mobile pixel
checks, the committed source package and live verification. A private source
copy is not deployment. The existing `.176` recovery host is the Dev target;
the September 10 edition remains dated September 10. No production, scheduler,
email, distribution or paid OpenAI API fallback is authorized.

## Current contract — September 10, 2026

The owner authorized Michael review items 2–9 and six new SMN Dev articles for
the September 10 production edition. Production already published HRL, TRV,
IBM, KDP, KMB and JNJ. Use those exact published picks, not the first six rows
of the candidate queue. This is a manually initiated edition, not a scheduler
activation or permission to write to production.

The September 8 independent history and median-price-path design is withdrawn.
It violated the existing rule that TradeWave owns every calculation it already
provides. Do not use `private_history`, `build_cell_evidence`, or
`seasonal_price_path.derive` as an input to the new article workflow. The
`seasonal_edition` entry points now require `engine_seasonal`; legacy cards fail
closed rather than silently obtaining a new calculation.

## Source authority and fidelity

`tradewave_engine_export.py` is read-only transport executed on the TradeWave
host with its existing configuration. It requests ChartData4, OHLC and seasonal
responses through the existing authenticated library. It invokes the existing
TradeWave function `site/lib/svg_wave_chart.py::compute_projection` ON
TradeWave and returns its points. No projection formula is copied into SMN.
The library SHA and TradeWave checkout commit are retained. This does not
change any TradeWave mathematics, service, configuration or production page.

`engine_seasonal.make_card` binds the original production resource, symbol,
start date, inclusive duration, direction and literal lookback string. It
compares the returned values to retained production evidence. The old writer
packet exposes at most ten rows; IBM's fifteen-row engine response is also
checked against the complete published dataset. Current-year placeholders are
not observations. Real flat years are preserved. Engine extrema need not
include zero: the client must not repair those values.

Primary statistics keep the production engine's exact strings and precision.
`Avg Profit` describes winners only; `Avg Profit - All` is the rounded mean of
all windows. Short-side profits and underlying negative price changes are
labeled separately. The price-chart median reference is used only when the
engine supplies a median in the same price/long basis. SMN does not negate or
recompute a short median to add a chart line.

Additional histories are separate requests to the SAME source engine. No
local slicing, reaggregation, date snapping or normalization is permitted.
Ten and twenty consecutive windows overlap. Midterm histories are selected
years, not consecutive years. Material differences are explained in the prose
beside the relevant claim and in the takeaways, not only in a disclosure.

During this edition, the .176 dev engine differed from production in data
adjustments and extrema behavior. It was rejected as the source for a faithful
production recreation. Read-only exports from the engine used by production
reproduced the original study. That engine difference is an upstream review
item, not authorization to change either calculation here.

## Writing and presentation

`engine_edition_workflow.py` consumes the captured production records, source
exports and inspected editorial commissions. `subscription_writer` runs the
official Codex CLI with saved ChatGPT authentication, Astra Extra High, API-key
and tool overrides removed, immutable jobs and per-job receipts. There is no
paid OpenAI API fallback or new image generation in this edition.

The six sections connect the business question and seasonal reason by the
second opening paragraph. The business graphic stays beside current context.
The seasonal record and range charts explain the main study, followed by a
meaningful comparison and outlook. The outlook introduces the price chart
before it appears. Its existing TradeWave 60-weekday-step horizon has explicit
displayed dates and is distinct from the full study window. It superimposes a normalized section of the selected TradeWave trend chart
on the actual price chart; no new mean/median return path is calculated.

A risk example identifies its selection reason and full nominal historical
window. Its ending result is separate from its range from entry. Do not call an
entry-relative low peak-to-trough maximum drawdown or infer the order of moves.
The reader gets useful takeaways, exact TradeWave links, responsive charts,
accessible data tables, downloadable engine values and the production hero.
Both capped and uncapped range graphics are generated with identical data. A
stable article-identity choice uses caps in approximately 75% of articles.

## Review and deployment

Mechanical schema/source-budget checks, an independent subscription editorial
review, engine-value fidelity and actual desktop/mobile rendering are required
before `subscription_publication.package` accepts an edition. Review bindings
include the final article, price-path evidence, displayed figure and assets.
Only public HTML, figures, cited evidence and CSVs enter the package. No prompt,
credential, account record or model log is published. Lookback metadata must
retain the original string; there is no hard-coded twenty-year replacement.

Explicit copyediting preserves the original subscription writer output and
records exact before/after text, source references and evidence hashes. It
requires a fresh independent review; an old approval cannot follow revised
copy. Evidence corrections use a new immutable writer job, with the prior
prepared result archived. The Travelers quarter-label correction is an example:
the 2025 and 2026 amounts stayed unchanged, while the reporting-period label
was fixed in the chart, table, CSV and evidence supplied for renewed review.

The primary SMN Dev host .180 was offline. The already-active temporary Dev
site is served by .176 nginx `smn-dev`, rooted at
`/var/www/smn-dev-recovery/current`. `install_smn_recovery_edition.py` only
accepts that exact host, active recovery root and `smn-dev.trxstat.com` origin.
It preserves existing editions, installs versioned public/source artifacts,
updates the edition home redirect, and switches the actual served pointer.
The generator source pointer is `/var/lib/tradewave/smn-editorial/current`.
This is not a claim that the primary application or its queues were restored.

Build before the brief dev activation lock. Preserve rollback pointers and the
nginx file. After activation, prove rendered behavior and pushed main parity,
then finalize the receipt and release the lock. Roll back if a live check or
concurrency-safe main update fails. No DNS, staging, production, email, SEO or
scheduled queue changes belong to this authorization.

The recovery installer preserves nginx configuration bytes, including mixed
CRLF/LF line endings, for its drift comparison and rollback. The September 12
Linux activation/rollback regression verifies that the previous pointer and
configuration are restored exactly.
