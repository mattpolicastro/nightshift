## Why

Lint and feasibility review miss spec defects that a human reviewer caught on 2026-10-21: oversized mechanisms, collisions with unfinished work, instructions an implementer cannot follow, newly incorrect assertions that the spec forbids changing, conflicts with validators elsewhere, and tests that another pending change will break. Catching these before opening the spec PR avoids rejected drafts and blocked builds.

Dependency: `blocked-by: #14`. Its CLI and strict scenario-reference behavior remain prerequisites, not work for this change.

## What Changes

- Add a separate read-only spec critic after a draft passes lint and feasibility, including spec-stage revisions through `propose`.
- Apply all five items of the human checklist, with evidence for blocking findings. Reuse `feasibility.SCHEMA`: `contradictions` block; `risks` are notes.
- Give the critic the existing in-flight path-to-change map and a harness-collected map of open PRs' changed files, including production files.
- Feed blocking findings into at most one redraft within the existing drafting budget. Recheck that revision and reject unresolved findings.
- Append notes to the draft's existing `risks`, prefixed `Critic: `. The existing daemon renders them in the PR body under its existing `Feasibility risks` heading. The requested separate “Critic notes” heading is superseded by the user's explicit no-intake-change decision.
- Add new test files. The user-approved exception permits fake-agent and fixture setup changes in `tests/test_specloop_propose.py` and `tests/test_specloop_command_json.py`, preserving every existing assertion; identify critic calls by prompt and return empty findings in existing fixtures. Explain every fixture change in the implementation PR.

Production edits are confined to `specloop/propose.py` and new `specloop/critic.py`. No edits to the daemon, build loop, CLI result schema, manifest schema, existing feasibility implementation, or admission checks. In particular, do not edit `nightshift/specloop_intake.py` or any other work-in-flight file named in the design. No new CLI result field, critic role configuration, parser framework, or automated substitute for human calibration.

## Capabilities

### New Capabilities

- `spec-critic`: pre-PR checklist review, concurrent-file context, bounded redrafting, and notes through existing risk delivery.

### Modified Capabilities

None. Preserve all existing requirements in `specloop-interface`, `issue-intake`, and `pr-revision`, including version-1 command results, manifest overrides, rejection delivery, and spec-stage revision on the existing branch. The critic adds a drafting gate without changing those contracts; no MODIFIED or REMOVED delta is needed.
