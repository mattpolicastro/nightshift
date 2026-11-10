## Purpose

Explain active attention sources, stale evidence and historical compatibility
without changing enrollment, Factory controls or daemon escalation policy.

## ADDED Requirements

### Requirement: Front door attention guidance

Attention guidance in `README.md`, `plugins/nightshift/README.md`,
`docs/specloop/README.md` and `config.example.toml` SHALL consistently describe
escalations, optional summaries, Factory pointers, retained historical APIs,
bounded reads, stale/offline states and JSON compatibility. Refresh triggers,
config values and enrollment examples SHALL remain unchanged. Live escalation
latency and phone readability SHALL remain pending human checks, never claimed
automated results.

#### Scenario: Operator understands the attention list
- **WHEN** an operator reads the CLI usage, What needs you and plugin refresh passages
- **THEN** they can distinguish confirmed empty results, cached/stale or unchecked repositories, optional comment summaries, historical storage and Factory pause references
- **THEN** they can identify the configured label, the 40-second shared GitHub budget, eight-second request limits, at most four concurrent reads and JSON compatibility limits without obsolete worker-refresh or repository-scheduler promises
- **THEN** they understand that a complete listing appears even when summary enrichment fails, with retained summary text or "Summary unavailable" and separate listing/summary freshness diagnostics
- **THEN** the guidance leaves label meaning, daemon escalation and Factory actions unchanged and identifies real escalation/phone checks as pending
- **THEN** version-1 additive fields, empty legacy arrays, legacy-producer unavailability, unsupported-forge diagnostics and retained historical APIs are explicit
- **THEN** `config.example.toml` no longer promises historical outcomes through attention, and `docs/specloop/README.md` describes empty legacy arrays rather than historical worker tasks in attention JSON

## MODIFIED Requirements

### Requirement: Worker retirement guidance

Current guidance in allowed documentation SHALL explain sole-engine enrollment
and worker retirement, distinguish retained history from execution and active
attention display, and document compatibility boundaries and human rollout checks
without claiming unperformed validation.

#### Scenario: New enrollment follows one engine
- **WHEN** an operator follows README, config.example.toml and the specloop guide
- **THEN** the runnable example enrolls a specloop repository with a trusted manifest and explains omission defaults, explicit worker rejection and inert obsolete settings
- **THEN** it explains repository-local holds for missing or unusable manifests and removal of worker status output, including the off-subscription model report, while all other daemon status output remains unchanged under nightshift daemon status
- **THEN** direct Python front-door instructions do not require worker verification strings, endpoint models or OAuth; manifest providers retain their own requirements
- **THEN** bundled service instructions explicitly retain the frozen daemon wrapper's env-file, worker OAuth, GH_TOKEN and gh/claude/pnpm/node/git/swift checks, distinguishing direct invocation from service startup without claiming Python changes bypass shell checks

#### Scenario: Existing installation is checked before merge
- **WHEN** an operator reads retirement and rollout guidance
- **THEN** it identifies retained outcome history/recovery, current front-door attention and Factory references, the canonical nightshift CLI/state root and one-release legacy compatibility, separate daemon/supervisor jobs and the need to inspect live config/plists/external callers before merge
- **THEN** the specloop guide's rollout checklist distinguishes checking current attention from checking retained history through historical APIs, preserving leftover recovery and independent supervisor checks without requiring historical worker rows in attention
- **THEN** it explains the CLI merge and state migration delivered by #40, preserves unrelated limitations, and cites issue #16 without inventing an implementation PR
- **THEN** live config, plist and external-caller inspection remains a pre-merge human checklist, never a claimed CI result
- **THEN** it describes guarded migration without claiming host checks performed, safety on the live machine, rollback of remote effects or automatic restart of interrupted worker work
- **THEN** it identifies obsolete worker instructions/metadata in frozen CLAUDE.md and pyproject.toml, historical worker documents and their removed-source references, and resolved type-baseline entries as deferred cleanup outside the implementation allowance
