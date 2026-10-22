## Why

Section evidence gates currently verify mutants one at a time. Each can take
about a minute, so independent mutant checks unnecessarily delay the gate.
Running a small, bounded number together can reduce that wait while preserving
the evidence used to decide whether a section passes.

## What Changes

- Add trusted manifest `[gates] mutation_parallel`, a positive integer with
  default `2`; `1` selects serial execution.
- Run at most that many mutants concurrently in `specloop/evidence.py`, after
  one successful unmutated baseline. Give each mutant private source, build,
  environment directories, sandbox profiles, logs and receipts.
- Preserve generated mutant order, classifications, scoring and evidence JSON
  regardless of completion order. Preserve baseline-derived per-step budgets;
  apply them consistently to prepared mutants as well, closing the current
  prepare-path bypass.
- Extend tests for configuration, isolation, bounded overlap, serial/parallel
  result equivalence, timeouts and cleanup. The only supporting production
  change outside `specloop/evidence.py` is manifest parsing and its dataclass
  in `specloop/manifest.py`, required to expose the requested setting.

Out of scope: new mutation operators, sampling or scoring rules, a gate-wide
deadline, adaptive concurrency, cross-run resource scheduling, shared build
caches, CLI/UI controls, sandbox policy changes and guaranteed speedup. Real
section timing and contention with another build remain human checks.

## Capabilities

- **Added: `parallel-evidence-mutants`** — configurable bounded concurrency,
  independent mutant workspaces, stable evidence, and baseline-derived step
  time-boxes for section mutation checks.
- Existing capabilities retain their contracts; prepare steps still precede
  offline verification and use the existing sandbox and receipt interfaces.
