## Why

Evidence gates consumed 32% of build wall time across 19 Nightshift changes measured on 2026-10-23, about 14 minutes per section gate. Each mutant currently runs every selected mutation step. Matt's combined Python/plugin command costs 100–330 seconds per mutant regardless of the mutated language, although a few relevant tests often kill it.

Dependency: `blocked-by: #67`. Build on its reconciled evidence documentation; its archived change is present in this checkout.

## What Changes

- Add ordered, optional `[[gates.mutation_route]]` entries with path globs and step IDs. The first matching route wins; unmatched files use `mutation_steps`.
- Add `[[mutation_step]]` definitions using the existing verify-step shape. Mutation routes and the fallback can reference verify or mutation-only steps, keeping normal verification combined.
- Expand `{mutant_path}` to the repository-relative path for narrowed runs. Only a passing narrowed run proceeds to full confirmation, with the placeholder empty; only that full run can establish survival.
- Run one full unmutated baseline for each distinct step sequence actually selected. Derive each mutant step's existing time budget from its corresponding full baseline.
- Retain separate narrowed/confirmation receipts and record the run deciding each result without changing the existing evidence JSON shape or scoring.
- Add a stdlib Python import selector, wire it into the example manifest, and document configuration and rollout in the guide's Section evidence subsection.
- Preserve legacy manifests, existing assertions, mutation generation, timeout/invalid distinctions, worker isolation and cleanup. Add tests only in new test files.

Out of scope: operators, candidate generation and sampling, scoring, the coverage gate, `specloop/loop.py`, verifier sandbox changes, a timing-report implementation, automatic edits to trusted manifests, and changes to the Nightshift front door or landing workflow. The only necessary concurrent-file overlap is `docs/specloop/README.md`, restricted to Section evidence.

## Capabilities

### Modified Capabilities

- `mutation-selection`: add mutation execution routing, mutation-only step definitions and the example import selector; retain all candidate-selection requirements.
- `parallel-evidence-mutants`: replace the single-baseline execution assumption with baselines per selected sequence, add narrowing/confirmation and decision receipts, and preserve concurrency, classifications, budgets and cleanup.
- `specloop-documentation`: add routed-evidence guidance while preserving the reconciled guide's other requirements. Scope the historical documentation-only restriction to #67 so it does not prohibit this runtime change.

No capability is removed. The parallel spec also explicitly scopes #49's historical test-only edit restriction to that regression repair; its protected test and all assertions remain unchanged.
