## Purpose

Distinguish transient OpenSpec infrastructure failures from admission refusals and draft rejections while preserving the versioned CLI contract and runtime `SpecError` fallbacks.

## ADDED Requirements

### Requirement: Validator availability classification

`specloop/openspec.py` SHALL define `ValidatorUnavailable(SpecError)`. `_run` SHALL raise it for `subprocess.TimeoutExpired` or no-JSON results with the recognized npm network/registry codes below. Other no-JSON results SHALL remain plain `SpecError`; parseable JSON SHALL retain existing interpretation. Each invocation SHALL make one subprocess call with timeout 120 seconds and SHALL NOT retry or back off.

#### Scenario: Validator timeout is transient
- **WHEN** the OpenSpec subprocess raises `subprocess.TimeoutExpired`
- **THEN** `_run` raises `ValidatorUnavailable`, also catchable as `SpecError`, with a command/timeout diagnostic and the timeout as its cause, after exactly one call with timeout 120

#### Scenario: Registry failure is transient
- **WHEN** stdout cannot be decoded as JSON and stderr contains a standalone case-sensitive code `ENOTFOUND`, `ETIMEDOUT`, `ECONNRESET`, `EAI_AGAIN`, or `ECONNREFUSED`
- **THEN** `_run` raises `ValidatorUnavailable` after one call, including when the code occurs beyond the diagnostic's truncation boundary

#### Scenario: Other failures retain classification
- **WHEN** a no-JSON result contains none of the five standalone codes, including an unrelated error or a code embedded in a longer identifier
- **THEN** `_run` raises plain `SpecError` with the existing diagnostic rather than `ValidatorUnavailable`
- **WHEN** stdout contains parseable validation JSON with problems, even with a recognized code in stderr
- **THEN** `_run` returns the parsed result and validation reports the problems normally

#### Scenario: Runtime fallbacks remain effective
- **WHEN** runtime scenario discovery raises `ValidatorUnavailable`
- **THEN** the existing `SpecError` handlers preserve scenario-lookup diagnostic text and the `gate_scenarios_failed` event
- **THEN** an evidence-gate lookup failure remains closed, without passed evidence or appended tasks, and requires no edits to `specloop/loop.py` or `specloop/evidence.py`

## MODIFIED Requirements

### Requirement: Command JSON

`propose`, `admit`, `revise` and `publish` SHALL accept `--json` and then print exactly one version-1 JSON document describing the outcome, including any questions, contradictions, risks, reasons, branch, commit or PR URL. As an explicit exception, `ValidatorUnavailable` from admission or drafting validation, including its scenario discovery, SHALL produce exactly `{"version": 1, "error": "..."}` with a nonempty diagnostic and exit 1, with no `outcome`. These paths SHALL catch the subclass before `SpecError` and broader refusal/rejection handling, and SHALL NOT retry validation or feed the infrastructure failure into drafting correction rounds. Existing publication exception behavior SHALL remain unchanged.

`docs/specloop/command-json.md` SHALL extend its exception paragraph to describe this admission/drafting exception, retained approval/arming triggers, retry on later eligible daemon polls and existing threshold-based `needs-human` holds. Ordinary validation problems SHALL retain refusal/rejection semantics.

#### Scenario: Refused admission as JSON
- **WHEN** `specloop admit --json` refuses a change because a task is impossible as written
- **THEN** it prints outcome "refused" with the contradiction among the reasons and exits 1

#### Scenario: Questions as JSON
- **WHEN** the drafting session asks for human input during `specloop propose --json`
- **THEN** it prints outcome "needs_input" with the questions

#### Scenario: Admission validator unavailable
- **WHEN** validation or its scenario discovery raises `ValidatorUnavailable` during `admit --json`
- **THEN** the command exits 1 with exactly one version-1 error document and no outcome, cleans up partial admission without saving admitted state, and performs no in-process retry

#### Scenario: Draft validator unavailable
- **WHEN** validation or its scenario discovery raises `ValidatorUnavailable` during `propose --json`
- **THEN** the command exits 1 with exactly one version-1 error document and no outcome, without another correction round or proposal publication for that attempt

#### Scenario: Real validation failure still refuses
- **WHEN** the validator returns parseable JSON with real validation problems
- **THEN** admission exits 1 with outcome "refused" and the problems among its reasons, while drafting retains its existing correction and rejection behavior
- **THEN** no transient error envelope replaces those outcomes, even if stderr contains a recognized network code

### Requirement: Proposal source ref JSON

Successful `specloop propose --json` output SHALL set `branch` to the exact ref returned by local proposal publication and `commit` to the drafted commit retained there. It SHALL retain version 1, one stdout JSON document, existing fields and exit codes. Publication SHALL continue preserving checked-out source worktrees. Non-success outcomes SHALL retain their existing contract except for the transient validator exception explicitly defined by Command JSON.

#### Scenario: Checked out branch reports private ref
- **WHEN** a successful proposal targets a branch checked out in the main source worktree or a linked worktree
- **THEN** JSON reports `branch` as `refs/specloop/proposals/<change>` and that ref resolves to the reported commit in the manifest's source repository
- **THEN** the checked-out branch tip, HEAD, index, staged and unstaged edits and untracked files remain unchanged

#### Scenario: Ordinary branch retains its spelling
- **WHEN** a successful proposal can publish to its effective branch without the checked-out-branch fallback
- **THEN** JSON reports that branch without `refs/heads/`, including an explicitly configured custom CLI branch, and the branch resolves to the reported commit
- **THEN** stdout contains exactly one version-1 document with the existing fields and exit status 0

#### Scenario: Unsuccessful proposal retains its outcome
- **WHEN** drafting needs input, is rejected, or local publication fails
- **THEN** JSON and exit status retain their existing needs-input or rejection behavior, with questions or failure details as applicable
- **THEN** the command does not report a successful fallback publication
