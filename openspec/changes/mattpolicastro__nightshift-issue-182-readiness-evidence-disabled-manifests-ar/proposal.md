## Why

Published runs with trusted `[gates] evidence = false` do not emit evidence events, but merge readiness currently requires them anyway. On 2026-11-02 an enrolled Gitea repository with green CI and no human checks received `merge:blocked` solely for missing section events. Readiness must honor the admitted run's evidence policy.

## What Changes

- Return no evidence reasons from `_readiness_evidence` after existing state and manifest validation when the saved run manifest disables evidence.
- Preserve the enabled-evidence path, its fail-closed diagnostics, and all other readiness checks.
- Explicitly enable evidence in the shared readiness test fixture and add the two requested regression tests without weakening existing assertions.
- Record the language-specific `tests/` fallback in `KNOWN-LIMITATIONS.md`; paths such as `packages/*/test/` remain unsupported.
- Limit implementation to one task and about 100 changed lines across `nightshift/specloop_intake.py` (only `_readiness_evidence`), readiness tests/fixtures, the matching readiness spec, and the explicitly requested limitation note.

Out of scope: evidence gates in `specloop/evidence.py` and `specloop/loop.py`, manifest parsing/defaults, test-path detection redesign, label/comment delivery, polling/cache changes, and other readiness or landing policy.

## Capabilities

- Modified: `merge-readiness`. Add the disabled-manifest exemption and modify **Local evidence assessment** to apply section evidence requirements only when enabled. Its unconditional policy is the defect; retain all existing scenarios for enabled evidence and all other requirements unchanged.
