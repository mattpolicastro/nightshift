## Purpose

Allow mutation evidence to reuse trusted dependency snapshots while preserving independent baseline, mutant and confirmation executions.

## ADDED Requirements

### Requirement: Snapshot isolation across evidence phases

The evidence harness SHALL pass the run snapshot context to every used-sequence baseline and executable mutant phase, compute keys from each actual source extraction, and supply a fresh private clone per build. It SHALL never warm or publish dependencies from evidence execution. Existing selection, baseline ordering, concurrency, expected exits, classifications, scores, timeout budgets, receipt schemas and cleanup order SHALL remain unchanged.

#### Scenario: Baselines mutants and confirmations stay independent
- **WHEN** routed mutants run concurrently and a passing narrowed phase requires full confirmation
- **THEN** all required baselines finish before workers, each baseline and phase receives a distinct writable snapshot clone, candidate mutations remain visible, and a narrowed phase's cache writes cannot affect confirmation or siblings
- **THEN** the store remains unchanged, workers retain their existing slot and timeout rules, and existing deterministic results and receipt schemas are preserved

#### Scenario: Mutated lockfile falls back without changing classifications
- **WHEN** a mutant changes a declared lockfile while retaining the candidate SHA argument, or any evidence build cannot use its snapshot
- **THEN** lookup uses that build's actual lockfile bytes and records fallback per affected execution, with thread-safe run-event forwarding and no warm invocation
- **THEN** original prepare/test outcomes govern baseline failure, invalid, timeout, killed or survived classification, and setup misses fabricate no test receipts or mutation kills
