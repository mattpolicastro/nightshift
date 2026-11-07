## Why

The engine currently mixes orchestration with OpenSpec paths and operations.
The native spec-kit initiative (#184) needs a small format boundary before a
second adapter can land. Issue #187 extracts that boundary while preserving
the behavior of existing OpenSpec manifests and admitted runs.

## What Changes

- Add `specloop/spec_format.py` with the specified format protocol, `OPENSPEC`
  adapter and manifest-based selector. Move existing path and checklist rules
  behind it; delegate validator, scenario and archive calls at call time.
- Substitute adapter calls in `specloop/cli.py`, `specloop/loop.py`,
  `specloop/revise.py` and `specloop/propose.py`, including status reads,
  drafting confinement, context copying, publication and interrupted finish.
- Add optional `[run] format`, defaulting to `"openspec"`. Parse `"speckit"`
  but refuse admission with `spec-kit format is not available yet`.
- Document formats in the engine guide, manifest/JSON guide and self-hosting
  example. Add focused tests in `tests/test_spec_format.py`; preserve existing
  assertions, rendered prompts, messages, subprocess argv and commit messages.
- Permit adding `format="openspec"` to the existing `SimpleNamespace` manifest
  fixtures in `tests/test_mixed_root_readers.py` and
  `tests/test_specloop_messages.py` when their callers adopt `get(m)`; keep
  every assertion unchanged.

Out of scope: a spec-kit adapter or `specloop/speckit.py`, OpenSpec CLI/binary
changes, edits to `specloop/openspec.py` internals, `nightshift/`, prompt wording,
new state schemas or migrations, new Markdown parsing, and changed workflow
or recovery policy. No new behavior or error paths beyond the unavailable
spec-kit selection. Live resume validation remains a post-deploy human check.

## Capabilities

- **Added — `spec-format`:** the format interface, compatible OpenSpec adapter,
  engine routing and manifest selection/admission contract.
- **Extended — `specloop-documentation`:** additive formats guidance covering
  defaults, the reserved spec-kit selection and the unchanged OpenSpec workflow.

All existing requirements remain in force, notably `specloop-interface`
command JSON, validator classification, archived status and keyed revisions;
`issue-intake` design-check collection/publication; `specloop-forge` finish
gates; and `spec-drafting` and `in-flight-file-map` drafting rules. No existing
requirement is modified or removed.
