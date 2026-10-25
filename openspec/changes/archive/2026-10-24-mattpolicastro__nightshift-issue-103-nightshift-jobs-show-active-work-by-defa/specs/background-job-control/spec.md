## Purpose

Make local job inventory emphasize unfinished or stopped work, retain explicit access to history, and safely compact proven delivered historical artifacts on operator request.

## ADDED Requirements

### Requirement: Receipt aware delivery display

Inventory SHALL label a finished job `delivered` only when its readable item receipt has `delivered` equal to JSON boolean true; otherwise it SHALL label a finished job `finished (delivery pending)`. This item-level display SHALL NOT establish attempt-level compaction authority or change execution state. Other execution states SHALL retain their labels.

#### Scenario: Finished undelivered job reads pending
- **WHEN** a finished attempt has a missing receipt or a receipt with an absent or false delivered field, including completion while paused
- **THEN** both listing modes show `finished (delivery pending)` and the default retains the row without treating a missing legacy receipt as an error
- **THEN** result, collection, age and cleanup evidence alone do not mark delivery complete

#### Scenario: Delivered item history reads delivered
- **WHEN** finished attempts share a readable item receipt whose delivered field is true, including a receipt without job identity or pointing at a later attempt
- **THEN** `--all` labels those finished rows `delivered` and default inventory hides them unless they have diagnostics
- **THEN** non-finished execution states retain their labels and the display grants no deletion permission for unmatched attempts

#### Scenario: Damaged receipt stays visible
- **WHEN** an item receipt is malformed, unreadable, symlinked, not an object, or declares a non-boolean delivered value
- **THEN** its jobs remain visible, finished rows read `finished (delivery pending)`, a diagnostic identifies the receipt, and inventory exits 1 while healthy items remain available
- **THEN** no receipt symlink is followed and no record is repaired, created or rewritten

### Requirement: Historical compaction pass

`nightshift jobs compact` SHALL perform one offline pass over full inventory. Explicit invocation SHALL authorize local cleanup while paused or without current labels, subject to a matching delivered receipt and existing identity, locking and ownership checks. It SHALL preserve receipts and perform no delivery, scheduling, signaling or external work. It SHALL report each attempt as compacted or skipped with identity, reason and totals.

#### Scenario: Compact delivered historical attempts only
- **WHEN** the pass encounters old proposal, admission, spec-revision and keyed build-revision attempts with matching delivered receipts, alongside live, pending, stopped, released and failed attempts
- **THEN** only eligible delivered attempts lose logs and safely owned idea inputs and receive redacted minimal requests using the existing compactor
- **THEN** keyed round identity, original request mtime, remaining args, results, process/control records, directories, receipts, feedback files and touchpoint attribution remain preserved
- **THEN** all other attempts are skipped with reasons and their evidence remains intact; full inventory remains discoverable afterward

#### Scenario: Unmatched historical identity is skipped
- **WHEN** a receipt is absent, lacks job identity, names another attempt, conflicts with request/result context or keyed provenance, or the lifetime lock is busy
- **THEN** the affected attempt is skipped with a reason and its artifacts are retained, including when an older cleanup journal exists
- **THEN** no job field is synthesized, round_job alone is insufficient, and journal-only recovery cannot bypass the current receipt match

#### Scenario: Explicit cleanup works while paused
- **WHEN** compact runs with the pause file present, no valid config or credentials and matching delivered historical evidence
- **THEN** it can compact eligible evidence without checking remote labels or loading configuration, leaving pause and receipts unchanged
- **THEN** no launch, collection, delivery, network request or process signal occurs, and automatic daemon cleanup still obeys its existing eligibility and authorization gates

#### Scenario: Receipt replacement during cleanup is refused
- **WHEN** a receipt changes between scan and acquisition of the existing compaction locks
- **THEN** under-lock rereading refuses cleanup of the stale attempt, preserves replacement state and reports the skip
- **THEN** locks retain control-lock then existing release-lock coordination and no release lock is created

#### Scenario: Interrupted and repeated passes are safe
- **WHEN** a pass is interrupted after journal creation or artifact mutation and a later invocation encounters the attempt
- **THEN** a still-matching delivered receipt permits idempotent completion using the existing journal and original request mtime, while a replaced receipt causes a skip
- **THEN** a validated already-complete attempt is skipped as already compacted without rewriting artifacts or inflating the compacted count
- **THEN** no completion delivery or job execution is replayed

