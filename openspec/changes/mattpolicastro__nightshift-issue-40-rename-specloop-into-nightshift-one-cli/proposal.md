## Why

Issue #16 retired the worker engine. Operators still address the surviving engine as `specloop` and the front door as `nightshift`, with build state in a separate home directory. Issue #40 unifies the public CLI and state location while preserving existing runs, automation and credential protection. This change depends on #16.

## What Changes

- Make `nightshift` canonical for all build commands. Put colliding daemon controls under `nightshift daemon`; retain bare `nightshift run` as the sole implicit daemon compatibility form used by the frozen wrapper. `nightshift run CHANGE` drives a build.
- Retain the installed `specloop` script and module entry point for one release, forwarding with a stderr rename notice and preserving machine-readable stdout and exit codes.
- Resolve new build state under `~/.nightshift`. Add `nightshift migrate` for guarded, resumable relocation of legacy `runs/`, `proposals/` and `homes/`, a completion marker, legacy read fallback, and migration at supervisor startup. Never relocate a locked run or migrate while a supervisor is live.
- Deny agent homes and canonical credential targets in both verification modes independently of manifests.
- Migrate only landing and supervisor-driver Python command callers, plus Factory server and client command text, notifications, operational docs (including config.example.toml comments) and affected fixtures. Daemon-side callers prescribed by unchanged requirements keep their `specloop` invocations for this release. Move canonical CLI tests off the alias, verify both names against existing machine contracts, and migrate documentation command selectors while preserving output, schema and replay coverage. Split locking, relocation, recovery and activation into bounded sessions.

Out of scope: edits to **any `bin/**` or `launchd/**` file, including comments**; live migration/service operations; service labels/bootstrap portability; worker execution; changing enrollment `engine = "specloop"`, internal Python module names, Git refs, JSON/RPC keys, environment-variable names or trusted manifest locations. Internal `specloop` modules remain the implementation package during compatibility; this is a public CLI/state rename, not a wholesale import-path move. Existing `pyproject.toml` script targets and package declarations already support this layout and need no edits. The alias and frozen wrapper commands are modernized only in a follow-up release.

## Capabilities

New: `nightshift-cli` (routing and one-release alias), `state-migration` (roots, exclusion, relocation, recovery, fallback and migration-only operation).

Changed: `verify-preparation`, `worker-retirement`, `background-job-control`, `host-paths`, `reviewed-pr-landing`, `paseo-runs-page`, `run-context`, `run-actions`, `build-timing-report`, `touchpoint-report`, and `specloop-documentation`.

All requirements not explicitly modified remain in force, including every `specloop-interface` requirement, `issue-intake`, `pr-revision`, `attention`, `deployment-drift`, `agent-test-rules` Documentation tests, revision idempotency, authorization, candidate provenance and independent daemon/supervisor roles. The existing machine contracts apply to both CLI names. Canonicalizing the retained daemon calls and supplying their MODIFIED deltas is out of scope until the alias-removal follow-up.
