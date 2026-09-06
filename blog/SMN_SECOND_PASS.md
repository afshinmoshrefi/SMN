# SMN private selection and reader-value pilot — September 6, 2026

**Later owner review rejected these automated articles' editorial direction.**
The calculations were retained. The subsequent current-context correction is
documented in [SMN_EDITORIAL_CONTEXT.md](SMN_EDITORIAL_CONTEXT.md); it supersedes
this document's history-first private writing flow on the current branch.

The accepted first development pass writes clearer articles, but its inherited
selector can lead with whichever annual or election-cycle sample looks strongest.
This second pass fixes the article window before comparison, keeps one main
annual reference, and preserves contrary period evidence through planning,
writing and independent review. It remains an explicit private entry point.

## Scope and entry points

`private_selection.select_private_candidates` accepts frozen candidate packets.
It recalculates the cohort gate, checks the reviewed instrument/history manifest,
and returns the full decision slate. `generate_private_article` recalculates the
same gates before calling the bounded Astra writer. `angle_pipeline` accepts an
explicit `private_preview` packet and branches before legacy selection, research,
hero fetching, article audit, queues or publishing. `publish=True` and alternate
angle indexes are rejected on this path. Legacy entry points retain their defaults.

No service, schedule, live checkout, access rule or public feed is activated.
Every private result has `publishable=False`. Calibrated probability is absent
from both selection and reader copy. Coding-session effort does not change the
article model: the existing first-pass default remains `gpt-6-astra` / `low`.

## Historical selection

The caller chooses instrument, anchor and inclusive calendar duration before
examining the comparisons. The main sample is the latest twenty completed annual
windows in a fixed calendar span; it is not the best available twenty outcomes.
Recent five/ten versus preceding observations and matching cycle versus noncycle
years are explicit comparisons. The full ten-cycle request is separated from its
recent and earlier components. Overlapping histories cannot be called independent
confirmation; unavailable full-span annual comparisons remain unavailable.

Classification describes observed consistency, period sensitivity or contrast.
It does not establish causality, forecast calibration or a statistically proven
trading advantage. The private sample floors (eight annual, six cycle) and
twenty-year reference are proposed editorial controls to evaluate, not validated
optima. A checked but sparse cycle record can qualify an otherwise eligible
annual article. Unchecked cycles, unexplained gaps, conflicting rows, wrong
identity and incomplete-window problems hold the historical premise.

Counts, medians and paired giveback arithmetic are computed in Python. The shared
evidence helper uses decimal arithmetic and consistent display rounding; missing
observations or excursions stay missing. Comparison cells license only their
own exact facts and cannot become fallback leads after a writer veto.

`private_history.derive_history_panel` calculates percentages from explicitly
supplied adjusted OHLC and a reviewed exchange calendar. Entry is the first
in-window session's adjusted close; exit is the last session on or before the
inclusive end. High/low excursions exclude the entry session because entry is at
its close. A completed-session boundary is derived using the audit clock and
the calendar's session-close timestamp, including before/after-close cases.
An optional candidate `history_input` invokes this calculation before selection,
replaces supplied gates/observations, and removes raw prices from prepared output.

The source audit found that SMN already sends `days - 1`, while the legacy
TradeWave endpoint can move a non-session end forward beyond the labeled window.
The private OHLC path corrects that convention without editing the live endpoint.
Previously cached returns cannot be relabeled with stricter dates. Actual review
examples use retrospective calculations from the inspected September 4 adjusted
data snapshot; they are not a point-in-time reconstruction of August knowledge.

## Markets and the daily slate

Eligibility follows the actual series, not a broad resource label. Five identity
fields bind resource, provider, exchange, symbol and series. Reviewed semantics
distinguish adjusted equity prices, total-return series, bond prices, yield levels,
spot FX, crypto and futures/roll conventions. Freshness follows the declared
market calendar. A hash detects a changed manifest; it does not certify assertions
made by an untrusted source or a language model.

The lineup compares canonical event/development, reader question, answer evidence
and overlapping exposure. It consolidates repeated answers, permits supported
new developments, and does not impose a four-day ticker blackout or a mandatory
market quota. Reviewed consequence, timeliness, relevance and completeness order
eligible candidates; ordinary stocks/plain ETFs receive only a provisional final
tiebreak preference. Major news has its own dated evidence gate and does not
need a seasonal setup. Material events omitted at the private daily ceiling
remain visible with reasons.

