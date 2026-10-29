## Why

Issue #129 supplies explicit Gitea publication and finish, but `nightshift.land` still assumes GitHub. Wave 6 of #122 needs an operator to land the reviewed Gitea head without lending the daemon human credentials or weakening the existing landing gates. This change is blocked by #129; its published PR identity and finish behavior are prerequisites.

## What Changes

- Resolve Gitea landing from the PR URL hostname against configured forges, then validate the complete configured origin, repository enrollment and PR identity. Preserve existing GitHub URL/number behavior and byte-identical argv.
- Read the selected Gitea `land_token_env` from the operator environment overlaid by `~/.config/nightshift/land.env`, parsed by `land` itself. Never use the bot token as a fallback or export newly loaded secrets to child processes.
- Use `trusted_logins` for Gitea publication and attestation trust, retaining checklist, marker, pagination, revalidation and finish protections.
- Update a stale Gitea branch with one merge-style POST per required update, after checking the expected head; accept only a new head with exactly ordered parents `[expected_head, base]`.
- Wait on combined status for the exact head SHA, then request a merge commit pinned by `head_commit_id` and confirm `merged` before success.
- Migrate Factory Land's GitHub command definitions into the landing operation boundary and remove `nightshift/factory_land.py` from the #126 AST allowlist. Preserve its existing confirmation and execution isolation.
- Update `docs/landing.md` and add offline regressions, including the four issue-named tests.
- Amend `specloop-documentation`'s Explicit forge command guidance to replace its GitHub-only terminal landing guarantee with the #130 support boundary in `docs/landing.md`. Preserve publication guidance and the disabled daemon, arming and Factory UI boundaries without editing the shared guides.

Implementation scope is `nightshift/land.py`, `nightshift/factory_land.py`, `docs/landing.md`, tests and specs. No daemon, `specloop/`, plugin, shared forge implementation, service or configuration-schema changes. Factory UI eligibility remains unchanged; this is not a Gitea Land-button rollout. Merge remains explicitly human initiated. Automatic landing, rebase, protection bypasses, alternate base branches, live-service tests by default and credential provisioning are out of scope.

Avoid the concurrent work files named in the design, including `README.md`, `KNOWN-LIMITATIONS.md`, `docs/specloop/README.md` and all listed loopback tests. Use new landing test files.

## Capabilities

### New Capabilities

- `gitea-landing`: configured-origin selection, human credential isolation, allowlisted check evidence, single-attempt branch updates, exact-SHA CI and pinned confirmed merges.

### Modified Capabilities

- `reviewed-pr-landing`: broaden the existing operator and identity contracts to Gitea; qualify GitHub-specific authentication, update and merge operations while retaining every other landing gate.
- `factory-landing-jobs`: replace the old restriction on landing implementation changes with the shared operation boundary, preserving confirmed handoff semantics.
- `forge-interface`: retire the Factory worker's temporary argv exception and describe the landing guide as the current scoped documentation. Other staged exceptions remain unchanged.
- `specloop-documentation`: amend Explicit forge command guidance so the landing guide supersedes historical GitHub-only terminal landing wording while retaining engine publication/finish guidance and all unrelated boundaries.
