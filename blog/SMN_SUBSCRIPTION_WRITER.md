# Private SMN subscription writer pilot

SMN can hand a prepared assignment to the official Codex CLI using a saved
ChatGPT login and receive structured article text for its existing renderer.
This entrypoint is private. It does not replace the live API writer, attach to
the daily queue, install a service, or publish an article.

The September 7, 2026 pilot used Codex 0.153.1, `gpt-6-astra`, and `xhigh`.
It ran on the owner's Windows machine using its existing CLI login. The CLI
was installed separately for the test; the user's global CLI was not changed.
The branch continues the accepted private SMN continuity source at `569c7fc`.

## Contract

1. An SMN source adapter validates current evidence and numerical conventions,
   then creates an immutable prepared job with `prepare_job`. Supply an explicit
   as-of time, expiry, evidence digest, prompt and output schema. Use fixed enums
   for commissioned angle IDs and chart IDs. The prompt must contain original
   source passages, chart values and all material editorial obligations.
2. Invoke `run_job` with the absolute path to the official CLI executable. It
   requires a ChatGPT account and the requested Astra effort in the account's
   catalog. API billing overrides are removed only from the child environment.
   No login is changed and there is no API/model/effort fallback.
3. The model gets prepared evidence through standard input. Shell, browser,
   app and agent tools are disabled. The CLI is ephemeral and read-only. SMN
   stores the JSON output, usage, input hashes and receipt outside live content.
4. `subscription_edition.receive_draft` verifies the receipt and evidence, runs
   existing SMN structural/source checks, and renders the private draft with
   supplied TradeWave charts, exact study links, extra charts and hero.
5. Perform an independent editorial review of the whole reader package. Include
   the actual displayed statistics table, source passages, chart values and
   exact link destinations. A table-presence flag is insufficient review evidence.
   Bind review results to the final article/evidence hashes. An editorial pass
   does not authorize publishing.

The receiver can normalize an exact commissioned angle followed by a colon
and explanation into the commissioned ID. It retains the raw output and an
audit of the metadata change; it never silently rewrites reader prose.

Example entrypoints, from this directory:

```text
python subscription_writer.py probe PREPARED_JOB_DIRECTORY --codex ABSOLUTE_CODEX_EXECUTABLE
python subscription_writer.py run PREPARED_JOB_DIRECTORY --codex ABSOLUTE_CODEX_EXECUTABLE
python -m unittest -v test_subscription_writer
```

`prepare_job` and `receive_draft` are Python interfaces for a future SMN adapter.
The CLI above intentionally cannot create an assignment from an unverified
symbol alone. This pilot's source-specific preparation and review harness is
saved with its local evidence, not imported into the live application.

## Duplicate, failure and billing behavior

An exclusive claim and a state/receipt recheck under that claim prevent two
workers from generating the same ready job. Completed receipts are reused.
Changed or expired assignments cannot start. Failed jobs retain diagnostics
for explicit recovery and are not retried automatically. A crashed process can
leave a claim that requires an operator to verify and recover; automatic lease
reclamation is not implemented. Run one serialized stream per account login.

The JSON schema checker covers only the explicit type/enum/object/array/string
subset used by this bridge. It is not a general JSON Schema implementation.
Structural checks are not semantic fact checks or image inspection. The final
preview remains held pending the separate review process.

Usage snapshots use documented read-only app-server account/model RPCs, with
no model turn. The experimental app-server transport is measurement plumbing;
article generation uses the documented non-interactive `codex exec` interface.
The snapshot omits account IDs and email. No raw credentials are read or logged.
The account-wide percentage is coarse and may include other work. Output token
counts include reasoning tokens; do not add reasoning a second time.

## Observed pilot result

One new COST article used the same frozen September 7 evidence and image/chart
bytes as the earlier comparison, with no earlier article prose in its first
writing prompt. It used four Astra Extra High turns: writing, review, one
targeted repair, and final review. The turns consumed 65,801 input and 19,282
output tokens, including 15,040 reasoning tokens, and 699.906 seconds of worker
time. The final text passed all seven independent editorial checks; desktop
and mobile checks passed with both native charts and exact study links intact.
The inherited hero's unwanted wordmark remains a separate asset review item.

No new paid OpenAI API, Tavily or image call occurred. The refreshed API
dashboard remained at $15.25 and 68 requests after the test. This demonstrates
the prepared-job writing/review handoff, not a complete fresh discovery run,
an unattended server deployment, a cost per approved article, or the weekly
capacity of a $20 subscription. All prose, receipts, checks and findings are
local under `smn-review-20260905/subscription-article-20260907/` in the owner's
orchestrator workspace. Eleven focused offline tests passed.

## Dedicated account logistics

Install a pinned official Codex CLI under a dedicated operating-system user
with its own persistent home and private input/output directories on an
always-on SMN worker. The owner signs the worker into the dedicated ChatGPT
account once through browser/device authentication. Codex maintains the login;
an expired or revoked session holds jobs for sign-in, not API fallback.

Have SMN mark an assignment ready only after research, data, charts and the
brief are complete. A serialized worker consumes that ready job, returns the
draft, and SMN validates/renders it for review. The data schedule is the trigger;
there is no need to synchronize a separate ChatGPT reminder with data readiness.
Grokbot or Hermes can later be the launcher, but neither was used or verified
as part of this pilot. The server integration and eventual publication gate
remain future work. The owner's desktop need not remain on for a server worker.

Official references checked September 7, 2026:

- https://learn.chatgpt.com/docs/non-interactive-mode
- https://learn.chatgpt.com/docs/auth
- https://learn.chatgpt.com/docs/auth/ci-cd-auth
- https://learn.chatgpt.com/docs/app-server

OpenAI generally recommends API keys for CI/CD. Its account-auth guide also
documents advanced trusted private automation when the workflow specifically
needs the Codex account. Keep one persistent login per serialized worker and
let the official CLI own refresh; never place credentials in repository files.
