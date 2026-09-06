# SMN first development pass — September 5, 2026

**Implementation branch only. Nothing deployed or activated.** Based on SMN Dev's `57055c2b905f4749de5938b11a0289d1e33eeeba` on `deterministic-sources`, including its four unpublished commits. The active Dev checkout, services, queues, publishing configuration and production remain unchanged.

## Article generation

Retains the angle engine and PLAN → WRITE → deterministic checks → independent editorial review architecture. `ArticleLLM` defaults to `gpt-6-astra` with low reasoning effort. The earlier comparison preferred Astra for JPM, WMT and JNJ. Article effort is separate from the coding agent's effort setting.

- Schema-2 plans name the reader's question, one thesis, and a claim/source support map. Existing saved plans remain readable.
- Planner, writer and reviewer receive substantive retrieved passages and computed seasonal evidence. A synthesized URL alone cannot become a source. Source filtering and citation renumbering preserve nested references.
- Historical end dates are inclusive. Actual sampled years/counts are explicit, including election cohorts. Recent-five comparisons use a disjoint earlier sample. Giveback is computed within matched yearly observations. Missing excursions remain unavailable, with separate sample counts; risk and outlier sensitivity are supplied to the writer.
- Source freshness requires a supported, source-linked event claim/date. Updating a generic estimates page cannot create a new event.
- Factual problems and material repetition, excess length or weak interpretation receive one combined revision. Facts, citations and editorial quality are checked again. Author-written prose has a +10% budget tolerance; generated tables/captions do not count against it.
- Michael's feedback informs concise openings, less repetition, labeled Key Takeaways, earlier hero placement, useful tables and accurate cohort interpretation. A 40% cut is not imposed on every article. Prompts no longer require speculative mechanisms or an arbitrary source quota.
- Selected charts are checked against explicit renderer sample/window metadata. Cumulative short-side wording is preserved. Legacy charts missing metadata remain compatible; that is a documented verification limit.
- A fallback to a different window rebuilds charts and the TradeWave link. Optional SEO changes receive a factual check and independent approval, then update the visible and structured headline together.

`SMN_ARTICLE_MODEL` and `SMN_ARTICLE_REASONING_EFFORT` configure the seasonal article transport. News functions also accept an explicit `model` argument. Existing explicit mini/nano research calls retain their models. No live environment variables were changed. The transport strips unsupported Astra sampling parameters and refuses incomplete, empty or refused responses. Actual stage model/token/cache usage is recorded separately from configured defaults. No automatic provider retry loop was added.

## General financial news

`news_selection.py`, `news_prompts.py`, `news_pipeline.py` and `news_discovery.py` provide a separate news path using the same evidence/planning/review principles. See [NEWS_PREVIEW_DESIGN.md](NEWS_PREVIEW_DESIGN.md) for contracts and limits.

One explicit scan makes at most two searches and one discovery call, then proposes up to four drafts. It uses dated retrieved passages, source quality, event significance, reader relevance and freshness. One lead plus an optional second story is the baseline; major events have bounded overflow. The same event can propose an update to its article; another event involving the same ticker remains eligible. Selection scores are editorial priorities, not financial probabilities.

News does not need a seasonal pattern, a TradeWave promotion, an image or a prediction score. Source-linked JSON becomes escaped HTML. Generation uses at most five calls: plan, write, review, one revision, re-review. All results have `publishable=False`. No scheduler, queue integration or automatic publisher was installed. Cross-publisher event deduplication remains a preview-review limitation; the shared canonical source/date and claim fingerprint cover the supported cases.

## Audience previews

`article_preview.py` writes a local review page with Visitor, Registered TradeWave user and Full article views. Its access policy defaults off. The proposed arrangement is fully public general news and a seasonal introduction for visitors, with full seasonal articles for every registered TradeWave account, including free accounts. The visitor file physically omits the rest of the article. Paid-only mode is modeled but deferred. Held results are visibly labeled.

This pass does not install a live authentication gate. A future serving adapter must derive identity/entitlements server-side before access can be activated. The preview tabs simulate audiences; they are not authentication. Public calibrated probabilities and their influence on seasonal selection remain shelved. Automated hero-lettering/OCR review is also outside this pass.

## Actual Astra example and offline replay

