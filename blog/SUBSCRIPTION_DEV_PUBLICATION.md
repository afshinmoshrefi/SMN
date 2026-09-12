# Subscription Edition: Engine-backed Dev Publication

## Publication Hold - September 12, 2026

The six September 10 articles are written and independently reviewed, but this
edition is **not approved for publication**. The existing TradeWave helper used
for all six price projections converts differences in a normalized 0-100 seasonal
curve into percentage price changes. Read-only inspection confirmed that mismatch
in production source. The Wave Viewer source contains the same conversion.

The raw primary seasonal rows/statistics remain on their separately verified,
production-matched path. Do not recalculate them, alter the selected years,
silently drop the required price chart, or correct the projection in SMN.
The owner must agree to the proposed TradeWave-engine methodology change first.
Existing editorial, source-fidelity and layout passes do not clear this hold.
This is an operational publication hold; it is not a newly implemented runtime
gate and must not be represented as one.

Canonical incident, source references and unimplemented correction proposal:
TradeWave `docs/TRADEWAVE_ECOSYSTEM.md`, section 7.1, on branch
`codex/smn-projection-incident-20260912`. Source receipts and the exact six
affected exports are retained locally under
`smn-review-20260910/engine-editorial/projection-incident-20260912/`.

Application source remains `354c7d254e723c9930bb9052b78527aeace59ed9`;
the later documentation-only commit does not change its runtime. All six final
reviews and 48 focused tests passed before the projection hold. September 12
JNJ/TRV desktop/mobile layout assertions passed; final pixel approval and
affected chart re-review remain pending. No September 10 package, Dev activation,
main integration or live publication receipt exists. The private `.176` source
copy/smoke test is not deployment. The prior Claude handoff omitted this blocker
and is superseded by this notice. Afshin confirms Claude made no updates.

After an agreed engine repair, refresh the projection evidence for the original
six studies, regenerate/review affected output, and complete Dev-only publication
with live comparison checks. The edition stays dated September 10; do not present
the frozen sources as a new current-news edition. Production and schedulers stay
outside this authorization.

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
displayed dates and is distinct from the full study window. It uses the average
seasonal curve, not an independently computed median or a price target.

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
