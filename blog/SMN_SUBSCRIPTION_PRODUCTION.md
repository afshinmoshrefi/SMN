# Subscription reader cutover

Status: candidate preparation; not approved for production activation. The exact
commit and validation results are recorded in shared task TW-TASK-0009.

The isolated runner reads the existing daily selector's same-date CSV and exports
the selected studies from TradeWave. It does not need API-written articles. One
immutable selection/engine/hero package feeds separate provider directories.
ChatGPT is the only reader publisher. Claude writes the first two selected daily
editions for comparison; their dates are retained in `comparison-state.json`.
Rerunning a reserved date resumes it. Later dates remain ChatGPT-only; comparison
completion is visible per provider in `comparison.json`, never inferred from a timer.

Research, writing, review, and visual model turns use supported saved subscription
logins. There is no paid article-writer fallback. Hero prompt/image/check calls use
the existing paid API workflow, once per shared input package. Those costs are
separate: `input-heroes.json` records models/provenance and explicitly marks unknown
usage/cost rather than claiming they are free. A partial hero request is held for
inspection to avoid duplicate charges.

## Private authentication

Use a private operator terminal and the official browser login. Do not put codes,
tokens, browser credentials, or authentication files into chat or the repository.
Do not copy Windows login files to either server.

On SMN Dev (`192.168.1.180`), the already installed qualified CLI is:

```sh
/opt/smn-codex-0.155.0-alpha.16/node_modules/.bin/codex login --device-auth
```

If device login is disabled, enable it in the account/workspace security settings
or use the official SSH-forwarded browser login. Official instructions:
https://developers.openai.com/codex/auth#login-on-headless-devices

Production currently needs its own Codex installation and login. This is an
operator production write, subject to the current-day snapshot gate. Pin the same
CLI version that completes Dev qualification; do not upgrade during cutover.
Claude must also pass `claude auth status --json` with a first-party `claude.ai`
subscription. Authentication preflights do not generate model turns.

## Qualification

Use a clean isolated checkout of the candidate on Dev. Never overwrite the dirty
`/home/flask` checkout. Run the focused tests recorded in the release manifest,
then qualify a fresh edition with actual subscriptions and final desktop/mobile
checks. No production API hero generation is permitted from this qualification
session; stage retained, hash-bound hero assets or qualify an explicitly approved
Dev-only hero callback. Production must not be activated until this full check
and Dev public publication/landing proof have passed for the exact candidate.

The existing `smn_daily.py --publish` remains Dev-only. Qualify the new controller
with `--target dev --publish` in a separate private state root; it requires staged
hero receipts and refuses to generate heroes on production. The new
`smn_subscription_daily.py --root ROOT --date YYYY-MM-DD` generates private
provider editions; `--publish` invokes the separately guarded production adapter.
Production-shaped article HTML receives its canonical URL and robots policy
before visual review. It cannot be relabelled as a Dev package after review.

## Operator activation after qualification

1. Confirm today's production **web and appserver** snapshots and exact-release
   production approval in the release conversation. Record their references,
   UTC date, source commit, and approver in a private JSON file with keys
   `date`, `source_commit`, `production_web_snapshot`, `production_app_snapshot`,
   and `approved_by`.
2. Obtain the exact Dev qualification receipt (`status: dev_qualified`,
   `source_commit`, `live_verification_sha256`) and clean committed candidate.
   Install that checkout under `/opt/smn-subscription/releases/COMMIT`. Preserve
   `/home/flask`, `/opt/smn-shadow`, their local changes, and existing credentials.
3. Install the qualified Codex CLI and log in privately. Verify both subscriptions,
   the existing `/opt/smn-playwright` browser runtime, the existing engine export
   SSH key, and `/home/flask/venv` dependencies. Neither CLI receives API overrides.
4. Wait for an in-progress `smn-shadow.service` to finish. Run as the production
   operator, using the concrete commit/path recorded in the release manifest:

```sh
/home/flask/venv/bin/python /opt/smn-subscription/releases/COMMIT/blog/install_smn_subscription.py activate \
  --dev-proof /PRIVATE/dev-qualification.json \
  --snapshots /PRIVATE/current-day-snapshots.json \
  --codex /opt/smn-codex-0.155.0-alpha.16/node_modules/.bin/codex
```

The installer verifies host, source, proof, snapshots and subscription logins;
snapshots the scheduler/configuration; disables the old API daily queue cron;
gates quote updates during the publication transaction; replaces the old shadow
timer with a weekday 03:00/06:00 UTC resumable subscription timer. Daily selection
and existing email scheduling stay in place. The operator receives an exact
scheduler rollback command. Installation failures restore the prior scheduler.

Each reader publication reuses the existing catalog/pin lock, native homepage and
search renderer, sitemap/feed functions, snapshots, byte checks, browser checks,
and live landing visual review. A failed live check rolls the edition back.
The immutable private release records retain both successful and held outcomes.
An explicitly rolled-back publication requires inspection before reactivation;
the controller does not hide it by creating a replacement edition.

## Recovery and comparison

Inspect `/var/lib/tradewave/smn-daily/subscription-primary/DATE/comparison.json`,
`HOLD.json`, and each provider's saved jobs. Fix login/quota and rerun that same
date to resume without rewriting completed articles. Article-specific editorial
holds stay held. Do not delete attempts, change the frozen input package, change
model roles mid-edition, or resume a production-shaped edition as a Dev edition.

Keep Claude's comparison output private. The public catalog accepts only
ChatGPT/OpenAI subscription writer provenance. Review both saved editions after
each of the two comparison days; retaining output is not evidence of a successful
run. Amanda monitoring is outside this change.

## Editorial completion contract (September30 correction)

Research must capture the relevant management explanations, counterevidence and event calendars,
then bind 1–8 material items to exact primary quotations before writing. Discovery attempts all
four candidates instead of stopping at the first two pages. A missing required account holds
research; a secondary summary is not evidence that the causal account is complete.

The configured independent reviewer sees the raw captured primary text and returns coverage and
claim ledgers. Required omissions, unsupported causal/event assertions, instrument errors and
incorrect cohort overlap block even when labeled minor. Cohort checks compare engine-provided
year identities only; no financial statistics are recalculated. Generic conditional outlook
analysis does not establish an event calendar. Factual upcoming events need a future date in
captured primary evidence.

The controller permits one editorial correction and a fresh review, then holds on failure.
Mechanical, editorial, rendered HTML, screenshot inputs and subscription job receipts must all
match the current article before it counts as complete. Scheduled, resumed, manual finalize,
packaging and activation paths enforce the gate. Old packages lacking gate version1 are held;
do not retrofit approvals or overwrite historical evidence. Revise sources explicitly and run
fresh review/visual jobs when the evidence or copy changes.

Offline regressions verify known bad excerpts, clean control boundaries, custody failures and
bounded recovery. They do not demonstrate that a live model detects every semantic omission.
Actual provider qualification remains blocked by the missing saved ChatGPT login; no new
production publication is approved by these offline checks.
