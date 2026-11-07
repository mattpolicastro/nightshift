## Purpose

Retain worker retirement and service boundaries while removing the supervisor's obsolete OpenSpec Node path.

## MODIFIED Requirements

### Requirement: Service prerequisites match the surviving engine

The Python CLI and preflight SHALL operate without worker-only prerequisites while preserving env-file loading and front-door checks. Manifest providers SHALL retain their own requirements. Frozen wrappers and plists SHALL remain unchanged except that bin/specloop-serve SHALL remove its ~/n/bin PATH prefix and update the associated OpenSpec comment for standalone execution. Its checkout, uv command, remaining PATH and credential behavior SHALL remain unchanged. The daemon wrapper's shell startup checks remain outside the Python prerequisite guarantee.

#### Scenario: Codex enrollment needs no worker login
- **WHEN** Python CLI one-shot polling and preflight are exercised directly with a valid specloop enrollment and front-door prerequisites but no worker OAuth token, Claude executable, pnpm or Swift
- **THEN** worker-only prerequisites do not prevent Python operation, and the CLI retains existing env-file loading
- **THEN** deep preflight remains accepted, emits an explicit retired-probe notice and launches no model or tool-loop probe or worker verify-allowlist check
- **THEN** missing GH_TOKEN, token scoping or alert failures still fail Python preflight, and manifest providers retain their own credential/tool requirements
- **THEN** this does not assert successful startup through the unchanged daemon wrapper, which still requires its environment file, worker OAuth, GH_TOKEN and legacy tool list before Python starts

#### Scenario: Services retain independent roles
- **WHEN** bundled launchd jobs and wrappers are inspected read-only
- **THEN** the daemon still invokes `nightshift run` and the independent supervisor still invokes `specloop serve`, with existing labels, bootstrap paths and lifecycle policy; bare nightshift run dispatches to the same daemon function as nightshift daemon run, and the alias supervisor uses canonical state with legacy fallback
- **THEN** no live service is installed, unloaded or restarted by CLI rename or state migration code
- **THEN** apart from the supervisor PATH/comment exception above, wrappers, plists and their comments remain unchanged; live installation and other service adaptation remain separate operator work, with host readiness checked before rollout
