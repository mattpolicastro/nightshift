## Purpose

Support explicitly configured localhost TCP exchanges during verification while preserving network denial by default, existing preparation behavior and all non-network isolation.

## ADDED Requirements

### Requirement: Step loopback flag

`Step` SHALL append boolean `loopback`, default false. All four step kinds SHALL validate it without coercion and preserve it through template reloads. Unknown keys, positional construction, env semantics and other fields SHALL retain their existing behavior. The flag SHALL apply only in verification mode; preparation SHALL ignore it. Directly constructed steps SHALL receive boolean validation before launch.

#### Scenario: Boolean flags round trip
- **WHEN** verify, prepare, acceptance and mutation-only steps contain omitted, false or true loopback values alongside env tables and unknown keys, then are parsed, templated with run overrides and reloaded
- **THEN** loopback defaults to false or retains its boolean value, unknown keys remain ignored, and all existing step fields, env values, defaults and ordering remain intact
- **THEN** existing positional Step constructors retain their meaning and default to false

#### Scenario: Invalid flags cannot launch
- **WHEN** a parsed or directly constructed step supplies a string, number, null or container for loopback
- **THEN** a contextual ValueError identifies the step and loopback setting before that command launches, without coercing the value

#### Scenario: Preparation retains its policy
- **WHEN** a prepare step declares either loopback value or internal dependency warming runs
- **THEN** preparation uses its existing byte-identical profile and network policy, takes no listener snapshot and preserves source, credential, environment, write and ordering restrictions

### Requirement: Opted in localhost profile

An opted-in verification profile SHALL require an explicit successful listener snapshot, retain `(deny network*)`, allow inbound and outbound localhost TCP, then deny outbound localhost TCP to its sorted unique ports. Denies SHALL follow all network allows. It SHALL preserve non-network restrictions and grant no other networking. Without opt-in, the entire profile SHALL remain byte for byte identical.

#### Scenario: Localhost TCP is explicitly allowed
- **WHEN** an opted-in profile is constructed with a successful empty listener snapshot
- **THEN** `test_loopback_step_profile_allows_localhost_tcp` verifies exactly `(allow network-inbound (local tcp "localhost:*"))` and `(allow network-outbound (remote tcp "localhost:*"))` after the original network denial and unchanged non-network restrictions
- **THEN** no numeric-address wildcard, explicit network-bind, UDP or external-network allowance is added
- **THEN** omitting the snapshot or supplying an invalid port refuses profile construction instead of granting unrestricted localhost

#### Scenario: Default profiles retain network denial
- **WHEN** a step omits loopback or explicitly sets false, including a default step following an opted-in step
- **THEN** `test_steps_without_loopback_keep_deny_network` verifies byte-identical ordinary profile output with the existing `(deny network*)`, no localhost allowances or discovery call, and no leakage from the preceding step's profile

### Requirement: Launch time listener exclusions

Immediately before each opted-in verification launch, the harness SHALL take a fresh bounded snapshot of IPv4/IPv6 loopback-reachable TCP listeners, including wildcard binds, outside the child sandbox and after preceding cleanup. It SHALL use numeric ports to write a private profile outside the build directory, reject uncertain discovery and never reuse a snapshot across launches. Ports absent from the snapshot SHALL remain eligible for subsequent localhost TCP exchanges.

#### Scenario: Existing listener ports are denied
- **WHEN** listener discovery reports IPv4 loopback, IPv6 loopback, IPv4-mapped loopback, IPv4/IPv6 wildcard, duplicate and specific non-loopback TCP endpoints
- **THEN** `test_loopback_denies_ports_listening_at_launch` verifies one `(deny network-outbound (remote tcp "localhost:PORT"))` per loopback-reachable port in sorted order after both allows, across localhost address families, and valid specific non-loopback endpoints add no exclusion
- **THEN** the discovery command uses numeric TCP LISTEN output without PID/user filtering, a shell or the step's environment, and recognizes a valid empty result

#### Scenario: Each command observes its own launch
- **WHEN** two opted-in commands run sequentially and listener results change after the first completes, or another execution uses the same step IDs
- **THEN** each snapshot occurs after preceding cleanup and immediately before its own launch, each command receives its own exclusion set and profile, and the shared default profile and other executions remain unchanged
- **THEN** newly opened ports absent from the command's snapshot receive no deny, while an observed port remains denied for that command even if its original listener later exits

