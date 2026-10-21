## Why

Nightshift now has two executors. The daemon's worker path (Claude Code only,
the worker commits, CI is the only independent check) ran 79 tasks between
August and September and has been paused since September 30. specloop (any
provider, harness-sealed commits, sandboxed trusted verification, gated review
with measured catch rates, recoverable pauses) built the a sample module's 21
tasks on October 19–20. Maintaining both is waste, and specloop is the better
executor.

What specloop lacks is the daemon's front door: a GitHub queue, a review loop on
the pull request, and the attention list. Work should start from specs, and
issues and PR review should remain how a human asks for and steers it.

## What Changes

- specloop exposes a stable command-line contract for a front end: JSON output
  for `status`, `propose`, `admit`, `revise` and `publish`; one manifest per
  repository used as a template for every change; adoption of an existing PR;
  and `specloop revise`, which turns review feedback into a round of tasks.
- Manifests gain `prepare` steps that run before network-free verification,
  in their own sandbox that allows network but confines writes, so repositories
  that need dependencies fetched (sample's pnpm workspace) can then be verified
  offline.
- The daemon routes each enrolled repository to an engine (`worker`, the
  default and unchanged, or `specloop`). For a specloop repository:
  - an issue labelled `spec:draft` (or `agent:ready`) is drafted into an
    OpenSpec change and opened as a draft PR for spec review;
  - `spec:approved` on that PR admits it;
  - the run publishes to the same PR;
  - `agent:revise` re-drafts the spec before approval, or adds a review round
    of tasks after it.
- `nightshift attention` lists specloop runs that are waiting for a human.

Out of scope:
- deleting the worker path (a later change, once one real issue has gone
  through the new path end to end);
- a Paseo page for specloop;
- quota pacing for specloop runs;
- porting the Paseo plugin to 0.11 or later.

## Capabilities

### New Capabilities

- `specloop-interface`: the JSON contract, manifest templates, PR adoption and
  revision rounds a front end drives specloop through.
- `verify-preparation`: trusted pre-verification steps with network access.
- `issue-intake`: per-repository engine routing, drafting from issues,
  approval and publication on the PR.
- `pr-revision`: `agent:revise` at the spec stage and at the build stage.
- `attention`: specloop runs in Nightshift's attention list.

## Impact

- Code:
  - `specloop/`: cli, manifest, verify, loop, plus a small revise module;
  - `nightshift/`: config, queue labels, daemon routing, a specloop client
    module, attention.
- Tests under `tests/`; docs in README.md, FACTORY-STATUS.md and
  config.example.toml.
- The live `config.toml`, the trusted manifests and the service wrappers are
  not changed by this change. Enrolling a repository with `engine = "specloop"`
  is a separate human edit.
