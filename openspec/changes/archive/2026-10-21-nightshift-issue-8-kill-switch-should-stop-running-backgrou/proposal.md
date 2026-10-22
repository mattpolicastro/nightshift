## Why

Nightshift launches specloop drafting, admission and revision jobs in separate
process sessions. They survive daemon shutdown, but operators cannot list or
stop them through Nightshift. The pause file blocks daemon actions without
terminating those processes. Completion delivery needs an explicit regression
contract so a job finishing during a pause cannot trigger daemon side effects.

Admission itself writes runnable specloop state before Nightshift delivers its
result. Holding delivery therefore does not prevent the separate specloop
supervisor from building an admitted change.

## What Changes

- Add local, configuration-independent `nightshift jobs` and
  `nightshift jobs stop <id>` commands for durable background jobs.
- Persist process identity and stop state; stop the selected process group,
  preserve its logs, and prevent its result from being delivered or retried
  automatically.
- Preserve pause as a hold on new work and delivery. A job may finish while
  paused; its result waits for resume. Termination is an explicit per-job action.
- Cover process-group termination and paused admission delivery with the named
  acceptance tests, plus restart, race and failure tests.
- Update README operation examples and explain the boundaries between pause,
  job termination, daemon unloading and independently supervised builds.

Implementation scope is `nightshift/specloop_job.py`, `nightshift/daemon.py`,
`nightshift/cli.py`, tests and README. Explicitly out of scope: automatically
killing every job on pause or daemon shutdown; cancelling worker-engine tasks
or already-admitted specloop runs; changing admission or supervisor protocols;
rolling back git/GitHub effects; a job retry command; plugin UI and JSON output.

## Capabilities

- `background-job-control`: discover durable jobs and explicitly terminate a
  running job's process group, with persistent cancellation semantics.
- `paused-job-delivery`: hold launches and completion delivery while paused,
  then resume delivery once. This adds background-completion scenarios to the
  existing `issue-intake` kill-switch behavior.
