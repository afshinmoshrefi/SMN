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

`promotion_worker.run_one(root, id, configuration)` currently implements article_video.
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
Current avatar and other generation kinds stay held until their actual account route,
identity, credit quote and configuration are verified.
