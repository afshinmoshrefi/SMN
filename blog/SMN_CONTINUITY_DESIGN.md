# Improve SMN Without Replacing Its Identity

Status: private development, no deployment or access-policy change authorized.

## Comparison Before Implementation

Evidence: original August 21 JPM `JPM_na_na_na_07f47fe3/final.html` and its
angle card/plan; JPM-v3 visual edition; the actual August 24 Zoom meeting
transcript, retrieved again September 7 UTC. Transcript timestamps below are
elapsed meeting times, not the later-starting recording clip.

| Element | Original SMN | Visual JPM-v3 | New seasonal contract |
|---|---|---|---|
| Reader promise | Explicit seasonal window and historical finding | Operating growth and October earnings dominate | Seasonal insight connected to a current investor question in title/dek/opening |
| Summary | Useful answer, but several repetitive layers | Takeaways only at the bottom | One labeled Key Takeaways block near the top, after the hero |
| TradeWave evidence | Window, sample, key stats, year bars, path/range and price charts | Two editorial summary graphics; native TradeWave components absent | Exact window/sample, compact stats and native TradeWave charts are structural requirements |
| Current context | Late in the article; some cited material misattributed | Earlier, more useful and better sourced | Keep the improved research; connect it to this window rather than letting it replace the seasonal purpose |
| Additional visuals | Mainly TradeWave charts | Useful financial comparison and historical comparison graphics | Retain editorial graphics alongside native charts; every graphic answers a distinct question |
| Angle | Evidence-driven CLOCKWORK framing, overconfident prose | Upstream GROWTH_CHECK survives only as an operating story | Preserve the commissioned angle and require an explicit seasonal question, contribution and evidence-led payoff |
| Detail | Repeated win rates, table narration, speculative mechanisms | Seasonal interpretation capped at about 120 words | No arbitrary seasonal cap; compact explanation of the actual finding and its consequential risk |
| Reproducibility | TradeWave pattern link, methodology, book | All missing | Visible Open in TradeWave link generated from the exact instrument, dates and year code; methodology retained |

The original and current JPM statistics are not identical datasets. The old
article used 15 observations; the corrected private policy uses 20 completed
annual observations plus explicitly labeled cycle context. Do not restore old
statistics/images into the new 20-year article. Preserve numerical corrections,
inclusive dates and honest overlap accounting.

## Michael's Feedback: Direct Evidence Versus Design Interpretation

- 22:43-24:00: move the hero before the summary and label that summary.
- 24:38-25:33: clarify what the recent 10 years means; shorter versus longer
  histories are useful only when the reader understands the comparison.
- 26:14-26:24: general context flows naturally and the table is useful.
- 26:59-27:34: remove artificially clever language; the flow into the chart is good.
- 29:03-30:23 and 32:58-33:08: retain the strong opening, summary, table and
  charts; condense unnecessary detail around them.
- 30:35-32:17: news can introduce a free story; an informed subscriber wants
  the analysis promptly. Access gating is a separate, deferred decision.
- 33:31-33:55: roughly 40% could be removed from that draft. This is not a
  universal length quota.
- 34:45-34:57: remove unsupported speculation.
- 35:44-36:37: inspect actual generated hero lettering for errors.

Our interpretation: the analysis and TradeWave evidence define the product;
the writing should help readers understand them faster. Michael did not request
replacement of the article with generic company news. The owner confirms both
native TradeWave and additional editorial visuals are desired. Promotion remains
the owner's stated expectation, not evidence of Michael approving a new preview.

## Article Design and Angle Responsibilities

Keep a recognizable SMN sequence, with flexibility in the middle: title/dek,
hero, labeled takeaways, connected opening, article-specific price/seasonal path,
seasonal record/key stats/native
chart, useful current context plus an editorial visual, consequential risk or
historical comparison with supporting charts, next checkpoint and TradeWave
research link, sources/methodology. Readers can jump directly to the record.

The original engine's six angles remain meaningful: unusual consistency,
cycle-specific behavior, a genuine horizon/lookback split, news/history conflict,
news/history alignment, or a seasonal opportunity without a fresh news event.
They are editorial lenses, never permission to select the most flattering
sample or call history a forecast. Current-context labels such as GROWTH_CHECK
must explicitly explain their seasonal contribution. A finishing editor cannot
silently discard or change the commissioned angle. Angle diversity must be
tested on representative real inputs; one JPM example cannot validate the six
families or prove reader preference.

Required seasonal evidence is separate from the optional editorial-chart
budget. A generic news editor must reject a seasonal card unless the seasonal
contract is supplied. Missing assets, an incorrect study link or a mismatched
window hold the preview. They must not silently produce a generic-news fallback.

The first implementation reuses the production `chartkit.record_bars` renderer
for the historical record and the range-from-entry view, from the exact reviewed
completed observations. Other native chart types require their own verified
data, and are not synthesized from annual endpoints. Existing production chart
generation remains unchanged. The September 8 owner request adds the native
price/seasonal-path graphic to the updated six-article format. Its historical
sample must be the article's actual sample, never the original production
article's different lookback. The updated contract sets `price_path_required`;
missing verified daily inputs hold generation/rendering instead of dropping it.
Legacy bundles remain readable for previous review artifacts.

The price-path chart follows the connected opening. Desktop joins the latest
12 months of observed prices to a dashed historical illustration. Phone layout
keeps all the same data in two panels, expanding the 60-calendar-day illustration
with its own explicitly labelled price scale. The path is a pointwise median
of individually normalized daily historical closes, anchored to the latest
recorded close. It is not a price target, a forecast, a calibrated probability
or the full-window return from the annual bars. The annual bars, ranges, study
links, existing article prose and additional editorial visuals remain intact.

## Acceptance

Require evidence-based editorial review of the complete rendered article,
including SMN identity, angle delivery, Michael's improvements, seasonal/news
connection, every chart, source budgets and statistical meaning. Then inspect
desktop/mobile pixels, exact study-link payload, all image bytes, visible key
stats and source links. Preserve the original and rejected previews in the
comparison. A private preview is not published or deployed, and a technically
passing result is not owner or Michael acceptance.

## First Private Result

Local review: `smn-review-20260905/smn-continuity-20260907/report.html` in the
owner's orchestration workspace. `results/JPM-final/article.html` uses frozen
September 6 sources and the commissioned GROWTH_CHECK angle. It contains two
native TradeWave chart types and two additional editorial charts. The exact
study payload is `2|JPM|2026-08-21|90|20`. The authenticated destination was not
exercised; both rendered link payloads and the inclusive endpoint were checked.

Sixty focused offline tests passed. Thirty chart/seasonal tests passed again
after the final mobile header adjustment. Desktop and 390px phone browser checks
passed, followed by actual image inspection and a byte-bound readiness receipt.
The final complete article passed a separate Astra editorial review. Seven
text/review calls were used across retained iterations; the existing hero was
reused. Two documented primary-editor changes shortened the title and removed
an overclaim from a comparison heading before that final review. A later mobile
pixel refresh preserved the reviewed HTML exactly and made no provider call.

This establishes a reusable private seasonal finishing path and one reviewed
example. It is not a real-input quality benchmark of all six angle families,
owner/Michael acceptance, live discovery coverage, or an activated release.
