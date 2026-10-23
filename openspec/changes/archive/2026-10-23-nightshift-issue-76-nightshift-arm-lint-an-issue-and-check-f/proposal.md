## Why

Issue wording and concurrent file ownership currently depend on operator notes.
Three spec revisions followed misleading issue wording or an unexecutable
definition of done; issue #13 escalated on 2026-10-23 over documentation another
armed issue was rewriting. Checking these conditions before adding `spec:draft`
can avoid a drafting round or an escalation.

## What Changes

- Add `nightshift/arm.py`, invoked as `uv run python -m nightshift.arm <issue>...`,
  to read GitHub issue bodies, report all lint findings, and check Scope paths
  against armed issues, running/paused runs, and open front-door PRs.
- Accept overlaps when either issue transitively depends on the other using
  the existing `blocked-by:` syntax; otherwise suggest the source issue as a
  blocker and identify the overlapping file and evidence.
- Add `spec:draft` when clean; provide read-only `--check` and explicit `--force`
  modes with defined batch and exit behavior.
- Add `tests/test_arm.py` with isolated GitHub/local-state fixtures and all eight
  requested named tests. Add `docs/arming.md` with usage, issue-writing guidance,
  limitations, and the later human comparison with drafting/critic feedback.

Implementation may create only those three files. Every pre-existing file is
out of scope, including `nightshift/queue.py`, `specloop/propose.py`,
`specloop/critic.py`, both CLI modules, `pyproject.toml`, existing tests,
`README.md`, `KNOWN-LIMITATIONS.md`, `docs/specloop/README.md`, and plugin files.
Existing helpers may be imported, but their contracts and existing assertions
are preserved. Integration into the single CLI belongs to #40. Automatic issue
rewriting, dependency insertion, daemon enforcement, new labels, PR creation,
and changes to trust, admission, drafting, or review policy are out of scope.

## Capabilities

### New Capabilities

- `issue-arming`: deterministic issue lint, dependency-aware overlap reporting,
  and optional application of the existing intake label.

### Modified Capabilities

None. The requirements in `issue-intake`, `in-flight-file-map`, and `spec-critic`
remain intact. This command consumes their existing interfaces; the daemon's
closed-state dependency gate and the critic's treatment of context limitations
are unchanged. The standalone entry point follows the module invocation style
of `touchpoint-report` without changing that report's offline contract.
