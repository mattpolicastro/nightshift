## Why

A confirmed stopped background job keeps its issue or PR's intake receipt
pointing at that attempt indefinitely. Resume and relabeling cannot release
the hold; operators currently have to edit receipts. Issue #34 closes that
gap from issue #17's ledger and spec PR #28, building on #8's stop protocol
and #13's repository-health isolation, both present in this checkout.

## What Changes

- Add offline `nightshift jobs release <id>` for a confirmed stopped proposal,
  admission or revision attempt. Atomically remove only its matching receipt
  `job` reference; preserve the stopped attempt and all other receipt fields.
- Refuse unsafe or unverifiable attempts with exit 1; make repeated release
  succeed without writes or interference with a replacement attempt.
- Coordinate release with job collection and legacy revision initialization
  so stale reads cannot restore the reference. Create a receipt lock only
  for an actual matching release, never as a side effect of ordinary polling.
- Let subsequent polls use normal eligibility, authorization, pause,
  dependency, manifest-health, approval and recovery rules. Release neither
  launches work nor delivers the stopped result.
- Give every build-revision invocation a distinct durable feedback file,
  including replacements under the same label event.
- Correct the root README, `KNOWN-LIMITATIONS.md`, and the relevant paragraphs
  of `docs/specloop/README.md`; remove the missing-release-command claim from
  the documentation capability's Current limitations requirement.

Implementation scope: `nightshift/specloop_job.py`, the collection and revision
input paths in `nightshift/specloop_intake.py`, `nightshift/cli.py`, corresponding
tests, and those three documentation files. Preserve every existing assertion;
explain any fixture/fake changes in the implementation PR.

Out of scope: changing #8's signals, stop deadlines, identity checks or stop
protocol; automatic retries; rollback or cleanup; daemon/supervisor lifecycle
changes; cancelling admitted runs; fixing revision commit/result atomicity;
branch/default-base repair. The reported missing `main` ref is a checkout
setup failure, not missing product intent or part of this feature.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `background-job-control`: explicit release, rejection and idempotency;
  amend durable cancellation and command syntax to allow explicit detachment.
- `issue-intake`: released attempts return to ordinary intake eligibility
  while preserving filesystem and recovery behavior.
- `pr-revision`: immutable per-invocation feedback and released-round recovery;
  qualify existing stopped snapshot/legacy holds with explicit release.
- `specloop-documentation`: concise release guidance and a corrected Current
  limitations requirement, retaining every unrelated limitation.
