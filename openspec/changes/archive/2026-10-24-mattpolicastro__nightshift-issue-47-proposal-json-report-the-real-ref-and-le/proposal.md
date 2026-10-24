## Why

When a reviewer has `spec/<change>` checked out, proposal publication retains
the new commit at `refs/specloop/proposals/<change>` to protect their checkout.
`propose --json` nevertheless reports `spec/<change>`, which still points to
the old commit. Correcting the producer alone would break Nightshift: its
shared proposal-result validator accepts only the public branch spelling.

This is issue #47, split from the refused #14 proposal, with implementation
dependencies `blocked-by: #13, #9`.

## What Changes

- Report the actual ref returned by local proposal publication in successful
  version-1 JSON, retaining the existing `branch` field.
- Accept exactly `spec/<change>` or `refs/specloop/proposals/<change>` for the
  recorded change in both initial drafting and spec-stage revision.
- Treat the returned ref as the local proposal source and keep the GitHub
  destination `refs/heads/spec/<change>`. Preserve commit-pinned pushes,
  checkout isolation, delivery retries and existing PR identity.
- Add `test_propose_json_reports_real_ref` and
  `test_daemon_delivers_a_fallback_ref_proposal`, plus focused rejection,
  revision and recovery coverage. Implementation acceptance is green
  `uv run pytest`.

Runtime scope is `specloop/cli.py`, `nightshift/specloop_intake.py` and tests.
Out of scope: changing fallback selection in `specloop/propose.py`, arbitrary
daemon branch names, new JSON fields or versions, admission/build publication,
worker behavior, receipt migration, ref cleanup, deployment and unrelated #14
CLI polish. The live Mac Studio revision check remains a human checklist item.

## Capabilities

- `specloop-interface`: add the successful proposal source-ref contract.
- `issue-intake`: add exact source-ref acceptance and delivery guarantees.
- `pr-revision`: add fallback-ref delivery on the existing spec PR.

These are additive requirements. Existing Command JSON, Manifest templates,
Drafting from an issue, Owner qualified new identities, Recorded identities
remain authoritative, Spec-stage revision and Durable round snapshot
requirements remain intact. In particular, a private local ref does not
rename the requested branch, recorded change or public PR head. No existing
requirement is removed or weakened.
