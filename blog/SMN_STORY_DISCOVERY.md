# Timely stories and optional historical context

Private development only. No services, schedules, access tiers or publishers are activated by these modules. This extends `SMN_EDITORIAL_CONTEXT.md` and the existing private selection path.

## Commission a reason to read now

`current_context.py` requires a source-bound `story_connection` on each seasonal assignment. Its trigger is the current seasonal window, a recent material development, or a verified upcoming checkpoint. Old results can explain the business backdrop; rechecking them does not turn them into new news. Seasonal triggers bind the actual, nonexpired window; recent developments must be within seven days. The first two body paragraphs must explain the timing and connection before a fundamentals recap. Independent passage-bound review checks `publication_reason` and `story_connection` against both current facts and history. A semantic review is a useful check, not proof that readers will find the copy compelling.

## Research unusual stock activity

`stock_activity.py` calculates completed-session share volume from supplied rows. Provisional discovery defaults are at least twice the prior 20-session median, at least the 95th percentile against the prior 60 sessions, and median volume of at least 250,000 shares. The current session is excluded from both comparisons; percentile counts strictly lower observations. Duplicate dates, gaps, incomplete sessions, stale data and invalid values hold the input. An explicit universe is limited to 1,000 instruments. Thresholds have not been validated as an optimal editorial policy or return predictor.

EODHD's volume is already split adjusted. TradeWave's CSV adjustment code modifies OHLC with the adjusted-close ratio and leaves that provider volume unchanged. Do not adjust volume again. Price-change evidence is explicitly based on provider split/dividend-adjusted closes.

An `activity` candidate supplies `volume_input`, a reviewed `activity_editorial` materiality assessment, a reader question, priority evidence, and any inspected company context in `news_packet.research`. `private_news.py` recalculates the measurement, constructs its event claim and applies the ordinary editorial lineup. A caller-supplied passing gate cannot bypass arithmetic. This is discovery for research, not automatic publishing or an inference about investor identity, buying intent or future direction. Validated latest completed-session alerts receive a bounded holiday/weekend freshness allowance; ordinary news keeps its existing 48-hour default.

## Check history for news without requiring a pattern

`news_seasonality.py` evaluates `research.seasonal_research`: the exact event ID, reviewed timestamp and research scope, and one to four instruments with a relevance reason and a window chosen before inspecting returns. Each contains the reviewed instrument manifest and raw history/session manifest used by `private_history.py`. Statistics are recomputed; supplied eligibility flags are ignored.

The existing cohort policy uses a fixed 20-year calendar span, reports actual available observations and documented missing years, and retains recent-period and election-cycle contrasts. It does not silently substitute a more favorable lookback. News eligibility is independent of historical availability. An absent input means **unavailable**, not **no pattern**.

When context is available, the plan records include/omit and a reason. Inclusion uses one compact paragraph and at most two relevant instruments. Code renders the detailed comparison tables, while the reviewer checks the actual paragraph's connection and material qualifications. An explicit reader question about historical context must be answered or held; the planner cannot promise that answer and omit it. Calendar returns are not event-conditioned returns, remaining-window returns or predictions of policy decisions. Fund-price returns are not yield changes.

All calculations are deterministic Python. News prose should explain the development and reader consequence; it should not copy source-audit limitations into an opening lecture.

## September 6 validation and source scope

The private scan read 500 of the 503 symbols in TradeWave Dev's existing S&P 500 list; three CSVs were absent and four supplied series were held. Four research candidates were flagged: LULU, EFX, TSN and CDNS. LULU's September 4 volume was 37,426,243 shares, 12.00 times the prior 20-session median. No provider refresh or production data write was performed.

The old US/SPY copy ended August 14 and was held. The current ETF/SPY series was separately identified and used with its complete identity. TLT also came from the ETF resource. Do not treat exchange/resource aliases as interchangeable. The jobs example is an explicit September 6 weekend follow-up to the September 4 release with a private 72-hour freshness policy; it does not change automatic discovery's default or pretend to be a fresh release.

Local evidence and generated previews: `smn-review-20260905/story-discovery-20260906/`. Model requests, responses, usage, exact input/source hashes and unsuccessful drafts are retained. Article generation uses Astra at the previously chosen low article effort. No calibrated-probability feature was added.

## Options research

Tavily can discover attributed reporting but is not a complete options-volume database. A bounded read of OCC's documented `volume-query` endpoint returned September 4 LULU CSV rows grouped by option symbol, account type, call/put and exchange. This proves access, not a validated unusual-options scanner. Account-side quantities, adjusted option roots, history coverage and baseline calculations require a dedicated adapter before article claims. This pass does not label options flow bullish/bearish or activate an options scanner.

Primary references: [EODHD field definitions](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes), [OCC batch query](https://www.theocc.com/market-data/market-data-reports/other-market-data-info/batch-processing/volume-query-batch-processing), [OIC volume and open-interest definitions](https://www.optionseducation.org/referencelibrary/faq/general-information), [Tavily search](https://docs.tavily.com/examples/quick-tutorials/search-api).
