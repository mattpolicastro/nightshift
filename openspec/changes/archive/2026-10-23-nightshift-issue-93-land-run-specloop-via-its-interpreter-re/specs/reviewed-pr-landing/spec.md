## Purpose

Make reviewed PR landing independent of the specloop console script and reconcile narrowly identified uncertain branch updates while preserving all existing landing gates.

## ADDED Requirements

### Requirement: Discovery through the interpreter

Landing SHALL invoke every specloop status read as `[sys.executable, "-m", "specloop.cli", "status", "--json"]` through the existing injectable runner.

#### Scenario: Console script absent
- **WHEN** the interpreter running landing can import `specloop.cli` but PATH contains no specloop executable
- **THEN** initial discovery and later status refreshes use that interpreter and exact module argv with existing timeout and JSON validation behavior
- **THEN** no console-script lookup or fallback is attempted, including for a direct PR

#### Scenario: Module invocation fails
- **WHEN** the module cannot be imported or status exits unsuccessfully
- **THEN** landing refuses through the existing run-discovery error path before subsequent mutations and does not assume the PR is direct

### Requirement: Finish through the interpreter

Landing SHALL invoke eligible specloop finish as `[sys.executable, "-m", "specloop.cli", "finish", change]` through the existing injectable runner.

#### Scenario: Eligible finish uses the current interpreter
- **WHEN** a matching run paused at human checks passes all existing pre-finish gates
- **THEN** landing invokes finish once using the current interpreter, preserving the change argument and 300-second limit
- **THEN** finish failure or failure to establish the same done run and matching remote head refuses before CI or merge; direct PRs and initially done runs still skip finish

### Requirement: Landing interpreter prerequisites

The prerequisites passage of `docs/landing.md` SHALL require the interpreter running landing to import `specloop.cli` without requiring its console script on PATH.

#### Scenario: Operator prepares the environment
- **WHEN** an operator follows the landing prerequisites
- **THEN** the guide explains the interpreter import and applicable run-environment requirements without requiring virtual-environment activation or a specloop executable on PATH
- **THEN** it retains the existing GitHub tool, authentication and enrollment prerequisites

### Requirement: Narrow uncertain update responses

Landing SHALL reconcile the specified nonzero parser-error and silent-output PUT responses as uncertain only after ruling out definite failures. Existing transport-loss reconciliation SHALL remain supported.

#### Scenario: Parser error after accepted update
- **WHEN** update-branch exits nonzero, stderr contains the case-sensitive literal `unexpected end of JSON input`, stdout is empty or unparseable, and no definite-failure rule applies
- **THEN** landing observes the head under the existing reconciliation rules instead of refusing solely for that parser diagnostic
- **THEN** the rule is not restricted to exit 1 and does not apply to other GitHub operations

#### Scenario: Silent update result
- **WHEN** update-branch exits nonzero with exactly empty stdout and stderr and no known authentication exit
- **THEN** landing treats the result as uncertain and observes the head without another PUT
- **THEN** whitespace-only streams do not qualify as a silent response

#### Scenario: Existing transport loss
- **WHEN** an existing recognized transport-loss diagnostic or subprocess exception makes the PUT outcome uncertain and no definite rejection is established
- **THEN** landing retains its existing bounded reconciliation and single-request behavior

### Requirement: Definite update failures refuse promptly

Landing SHALL refuse nonzero PUT results reporting an HTTP status, a JSON document, a known authentication exit, or an otherwise unrecognized diagnostic before polling. These refusals SHALL take precedence over parser-error and transport-loss wording.

#### Scenario: JSON error takes precedence
- **WHEN** a nonzero PUT returns an expected-head mismatch or conflict JSON document in stdout or a complete JSON document in stderr, including alongside parser-error wording
- **THEN** landing immediately reports a branch-update refusal without observation, sleep, another PUT or merge
- **THEN** any other parseable JSON document on a nonzero exit also refuses conservatively

#### Scenario: HTTP error takes precedence
- **WHEN** a nonzero PUT's stderr carries an HTTP status, including 403, 409 or 422, with or without `unexpected end of JSON input` or transport-loss wording
- **THEN** landing refuses immediately with the branch-update diagnostic and performs no observation, sleep, repeat PUT or merge
- **THEN** status detection is case-insensitive and accepts an optional HTTP version

#### Scenario: Other failures do not wait
- **WHEN** the nonzero PUT has a plain-text authentication or permission error, a known authentication exit of 4 even without output, or any output outside the recognized uncertain cases
- **THEN** landing refuses immediately without observation or sleep and preserves the branch-update diagnostic and exit code
- **THEN** empty stdout alone cannot hide an unrelated stderr diagnostic

### Requirement: Uncertain update observation preserves gates

An uncertain PUT result SHALL use the existing exact ordered two-parent check and shared deadline before advancing the expected head. Landing SHALL NOT repeat that PUT to resolve uncertainty.

#### Scenario: Proven update proceeds through fresh gates
- **WHEN** observation after an uncertain PUT first sees the previous head H and then a commit with a matching SHA and exactly ordered parents `[H, B]`, where B is main read immediately before the PUT
- **THEN** landing adopts that head, retains B as the round baseline and requires fresh head-bracketed CI and all existing final checks before a head-conditioned merge
- **THEN** exactly one PUT was issued for that update, carrying `expected_head_sha=H`

#### Scenario: Unexpected parents refuse
- **WHEN** the first changed head after an uncertain PUT is a descendant, has reversed or extra parents, merges another base, or cannot be established as the exact requested merge
- **THEN** landing refuses provenance without CI for an accepted replacement head, merge or another PUT

#### Scenario: Observation cannot establish the update
- **WHEN** the head stays unchanged until the shared deadline or a reconciliation read fails, returns invalid metadata, or reveals a closed or draft PR
- **THEN** landing refuses without another PUT or merge and reports the original uncertain-response error with the reconciliation cause
- **THEN** command time and capped sleeps consume the original deadline across CI rounds, with no reset, extra grace period or read after expiry
