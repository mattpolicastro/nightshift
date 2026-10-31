## Why

Blocked by #165; part of #122 and a prerequisite for #150. After the label-action
and comment fixes, the issue reports that all four live contract cases still
error against the pinned `gitea/gitea:28.2.0` from PR #167. Timeline pagination
rejects the real null terminal page and mistakes per-page counts for listing
totals. Combined-status reads expect row `state` instead of `status` and reject
the terminal envelope's changed state/count. These failures block label-event
reads used by intake and exact-SHA CI reads used by landing.

The issue reports eight passing contract cases after both fixes in a scratch
copy, with matching shapes on a self-hosted 28.2.0. This change makes that
behavior explicit and testable without treating those reports as verification
of the eventual implementation.

## What Changes

- Accept JSON null as empty terminal evidence only for the timeline endpoint;
  stop treating its `X-Total-Count` as a listing total. Preserve the remaining
  pagination, record, conflict and termination checks.
- Read combined-status rows from `status`. Accept the zero-count null-statuses
  terminal envelope without replacing or comparing the nonempty-page snapshot.
  Preserve exact-SHA validation, status mapping, completeness and refusal rules.
- Make FakeGitea emit both real wire shapes; add the four requested regressions
  and narrowly update fixtures that encode the old shapes, preserving assertions.
- Amend `gitea-forge` requirements explicitly; deliver in two implementation
  tasks of about 200 changed lines each, with tests alongside each fix.

Implementation scope is `nightshift/forge/gitea.py`, `tests/fake_gitea.py`,
`tests/test_forge_gitea.py`, and only the affected status fixtures/assertions in
`tests/test_land_gitea.py`. The canonical documentation destination is
`openspec/specs/gitea-forge/spec.md`, via application of this change's deltas.
This draft itself writes only inside its change directory.

Out of scope: `tests/contract/` (the unchanged oracle), all other endpoints'
pagination, GitHub behavior, landing policy or `nightshift/land.py`, transport
retries, trust rules, daemon dispatch, configuration, version detection, and
PR #167's service provisioning. No dispatch hold is lifted here.

## Capabilities

### Modified Capabilities

- `gitea-forge`: endpoint-specific timeline termination/count semantics and
  combined-status decoding, with the same factual interfaces and conservative
  failure boundaries. Existing requirements remain in force except the explicit
  MODIFIED deltas; no requirement is removed.

### New Capabilities

None. The new requirements refine the existing Gitea adapter capability.
