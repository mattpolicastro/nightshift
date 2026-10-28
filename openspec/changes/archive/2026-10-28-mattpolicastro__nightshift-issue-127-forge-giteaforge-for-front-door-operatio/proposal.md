## Why

Issue #127 supplies the Gitea backend for the front-door Forge seam introduced
by #126, using the guarded HTTP transport from #123. It is wave 3 of #122 and
depends on both prerequisites. Gitea differs from GitHub in ways that can silently
select the wrong issues, lose label writes, trust the wrong author, or duplicate
comments. These differences need explicit adapter contracts and offline tests
before daemon integration.

## What Changes

- Add a standalone `GiteaForge` implementing the existing `Forge` interface,
  with constructor-injected trusted logins, bot login and WIP title prefix.
- Resolve labels afresh, filter locally, verify mutations by reading back,
  normalize label timeline events and preserve commit parents.
- Resolve PR branches across all states, represent drafts by title prefix,
  and map combined status for an exact commit SHA.
- Reconcile ambiguous marked-comment creates without repeating POST, and
  restrict marker edits to bot-authored comments. Preserve guard exceptions
  unchanged, including exceptions sharing the HTTP error class.
- Add an in-process `FakeGitea`, adapter tests and reusable contracts with an
  explicitly configured live mode that skips without a URL.
- Correct the root README's module layout to name the implemented standalone
  Gitea backend while keeping enrollment and daemon integration deferred.

Out of scope: configuration keys, daemon enrollment or wiring, membership
lookups, `specloop/`, `nightshift/land.py`, plugin changes, unrelated README edits
and CI workflow changes. Existing GitHub assertions, core authorization/lifecycle
policy and transport behavior remain intact. Implementing agents edit only the
new Python implementation/test files and scoped README passage listed in the
design. They never edit
`openspec/**`; the harness archives this change's capability deltas at finish.

## Capabilities

### New Capabilities

- `gitea-forge`: Gitea front-door operations, conservative response handling,
  bot-owned comment reconciliation, offline fake and optional live contracts.

### Modified Capabilities

- `forge-interface`: narrow the Module layout guidance's historical “Gitea
  support is future work” statement to Gitea enrollment/integration. A standalone
  backend may now exist without claiming that the daemon can use it. All other
  requirements, including GitHub transcripts and request budgets, are retained.
