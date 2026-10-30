## Purpose

Retire the Factory worker's #126 command-boundary exception without widening any other exception or changing the shared Forge protocol. Scope current landing guidance to docs/landing.md because broader guides are concurrently edited and outside this change.

## ADDED Requirements

### Requirement: Factory exception retirement

The source boundary SHALL reject literal GitHub argv in `nightshift/factory_land.py` after its command definitions move into the permitted landing boundary. No replacement exception or syntactic disguise SHALL be introduced.

#### Scenario: Factory is no longer deferred
- **WHEN** the AST boundary runs against the migrated source or a fixture containing a forbidden literal list in nightshift/factory_land.py
- **THEN** production passes, the injected violation fails, and all existing scanner assertions remain intact

## MODIFIED Requirements

### Requirement: AST source boundary

`test_no_gh_argv_outside_backends` SHALL parse all Python sources under `nightshift/` and `specloop/` with ast and reject every literal list whose first element is the string gh, issue, pr or api outside `nightshift/forge/github.py` and the exact remaining temporary allowlist: `nightshift/queue.py`, `nightshift/land.py`, `nightshift/arm.py`, `nightshift/outcomes.py`, `nightshift/preflight.py`, `specloop/cli.py`, `specloop/critic.py`. Parsing or reading failures SHALL fail validation. The allowlist SHALL NOT include nightshift/factory_land.py after #130 migration.

#### Scenario: Scan rejects new argv sites
- **WHEN** an unallowlisted source contains a prefixed list, including an assigned list, multiline list or starred-tail list, or cannot be parsed
- **THEN** validation fails with the source path and available line information
- **THEN** comments and string contents alone do not count as argv, and tests exercise each forbidden literal prefix without invoking live services

#### Scenario: Deferred modules remain allowed
- **WHEN** the scan encounters existing argv in queue.py, land.py, arm.py, outcomes.py, preflight.py, specloop/cli.py or specloop/critic.py at the exact paths above
- **THEN** it permits those deferred sites and the GitHub backend, while rejecting near-miss paths and direct builders in intake, daemon or factory_land
- **THEN** no directory-wide exception, new allowlisted module or command-construction disguise is introduced

### Requirement: Module layout guidance

The root README's module layout SHALL retain the Forge contract, factual records, GitHub backend, Gitea backend and retained queue transport. Its staged migration description SHALL remain historical until separately updated; `docs/landing.md` SHALL describe the current #130 terminal Gitea landing boundary. Gitea configuration, adapter construction and read-only preflight SHALL remain distinct from disabled daemon execution and GitHub-only worker recovery. This scoped documentation change SHALL not require edits to README.md or other concurrently edited guides.

#### Scenario: Layout describes staged boundary
- **WHEN** a contributor reads the root module-layout table and the current landing guide
- **THEN** the named modules resolve to implemented files and distinguish backend operations from queue transport and core policy
- **THEN** the table names forge/gitea.py over forge/http.py, configuration and preflight do not imply enabled Gitea workflows, and worker recovery remains GitHub-only
- **THEN** docs/landing.md explains terminal Gitea landing, unchanged Factory UI eligibility and disabled daemon dispatch, superseding the historical future-work note for landing alone
