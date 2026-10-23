## Why

Landing a reviewed PR currently depends on a shell script on one machine and leaves no durable record that its human checks were performed. On 2026-10-22, #50 and #21 each passed CI against a base that was stale by merge time, turning `main` red. Operators need a repeatable landing command that records checks, finishes eligible specloop runs, waits for CI against current `main`, and refuses unsafe progress.

## What Changes

- Add `nightshift/land.py`, invoked explicitly by a person or their delegated session as `uv run python -m nightshift.land <pr-url-or-number> [--repo owner/name]`. Require enrollment in `config.toml`.
- Read human checkboxes from the PR body and, for front-door PRs, the current daemon publication comment. `--checks-done` ticks checks in place and creates or updates one head-specific attestation identifying the authenticated GitHub user.
- Finish matching runs only at `human_checks`; continue already-done runs whose PR remains open; skip finish for direct PRs.
- Bound CI waiting to 30 minutes by default and three rounds. Update stale branches through the GitHub REST update-branch endpoint with `expected_head_sha`; accept only the exact expected merge parents. Merge with a merge commit and `--match-head-commit`.
- Add isolated runner-based tests in `tests/test_land.py` and operator instructions in `docs/landing.md`.

Implementation is limited to those three new files. Every existing file is out of scope, including `nightshift/cli.py`, `nightshift/queue.py`, `nightshift/daemon.py`, `specloop/cli.py`, `specloop/serve.py`, `pyproject.toml`, existing tests, `README.md`, `KNOWN-LIMITATIONS.md`, `docs/arming.md`, `docs/specloop/README.md`, and all plugin files. Do not integrate with the daemon, supervisor, launchd, or plugin. Unified CLI integration belongs to #40; deployment belongs to #77, so this command prints no deploy hint. It does not perform human checks, grant review approval, bypass branch protection, or guarantee an atomic check of the base at merge time.

## Capabilities

### New Capabilities

- `reviewed-pr-landing`: An operator-invoked, recoverable landing sequence with recorded human checks, specloop lifecycle gates, bounded CI/base synchronization, and a head-conditioned merge.

### Modified Capabilities

None. Preserve all existing requirements of `specloop-interface`, `run-actions`, `issue-intake`, `pr-revision`, and `publish-base-sync`. This is a new consumer of the existing status/finish interface and publication comments; it does not change publication, finish, revision, or daemon behavior.
