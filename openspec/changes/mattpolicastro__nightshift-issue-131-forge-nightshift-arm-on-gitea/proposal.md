## Why

Issue #131, part of #122 and dependent on #128 and #129, extends the existing
arming command to configured Gitea repositories. Arming currently issues GitHub
commands directly for target and dependency reads, concurrent context, label
writes and uncertain-write confirmation. Gitea already supplies the required
transport, client-side label selection, verified mutations and critic context.

The current issue-arming requirements mandate GitHub reads, and #129's
documentation requirement preserves a GitHub-only arming boundary. Those
guarantees need explicit, narrow modifications rather than contradictory additions.

## What Changes

- Select Gitea explicitly with `python -m nightshift.arm --config PATH ISSUE...`
  using the current checkout's unique configured enrollment. Omission retains
  the existing GitHub discovery and byte-identical command transcript.
- Route target/dependency reads, armed-issue and open-PR context, label addition
  and fresh confirmation through an arming-specific forge interface in
  `nightshift/arm.py`. Preserve lint, cache, overlap, mode and batch semantics.
- Use existing Gitea label resolution and complete client-side filtering;
  verify every label write without replaying an uncertain mutation. Preserve
  incomplete-context findings and the real critic helper's rename evidence.
- Update `docs/arming.md` and only the invalidated arming passage in
  `KNOWN-LIMITATIONS.md`. A necessary narrow overlap with work in flight also
  replaces the GitHub-only arming sentence under `### Explicit forge publication`
  in `docs/specloop/README.md`; preserve its daemon, landing and publication text.
- Add offline regression tests in new test files, including the three requested
  test names, retaining existing assertions and the type-error baseline.

Out of scope: enabling Gitea daemon stages or pending delivery, landing, CLI
consolidation, shared forge/critic refactoring, new rich-text parsing, changing
label authorization, local receipts, live-service automation, and unrelated docs.
No root `README.md` edit is needed: its explicit-engine and daemon distinctions
remain accurate. Implementation edits are confined to `nightshift/arm.py`, new
tests and the three narrowly scoped documentation passages described in design.
These OpenSpec deltas are read-only implementation inputs; implementing tasks
must not edit `openspec/**`.

## Capabilities

### Modified capabilities

- `issue-arming`: replace GitHub-specific read/collection/mutation boundaries
  with selected-forge behavior; add configured selection, compatibility,
  Gitea collection and verified-write contracts. Preserve every other requirement.
- `specloop-documentation`: add concise arming guidance requirements and MODIFY
  the full `Explicit forge command guidance` requirement, retaining both scenarios
  and changing only the arming boundary.

The existing `forge-interface`, `gitea-forge`, `forge-configuration`,
`spec-critic`, `in-flight-file-map` and daemon requirements remain unchanged.
