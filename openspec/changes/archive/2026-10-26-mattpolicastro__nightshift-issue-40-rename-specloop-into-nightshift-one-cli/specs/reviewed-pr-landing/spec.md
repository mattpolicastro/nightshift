## Purpose

Use canonical CLI module names while retaining every landing safety gate.

## MODIFIED Requirements

### Requirement: Reliable run discovery

Landing SHALL identify a front-door PR only by a matching run's canonical `pr` URL from version-1 `nightshift status --json`. It SHALL treat no match as direct only after complete successful discovery, and SHALL refuse ambiguous or unreadable discovery.

#### Scenario: Direct PR skips finish
- **WHEN** valid status contains no run matching the open non-draft PR URL
- **THEN** landing uses the body checklist and proceeds through CI and merge gates without invoking finish

#### Scenario: Uncertain discovery refused
- **WHEN** status fails, has an unsupported version or malformed data, contains any error row, or contains multiple matching runs
- **THEN** landing refuses before mutations instead of assuming a direct PR

### Requirement: Eligible run state

A matching run SHALL be paused at `human_checks` or already `done` before landing may mutate anything. Landing SHALL invoke finish only for the former state, after the human-check gate passes.

#### Scenario: Finish at human checks
- **WHEN** the matching run is paused at `human_checks` and human checks pass
- **THEN** landing refreshes eligibility and invokes `nightshift finish <change>` once through the runner before waiting for CI
- **THEN** it accepts the resulting head only after status reports the same run done and its finished head equals the remote PR head

#### Scenario: Wrong state refused
- **WHEN** the matching run is running, aborted, or paused for any reason other than `human_checks`, including `ready_to_publish`
- **THEN** landing refuses immediately with that state and makes no checklist edit, finish, update or merge call

#### Scenario: Finish fails safely
- **WHEN** finish fails or its refreshed result does not establish the same done run and finished remote head
- **THEN** landing exits nonzero without waiting for CI or merging and leaves existing run recovery intact

### Requirement: Discovery through the interpreter

Landing SHALL invoke every specloop status read as `[sys.executable, "-m", "nightshift.cli", "status", "--json"]` through the existing injectable runner.

#### Scenario: Console script absent
- **WHEN** the interpreter running landing can import `nightshift.cli` but PATH contains no nightshift executable
- **THEN** initial discovery and later status refreshes use that interpreter and exact module argv with existing timeout and JSON validation behavior
- **THEN** no console-script lookup or fallback is attempted, including for a direct PR

#### Scenario: Module invocation fails
- **WHEN** the module cannot be imported or status exits unsuccessfully
- **THEN** landing refuses through the existing run-discovery error path before subsequent mutations and does not assume the PR is direct

### Requirement: Finish through the interpreter

Landing SHALL invoke eligible specloop finish as `[sys.executable, "-m", "nightshift.cli", "finish", change]` through the existing injectable runner.

#### Scenario: Eligible finish uses the current interpreter
- **WHEN** a matching run paused at human checks passes all existing pre-finish gates
- **THEN** landing invokes finish once using the current interpreter, preserving the change argument and 300-second limit
- **THEN** finish failure or failure to establish the same done run and matching remote head refuses before CI or merge; direct PRs and initially done runs still skip finish

### Requirement: Landing interpreter prerequisites

The prerequisites passage of `docs/landing.md` SHALL require the interpreter running landing to import `nightshift.cli` without requiring its console script on PATH. It SHALL describe omitted or explicit specloop enrollment and explain that explicit worker enrollment fails configuration loading because the engine is retired. The rename SHALL preserve current-interpreter execution, operator authentication, canonical PR run discovery or landing gates.

#### Scenario: Operator prepares the environment
- **WHEN** an operator follows the landing prerequisites
- **THEN** the guide explains the interpreter import and applicable run-environment requirements without requiring virtual-environment activation or a nightshift executable on PATH
- **THEN** it retains `uv` and `gh` on PATH, operator authentication with permission to read/edit PRs and issue comments, update the branch and merge under repository protection rules, and exactly one case-insensitive enrollment
- **THEN** omitted and explicit specloop engines are supported, explicit worker enrollment is refused with migration guidance, and the current interpreter's `-m nightshift.cli status --json` must still read the applicable runs
- **THEN** front-door discovery still uses canonical PR URL identity independently of labels or branch names, with canonical nightshift.cli status/finish argv and unchanged validation and landing gates

