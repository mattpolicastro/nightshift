## Why

Native projects need mutation operators that match their source language and a
way to distinguish failed compilation from a test killing a mutant. Dart is
the first target, with usefulness to be checked on the scheming-vizier engine.

## What Changes

- Add `gates.mutation_invalid_steps`, defaulting to `configure` and `build`.
  An explicit list replaces those defaults; prepare failures remain invalid.
- Select C-family operators for `.dart`, `.ts`, `.swift`, `.kt`, `.c` and
  `.cpp`; preserve the Python and other-extension legacy paths.
- Exclude C-family physical lines containing any quote or backtick, and lines
  whose first non-whitespace text starts with `//`, `/*` or `*`. Keep existing
  complete comment-block protection and cosmetic-literal exclusions.
- Keep C-family overlap checks separate from the annotated legacy check.
  Preserve existing assertions, with an explicit migration of legacy
  integer-mutation fixtures to fallback extensions and new C-family coverage.
- Update only the evidence section of `docs/specloop/README.md` and add a small
  Dart manifest example with structural tests. Implement in three tasks.

Out of scope: delimiter scanning, interpolation parsing or refusal, division
handling, whole-file refusal rules, language-specific parsing, new sandbox or
toolchain provisioning, scoring changes, additional extensions and automated
claims about real-project usefulness. Skipping lines containing strings is an
accepted conservative loss of candidates.

## Capabilities

- `mutation-selection`: extension dispatch, C-family operators and separate
  quote-line and comment-line exclusions; qualify existing compatibility
  guarantees where C-family eligibility intentionally changes.
- `parallel-evidence-mutants`: configurable invalid step IDs; replace the
  hard-coded classification exception while retaining default outcomes,
  prepare handling, timeouts, baselines, routing and scoring.
- `specloop-documentation`: explain the new defaults and limitations, provide
  a Dart configuration example and retain the real-engine human check.
