## Purpose

Retain the staged Forge boundary while permitting the standalone Gitea backend
without claiming Gitea enrollment or changing existing GitHub behavior.

## MODIFIED Requirements

### Requirement: Module layout guidance

The root README's module layout SHALL name the Forge contract, factual records,
GitHub backend, standalone Gitea backend and retained queue transport, describe
the staged boundary and avoid claiming Gitea enrollment is available. It SHALL
describe `forge/gitea.py` as implemented over `forge/http.py`, replacing the
obsolete future-backend claim while keeping Gitea enrollment and daemon
integration as future work. This change SHALL leave the concurrent engine
guide and unrelated documentation intact.

#### Scenario: Layout describes staged boundary
- **WHEN** a contributor reads the updated root module-layout table and adjacent explanation
- **THEN** the named modules resolve to implemented files and distinguish backend operations from queue transport and core policy
- **THEN** the scope remains intake and readiness, with later landing migration and Gitea enrollment/integration described as future work; the standalone backend does not imply enrollment support
- **THEN** the table names forge/gitea.py and the explanation describes the backend as implemented over forge/http.py, without retaining the claim that the backend remains future work
