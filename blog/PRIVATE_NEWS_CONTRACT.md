# Private news adapter and reader review

This is an explicit private entry point. It does not fetch, schedule, publish,
activate access rules, or introduce probabilities. General news does not require
a seasonal pattern, historical return measurement, ticker, or chart.

## Input and preparation

`private_news.prepare_news_candidate(candidate, as_of=...)` expects the existing
market-policy candidate fields plus `news_packet: {event, research}`. This is
the inspected packet accepted by `news_selection.validate_event`, including
source excerpts, URLs, published/verified timestamps and claim/source links.
Macro news may use `instrument: {}`. Priority and exposure IDs remain explicit
editorial inputs, not forecasts of audience engagement.

Use `question.answer_claim_ids` and `priority.claim_ids` to name source-supported
claim IDs in the packet. The adapter maps those references into the lineup's
`answer_evidence_ids` and `evidence_ids`. Unknown references cause the lineup to
hold the candidate. It does not fill missing question/priority support itself.

Every preparation discards supplied eligibility gates and event bindings, then
revalidates the packet against the explicit clock. The resulting `news_gate`
binds event ID, development ID, occurred_at, fact IDs and source evidence IDs.
It expires at the earliest event/source deadline. It is a deep-copied snapshot,
not a signed credential: mutate neither prepared candidates nor their gates;
recompute them immediately before selection and generation.

Source evidence IDs hash canonical URL and the upstream retrieved body hash
when present, otherwise the inspected excerpt bytes. They survive citation
renumbering and reworded claims. The body hash is upstream provenance, not proof
of remote truth. `news_validation.evidence_index` preserves both body identity
and the actual excerpt hash. Fact IDs hash normalized claim text and source URLs;
these hashes do not prove semantic novelty.

This adapter never carries a caller's `supersedes_development_ids` into the
derived event. Same-event updates need a separately reviewed novelty relation;
this first adapter conservatively consolidates them. The earlier standalone
news-discovery update workflow is unchanged. A verified distinct new event can
still pass the lineup despite recent coverage of that ticker or exposure.

`validate_event` checks source/date/support contracts. It does not prove a
paraphrased claim is entailed by a source. Source inspection is the upstream
trust boundary, and the subsequent article reviewer must check actual support.
Never turn an LLM's assertion of verification into an inspected packet.

## Drafting

`generate_private_news(candidate, as_of=..., send=..., output_dir=...)` repeats
preparation and market-lineup checks, then calls:

```python
run_news_article(event, research, now=as_of, send=send,
                 reader_policy="private_v2", reader_question=candidate["question"])
```

The existing plan/write/review/revise/re-review budget remains at most five
provider calls. The private plan adds a reader promise and exact evidence
references. The selected question and ID must be retained. Review instructions
are appended to the existing review call, including the exact rendered HTML
and its hash. A missing or stale review keeps the text held; one existing
revision may repair it. Reader-review issues are included in that revision.

The opt-in requires `reader_promise.py` from the integrated second pass. Omitting
`reader_policy` retains the original news workflow. The CLI optionally accepts
`--reader-policy private_v2`, including its no-call `--dry-run` path.

Successful text reports `status=draft_ready`, `text_ready=true`,
`visual_ready=false`, `publishable=false`. The absent hero remains pending a
separate actual-image review. Reader brief, plan, review, validation, prompts,
article and provenance remain inspectable in private artifacts.

## Verification

`tests/test_private_news.py` uses explicitly fictional offline news fixtures.
It covers gate recalculation, missing/unverified/stale evidence, expiry,
source renumbering, same-body excerpt changes, unsupported questions, selected
question integrity, exact article/review binding, the unchanged call budget,
and independent text/visual readiness. The frozen real jobs packet is an
external private replay input, not an invented fixture or current-news claim.