#### Scenario: Report partial progress and safe skips
- **WHEN** a pass encounters eligible jobs alongside expected ineligible attempts, damaged evidence, unsafe idea targets or an injected filesystem failure
- **THEN** independent healthy jobs continue, each attempt receives one outcome in inventory order, and stdout ends with `Compacted: N; skipped: M.`
- **THEN** only newly completed cleanup counts as compacted, partial transactions count as skipped with diagnostics, and retained unsafe idea targets receive explicit warnings rather than claims of deletion
- **THEN** ordinary identity/ineligibility skips exit 0 unless another error occurs; malformed/unreadable evidence, unsafe evidence symlinks and filesystem failures cause exit 1 with diagnostics on stderr
- **THEN** unsafe deletion targets remain intact under the existing ownership rules, no recursive deletion occurs and evidence symlinks are not followed

#### Scenario: Empty compaction is a no op
- **WHEN** no background attempts exist
- **THEN** compact prints `Compacted: 0; skipped: 0.`, exits 0 and creates no state

## MODIFIED Requirements

### Requirement: Local job inventory

`nightshift jobs --all` SHALL list all persisted background attempts with full job ID,
stage, repository/item, command, execution state, available PID/PGID and log
path. It SHALL read local state independently of daemon process handles and
configuration, in deterministic stage/repository/item/ID order. It SHALL
distinguish execution completion from delivery and retain historical attempts.
Default `nightshift jobs` SHALL show starting, running (including overdue),
stopping, stopped and finished-undelivered attempts, plus diagnostic rows.
It SHALL hide other historical rows, including ordinary failed attempts and
finished-delivered rows, and print their count when nonzero. Filtering SHALL
be confined to CLI rendering; shared inventory and other consumers SHALL
continue to receive all attempts. Listing SHALL never mutate or compact state.

#### Scenario: List jobs after daemon restart
- **WHEN** persisted drafting, admission and revision jobs exist and the daemon's process handles have been lost
- **THEN** `nightshift jobs --all` lists their stable IDs, context and current states, including historical attempts, without loading repository configuration or contacting external services
- **THEN** default inventory retains the same fields and ordering for its visible rows, and neither mode writes state

#### Scenario: Empty inventory
- **WHEN** no background job directories exist
- **THEN** `nightshift jobs` prints `No background jobs.` and exits 0 without creating job state

#### Scenario: Legacy and unreadable records
- **WHEN** inventory contains an older job lacking process identity and a malformed record alongside valid jobs
- **THEN** both modes retain malformed diagnostic rows, report the malformed record and exit 1; eligible default rows remain visible, and --all retains every historical row including the older job with unavailable identity identified

#### Scenario: Default hides delivered history
- **WHEN** inventory contains starting, running, overdue, stopping, stopped, finished-undelivered, finished-delivered and ordinary failed attempts
- **THEN** default output includes all active, stopped and pending rows and excludes finished-delivered and ordinary failed rows, preserving inventory order
- **THEN** it prints exactly one `Hidden historical jobs: N (use --all to show).` line counting excluded rows, and omits that line when none are excluded
- **THEN** the unfiltered inventory interface and status, stop, release and deployment consumers retain existing behavior

#### Scenario: All lists history
- **WHEN** the operator invokes `nightshift jobs --all` on mixed inventory
- **THEN** every persisted attempt appears in deterministic order with full job ID, stage, repository/item, command, execution/delivery label, available PID/PGID and log path
- **THEN** the hidden-history footer is absent and all files remain unchanged

#### Scenario: Only history remains
- **WHEN** valid inventory contains only rows excluded by the default view
- **THEN** default output prints `No active background jobs.` followed by the nonzero hidden-history count, while --all still prints every row
- **THEN** a genuinely empty inventory retains `No background jobs.` and an inventory containing only diagnostics is not reported as empty

### Requirement: Job command syntax

The CLI SHALL accept `nightshift jobs`, `nightshift jobs --all`,
`nightshift jobs compact`, `nightshift jobs stop <id>` and
`nightshift jobs release <id>`. Only the default jobs listing SHALL change;
other commands' defaults and existing options SHALL remain compatible.
The --all option SHALL be valid only for jobs listing, and compact SHALL
accept no positional ID. Invalid syntax SHALL exit 2;
IDs SHALL be treated as identifiers rather than filesystem paths.

#### Scenario: Reject malformed command
- **WHEN** jobs is given an unknown action, stop or release lacks an ID, extra positional arguments are supplied, compact has an ID, or --all accompanies an action or a different command
- **THEN** the CLI exits 2 with usage information before inventory or mutation and does not modify state or signal processes

#### Scenario: Reject path identifier
- **WHEN** the supplied stop or release job ID is a path, contains traversal components or is only an ID prefix
- **THEN** the selected action exits 1 without reading outside the enumerated job directories, modifying state or sending termination signals

### Requirement: Delivered attempt compaction

