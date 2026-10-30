## Why

Gitea 28.2.0 exposes label actions in timeline `body` values and returns complete
issue-comment and review-comment lists without pagination. The adapter currently
requires a nonexistent `removed` field and repeatedly requests comment pages,
causing valid evidence to fail. These #122 compatibility fixes must land before
#150 lifts the Gitea dispatch hold.

## What Changes

- Read label actions solely from `body`: `"1"` means labeled, `""` means unlabeled;
  reject every other value and preserve actor, label, timestamp and ID checks.
- Read each issue-comment or per-review comment listing with one unpaginated
  GET, retaining complete-response validation and duplicate-ID refusal. Preserve
  the public list-of-pages interface and existing transport retries and guards.
- Make FakeGitea return those real shapes, including when unchanged contract
  fixtures supply legacy label data. Keep paginated endpoints paginated.
- Update only the Gitea comment request fixtures/assertions in
  `tests/test_land_gitea.py` and the Gitea branch of
  `tests/test_land_docs_only.py` that encode pagination. Landing continues to
  refuse malformed or duplicate evidence. Include the approved landing delta.
- Explicitly enumerate replaced fake-shape assertions in the design; preserve
  all other assertions. Deliver three tasks, each targeting about 200 changed
  lines and never more than about 300.

Implementation scope is `nightshift/forge/gitea.py` (the named readers and private
listing helper), `tests/fake_gitea.py`, `tests/test_forge_gitea.py`, the narrowly
approved portions of the two landing test files, and the `gitea-forge` and
`gitea-landing` specifications. This draft writes only its own change directory.

Out of scope: `tests/contract/`, every GitHub path and assertion, `nightshift/land.py`,
HTTP transport policy, authorization, dispatch enablement, CI configuration,
type-baseline edits, other endpoints, and other files. The contract suite remains
the unchanged oracle; its direct private-helper calls must work too.

## Capabilities

- Modified `gitea-forge`: real label action encoding, endpoint-specific complete
  reads, unchanged feedback meaning, and accurate offline transport fixtures.
- Modified `gitea-landing`: existing human-check semantics use complete single
  comment-list reads while retaining all landing gates and refusal behavior.