## Reader and image review

The plan commits to one question and answer, a useful reason to read now, one
consequential risk, exact headline support, and every material qualification.
Contrary period evidence must appear at its required prominence. The existing
independent editorial call checks actual passages in the final title, dek,
opening answer and body against a hash-bound brief. Missing qualifications and
unsupported promises use the same single bounded revision; they do not start an
unlimited rewrite loop.

The transport binds each fresh editor response to the exact HTML and brief sent
in that call. The raw model response and its echoed hashes remain auditable;
copying a hash is no longer delegated to the language model. Imported/cached
reviews still need matching digests. This binding never supplies a missing
judgment, quote, evidence reference or qualification. Prominent qualification
checks quote the existing preview/opening explanation, while their reasons
verify detailed body statistics. This avoids rejecting a clear opening just
because the reviewer selected a more detailed body paragraph.

`text_ready` and `visual_ready` are separate. A hero concept, URL, alt text or
writer's approval is insufficient for visual readiness. Local image bytes,
provenance, specific observations about lettering/identity/crop/implications,
and desktop/mobile screenshots are bound to the exact final HTML and trusted
reviewer. Private historical charts show actual sample years and percentages.
They are labeled illustrations, not documentary photographs or forecasts.
Responsive comparison heroes use a portrait mobile image and landscape desktop
image with identical evidence and scale. Both files, their displayed URLs and
the corresponding screenshot bytes require review. Asset changes invalidate
approval; the mobile image cannot inherit an unseen desktop-only approval.

## Reproduce privately

```bash
python3 blog/private_selection.py --packet candidates.json --out /tmp/smn-selection-review
```

This calculates selection only. Add `--generate` only for an explicitly requested
paid preview batch. A generation-started marker prevents automatic reruns in the
same output directory. Use injected transports for offline tests and exact replay.
The frozen packet includes `as_of`; historical review examples must be labeled
with that replay date, not presented as today's market analysis.

The existing automatic news discovery coverage limitation is separate: a verified
manually sourced example demonstrates selection/writing, not that a search scan
will catch every major event. Before activation, verify macro/company/energy
coverage, event-calendar inputs and dated weekend handling. Reader comprehension,
repeat usefulness and subsequent engagement need real pilot observation; no
audience experiment or claim of improved click-through is part of this pass.

See `COHORT_POLICY_CONTRACT.md`, `MARKET_POLICY_CONTRACT.md` and the focused tests
for exact input contracts. Private generation requires upstream reviewed source
semantics and data quality; unknown inputs must remain held.

## Development validation

The final integrated isolated run passed 466 tests using SMN Dev's existing
Python dependencies with code copied only into `/tmp/smn-second-pass-20260906`.
Local testing lacked `matplotlib` and `requests`; these were dependency import
errors, not passes. Subsequent private-history and private-news changes received
focused tests and independent review; the final receipt records the integrated
count and exact source hashes.

Four real Astra seasonal examples retained JPM/WMT/F/COST's original windows
and used the corrected inclusive calculation. Independent arithmetic review
recomputed 36 evidence summaries and found all prose counts, dates, returns,
loss examples and overlap assertions supported. JPM/WMT explain cycle-era
reversals, Ford preserves annual-recency and matched-cycle contrasts, and
Costco describes agreement with weaker recent outcomes. VIXY was held before
paid generation for unresolved specialized-fund semantics. Revised drafts used
five calls each; one final editorial call reviewed unchanged prose after the
responsive chart assembly. Raw responses, prior holds and final reviews remain
separate artifacts. The final review page reports any remaining editorial hold.

Browser assertions passed on all five example articles at 1280px and 390px:
no horizontal overflow, unloaded images, broken internal anchors or duplicate
heroes. Actual visual review separately checks both responsive variants; browser
assertions alone do not certify a chart's meaning or pixel quality.

The actual financial-news replay completed in five Astra Low calls, including
one revision, with no seasonal premise. Its exact-byte reader review passed;
independent source review found no material unsupported claim. It is a manually
sourced September 5 replay, not evidence of automatic discovery coverage. Its
hero remains intentionally absent. The stronger evidence and question controls
do not by themselves prove a material prose-quality improvement over the already
accepted first-pass jobs article.
