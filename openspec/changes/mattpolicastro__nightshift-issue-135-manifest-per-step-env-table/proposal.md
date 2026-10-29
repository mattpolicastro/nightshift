## Why

Trusted verification commands sometimes need tool-specific environment settings,
such as a cache under `{build}` or `{snapshot:deps}`. Today operators must put
assignments inside shell commands or `/usr/bin/env` argv. An optional per-step
table makes these settings explicit while preserving the harness-owned
environment and sandbox boundaries.

This change depends on #134 (trusted dependency snapshots) and #128 (forge
configuration and sealing); both implementations are present in this checkout.
Tracking issue: https://github.com/mattpolicastro/nightshift/issues/135.

## What Changes

- Accept optional `env = { NAME = "value" }` on `[[verify]]`, `[[prepare]]`,
  `[[acceptance]]` and `[[mutation_step]]`, defaulting to an empty mapping.
- Expand each value with the same function and execution-time values as `run`,
  including available snapshot paths. Add the result to a fresh copy of the
  fixed command environment for that step only.
- Reject malformed tables and attempts to set `PATH`, `HOME` or `TMPDIR`
  before execution. Other fixed defaults may be explicitly overridden.
- Preserve existing commands, defaults, assertions, sandbox policies, receipts,
  snapshot behavior and mutation scheduling. Document the option and test both
  substitution and harness-variable protection.

Implementation scope is `specloop/manifest.py`, `specloop/verify.py`,
`docs/specloop/README.md`, new focused tests and specification deltas. Explicitly
out of scope: global environment configuration, inheriting host variables,
secret loading, agent-session environments, forge/seal changes, new snapshot
warm fields, sandbox grants, mutation-route or narrowing changes, and migration
of existing manifests or shell commands. This draft edits only its change
directory.

## Capabilities

- `verify-preparation`: add requirements for the shared step environment
  schema, substitution, per-command isolation and harness-owned variables.
- `specloop-documentation`: add guidance and a validated manifest example for
  all four step kinds.

Existing requirements in these capabilities, `mutation-selection`,
`parallel-evidence-mutants` and `config-resilience` remain in force. No existing
requirement is removed or weakened, so only ADDED deltas are needed.
