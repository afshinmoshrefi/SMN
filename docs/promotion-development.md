# Private promotion generation

The dashboard uses `promotion_jobs.create(root, kind, inputs, actor)`, `list_jobs(root)`,
`get_job(root, id)`, `transition(root, id, action, expected_version, actor, data=None)`,
`pause(root, scope, enabled, actor)` and `get_artifact(root, id, name)`.
Root and configuration are server-owned. Inputs use canonical article or briefing identity,
exact source revision/hash and source-bound script; no browser-supplied filesystem paths.
The integration must compare these against the current ContentStore revision before creation.

Job summaries contain id, kind, stage, status, version, source_revision, source_hash,
review_status, generation_status, dispatch_status, holds, artifacts, updated_at,
attempts, actor and error. Artifacts expose safe relative_path, name, media_type,
sha256 and review_status. Authenticated downloads use `get_artifact` for containment
and byte-hash checks. Private storage and receipts must remain outside public output.

Actions are retry, cancel, edit and review. Edit returns a new immutable job ID and
supersedes the old job. Optimistic version conflicts require reload. Running or unknown
provider outcomes cannot be canceled/retried as though no generation occurred.
Pause applies to all or a specific generation kind. Dispatch is disabled throughout.

`public_derivative_jobs.generate(root, prepared, settings, codex)` uses existing
immutable subscription CLI jobs and validates source-bound output. It needs an explicit
supported model/effort and does not change the existing provider configuration.

`promotion_worker.run_one(root, id, configuration)` implements derivative writing,
article_video, daily_briefing writing/narration/cards, private social/Substack exports,
and verified actual ElevenLabs avatar delivery import.
Its server callback `resolve_source(inputs)` returns prepared, copy, source_review,
chart_path, chart_sha256, title and on_screen. The worker rechecks retained hashes,
canonical revision, derivative claims, exact script/chart and independent source review
before any provider call. Configuration supplies generation_enabled, voice_verified,
model_verified, credential_ready, voice_id, model_id, voice_settings, quote and optional
ffmpeg/ffprobe/font paths. Missing readiness produces a truthful hold.

Source/script review permits generation only. Post-generation `review` binds current
inputs and output hashes, and still does not authorize external publication.

ElevenLabs speech uses only server `ELEVENLABS_API_KEY`, fixed vendor API, verified
exact-request credit quote, a durable shared 5,000-credit ledger and two attempts per
request/asset. Submitted unknown outcomes reserve budget until reconciled. No purchases,
plan changes, automatic top-ups or provider fallback occur. API errors are sanitized.
Existing authenticated UI pilots retain before/after balance, exact script/copy hash,
native download name and audio hash in private receipts; charge reservations share the ledger.

The compositor requires complete real narration lasting 10-15 seconds, preserves the
native chart for six seconds, renders 1080x1920 H264/AAC, probes and decodes the result,
and writes a pending-review receipt. Automated decoding is not listening or visual approval.
Daily resolver records return `briefing`, `review` and `active_revision` for speech;
`source_hash` is the exact briefing digest. Alternatively a first-writing record returns
`source_bundle` and `active_revision`, with source_hash equal to its digest. That runs
the existing immutable subscription writer and returns actual editorial-review artifacts;
approved narration is a subsequent separately bound job. Derivative first-writing records
require only prepared, with explicit writer_settings and codex in server configuration.
Existing job IDs are retained throughout writing/export execution.

The runnable `briefing_sources.py capture --manifest ... --output ...` records actual
public documents, hashes and access failures. Its editor-owned manifest gives canonical
publisher URLs/origins; restricted pages remain metadata. The `write` action requires
explicit subscription CLI/model/effort. No paid fallback or schedule is created here.
An uncertain publication time can qualify only through the exact hash-bound version
actually observed before cutoff; the uncertainty remains visible.

Avatar UI generation is supported in the existing account but personal reference upload
currently needs the Chrome extension file-URL permission enabled by the user. API automation
is not claimed until the actual avatar route is account-qualified. Worker import uses
server avatar_ready/likeness_verified, verified voice/reference hash and avatar_delivery
(private path/provider receipt). Receipt binds briefing, actual intro/outro script,
voice/reference, video hash, provider generation ID/model and received shared-budget
reservation. Only real <=8-second intro/outro deliveries are admitted for pending review.
No stock substitute, fabricated avatar success, external post or provider switch occurs.

Publication aliasing uses optional keyword `publication_identity` on
`public_derivative.prepare_derivative`: canonical_id, source_original_id,
article_sha256, revision, binding_sha256 (digest of the first four fields).
The publisher supplies it only after binding the new canonical ContentStore URL/revision
to the exact retained article JSON. Original source/card/engine files remain unchanged.
Preparation and later rebuild validation reject cross-article aliases, stale revisions
and edited mappings; writing validates this complete prepared custody before a CLI call.
