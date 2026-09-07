# Private visual financial newsroom

The September 2026 previews buried comparisons in numerical prose. This stage makes one useful comparison visible near the start of the article, supplies a story-specific hero, and asks the writer to explain what the evidence means. It applies after the existing selection, current-context, seasonal-cohort and text-evidence gates. It does not activate a site, queue, service, schedule, access restriction or publication path.

## Reader contract

- Headline and opening establish the development, timing and investor stakes. A seasonal-led piece connects the current window with a dated disclosure or confirmed checkpoint by paragraph two.
- One commissioned primary chart follows one or two opening paragraphs. A second chart must answer a distinct reader question. The commissioning editor can require it explicitly. No decorative chart quota beyond those evidence-backed comparisons.
- A macro-news seasonal hint is usually one 60–90 word paragraph. A seasonal-led stock piece gets up to two short history paragraphs. Full cohort tables are an evidence reservoir; they are not an outline for the narrative.
- Hero illustrations are visibly labeled. They cannot imply documentary evidence, a specific unverified event, a forecast, a real person or invented trading data.
- Plot values, labels, dates, units and accessible exports come from the numerical ledger. The writer can select chart IDs, not manufacture series. Bar axes retain zero. Negative values and estimate revisions remain visible. Decimal display uses half-up rounding consistently across graphics, alt text and tables.

## Interfaces

`visual_editorial.generate_visual_private_article` calls the existing private generator first. It accepts a `visual_bundle` factory that receives the newly qualified result, plus explicit writer/reviewer/image-provider injections. A held upstream article never reaches the visual stage.

`visual_editorial.run_visual_edition` can upgrade an existing text-qualified result. Its bundle includes the exact source HTML SHA, dated sources with inspected excerpts, numeric records with extraction locators, a bounded chart catalog, primary/required chart IDs, material context and optional art direction. Each bundle has a digest. This detects mutation; it does not prove source authenticity. An independent editorial model compares source excerpts, numbers and final HTML before text is ready.

`hero_send(request)` is an explicit image-provider bridge returning a local raster path, prompt SHA and provider name. Alternatively a generated asset manifest can be supplied. A missing provider/asset leaves visual readiness pending. The current examples use the built-in image-generation tool; no hidden image API billing or server configuration is introduced. Generated hero requests and exact prompts are saved for repeatable orchestration. Routine image generation can use the existing publisher's provider only through a later explicit integration; that live path is not changed here.

```text
python blog/visual_editorial.py --source qualified-result.json --evidence visual-evidence.json --hero generated-hero.json --out private-output --generate
```

Without `--generate` the command validates the bundle without model calls. Generation requires a new directory marker and permits at most four editorial calls (write, review, optional repair, review). There are no automatic paid retries. Actual usage and prompts are retained. The defaults inherit the established Astra article transport.

## Data adapters and supported charts

The first renderer supports source-backed comparison bars and daily stock-volume bars. The latter requires the latest completed session plus exactly twenty previous sessions, calculates the prior median excluding the latest, and requires explicit provider split-adjusted-volume semantics. It does not infer options activity, buyer intent or price direction from volume. The upstream adapter remains responsible for verifying instrument identity, sessions, adjustment basis and source extraction.

Inputs can be official economic statistics, issuer financial metrics or TradeWave/provider data; a TradeWave seasonal pattern is not required. Earnings growth rates must identify their distinct measures and common comparison period. Seasonal prices retain fixed windows, sample counts, overlapping cohorts, era sensitivity and the difference between a fund return and a bond yield. Missing values are rejected; they never become zero. Forecast ranges, intraday relative volume, options data, dual-axis and yield-curve charts need their own explicit adapters; they are not silently approximated by the current renderer.

`visual_charts` produces desktop/mobile PNGs, SVG exports, CSVs, accessible tables, captions, source links and a file-hash manifest. Derived-source payloads are persisted as local evidence files rather than linking a calculated return to a provider documentation page that does not establish it. Raw values are retained in exports; display rounding is separate.

## Completion and limits

`text_ready_visual_pending` means the numerical/textual editorial review passed; it does not mean images were inspected. `visual_review.inspect_edition` requires a trusted human/vision inspector's recorded observations, exact article/evidence hashes, actual desktop/mobile screenshots, every selected chart's file hashes and the hero's provenance/crop/identity checks. Changed HTML, figures, exports or inspection bindings invalidate readiness. It validates recorded evidence; it cannot prove the inspector's judgment correct. `private_preview_ready` always retains `publishable=False`.

This is a private, reusable finishing stage with source-adapter and image-provider contracts. The three worked adapters are dated examples, not unattended retrieval for every financial category. Live scheduling, fetching and image-provider activation remain outside this no-deploy change. No claim of matching a newsroom's original reporting or measured reader retention is made. The editorial standard is a useful comparison, a credible visual hierarchy and a concise explanation of the reader's decision.

Offline tests cover rounding, unit mismatch, unknown sources, changed data, invalid values, future/duplicate volume sessions, latest-session exclusion, required graphics, bounded repair, no paid reruns, upstream holds and final image mutation. Private live Astra runs and actual browser inspection qualify the supplied examples separately.
