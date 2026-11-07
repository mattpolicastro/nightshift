## Why

OpenSpec currently runs through `npx -y --prefer-offline @fission-ai/openspec@1.14.3`, so validation, scenario discovery and finish depend on Node/npm and potentially the registry. A host-installed, checksum-verified OpenSpec 1.16.1 executable makes the tool selection explicit and removes package acquisition from those operations.

Tracking: #191 and #184. Blocked by #187: apply after its format seam lands, preserving late-bound delegation. Related: #179, whose timeout handling remains effective.

## What Changes

- Replace the npm package pin with `VERSION = "1.16.1"` in `specloop/openspec.py`. Resolve `OPENSPEC_BIN` from trusted `NIGHTSHIFT_OPENSPEC_BIN` or `<NIGHTSHIFT_BIN_DIR>/openspec`; check its version once per process for each selected path and invoke it directly with the existing JSON arguments, environment and 120-second timeout.
- Refuse missing, non-executable, wrong-version or otherwise unusable executables with plain `SpecError` and installation guidance. Only subprocess timeouts raise `ValidatorUnavailable`; remove npm stderr-code classification.
- Add one independent host check to preflight for executable presence, version and installed-file SHA-256 against committed `specloop/openspec-SHA256SUMS`. Document verified release installation; never download at run time or fall back to npx.
- Retain the plugin's Node configuration and PATH behavior, correcting its OpenSpec/npx rationale. Keep every file under `bin/**` unchanged, including `bin/specloop-serve`'s PATH and comment; their cleanup is deferred to separately authorized work. Verify OpenSpec independence from `~/n/bin` through direct invocations and a separate operator-controlled supervisor check.
- Document 1.16.1 strict validation changes and retain the harness's existing scenario-heading lint. Carry repository compatibility and Mac Studio cut-over checks into the human checklist.
- Modify existing requirements that explicitly preserve npx invocation or npm transient classification; preserve the existing wrapper freeze without exceptions. Shared documentation edits are restricted to passages made false by this change.

Out of scope: other engine behavior, format dispatch or spec-kit implementation, new Markdown parsing, runtime installers, automatic upgrades, broader service portability, all `bin/**` edits (including supervisor PATH/comments), launchd plist changes, state migrations, and edits to enrolled repositories' OpenSpec files. Archive history is not repaired. No deployed services or issue comments are changed by this draft.

## Capabilities

### New Capabilities

- `openspec-binary`: trusted executable selection, process version pin, offline preflight checksum verification, and direct strict-validator behavior.

### Modified Capabilities

- `specloop-interface`: timeout-only validator unavailability; other launch and output failures remain domain errors.
- `spec-format`: retain the seam while permitting the explicitly specified OpenSpec backend change.
- `specloop-documentation`: describe verified binary installation, correct format/host guidance and retain rollout evidence limits.
