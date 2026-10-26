# state-migration Specification

## Purpose
Move legacy build state safely into the Nightshift root with isolated tests and read-only fallback.

## Requirements

### Requirement: Lazy isolated roots

State path resolution SHALL be lazy, default to ~/.nightshift with ~/.specloop fallback, and honor explicit isolated roots without accessing the actual home in tests.

#### Scenario: Defaults and overrides are lazy
- **WHEN** HOME or a trusted root override changes before resolution, or a fixture patches state.ROOT
- **THEN** new calls use the selected temporary roots and reject invalid overrides without resolving import-time real-home state
- **THEN** proposals_dir() and CLI draft output use the canonical drafts directory immediately with the root switch, never the daemon proposals namespace

#### Scenario: Tests cannot touch live state
- **WHEN** in-process and child test probes attempt filesystem access to actual-home .nightshift or .specloop
- **THEN** the isolation guard fails before access, while temporary canonical and legacy state remain usable

### Requirement: Separate draft namespace

Build drafting SHALL use <state_root>/drafts/<change>, separate from daemon proposals/<repository> receipts and jobs. Creating or replacing a draft SHALL preserve daemon evidence regardless of change-name collisions.

#### Scenario: Draft replacement preserves daemon evidence
- **WHEN** canonical and alias propose invocations create and re-draft the valid change owner__repo while daemon proposals/owner__repo contains receipts and job evidence
- **THEN** the production output resolver selects drafts/owner__repo and only that workspace is replaced; receipt and job bytes, paths, mtimes and offline inventory identities are unchanged
- **THEN** this holds before migration, after migration and after the completion marker makes later migration a no-op; existing proposal result schemas, branch refs and delivery behavior remain unchanged

#### Scenario: Generated names cannot collide with another repository
- **WHEN** intake generates owner__repo-issue-7-add-a-gate for owner/repo and daemon evidence exists for owner/repo-issue-7-add-a-gate
- **THEN** drafting and re-drafting use drafts/owner__repo-issue-7-add-a-gate, preserving that other repository's proposals/owner__repo-issue-7-add-a-gate receipts and jobs unchanged

#### Scenario: Unsafe draft replacement refuses
- **WHEN** an existing managed draft path or its drafts parent is a symlink or non-directory, or the same change still has a real legacy proposal workspace
- **THEN** propose refuses before deletion or cloning, retaining both draft and daemon evidence and directing an unmigrated workspace to nightshift migrate
- **THEN** canonical daemon proposals are never searched as draft fallback; a completed old-path compatibility link to the exact canonical draft remains supported

### Requirement: Read only fallback

Readers SHALL discover canonical and unmigrated legacy runs once per change without moving state or weakening existing error isolation. Supervisor scheduling SHALL discover runs through the same iter_run_dirs() union as status.

#### Scenario: Mixed roots remain visible
- **WHEN** named and all-run reads encounter canonical, legacy and compatibility-linked runs across repositories
- **THEN** each run appears once in deterministic order, legacy-only runs remain accessible, and state bytes and directory contents stay unchanged

#### Scenario: Supervisor discovers both roots
- **WHEN** isolated roots contain a legacy-only run and a canonical run with a legacy compatibility link, and specloop.serve.runs() enumerates scheduling candidates
- **THEN** runs() returns both logical runs exactly once through state.iter_run_dirs(), including the unmigrated run, while retaining its existing created-time ordering
- **THEN** discovery leaves state and pauses unchanged; scheduling retains existing eligibility, run-lock and reload checks

#### Scenario: Conflicting roots remain visible
- **WHEN** both roots contain distinct unjournaled state for one change or a run is unreadable
- **THEN** readers report a diagnostic for the conflict or per-run error and retain other healthy runs; writers refuse the conflicting change

### Requirement: Supervisor exclusion

A supervisor SHALL hold a stable serve lock throughout its lifetime, and migration SHALL refuse when that lock or legacy supervisor liveness indicates an active or uncertain supervisor.

#### Scenario: Live supervisor blocks migration
- **WHEN** a subprocess holds serve.lock or a legacy heartbeat identifies a live or unverifiable PID
- **THEN** migration refuses with a stop-supervisor message before relocation; a second supervisor also refuses

#### Scenario: Supervisor lock survives startup and exit
- **WHEN** serve runs through canonical or alias invocation, including once, then exits or crashes
- **THEN** the same stable lock inode excludes rivals during its lifetime and is released by process exit; daemon heartbeat and lifecycle remain independent

### Requirement: Migration preflight

Migration SHALL acquire coordination and existing run locks before moving entries, validate all destinations, and refuse unsafe evidence without overwrite.

#### Scenario: Locked run is not moved
- **WHEN** a separate process holds any legacy run lock
- **THEN** test_locked_run_is_not_moved observes a clear refusal naming the run, unchanged legacy runs/proposals/homes and canonical runs/drafts/proposals/homes entries and no success marker; stable coordination lock files may be created

