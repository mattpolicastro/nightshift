## Why

A hung background command can remain pending indefinitely, without escalation or a line in `nightshift status`. Delivered attempts retain logs and idea inputs indefinitely. Daemon git timeouts can leave transport descendants running. Error and approval comments also lack retry reconciliation after an ambiguous POST, so deadline reporting would otherwise duplicate comments.

This change builds on #8's stop controls and #34's stopped-job release. Both dependencies are represented in the current repository. The accepted retention policy supersedes deleting whole job directories: compact delivered attempts while preserving their historical identity, result and request mtime.

## What Changes

- Persist per-command start times and deadlines; escalate an overdue live attempt on the next eligible poll through the existing human-intervention path, retaining its identity and recovery controls.
- Show active background attempts, elapsed time and deadline state in `nightshift status` using local inventory.
- After durable delivery, remove the attempt's log and owned idea input, redact idea arguments, and retain a minimal request plus result and control records. Preserve request mtime and touchpoint history through receipt replacement and restart.
- Extend only the comment-marker reconciliation regex in `nightshift/queue.py` to recognize draft/admit/revise/publish error markers and approval markers.
- Run daemon git commands in a new session and kill their process group on timeout. Preserve the existing 30-second command budget, retry behavior and pause checks.
- Update the affected job passages of `README.md`, `KNOWN-LIMITATIONS.md` and `docs/specloop/README.md`, including the guide's drafting-input and automatic-retention claims.

Implementation scope is `nightshift/specloop_job.py`, the required deadline/delivery/cleanup hooks and `_bounded_run` in `nightshift/specloop_intake.py`, status rendering in `nightshift/cli.py`, the marker regex in `nightshift/queue.py`, minimal `nightshift/daemon.py` runner wiring, corresponding tests, and those three documentation files. Existing tests receive only the explicitly justified schema, runner-seam, status-read and delivered-input assertion migrations described in the design.

Out of scope: automatic background-job termination or retry, stopped-job evidence deletion, bulk orphan/history purging, scrubbing idea text from workflow receipts or results, build-feedback deletion, worker behavior, supervisor execution, configuration knobs, changes to touchpoint reporting code or specloop's JSON interface, other daemon behavior, and changes to GitHub retry policy beyond marker recognition. Process-group termination cannot roll back remote effects or stop descendants that leave the group.

## Capabilities

### New Capabilities

None; this extends existing capabilities.

### Modified Capabilities

- `background-job-control`: durable command timing, active-job status, and safe delivered-attempt compaction without losing inventory or stop/release guarantees.
- `issue-intake`: overdue escalation, retry-safe error/approval comments, delivery completion hooks, process-group git timeouts, and a narrowed lifetime for delivered background idea files. The durable drafting payload requirement receives a MODIFIED delta because indefinite retention conflicts with requested cleanup.
- `touchpoint-report`: preserve the existing report's round counts, identities and date-filter behavior when producers compact delivered attempts; no reporting-code change.
- `specloop-documentation`: separate deadline, status, retention and git-timeout guidance, plus a narrow clarification permitting the explicitly specified runtime-test migrations.
