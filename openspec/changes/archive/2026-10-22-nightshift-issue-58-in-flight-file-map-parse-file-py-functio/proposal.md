## Why

The in-flight file map omits planned edits written as `specloop/loop.py:section_evidence` (as in issue #31), and root-level files such as `README.md`, `pyproject.toml`, and `CLAUDE.md` (noted in issue #57). Drafting and the spec critic consequently receive incomplete collision context.

blocked-by: #51

The dependency's critic integration and specification are present in this checkout. This change improves the map they already consume.

## What Changes

- Normalize single-colon function suffixes while retaining double-colon support in `specloop/propose.py`.
- Let `paths()` optionally receive the source repository root, and accept slash-free tokens only when they name files there. Pass the resolved source root from `in_flight()`.
- Add regression coverage in new `tests/test_specloop_in_flight_paths.py`, including the three requested test names.
- Use the permitted fixture-setup exception in `tests/test_specloop_propose.py` to preserve its existing assertions under the new existence rule; explain that adjustment in the implementation PR.
- Explicitly out of scope: critic implementation, lint and overlap rules, prompt text, PR lookup, run lifecycle, and unrelated production or test files.

## Capabilities

- New capability: `in-flight-file-map`, specifying normalization, source-root file recognition, and existing run attribution behavior.
- Existing capabilities remain intact. In particular, every requirement in `openspec/specs/spec-critic/spec.md`, including Concurrent file context, remains unchanged; the map becomes more complete without changing its consumers. The CLI requirements in `openspec/specs/specloop-interface/spec.md` remain unchanged. No MODIFIED or REMOVED delta is needed.