The September 4 US jobs report was manually verified against the linked BLS and Federal Reserve pages; bounded Reuters context was also supplied. The example is not represented as an automatically discovered story. Astra generated the plan/article and removed a repetitive ending after one review. Its final draft, review and actual usage manifest are in `examples/jobs-news-20260905/`. The rendered example is `examples/jobs-news-20260905/article.html`.

Generation used five Astra-low calls, approximately **$0.29** at the previously verified standard token/cache rates. This includes planning, writing, editing and reviews. It excludes paid search and image generation; it is not a per-article invoice or a general average.

Run the approved final article through the workflow without paid calls:

```bash
python3 blog/news_pipeline.py --packet blog/examples/jobs-news-20260905.json --responses blog/examples/jobs-news-20260905/approved-replay-responses.json --now 2026-09-05T21:10:58Z --out /tmp/smn-jobs-replay
```

This replay supplies the actual approved plan, final article and passing review. The original five-call run's manifest remains separate. For a new live draft, supply fresh inspected evidence and use `--generate`; for a one-shot scan, see the explicitly opt-in `news_discovery.py --live` CLI. Credentials must be supplied through the existing environment (SMN's OpenAI key is in its existing `/etc/SMN/secrets.env`); do not copy credentials into source or artifacts.

## Validation and handoff

The integrated article test suite passed **284 tests** using Dev's existing Python environment in an isolated `/tmp` source copy. No services were restarted and no application files were installed. The local run passed 250 tests and had three dependency-only import failures; the isolated run resolved these with the existing dependencies. Further targeted usage-isolation tests accompany the final integration.

The real news example passed its deterministic checks and independent model review. Desktop/mobile browser checks covered rendered content, citation targets, no horizontal overflow, tab switching and no browser errors. Generated prose still needs editorial judgment; these checks do not establish universal factual accuracy or calibrated investment predictions.

Before activation: review output/style, choose the news scan cadence and editorial ownership, verify live research performance and event deduplication, then separately authorize deployment and publishing/access behavior. The existing development/production pipelines continue operating as before this branch.

## September 6 validation — still undeployed

Three historical August 21 inputs (JPM, WMT, JNJ) were replayed through the pre-upgrade Dev generator/editor and the first pass. Each pair preserved annual observations, selected windows/angle, shared retrieved research snippets and original images. The old system used GPT-5.1 with its existing sampling settings; the new system used Astra Low and recomputed evidence. This tests the combined generation/editorial upgrade, not a model-only comparison or a new selection/research/image run. All six workflows returned ready; the updated Walmart draft used one editorial revision to reduce repetition. Original results, neutral A/B copies, source hashes, prompts, responses and independent review are retained in the local validation archive.

Estimated generation/editorial costs from actual returned token/cache counts: JPM $0.0495 old / $0.4425 updated; WMT $0.0656 / $0.9246; JNJ $0.0570 / $0.4174. These exclude search and images and are not invoices. The updated system's wider evidence context and review calls materially affect cost.

Automatic news discovery failed its coverage acceptance test: two default searches returned ten results, but Astra selected no events. Navigation occupied most of the first three excerpts. Corrections extract substantive bodies, preserve exact source/body/excerpt offsets and hashes, reject bodyless pages and allocate context across domains. Replaying the same saved search improved useful evidence from three mostly navigation-heavy pages to eight article bodies, but still selected no events under the unchanged full-event-date and 48-hour rules. An independent check found same-day OPEC+ coverage absent from the default search results. No automatic article was generated; this must not be confused with the successful manually sourced jobs example.

Same-day update corrections require a grounded development fingerprint, changed body on a known source and claim text not already covered; absent legacy evidence snapshots skip conservatively. Independent review's unchanged-body/shorter-quote regression is fixed. The 52 focused news tests pass. These checks establish changed evidence, not semantic novelty in all cases; editorial review still judges materiality. Code corrections are `5452c2b` and `3ab7d43`; the paid replay used `5452c2b`, and the follow-up update fix was tested offline without further paid calls. Total news test usage was two Tavily searches and two Astra extractions, approximately $0.4196 OpenAI cost.

Before activation, improve topic coverage using explicit macro, company and energy/geopolitics discovery and an event calendar; design grounded relative-date handling and clearly dated weekend carryover. Do not lower source standards simply to fill an article quota. No live checkout, service, schedule or configuration changed during this validation.
