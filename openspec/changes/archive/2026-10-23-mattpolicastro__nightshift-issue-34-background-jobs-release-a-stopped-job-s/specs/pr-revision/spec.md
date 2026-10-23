## Purpose

Preserve feedback history and historical input when an operator releases a
stopped spec-stage or build-stage revision.

## ADDED Requirements

### Requirement: Released revision selection

After explicit release, eligible revision intake SHALL select a new snapshot
using normal unconsumed-feedback rules, even under an unchanged trigger event.
It SHALL preserve consumed IDs and unknown receipt fields and SHALL NOT
acknowledge the stopped attempt's result.

#### Scenario: Same event permits replacement
- **WHEN** a stopped spec or build revision is released, its trusted revision label remains applied, and eligible unconsumed feedback exists
- **THEN** a later eligible poll starts one replacement with a new job ID using the normal feedback selection and stage rules without requiring relabeling
- **THEN** previously consumed IDs and unknown receipt fields survive, and stopped feedback is not consumed merely because release occurred

#### Scenario: Release does not replay consumed feedback
- **WHEN** a released revision is polled and only already-consumed feedback remains
- **THEN** normal empty-feedback handling explains that nothing remains, removes the corresponding label application and launches no revision

### Requirement: Immutable build revision input

Each build-revision invocation SHALL receive a distinct absolute UTF-8 JSON
feedback file, fully written and closed before dispatch. The file SHALL remain
unchanged and available with the historical request across release,
replacement and restart. Input preparation failure SHALL prevent dispatch.

#### Scenario: Replacement preserves stopped input
- **WHEN** a build revision stops, its feedback is edited or supplemented without changing the trigger event, and release permits a replacement invocation
- **THEN** the replacement request names a different input file containing its newly selected blocks
- **THEN** the stopped request still resolves to its exact original bytes after replacement and restart, including for a stopped attempt using the legacy event-named file

#### Scenario: Delayed reader retains complete blocks
- **WHEN** a build revision reads its input after the launching poll returns, including with a state path containing spaces and feedback containing Unicode and Markdown separators
- **THEN** its absolute JSON input contains the original complete blocks and remains available after daemon restart
- **THEN** synchronous injected runners also receive distinct files for separate invocations

#### Scenario: Feedback file preparation fails
- **WHEN** exclusive creation or writing of a build-revision feedback file fails
- **THEN** no revision command launches, no feedback is consumed, and the existing intake failure path reports the error without overwriting an earlier input

## MODIFIED Requirements

### Requirement: Durable round snapshot

Intake SHALL atomically save selected IDs, rendered blocks, and consumed history before dispatch and retain that snapshot through edits and restart. It SHALL durably consume only selected IDs after a validated build revision result with a landed commit or a successful spec push, before notifications or label removal and without waiting for publication. Unsuccessful new attempts SHALL consume nothing; legacy baseline initialization SHALL follow Feedback state compatibility. Explicit
release MAY detach a stopped attempt for normal selection of a new snapshot,
without consuming the stopped result or erasing prior consumed history.

#### Scenario: Snapshot is fixed before dispatch
- **WHEN** either revision stage starts, then a selected comment is edited and another comment appears while its job runs across a daemon restart
- **THEN** the runner reads the originally persisted blocks and IDs, and intervening polls start no replacement job
- **THEN** success consumes only the saved IDs, leaving the later comment for another round

#### Scenario: Unsuccessful work keeps feedback eligible
- **WHEN** a revision attempt fails to launch, returns refused, rejected, needs-input, or an invalid result, or its spec push fails
- **THEN** it adds no IDs to the consumed set and preserves previously consumed IDs
- **THEN** after normal failure resolution, the unconsumed feedback remains available; a saved successful draft retries delivery without drafting again

#### Scenario: Completion delivery retries preserve consumption
- **WHEN** a successful spec push or validated build revision is followed by a lost completion-comment response, failed label removal, or restart before delivery completes
- **THEN** the selected IDs are already durably consumed and retries use the saved result and original comment marker without invoking another revision
- **THEN** a later request selects only remaining IDs, including when the label removal succeeded remotely but its response was lost

#### Scenario: New label preserves completed round
- **WHEN** a trusted actor removes and reapplies the revision label while an earlier round is running or awaiting completion delivery
- **THEN** intake retains and completes the earlier round's saved work and consumption before selecting another round
- **THEN** delivering the earlier event does not remove the newer application, and a later poll handles it using the cumulative consumed set

#### Scenario: Pause and stop preserve snapshots
- **WHEN** a selected revision job completes while daemon delivery is paused, or the selected job has durable stop intent
- **THEN** pause retains the saved result for authorized resume without duplicate dispatch, and stopped work remains held without delivery or consumption until explicit release detaches its receipt; the stopped result remains ineligible afterward
- **THEN** resume of an eligible unstopped result acknowledges only the saved snapshot under the existing stage success rule

### Requirement: Feedback state compatibility

Intake SHALL validate state and preserve consumed IDs and unknown fields. Legacy receipts SHALL automatically consume all trusted IDs created at or before their saved time, or the current tip time if no time is recorded. Intake SHALL atomically persist that baseline once before further processing, without operator initialization. Invalid state SHALL fail without resetting history.

#### Scenario: Legacy saved time initializes consumption
- **WHEN** an authorized legacy receipt records a save time and trusted feedback from all three sources exists before, exactly at, and after that time, with the branch tip now later than all of it
- **THEN** intake automatically consumes only the IDs at or before the saved time using conversation/inline `created_at` and review `submitted_at`, including blank or self-prefixed trusted records, and persists the baseline without a branch-tip lookup
- **THEN** later IDs remain eligible, edits do not move the creation cutoff, and restart preserves the same baseline without operator initialization

#### Scenario: Legacy missing time uses tip once
- **WHEN** an authorized legacy receipt has no recorded save time and trusted feedback exists before, exactly at, and after the current PR head's committer time
- **THEN** intake initializes consumed IDs at or before that tip time using conversation/inline `created_at` and review `submitted_at`, and persists them before dispatch or delivery
- **THEN** a later tip advance or daemon restart does not recompute consumption, so remaining IDs still feed the next round

#### Scenario: Legacy recovery preserves saved work
- **WHEN** an authorized legacy receipt with a pending job or delivery is automatically initialized
- **THEN** the original payload, job, result, event and marker remain available under existing pause and stop rules, without inferring snapshot IDs from rendered text
- **THEN** eligible saved work finishes before a newer request starts, adds no IDs beyond the migration baseline, and subsequent new snapshots use exact IDs
- **THEN** stopped work remains held without delivery or replacement dispatch until explicit release; release retains the initialized ledger and permits only normal new-round selection

#### Scenario: Legacy initialization failure preserves receipt
- **WHEN** legacy initialization encounters malformed recorded time, missing or invalid required feedback timestamps or IDs, a failed page or fallback commit read, or an atomic save failure
- **THEN** the original receipt remains unchanged, no revision or delivery starts, and the existing error path retains the request for retry
- **THEN** malformed recorded time is not treated as absent, and a partial read is not saved as a baseline

#### Scenario: Validated state survives replacement
- **WHEN** a version-1 receipt contains consumed IDs and unknown fields, and a later empty or refused round replaces the prior round
- **THEN** the consumed IDs and unknown fields survive and previously consumed feedback stays excluded
- **THEN** malformed known fields or an unsupported version instead cause an error without overwriting the receipt or launching work
