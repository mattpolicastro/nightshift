## Why

Issue #123, part of #122 wave 1, needs a small HTTP foundation for a later
`GiteaForge`. Authenticated calls to a self-hosted API need a fixed destination
and redirect refusal before they can safely carry a token. This change makes
those rules independently testable without daemon or endpoint integration.

## What Changes

- Add `nightshift/forge/__init__.py` containing only a module docstring and
  `nightshift/forge/http.py` exposing `GiteaTransport` and `GiteaHTTPError`.
- Use stdlib urllib for origin-locked JSON requests, all-3xx refusal, bounded
  GET retries, an optional per-attempt guard, and secret-free diagnostics.
- Add `get_json` and single-page `get_page` helpers without completeness policy,
  plus in-process, socket-free tests in `tests/test_gitea_http.py`.
- Add the `gitea-http` capability. Preserve all existing requirements.

Explicitly out of scope: `GiteaForge`, a forge Protocol, configuration, daemon
wiring, callers, endpoint adapters, automatic pagination, live-server tests,
new dependencies, and edits to any existing module, including `queue.py`,
`specloop_intake.py`, and `land.py`. Implementation changes are limited to the
three new Python files above and the capability under `openspec/specs`.
This draft itself writes only within its requested change directory.

## Capabilities

### New Capabilities

- `gitea-http`: authenticated, origin-locked Gitea HTTP transport with no
  redirects, JSON/page helpers, bounded GET retries, guard checks, and secrecy.

### Modified Capabilities

None. Existing GitHub transport and workflow behavior remain unchanged.
