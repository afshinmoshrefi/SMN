# GPT-6.1-Sol Candidate Qualification, October 6, 2026

Status: WIP, not qualified for activation. No Dev or production deployment.

Dorothy (`01a0f25f-c964-7675-91ca-caa44db7c0c4`) delegated one bounded
subscription-backed XLK review at medium effort and local default changes.
This candidate does not take over the daily recovery or continuity release.
Canonical review and ownership: [TW-TASK-0006](https://github.com/afshinmoshrefi/tradewave-tw2/blob/codex/smn-approved-release-handoff-20261005/docs/tasks/TW-TASK-0006.md),
review ID `SMN-SOL61-20261006`. No peer agreement is claimed.

## Changes

Based on SMN main `185af5cbec060a41958650c1bc709cfc68f0946d`, which already
allowlists `gpt-6.1-sol` and `gpt-5.6-sol`. This patch preserves that work.

| Surface | Previous | Candidate |
| --- | --- | --- |
| Generic `prepare_job` default | Astra / xhigh | 6.1-Sol / medium |
| ChatGPT write and repair role | Astra / high | 6.1-Sol / medium |
| ChatGPT research and review roles | 6-Sol / medium | 6.1-Sol / medium |
| Visual and hero-check roles | Luna / low | Luna / low |

Explicit legacy model choices, stored job manifests, the all-Astra opt-in
workflow, Claude profiles, financial/source/layout gates and budgets are
preserved. There is no automatic fallback. A missing model or effort puts the
job into `failed_needs_review` before a provider process can start.

## Evidence and Limits

Sixteen focused tests pass: `cd blog; python -B -m unittest -v test_subscription_writer`.
New cases use the actual profile and dispatcher to reject an absent 6.1-Sol
model for all three text roles, reject absent medium effort, prevent automatic
retry and preserve explicit legacy manifests. Existing cases cover receipt
reuse, competing completion, claims, expiry, changed inputs, schema rejection
and login failure without paid fallback. All provider processes are mocked.
The baseline had 13 passing tests. `git diff --check` also passes.

Dev's pinned native CLI is `0.155.0-alpha.16`. Successful metadata-only probes
showed a saved ChatGPT subscription with allowance available, but the complete
`model/list` response, including hidden entries and with no remaining cursor,
did not list `gpt-6.1-sol`. Catalog acceptance is not backend model identity.

Automatic approval review rejected the proposed isolated Dev attempt before
execution because it could not find user-authored authorization to transmit
the retained fixture to the subscription model. Zero qualification CLI starts
and zero completed model turns occurred. Facts, numbers, unsupported-claim
detection and instruction following have therefore not been graded for 6.1-Sol.
The previous successful XLK review belongs to 6-Sol and must not be relabeled.

The retained trial uses the original XLK article and TradeWave values, reduced
source excerpts, the actual production review schema and three separately
labelled rejection probes. It permits one native CLI start, medium effort,
no tools, no paid API fallback and no retry. A single successful review would
not establish writer/research quality, visual ability, comparative cost or
durable publication reliability. Same-model writing/review is an additional
qualification limit. No Astra comparison or article regeneration is authorized.

## Activation Gate and Rollback

Do not merge or activate these defaults while model availability and quality
remain unqualified: the missing-model guard would intentionally hold new jobs.
The existing release owner must reconcile the draft, qualify the exact CLI,
account and response identity where exposed, and obtain separate authorization
for any production deployment. This patch does not update or install the CLI.

Rollback would restore the previous profile and generic default from the base
commit, while leaving all immutable jobs and receipts unchanged. No live
rollback or production switch was performed. Production remains on its last
verified configuration. Newsletter delivery is already complete; do not send,
retry or regenerate October 6 mail as part of model qualification.
