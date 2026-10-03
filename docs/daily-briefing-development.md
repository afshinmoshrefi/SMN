# Offline daily briefing foundation

TW-TASK-0018 / P10 preparation and P11 readiness only. This does not change the
article pipeline, model policy, dashboard, schedules or publication. Nothing is
submitted to ElevenLabs. There is no paid OpenAI API path.

Use Python 3.9+ with `jsonschema` and an installed America/New_York timezone
database (`tzdata` on Windows). The development checks used Python 3.12.
From `blog/`:

```powershell
py -3.12 daily_briefing.py validate --input examples/daily-briefing/2026-10-02.json
py -3.12 daily_briefing.py package --allow-held --input examples/daily-briefing/2026-10-02.json --output C:/private/smn/briefing-20261002-revision1
py -3.12 elevenlabs_briefing.py --input examples/daily-briefing/2026-10-02.json --config examples/daily-briefing/elevenlabs-unverified.json
py -3.12 -m unittest tests.test_daily_briefing tests.test_elevenlabs_briefing
```

Choose a new **private** output directory outside any web/public root. Packaging
refuses an existing directory, retains evidence and writes `review.html`, revision,
validation and hashed receipt. Validating a draft returns success with explicit
`pending_editorial_review`; that means mechanically usable, not approved. The
unverified ElevenLabs plan deliberately returns exit code 1 with its hold reasons.
No discovery/model call occurs: the author supplies original prose and evidence.
The dated example returns exit code 1 because Reuters' timestamp is unqualified.
Normal packaging refuses held input; `--allow-held` explicitly exports a held
private artifact with hold reasons and exit code 1. It never records approval.

The schema fixes edition date, New York timezone and an offset-bearing cutoff.
Publication/update versions after cutoff are held. Retrieval after cutoff is
reported as retrospective: a reviewer must verify the source represented that
information window. `reported`, `background` and `upcoming` distinguish same-day
results, older context and future events. A future scheduled release is not its
result. Source access failures/snippets can be recorded as leads but cannot
support a claim. Captured text hashes and exact quote locators bind each factual
claim; access metadata and quote relevance still require editorial verification.
`scope` distinguishes a complete document from selected passages retained after
reading the article/release. Do not label a snippet or search summary full text.
Unknown source offsets use `published_at: null`, `published_at_raw` and
`timestamp_status: uncertain`; they hold cutoff qualification instead of silently
inventing a timezone. A captured version cannot be updated after retrieval.

Group by underlying `event_key`; repeated keys are held. `origin_id` is the
original reporting organization, even when a different publisher republishes
Reuters. Coverage reports distinct origins, not the number of syndication URLs.
An exclusive can stand alone. Selection/exclusion reasons are required. There is
no fixed number of stories and no mandatory ticker, chart or 10-15 second limit.
Preferred-source gaps remain visible in review; no complete scan is asserted.

An actual editorial review is supplied separately via `--review`:

```json
{"status":"approved","briefing_sha256":"<validation binding>","reviewer":"<actual reviewer>","reviewed_at":"<timestamp with UTC offset>"}
```

Only record approval after checking factual support, qualifications, timing,
interpretation labels, story importance, source allowances and all prose/visuals.
The whole revision hash includes captures, selection, script and storyboard.
Any edit invalidates that review; rejected reviews hold the package. These local
records are evidence bindings, not cryptographic reviewer authentication.

The October 2, 2026 example is a **dated retrospective draft**, never current news
on a later date. Full source pages were accessed for BLS September employment,
Reuters market close via London South East, and the G7 statement from the Prime
Minister's Office via CNW. Only small selected supporting passages are retained;
Reuters contributes one reporting origin. Its displayed 21:23 time has no explicit
timezone; its raw timestamp is retained without assigning an offset. Public-page
metadata retrieval failed, so Reuters-dependent cutoff qualification is held.
Three narratives are represented: jobs/equities, Nike demand, and energy security.
This is a limited accessible-source pilot, **not the completed daily scan**.
Company-primary evidence, full preferred-source coverage and editorial review
remain outstanding. Script delivery duration, source visual rights and media QA
are unmeasured. Headline cards are proposals; no generated presenter is present.

The ElevenLabs module needs explicit operator verification of account, existing
voice, personal likeness/reference, model, exact route, settings and cost budget,
plus generation authorization and the bound editorial review. Never guess IDs or
substitute a stock presenter. It returns only an offline plan, even when every
prerequisite is supplied. `manual_avatar` records manual steps;
`flows_video_api` lists a candidate endpoint with **no submission payload**.
Exact model/asset payloads require account qualification in later authorized work.
Reusable [avatars](https://elevenlabs.io/docs/overview/capabilities/image-video/avatars)
and the newer [video API](https://elevenlabs.io/docs/api-reference/flows/video/create)
are different capability surfaces (documentation checked October 3, 2026 UTC).
Neither offline readiness nor an app-generated sample proves unattended daily
automation. Unknown charged outcomes must be reconciled before retrying later.