Only durably delivered, idle and unstopped attempts with validated matching identity SHALL be compacted. Automatic compaction SHALL require an eligible authorized unpaused item visit. Explicit `nightshift jobs compact` SHALL instead authorize a local historical pass, including while paused, but only with the current matching delivered receipt and the same identity, locking and ownership checks. Compaction SHALL remove logs and owned idea inputs, redact idea arguments and retain a minimal request, original request mtime, result and control records. It SHALL leave workflow receipts and build-feedback files intact.

#### Scenario: Delivered artifacts are removed
- **WHEN** a valid background attempt has completed all authorized delivery operations and its matching receipt is durably marked delivered
- **THEN** compaction removes its log, its safely owned idea file and inline or file-based idea arguments, including legacy equals-form arguments
- **THEN** the directory survives with stage, remaining exact args, original timing or legacy nulls, unchanged request mtime, byte-identical result and retained process, lock and collection records
- **THEN** inventory still reads the historical attempt without errors, and workflow receipts and build-feedback JSON files remain intact

#### Scenario: Ineligible work keeps evidence
- **WHEN** a job is live, stopped, released after a stop, overdue but still pending, failed without completed delivery, paused awaiting delivery, or awaiting authorization or notification retry
- **THEN** cleanup does not remove or rewrite its input, request, log, result or control evidence
- **THEN** completion, collection, age or current label absence alone never establishes delivery

#### Scenario: Cleanup cannot cross attempt ownership
- **WHEN** compaction encounters a replaced receipt, conflicting identity, a busy lifetime lock, malformed or symlinked evidence, or an idea-file target outside the item root, shared by another attempt, a directory or a symlink
- **THEN** it leaves unproven attempts and unsafe deletion targets intact with a diagnostic, never follows a symlink or recursively deletes the directory, and preserves replacement state
- **THEN** receipt coordination follows control-lock then existing release-lock order and creates no release lock

### Requirement: Recoverable cleanup

Compaction SHALL journal delivery evidence and original request timestamps before destructive changes and SHALL be idempotent after interruption. Automatic recovery SHALL run only during an eligible authorized unpaused visit to that item, without redelivering results or mutating a replacement receipt. Receipts and journals SHALL NOT substitute for current daemon authorization. Explicit `nightshift jobs compact` MAY resume interrupted cleanup only with a still-matching current delivered receipt; it SHALL NOT use a journal alone to recover a superseded attempt. This local exception SHALL NOT change any daemon poll or replacement behavior below.

#### Scenario: Interruption resumes safely
- **WHEN** cleanup fails or the daemon restarts after journal creation, request replacement, input deletion or before recording completion
- **THEN** a later eligible authorized unpaused visit resumes the same attempt from its journal, tolerates already-removed files and preserves the original request mtime and result
- **THEN** it neither relaunches work nor repeats remote delivery, and filesystem cleanup failures remain local diagnostics rather than item escalation

#### Scenario: Unarmed cleanup waits for authorized eligibility
- **WHEN** a matching delivered receipt was saved but the daemon exited before creating its cleanup journal, and the item is no longer armed
- **THEN** subsequent unarmed polls leave its receipt, request, result, input, log and file set unchanged rather than running an independent cleanup scan
- **THEN** a later trusted reapplication permits journal creation and cleanup only after normal eligibility checks pass, without replaying the older attempt's delivery as a cleanup side effect
- **THEN** the same deferral applies to existing incomplete journals, closed items and blocked new-draft polls; unproven orphan jobs and legacy delivered receipts without a job identity remain retained

#### Scenario: Authorization denial preserves interrupted cleanup
- **WHEN** delivery succeeded but cleanup was interrupted, and the next poll observes a newly untrusted trigger or cannot resolve current authorization, including after restart
- **THEN** it performs no cleanup transaction or journal preparation and preserves every existing receipt, job artifact and request mtime exactly as found, whether the journal is absent or incomplete or the receipt has been replaced
- **THEN** normal rejection or lookup-failure handling remains unchanged, healthy eligible neighbors continue, and repeated denied polls neither resume cleanup nor create cleanup or release locks
- **THEN** after trusted reapplication and normal eligibility checks, cleanup may resume for validated delivered attempts; an old journal never authorizes processing of an untrusted current trigger

#### Scenario: Replacement retains cleanup authority
- **WHEN** an eligible authorized unpaused new round would replace a delivered job-bearing receipt before compaction finishes
- **THEN** intake ensures the older attempt's cleanup journal is durable before replacing the receipt, deferring replacement if journal creation fails
- **THEN** later cleanup of the older attempt requires current item authorization and eligibility as well as that journal, and leaves the newer receipt, request, result and input untouched
- **THEN** paused polls withhold cleanup, status remains read-only, and absent item state is never created merely to discover cleanup candidates
