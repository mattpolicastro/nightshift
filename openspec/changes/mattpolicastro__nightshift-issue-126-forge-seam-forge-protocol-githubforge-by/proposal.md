## Why

Intake and merge-readiness currently construct GitHub commands throughout
`nightshift/specloop_intake.py` and `nightshift/daemon.py`. A future Gitea backend
needs an operation interface without moving authorization or lifecycle policy
into a provider. This is #126, part of #122 wave 2, dependent on #123's forge
package. That package and its standalone HTTP transport are present in this
checkout.

The change is an extraction, with no behavioral change or new GitHub reads.
Existing runner-form argv assertions are the compatibility contract and must
pass without edits.

## What Changes

- Define `Forge` and `ForgeError` in `nightshift/forge/__init__.py`, frozen
  factual records in `nightshift/forge/types.py`, and `GitHubForge` in
  `nightshift/forge/github.py`.
- Route intake, approval, revision, publication delivery, merge-readiness and
  daemon engine-switch recovery through named forge operations. Construct the
  backend inside functions from the current injected runner or `queue._gh`.
- Preserve every existing argument, field order, pagination flag, request
  count, guard, retry, error classification and delivery ordering. Existing
  queue helper operations delegate to those helpers with the bound runner.
- Add exact-operation tests and an AST boundary test in the two new test files;
  update only the root `README.md` module-layout documentation.
- Explicitly allow `nightshift/factory_land.py` in the temporary boundary
  allowlist. It remains unchanged; #130 covers its landing migration.

Out of scope: landing, arming, outcomes, preflight, engine CLI/critic migration,
Gitea integration or changes to `nightshift/forge/http.py`, configuration keys,
plugins, caching, new reads, pagination changes, new retries, changed trust
rules, changed marker/checklist parsing, and changes to existing test assertions.
The full implementation file allowance and concurrent-file exclusions are in
the design.

## Capabilities

- **New: `forge-interface`** — factual operation contracts, compatible GitHub
  execution, late binding, guarded dispatch and an enforceable staged source
  boundary.
- **Modified: `issue-intake`** — require forge-mediated GitHub operations and
  replace the dependency gate's literal direct helper call with an equivalent
  forge operation that delegates to that same helper. All six dependency-gate
  scenarios are retained verbatim.

All other requirements remain in force, particularly `merge-readiness`,
`pr-revision`, `paused-job-delivery`, `publish-base-sync`, `worker-retirement`,
`gitea-http` and `specloop-documentation`. No behavioral requirement is removed.