### Requirement: Loopback setup fails closed

Opted-in steps SHALL fail closed on discovery, profile, sandbox or launch-channel failure. The harness SHALL preserve diagnostics and completed receipts, raise RuntimeError and stop later commands without inventing a command outcome. Only confirmed command outcomes from a trusted supervisor in the same sandbox SHALL enter receipts; wrapper exits, command output and preflight success SHALL NOT authorize them. Defaults and preparation SHALL remain unchanged.

#### Scenario: Snapshot failure prevents execution
- **WHEN** discovery cannot start, is denied access, exceeds five seconds, reports errors with partial output, returns an unsupported failure status, cannot decode or parse its output, or the resulting profile cannot be generated or written
- **THEN** `test_loopback_snapshot_failure_fails_closed` verifies that neither the affected command nor subsequent commands launch, regardless of expected exit, and no permissive fallback profile is used
- **THEN** earlier receipts and the step-specific failure diagnostic remain available, a RuntimeError propagates, and no receipt is invented for the unlaunched command

#### Scenario: Enclosing sandbox cannot bypass exclusions
- **WHEN** an opted-in verification command is attempted inside an existing sandbox or without the required launch-time resolver
- **THEN** execution is refused before launching the command rather than relying on the enclosing sandbox or the shared verify profile
- **THEN** ordinary unflagged steps retain their existing nested-sandbox behavior

#### Scenario: Sandbox startup failure cannot satisfy expected exit
- **WHEN** sandbox-exec fails before command execution, including profile compilation exit 65 with either matching `expect = 65` or nonmatching `expect = 0`, or the supervisor cannot exec the command
- **THEN** `test_loopback_sandbox_startup_failure_is_not_command_exit` verifies a contextual RuntimeError regardless of expected exit, no affected-command execution or receipt, no later commands, retained previous receipts and wrapper diagnostics, and process-group and descriptor cleanup
- **THEN** missing, malformed or broken control records also raise setup errors rather than accepting a wrapper status or stdout/stderr text as a command result

#### Scenario: Actual command outcomes retain their meaning
- **WHEN** the opted-in command has confirmed startup and exits 65, exits on a signal, or exceeds its execution deadline
- **THEN** `test_loopback_command_exit_retains_expected_exit_semantics` verifies that a genuine exit 65 passes only with expect 65, an unexpected exit or signal remains a command failure, and a confirmed running-command timeout retains exit null
- **THEN** only trusted complete command-result records with successful wrapper completion authorize integer exits, a deadline before confirmed startup is a setup error, and wrapper failure without a completed result remains infrastructure failure
- **THEN** the command receives its original argv, cwd and configured env without the private control descriptor, the supervisor ignores candidate Python startup configuration, and command output resembling control messages cannot fabricate an outcome
- **THEN** receipt schema, one execution timeout budget, cleanup and all unflagged-step behavior remain unchanged

### Requirement: Real loopback enforcement evidence

Automated integration coverage SHALL exercise the real launch path, listener discovery and sandbox-exec policy using Python sockets without requiring Flutter or live services. It SHALL demonstrate both permitted own-port communication and refusal of a healthy pre-existing listener. Unsupported hosts or nested sandboxes SHALL produce explicit skips, while eligible-host policy failures SHALL fail the test.

#### Scenario: Own ephemeral listener works and existing listener is blocked
- **WHEN** `test_loopback_sandbox_allows_own_port_and_blocks_existing_listener` starts a reachable listener before launching an opted-in step on an eligible macOS host
- **THEN** the child binds an ephemeral IPv4 loopback port, listens, connects back and exchanges data successfully, while connecting to the still-healthy pre-existing listener receives a sandbox permission denial
- **THEN** IPv6-only and wildcard pre-existing listeners receive exclusion probes where supported, a non-macOS or nested-sandbox run skips explicitly, and a skipped probe is not reported as enforcement evidence

#### Scenario: Real sandbox rejection is a setup error
- **WHEN** an eligible macOS integration test launches an opted-in step through the real runner with malformed profile text and expect 65, followed by a separate valid-profile run whose command deliberately exits 65
- **THEN** sandbox compilation failure raises a setup error with retained diagnostics, leaves the command sentinel absent and produces no affected receipt, while the valid-profile command runs and passes with its genuine exit 65
- **THEN** non-macOS or nested-sandbox hosts skip explicitly, and unexpected failures on eligible hosts fail the test
