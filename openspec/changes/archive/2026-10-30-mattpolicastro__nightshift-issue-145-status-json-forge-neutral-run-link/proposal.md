## Why

Published runs on a configured non-GitHub forge currently have an empty status context link. A reader cannot navigate from the run to its change folder. Issue #145 completes the status-producer portion of #122, split from the Paseo work in #132, after prerequisites #129 and #138.

## What Changes

- Add `context.link` for runs whose manifest names a non-GitHub host, using the matching configured forge's instance URL and branch-view path.
- Preserve `context.github` and the complete existing JSON shape for GitHub runs. Keep every other status field, exit code, publication gate and read-only guarantee unchanged.
- Cover link construction, compatibility and failure isolation with offline tests, including `test_status_json_link_uses_run_forge`; retain existing assertions and require `uv run pytest` to pass.
- Limit implementation to `specloop/cli.py`, a new test file and this capability delta. Paseo/plugin consumers (#132), all of `nightshift/`, manifest/verification changes, operator documentation, new forge backends and publication behavior are explicitly out of scope.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `run-context`: add a separate forge-neutral status-link requirement. Existing context and Factory requirements remain intact; no existing requirement is removed or replaced.
