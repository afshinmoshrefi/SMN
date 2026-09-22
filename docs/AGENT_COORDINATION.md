# SMN shared work and peer review

## Canonical homes

- SMN code and this coordination policy: https://github.com/afshinmoshrefi/SMN, branch `main`.
- Cross-agent ownership, findings, discussion, bugs and handoffs: https://github.com/afshinmoshrefi/tradewave-tw2, branch `main`.
- In that shared repository read `docs/WORK_MANAGEMENT.md`, `docs/tasks/README.md`, `docs/bugs/README.md` and the relevant records. Follow its AGENTS.md, CLAUDE.md and Git/deployment policies for changes there.
- [TW-TASK-0006](https://github.com/afshinmoshrefi/tradewave-tw2/blob/main/docs/tasks/TW-TASK-0006.md) is the coordination entry and pending efficiency-review handoff. [TW-TASK-0005](https://github.com/afshinmoshrefi/tradewave-tw2/blob/main/docs/tasks/TW-TASK-0005.md) owns daily publication history. Substantive fixes/optimizations get linked records under the existing shared protocol; do not put every future task in one growing discussion.
- Architecture: shared `docs/TRADEWAVE_ECOSYSTEM.md`; operations: shared `ops/OPERATIONS.md`; SMN daily procedure: [SUBSCRIPTION_DAILY_RUNBOOK.md](../blog/SUBSCRIPTION_DAILY_RUNBOOK.md).

Fetch before reading: a local snapshot, private chat or stale worktree is not current shared state. Use an authenticated existing clone, or SSH `tradewave-vm` and Git as `flask` in a clean task worktree under `/home/tradewave-worktrees/`. Preserve the dirty shared `/home/flask` checkout. Do not copy credentials between agents or hosts. Local Windows SMN Git uses its existing local identity. If shared access fails, report that limitation instead of pretending coordination occurred; do independent authorized work only.

## Claim, work, hand off

1. Fetch both repositories. Read only the relevant current records and changes since the last checkpoint. Identify active claims, discussion requests and live release locks.
2. Search for the existing task/bug, then claim an authorized, non-overlapping scope before editing. Publish executor/provider, unique session, UTC timestamp, repository, branch/worktree, files/surfaces, next action and exclusions. Push to shared main without force and refetch; first accepted overlapping claim owns that scope. Do not take over from an old timestamp alone.
3. Acknowledge pending requests addressed to you in the record. Findings and responses must name evidence, exact versions and uncertainty. Do not reread or regenerate entire editions when a focused check suffices.
4. Checkpoint meaningful findings, changed plans and blockers; update at least hourly during extended work when practical. Before integration, fetch again, read peer updates and preserve their commits. Use separate worktrees; never edit the other agent's checkout.
5. Handoff before stopping: exact pushed commit(s), changed files and why, tests/results, known limitations, environment/release status, evidence access, unfinished work and executable next action. Keep the shared index and task status consistent. Do not store credentials or raw account data in records.
6. Follow existing SMN deployment controls and the Dev activation lock. One agent owns an activation window at a time. Documentation-only changes do not rebuild or activate the site. Do not invent an SMN staging environment or expand production authorization.

## Discuss model, effort and workflow disagreements

Afshin requests joint technical decisions, with current article quality preserved. Neither provider has automatic priority. Challenge a peer's decision when evidence warrants it, but do not silently revert it.

Use a stable review ID in the relevant shared task. Each entry includes UTC time, author/session, recipient, exact candidate commit/configuration, proposal or objection, supporting evidence and requested response. Use statuses `proposed`, `awaiting-peer`, `experiment`, `agreed`, `rejected` or `owner-decision-needed`.

For a model/effort or review-stage change, document:

- Baseline and proposed settings by stage (research, planning, writing, review, operational work).
- Measured input/cached/output/reasoning usage where exposed, retries, elapsed time and observation interval. Label unavailable fields and estimates. A weekly percentage is not a token count or dollar cost; other account activity and resets can affect attribution.
- A controlled comparison using the same authorized source package and unchanged TradeWave study. Reuse existing artifacts first; define a small experiment budget before any new generation, and avoid parallel duplicate trials. No paid API fallback.
- Quality evidence: timely context and opening, reader usefulness, angle, factual/source fidelity, preserved TradeWave study/charts/links, additional visuals and mobile/desktop readability. Do not accept savings by quietly deleting checks or reducing these requirements.
- Reviewer response, remaining disagreement, chosen settings, implementation owner and rollback.

Each agent writes its own acknowledgement. Consensus requires actual responses from both; one agent cannot impersonate the other or treat silence as assent. This applies symmetrically to Codex and Claude. If evidence resolves an objection, record why. If it does not, propose the smallest discriminating experiment. Escalate a material unresolved product/budget tradeoff to Afshin with a concise recommendation, not routine Git coordination.

Keep publication running on the last verified configuration while an optimization is awaiting review. Develop/test candidates in isolation. A pending peer response is not a daily publication hold. Already authorized operational recovery continues under the runbook, with any necessary exception documented and offered for peer review. Real content/data failures still require resolution, not bypass. Changes to TradeWave-owned math always follow the engine authority and owner agreement requirements.

## Delivery and discovery

The shared Git record is the durable message channel. Agents must check it at start/resume, meaningful checkpoints and before integration/deployment, and after a directed handoff. These files do not wake an idle agent or inject messages into an already-running session. Do not claim receipt until it acknowledges. A supported direct session message may point the active peer to the record when available and authorized, but the record remains authoritative. Avoid tight polling or spawning a second agent merely to manufacture peer approval.

New and existing SMN sessions must fetch/read these entry points; the daily runbook links here so the scheduled worker participates too. Keep private provider notes as short discovery pointers, not duplicate status histories.
