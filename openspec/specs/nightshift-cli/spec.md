# nightshift-cli Specification

## Purpose
Unify build and daemon commands while preserving automation and frozen service callers.

## Requirements

### Requirement: Canonical build commands

The nightshift CLI SHALL expose every existing build command with its arguments, outcomes and JSON contract, making build status the default command. The unchanged `specloop-interface` JSON contracts SHALL apply to both `specloop … --json` and `nightshift … --json`.

#### Scenario: Build commands retain their contract
- **WHEN** canonical admit, propose, revise, act, status, publish or other build commands are parsed and dispatched
- **THEN** the existing handler receives equivalent arguments and retains version-1 output, exit codes, authorization and action behavior

#### Scenario: Machine readable errors stay singular
- **WHEN** invalid canonical arguments include an exact --json token before a standalone --
- **THEN** one version-1 error document is printed to stdout with exit 2 before any handler runs; non-JSON errors and help retain their existing behavior

#### Scenario: Canonical tests retain output contracts
- **WHEN** existing status and argparse tests invoke nightshift.cli.main with explicit or process argv after the alias is installed
- **THEN** canonical status retains exact JSON and empty stderr, help uses the nightshift program name with empty stderr, and errors retain exit codes, JSON-token/terminator rules, side-effect guards and propagated handler exceptions
- **THEN** empty human argv selects build status, while lone --json and unsupported global --json syntax still fail before dispatch; alias notices are asserted separately without relaxing canonical checks

#### Scenario: Both names honor machine contracts
- **WHEN** existing specloop-interface JSON contract fixtures exercise equivalent canonical and alias commands against isolated state
- **THEN** both satisfy the existing contracts with equivalent stdout documents and exit codes; the alias notice appears only on stderr

### Requirement: Explicit daemon controls

The CLI SHALL expose daemon run/status/pause/resume under the daemon group, preserve noncolliding front-door commands, and accept bare nightshift run as the sole implicit daemon compatibility form.

#### Scenario: Bare run and named run dispatch separately
- **WHEN** nightshift run, nightshift daemon run and nightshift run example-change are invoked with handlers substituted
- **THEN** the first two reach the same daemon function and the third reaches only the build driver

#### Scenario: Other missing changes refuse
- **WHEN** nightshift pause or resume lacks CHANGE, or run CHANGE carries a daemon-only option
- **THEN** parsing fails without touching daemon or run state

#### Scenario: Daemon controls retain boundaries
- **WHEN** daemon status, daemon pause/resume and the noncolliding jobs/attention/preflight/reconcile/digest commands run
- **THEN** existing status, env-file, offline/config-independent control, exit and retired-worker safeguards remain; daemon alone selects daemon status

### Requirement: One release alias

The installed specloop script and specloop.cli module SHALL forward to the canonical CLI for one release, announce the rename on stderr, and preserve argument boundaries, stdout and exit status.

#### Scenario: Specloop alias forwards
- **WHEN** specloop or python -m specloop.cli receives existing valid command arguments including a quoted note and --json
- **THEN** test_specloop_alias_forwards verifies the canonical handler receives the same argv, exactly one rename notice appears only on stderr, stdout is unchanged JSON and the canonical exit code is returned
- **THEN** separate help and argument-error cases retain canonical stdout and exit behavior with one additional stderr notice each

#### Scenario: Packaging retains frozen callers
- **WHEN** project script declarations and wheel contents are inspected and alias serve is parsed
- **THEN** both existing script targets and packages remain installable and specloop serve reaches the canonical supervisor without recursive forwarding

### Requirement: Canonical internal callers

Only the status/finish subprocess calls in nightshift/land.py and driver child argv in specloop/serve.py SHALL migrate to canonical nightshift commands in this change, preserving interpreter provenance, argument boundaries, injectable runners and request schemas.

#### Scenario: Calls retain request identity
- **WHEN** landing status/finish calls and supervisor run CHANGE children are constructed
- **THEN** they use the current interpreter with -m nightshift.cli, changing only module spelling while retaining arguments, timeouts, result validation and landing gates

### Requirement: Alias routed daemon callers

Daemon-side callers whose existing requirements prescribe specloop invocations without a delta in this change SHALL retain those invocations through the one-release alias, preserving stdout JSON and exit handling.

#### Scenario: Daemon calls parse JSON despite notice
- **WHEN** intake, background revision, attention and deploy status paths receive alias output with one stdout JSON document and a stderr rename notice
- **THEN** their existing specloop argv remain unchanged and their parsers accept that one document under the existing success, structured-refusal and error contracts without treating the notice as JSON or a failure
- **THEN** existing request identity, feedback-file and round-ID boundaries, timeouts, stale handling and deploy safeguards remain unchanged
