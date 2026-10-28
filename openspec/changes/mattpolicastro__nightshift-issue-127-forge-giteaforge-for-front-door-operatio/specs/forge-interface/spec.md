## Purpose

Retain the staged Forge boundary while permitting the standalone Gitea backend
without claiming Gitea enrollment or changing existing GitHub behavior.

## MODIFIED Requirements

### Requirement: Module layout guidance

The root README's module layout SHALL name the Forge contract, factual records,
GitHub backend and retained queue transport, describe the staged boundary and
avoid claiming Gitea enrollment is available. A standalone Gitea backend MAY
exist while enrollment and daemon integration remain future work; the existing
README's historical statement about a future backend does not require a README
edit in this change. This change SHALL leave the concurrent engine guide and
unrelated documentation intact.

#### Scenario: Layout describes staged boundary
- **WHEN** a contributor reads the root module-layout table and adjacent explanation alongside the standalone Gitea capability
- **THEN** the named modules resolve to implemented files and distinguish backend operations from queue transport and core policy
- **THEN** the scope remains intake and readiness, with later landing migration and Gitea enrollment/integration described as future work; the standalone backend does not imply enrollment support
