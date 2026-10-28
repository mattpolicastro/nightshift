## Why

On 2026-10-28, one published Gitea run caused the entire Factory snapshot to fail with `Unexpected PR link`. Finished Gitea runs are currently parked outside `~/.nightshift/runs` by hand. As part of #122, Factory needs to display links from explicitly trusted forges without accepting arbitrary hosts or enabling unsupported landing operations.

## What Changes

- Read extra HTTPS origins from `~/.config/nightshift/forge-origins`, overridden by the trusted host setting `NIGHTSHIFT_FORGE_ORIGINS_FILE`, on every uncached Factory status read. GitHub is always allowed; missing, unreadable or invalid file content grants no extra origins.
- Accept configured non-GitHub PR links shaped as `/<owner>/<repo>/pulls/<n>`, retaining GitHub's existing `/pull/<n>` behavior. Use the same origin allowlist for the existing `context.github` display field. Foreign PR links still fail the whole read; foreign context links remain blank.
- Keep Land restricted to validated GitHub offers, with regression coverage proving an accepted Gitea PR is displayable but cannot be landed.
- Amend the existing pause-metadata validation scenario and the conflicting context-display requirement, preserving all unrelated requirements and assertions.

Implementation is confined to `plugins/nightshift/server/factory.ts`, `factory-landing.ts`, `host-paths.ts`, plugin tests, and the scoped OpenSpec requirements. No new dependencies. Explicitly out of scope: `specloop/`, `nightshift/`, status JSON changes (including `link`, deferred to #145), `plugins/nightshift/server/reader.ts` and worker outcomes, Gitea landing (#130), client labels/layout, and forge backend/enrollment work. Do not edit `README.md`, `docs/specloop/prepare.md`, `forge/gitea.py`, `forge/http.py`, or any root `tests/` files being developed by other changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `paseo-runs-page`: operator-configured origins, strict PR validation, preserved snapshot failure behavior, and explicit GitHub-only Land eligibility.
- `host-paths`: a plugin-only forge-origins file setting with existing literal path rules.
- `run-context`: allow configured forge origins in Factory context projection without changing status producers or the `context.github` field name.
