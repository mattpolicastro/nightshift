## Why

One specloop repository's missing manifest can prevent configuration from loading for every repository. Switching from worker to specloop strands worker claims, and basename-only change names collide across owners in specloop's global run directory.

Blocked by #6, #8 and #67. This draft builds on their landed feedback, pause/job-control and documentation contracts.

## What Changes

- Resolve manifest paths in configuration, but validate availability, regular-file status and manifest template contents per poll. Use `manifest.template()` with safe representative change/branch overrides, accepting templates that omit either per-change field. Report failures as repository health; healthy repositories continue.
- Hold new drafting, including spec-stage re-drafting, and admission while enrollment is unhealthy. Already-admitted runs keep publishing and taking build-stage revisions using their copied manifest and recorded branch.
- Reconcile leftover worker claims on specloop enrollment and restart without launching workers or discarding surviving work.
- Name new changes `<owner>__<repository>-issue-<number>-<title-slug>`. Preserve spelling; if the generated branch fails existing validation, report unsupported repository health and dispatch no new work. Do not encode names or relax safety checks.
- Retain recorded legacy identities and bounded exact-branch PR recovery. Do not rename existing runs.
- Keep touchpoint attribution working when proposal records are missing: recognize exact owner-qualified and legacy producer names within the receipt's repository, retaining unsafe/ambiguous-join diagnostics and offline operation.
- Update the relevant enrollment, recovery, naming and limitations passages in `README.md`, `docs/specloop/README.md` and `KNOWN-LIMITATIONS.md` as #67 leaves them.

Implementation scope is `nightshift/config.py`, `nightshift/daemon.py`, `nightshift/specloop_intake.py`, relevant tests and those three documentation files, plus the necessary naming-compatibility update in `nightshift/touchpoints.py`. That narrow scope extension preserves the existing touchpoint-report recovery guarantee when the producer changes its naming convention. Out of scope: changing `nightshift/queue.py` or transport retries; automatic migration of deployed runs, branches or PRs; name encoding; hot configuration reload; changes to touchpoint accounting, output schema or discovery rules; changes to supervisor, provider, evidence or unrelated operator workflows. This draft edits only its OpenSpec change directory.

## Capabilities

### New Capabilities

- `config-resilience`: repository-local manifest health and durable worker-claim reconciliation on specloop enrollment.

### Modified Capabilities

- `issue-intake`: owner-qualified new identities and readiness for new drafting/admission; publication stays unchanged.
- `pr-revision`: readiness for new spec-stage drafting; build-stage revision stays unchanged.
- `touchpoint-report`: compatibility with both producer naming conventions when proposal records are missing; existing attribution and read-only reporting guarantees stay intact.
- `specloop-documentation`: operator guidance for the three behaviors and their residual limits.
