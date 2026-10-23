## Why

One specloop repository's missing manifest can prevent configuration from loading for every repository. Switching from worker to specloop strands worker claims, and basename-only change names collide across owners in specloop's global run directory.

Blocked by #6, #8 and #67. This draft builds on their landed feedback, pause/job-control and documentation contracts.

## What Changes

- Resolve manifest paths in configuration, but validate availability, regular-file status and manifest contents per poll. Report failures as repository health; healthy repositories continue.
- Hold new drafting, including spec-stage re-drafting, and admission while enrollment is unhealthy. Already-admitted runs keep publishing and taking build-stage revisions using their copied manifest and recorded branch.
- Reconcile leftover worker claims on specloop enrollment and restart without launching workers or discarding surviving work.
- Name new changes `<owner>__<repository>-issue-<number>-<title-slug>`. Preserve spelling; if the generated branch fails existing validation, report unsupported repository health and dispatch no new work. Do not encode names or relax safety checks.
- Retain recorded legacy identities and bounded exact-branch PR recovery. Do not rename existing runs.
- Update the relevant enrollment, recovery, naming and limitations passages in `README.md`, `docs/specloop/README.md` and `KNOWN-LIMITATIONS.md` as #67 leaves them.

Implementation scope is `nightshift/config.py`, `nightshift/daemon.py`, `nightshift/specloop_intake.py`, relevant tests and those three documentation files. Out of scope: changing `nightshift/queue.py` or transport retries; automatic migration of deployed runs, branches or PRs; name encoding; hot configuration reload; changes to supervisor, provider, evidence or unrelated operator workflows. This draft edits only its OpenSpec change directory.

## Capabilities

### New Capabilities

- `config-resilience`: repository-local manifest health and durable worker-claim reconciliation on specloop enrollment.

### Modified Capabilities

- `issue-intake`: owner-qualified new identities and readiness for new drafting/admission; publication stays unchanged.
- `pr-revision`: readiness for new spec-stage drafting; build-stage revision stays unchanged.
- `specloop-documentation`: operator guidance for the three behaviors and their residual limits.
