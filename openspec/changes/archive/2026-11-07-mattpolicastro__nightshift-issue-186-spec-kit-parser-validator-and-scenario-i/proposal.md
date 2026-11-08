## Why

Native spec-kit support (#184) needs a local parser and validator before the
engine can consume feature directories. The existing task and scenario types
already provide the harness contract; this change supplies spec-kit's physical
line rules and validates traceability without invoking an external tool.

The earlier fixture blocker is resolved by #193: release-tagged v1.3.2 templates
and their MIT license are available in the checkout. Validation must also reject
empty features instead of accepting headings with no actionable story content.

## What Changes

- Add `specloop/speckit.py` with feature lookup, spec/task parsing, stable scenario
  IDs, validation, clarification questions, plan human checks and status completion.
- Reuse `specloop.tasks.Task`, its checkbox/reference regexes, and
  `specloop.openspec.slug` and `SpecError`. Add no dependencies or subprocesses.
- Add a synthetic three-story project in `tests/fixtures/speckit/`, including
  verbatim local copies of spec-kit v1.3.2 templates, attribution and license.
- Add `tests/test_speckit_format.py` covering the fixture, individual validation
  failures, conservative line recognition and helper behavior.
- Add a short parser paragraph to `docs/specloop/speckit.md` only if #185's guide
  exists at implementation time; otherwise record its omission in the PR description.

Explicitly out of scope: engine/CLI integration, format selection, task mutation,
drafting presets, publication, archiving, daemon changes and new dependencies.
Do not edit `specloop/tasks.py`, `specloop/openspec.py`, `specloop/cli.py`,
`specloop/loop.py`, `specloop/propose.py` or anything under `nightshift/`.
The shared README/limitations guides still accurately describe the connected
OpenSpec engine; this standalone module makes none of those passages false.
Leave them and the other concurrent-change files named in the design untouched.

## Capabilities

### New Capabilities

- `speckit-format`: Offline parsing and validation of a spec-kit feature directory,
  harness-compatible task records and scenario IDs, and pure text helpers.

### Modified Capabilities

None. Existing OpenSpec behavior and requirements remain intact. In particular,
this change does not change task mutation, proposal validation, publication's
design-check collection or the documented engine support boundary.
