## Why

The specloop guide and known limitations lag the changes merged since
2026-10-21. The guide still selects revision feedback by branch-tip time and
omits base preparation before publication. The limitations list still calls
parallel mutants, label authorization, job control and isolated status reads
missing. These descriptions send operators toward the wrong decisions.

This change depends on issue #6. Its implementation is already in this
checkout through merged PR #61; the documentation must preserve that behavior.

## What Changes

- Update `docs/specloop/README.md` to describe publication, spec-stage checks,
  the front door, mutation evidence and operations as implemented today.
  Cite the merged PR beside each changed behavior.
- Reconcile `KNOWN-LIMITATIONS.md` with tracking issue #17: remove demonstrated
  fixes, retain unresolved gaps, and describe admission while paused,
  stopped-job holds, touchpoint undercount, descoped JSX style filtering and
  GitHub-only intake/publication.
- Update only specloop/front-door descriptions in `README.md`, using short
  summaries and links to the guide where it carries the detail.
- Preserve the current voice: short, concrete, present tense. Keep existing
  assertions and run `uv run pytest` for implementation acceptance.

Out of scope: code, tests, dependencies, example manifests, CI configuration,
other documentation, `openspec/specs/`, `CLAUDE.md`, runtime changes, new
commands, and fixes for the limitations being documented. This draft writes
only this change directory; implementation edits only the three named docs.

## Capabilities

### New Capabilities

- `specloop-documentation`: accurate, traceable operator documentation for
  the shipped specloop workflow and its remaining limitations.

### Modified Capabilities

None. Existing runtime requirements remain in force. This change adds a
documentation contract; it does not modify or remove behavior guaranteed by
any existing capability.