#### Scenario: Conflicts preserve existing evidence
- **WHEN** an unjournaled mapped destination, including drafts/<change>, collides with legacy content, roots overlap, or relocation would cross devices
- **THEN** migration refuses before moves, preserving daemon proposal receipts and all source/destination data

#### Scenario: Writers coordinate with migration
- **WHEN** an upgraded proposal, run or home writer holds shared coordination ownership, or an incomplete journal exists
- **THEN** exclusive migration refuses while the writer is active, and ordinary writers refuse pending recovery without choosing stale paths

### Requirement: Destination preserving relocation

Migration SHALL relocate disjoint legacy runs, proposals and homes by same-filesystem rename, preserve evidence and lock identity, and retain legacy per-entry links.

#### Scenario: Entries move without replacement
- **WHEN** preflight succeeds with legacy entries alongside existing canonical daemon receipts
- **THEN** entry contents, modes, Git evidence and run-lock inodes are retained; old paths become links and existing canonical/unrelated state is unchanged

#### Scenario: Legacy drafts move outside daemon proposals
- **WHEN** legacy proposals/owner__repo holds a build workspace and canonical proposals/owner__repo holds daemon receipts and jobs
- **THEN** migration moves only the workspace to canonical drafts/owner__repo and links its legacy path there, preserving daemon receipt/job bytes, paths, mtimes and inventory identities
- **THEN** re-drafting uses the migrated canonical workspace and retains the compatibility link and all daemon evidence

#### Scenario: Relative links retain destinations
- **WHEN** an entry contains internal relative links and relative links escaping the moved tree, including dangling links
- **THEN** internal links remain relative and escaping links become absolute original destinations; migration never copies linked credential contents

### Requirement: Journaled completion

Migration SHALL resume proven journaled transitions after process interruption, write a completion marker only after success, and never repeat a completed migration.

#### Scenario: State migrates once and leaves a marker
- **WHEN** migration completes and is invoked again, including after a late legacy artifact appears
- **THEN** test_state_migrates_once_and_leaves_a_marker verifies the versioned marker and an unchanged second snapshot with no second move

#### Scenario: Interrupted transitions resume
- **WHEN** fresh processes restart after exits around link rewrite, entry rename, compatibility-link creation and marker publication
- **THEN** recovery finishes only the recorded identity once, retains evidence and readable paths, and never duplicates an entry
- **THEN** legacy proposal recovery follows its separately journaled proposals/<change> source and drafts/<change> destination, never selecting daemon proposals/<change> even when it already exists

#### Scenario: Ambiguous recovery refuses
- **WHEN** journal or marker is corrupt/mismatched, an entry identity conflicts, or both recorded paths are absent
- **THEN** migration exits nonzero without deletion, overwrite, success marker fabrication or scheduler work

### Requirement: Migration only command

nightshift migrate SHALL relocate legacy state and exit without lifting pauses or spawning drivers, and SHALL refuse a live supervisor.

#### Scenario: Due pauses remain paused
- **WHEN** migration processes runs with already-due infra and usage_limit pause deadlines
- **THEN** pause/state/event bytes remain unchanged and no driver or auto-resume path runs

#### Scenario: Dedicated command refuses live serve
- **WHEN** nightshift migrate runs while another process owns the serve lock
- **THEN** it exits nonzero with a stop-supervisor diagnostic and no state relocation

#### Scenario: Startup fallback precedes scheduling
- **WHEN** serve starts without a completion marker and obtains exclusive ownership
- **THEN** it completes or recovers migration before scheduling; refusal prevents polling and spawning, while help/status/attention never trigger migration

### Requirement: Revision fixture migration

Relocated state SHALL retain revision crash/retry and candidate-provenance guarantees across fresh child processes using resolver-derived run paths.

#### Scenario: Revision recovery survives relocation
- **WHEN** the existing revision crash fixture migrates between child invocations at prepared, ref or completed boundaries
- **THEN** children and refreshed parent handles resolve the migrated run, preserve candidate module/interpreter provenance and PATH sentinels, return the original keyed commit once, and preserve later control state

### Requirement: Representative isolated migration

Migration acceptance SHALL use generated sanitized copies shaped like real multi-repository state, without reading live host state.

#### Scenario: Other repositories and history survive
- **WHEN** temporary state includes several repositories, proposal artifacts, Git refs, revision history, internal/external relative links and one subprocess-held run lock
- **THEN** the first migration refuses without movement, a retry after release migrates all repositories once, and copied evidence, revision replay, symlink destinations and unrelated daemon receipts remain valid
- **THEN** legacy build proposals appear under canonical drafts, including names shared with daemon receipt directories, and later draft replacement preserves those receipts and their job evidence

### Requirement: All state consumers agree

Status, proposal context, reports and deployment liveness SHALL use resolver-backed discovery and deduplicate compatibility links without mutating state during reads.

#### Scenario: Reports and deploy see both roots
- **WHEN** timing/touchpoint reports, in-flight proposal context and deploy probes inspect mixed temporary roots and supervisor heartbeats
- **THEN** each logical run is read once with unchanged report accounting/error and busy/unknown guard behavior, explicit-root overrides remain isolated, and no migration or pause write occurs
