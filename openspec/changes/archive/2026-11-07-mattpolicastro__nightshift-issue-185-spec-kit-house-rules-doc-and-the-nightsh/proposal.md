## Why

Native spec-kit support needs an authoring contract before the engine adapter and
validator land. Spec-kit's sequential directory scan can allocate the same number
to parallel drafts, and its default scenarios lack stable IDs for Nightshift's
existing coverage evidence. Issue #185 supplies the human guidance and preset for
the initiative tracked by [issue #184](https://github.com/mattpolicastro/nightshift/issues/184).

## What Changes

- Add `docs/specloop/speckit.md` with eight house rules, their reasons, and concrete
  issue-first authoring examples.
- Add exactly five files under `presets/nightshift-speckit/`: `preset.yml`,
  `templates/spec-template.md`, `templates/tasks-template.md`,
  `commands/speckit.specify.md`, and `README.md`. The wrapper selects the issue's
  feature directory before executing spec-kit's composed core command.
- Add one sentence linking the house rules under `docs/specloop/README.md`'s
  "How the engine works" and one `presets/` row in root `README.md`'s Layout.
  Correct other passages in these two files or `KNOWN-LIMITATIONS.md` only if
  this change makes them false; adding authoring assets does not enable runtime
  support or invalidate existing OpenSpec lifecycle guidance.
- Add only the three explicitly authorized documentation integrity tests in
  `tests/test_specloop_documentation_integrity.py`, preserving every existing
  assertion. This is a narrow exception to the documentation-only test freeze.

Out of scope: engine behavior, format selection, validation/admission enforcement,
runtime freezing, archival/finish changes, migrations, a plan template override,
release publishing, new scripts/tooling/dependencies, and edits under `specloop/`,
`nightshift/`, or `plugins/`. No other test file, engine test, or fixture changes.
The operator confirmed on 2026-11-07 that `presets/**` is allowed in the trusted
manifest for newly admitted runs; the preset remains in scope without a manifest
edit or further permission request.

## Capabilities

### New Capabilities

- `speckit-preset`: spec-kit 1.3.2-compatible authoring templates, issue-number
  command wrapper, and installation guidance.

### Modified Capabilities

- `specloop-documentation`: add spec-kit house rules and discoverability;
  narrowly amend Documentation integrity to permit the three additive tests and
  preset artifacts. All other existing documentation and runtime guarantees stay
  intact, including pending human validation and OpenSpec naming/finish semantics.
