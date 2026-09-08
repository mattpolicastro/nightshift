# Native OpenAI worker implementation status

The first implementation milestone adds an offline transport and stronger task
verification. **Native OpenAI execution is disabled.** It cannot claim queue
items, fall back to Claude, or be enabled with an operator bypass.

## Implemented

- Typed worker requests, budgets, completed commands, results and review verdicts.
- An app-server stdio transport tested with synthetic processes: initialization,
  thread/turn identity, terminal completion, command exit status, structured
  review output, cumulative usage, cancellation and bounded private journals.
- Unknown server requests fail closed. Runtime, action, output and stream limits
  terminate the fixture session; stderr cannot supply protocol events.
- Explicit native endpoint configuration validation. Invalid routes, duplicate
  names, missing endpoints and ignored authentication settings are rejected.
- Host verification of the committed candidate in a fresh detached checkout,
  followed by a check that the candidate and shipping branch still match.
  Worker claims of running tests are no longer sufficient shipping evidence.
- Failed worker processes cannot succeed through a success-shaped final event.

The transport's private fixture runner accepts an explicit test process and
synthetic environment. It is not a production credential or launch path.
A transitional Claude adapter now emits typed results while preserving legacy
quota and verdict data separately. Missing telemetry stays unknown; requested
commands do not become completed-command evidence. Its invocation accepts the
legacy max-turn limit explicitly, not unenforced Codex budgets. Production
dispatch still uses the existing interface; integrating the adapters remains
implementation work.

An offline schema inventory records input fingerprints and available permission
fields separately from untested enforcement. It always returns a blocked
qualification result; schema presence cannot enable execution.

## Verification configuration change

Repository `verify` commands must be direct argv commands, optionally joined
with `&&`, such as `pnpm typecheck && pnpm test`. Shell expansion, pipelines,
redirections, background operators and compound shell syntax are unsupported.
Move complex verification into a trusted repository script and invoke it
explicitly. Preflight validates this syntax.

The host runs every clause with a clean environment and scratch home, within a
shared ten-minute deadline. Each command has a 16 MiB combined stdout/stderr
limit; exceeding it fails verification and terminates its process group. Only
the final 16 KiB is retained in memory. A pnpm lockfile triggers a frozen-lockfile install;
other repositories must supply self-contained verification commands. Failed
bootstrap, incomplete commands, background work, candidate edits or branch
movement prevent shipping. A private `.verification.json` artifact records the
candidate SHA and command outcomes alongside worker transcripts.

This executes trusted repository code on the host. Environment filtering and
process-group cleanup are not filesystem/network isolation and cannot contain
processes that escape their group. Native worker isolation is a separate gate.

## Remaining before native support can be claimed

1. Qualify effective filesystem, Git, credential and network boundaries on each
   supported platform/runtime. See [security qualification](codex-security-qualification.md).
2. Add the qualified runtime launcher, version checks, API-key authentication,
   model capability validation and production permission assembly.
3. Integrate driver dispatch, host-owned Git operations, normalized accounting
   and retry/error handling with the daemon's implementation/review phases.
4. Run a credential-safe OpenAI canary, then an end-to-end sandbox task with
   independent review, host verification, PR creation and GitHub CI.

The [specification](../SPEC-openai-workers.md) is the acceptance contract.
No paid OpenAI calls or production daemon changes were part of this milestone.

See the [fallback isolation experiment](isolated-tool-execution.md) for the
provider/tool separation required if native permission qualification fails.
