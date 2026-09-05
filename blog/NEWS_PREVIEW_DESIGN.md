# Financial-news preview workflow

This first pass is a callable development workflow. It does not install a schedule, enqueue articles, import application configuration, change access rules, or publish. Every result has `publishable: false`, including a draft that passes review. `NewsPolicy` defaults both activation and automatic publication to false; changing those metadata fields does not create a publication path.

The normal selection target is one lead story and an optional second. Up to two additional major events can receive drafts. These limits are configurable. An event does not need a seasonal pattern, ticker, chart or calibrated probability.

## Evidence contract

`news_pipeline.run_news_article(event, research, *, send=None, output_dir=None, now=None, model="gpt-6-astra", max_revisions=1, policy=None)` accepts JSON-compatible dictionaries. `send(prompt)` returns a JSON string or object. The default transport lazily imports `article_llm.ArticleLLM`; importing news modules performs no network, config or provider activity.

The `event` object contains:

| Field | Meaning |
| --- | --- |
| `event_id` | Stable identity for the underlying event, such as one company earnings release or one monthly employment report. Different tickers can belong to the same event. |
| `development_id` | Identity of the material new development within that event. Changing a headline is not a new development. |
| `headline` | Factual working description, not a promotional headline. |
| `event_time` | Time the described development happened or was officially released, with timezone. |
| `event_time_basis` | `official_release` or `reported_event`. A retrieval timestamp or generic page update is insufficient. |
| `source_ids`, `claim_ids` | Evidence that establishes the event. |
| `significance`, `significance_reason` | Supplied editorial assessment from 0 to 5 and its concrete reason. This is not a model prediction probability. |
| `audience_relevance`, `relevance_reason` | Supplied reader-relevance assessment from 0 to 5 and its reason. |
| `material_development` | Explicit boolean confirming a substantive change. |
| `major_event` | Optional boolean allowing a sufficiently significant event beyond the baseline draft limit. |

`research` contains `sources` and `claims`. Each source requires `id`, `title`, an HTTP(S) `url`, `published_at`, `verified_at`, `verified: true`, `source_type` (`primary` or `reporting`), and a substantive `excerpt` or `content`. Timestamps include a timezone. `verified: true` records an upstream inspection of the retrieved evidence; the local contract checker does not fetch the URL or independently establish that every source claim is true. Do not set the flag based on a model assertion alone.

Each claim has an `id`, a `text` statement, and `source_ids` referencing its actual support. The planner, writer and reviewer receive the same substantive excerpts. A valid citation ID alone does not prove the wording is supported: the semantic reviewer checks the source passages against the title, dek, takeaways, headings and body.

The event must be at most 48 hours old, news sources at most 96 hours old, and evidence verification at most 48 hours old, by default. Explicit `role: "context"` permits an older supporting document but cannot serve as the sole fresh news peg. Generic quote, estimates, search and landing pages cannot establish a news event merely by changing their displayed update time. One primary release or reports from at least two distinct reporting domains are required. Two domains are a corroboration heuristic, not proof of independent reporting; source inspection must identify syndication.

Packets are bounded to 12 sources, 80 claims, 18,000 characters per source excerpt, and 60,000 total excerpt characters. Oversized evidence is rejected rather than silently truncated. Provide focused passages with the context needed to interpret them.

## Selection and updates

`news_selection.select_news_events(events, research, coverage=[], now=..., policy=...)` returns `selected` draft candidates and an audit of rejected, skipped and deferred candidates. Its priority score combines supplied significance, reader relevance, actual event age, source quality and materiality. It is an editorial heuristic whose effect should be evaluated in preview mode.

Coverage rows contain `event_id`, `development_ids`, `article_id`, and optionally `last_event_time`. An already covered development is skipped. A later development in the same event produces an `update_draft` instruction pointing at the existing article. A different event involving a recently covered ticker is eligible immediately. The selector does not itself overwrite an article or persist coverage; the preview caller retains the proposed update for review. Stable event identity must come from the evidence/discovery stage. Only the latest development per event is selected from one batch.

## Generation and review

The bounded pipeline is evidence validation → plan → structured article → deterministic checks plus semantic editorial review → at most one revision and re-review. Maximum model calls: five. Provider failure, malformed structured output or persistent issues hold the draft. A planner veto ends generation. Factual and substantial editorial issues both trigger a revision.

The article contains a title, dek, two or three takeaways, and two to five short sections chosen for the story. Paragraphs distinguish facts, interpretation and unresolved questions. Fact and interpretation paragraphs carry claim IDs; code converts those into escaped, canonical source links. The writer supplies plain text, never trusted HTML. The final title is reviewed with the article and is not regenerated afterward. The total visible text must fit a plan of 300–700 words.

The result contains `status` (`draft_ready`, `hold` or `rejected`), `publishable: false`, `article_type: "financial_news"`, `article`, `article_html` when structurally safe, `plan`, `evidence`, `validation`, `reviews`, `prompts`, call/revision counts and optional artifact paths. The requested model is recorded as a request; actual returned model/usage belongs to the injected provider's records.

An explicit `output_dir` writes a private `preview.html`, article fragment, evidence/plan/article/review artifacts and manifest. Known web roots and directories containing publication indexes are rejected. No live access restrictions are applied. Local HTML is marked as a development preview and excluded from indexing.

## CLI and tests

Run from `blog`:

```text
python news_pipeline.py --packet packet.json --out /tmp/smn-news-preview --dry-run
python news_pipeline.py --packet packet.json --out /tmp/smn-news-preview --responses responses.json --now 2026-09-05T16:00:00Z
python news_pipeline.py --packet packet.json --out /tmp/smn-news-preview --generate
python -m unittest discover -s tests -p test_news_pipeline.py -v
```

`packet.json` contains `event` and `research`; replay responses are a JSON list of plan, article and review objects, plus revision/re-review responses when needed. Dry-run and replay make no paid calls. Live generation requires the explicit CLI switch or a deliberate Python call. Offline tests use clearly fictional evidence and cover freshness, corroboration, event deduplication, same-ticker news, optional daily capacity, major-event overflow, citation failure, bounded revision, publication isolation and safe provider failures.
