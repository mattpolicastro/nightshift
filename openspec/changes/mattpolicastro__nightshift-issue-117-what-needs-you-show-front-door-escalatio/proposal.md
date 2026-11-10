## Why

The Paseo page's “What needs you” list still projects the retired worker's
outcomes. Front-door drafting, revision and admission refusals instead leave
open issues or PRs labelled with the configured `needs-human` label. Those
escalations need a visible home when Slack alerts are disabled. Human-paused
builds belong here as pointers; Factory already supplies their detail and actions.

Blocked by #79 (Factory landing), whose implementation and archived change are
present in this checkout. Preserve that dependency and its action boundaries.

## What Changes

- Discover open labelled issues and PRs for enrolled GitHub repositories, with
  title, kind, URL and the first line of the latest recognized escalation comment.
- Persist independent repository snapshots. Failed, incomplete or deferred reads
  retain their last good entries and show a repository-level stale diagnostic,
  including on a first failure with no entries. Preserve the existing read budget.
- Keep human-paused specloop runs and their selection rules; render compact
  references to the matching change in Factory without duplicating its controls.
- Stop feeding CLI and page lists from worker outcomes. Preserve the historical
  database and compatibility APIs; retain version-1 JSON keys and types, with
  empty legacy `tasks`/`stale` arrays and additive repository data.
- Migrate the attention portions of existing recovery regressions to that
  contract. Preserve claim/evidence recovery assertions and test historical
  reconciliation through `outcomes.refresh()` directly; attention no longer
  performs that reconciliation as a side effect.
- Update the plugin projection and both allowed READMEs, with host-independent
  tests for collection, stale handling, pointers and worker-row retirement.

Explicitly out of scope: Factory run listing, search, sorting, detail and actions;
label definitions or transitions; daemon escalation behavior; Slack configuration;
history deletion or migration; new forge backends and Gitea attention discovery.
Every enrollment still receives a repository status: a non-GitHub enrollment is
explicitly stale/unavailable, never queried against a same-named GitHub repository.

Implementation files are limited to `nightshift/cli.py` (attention),
`nightshift/outcomes.py`, `nightshift/specloop_attention.py`,
`plugins/nightshift/server/attention.ts`, `plugins/nightshift/shared/attention.ts`,
`plugins/nightshift/client/main.tsx`, tests under the two allowed test directories,
`README.md` and `plugins/nightshift/README.md`.

## Capabilities

### Modified Capabilities

- `attention`: add front-door discovery, repository snapshots and the page's
  compact Factory references; replace only the worker-results preservation clause
  in the existing allowance/snapshot requirement.
- `worker-retirement`: replace the promise of historical attention rendering with
  historical database/API readability; update the production client projection
  while retaining command, installation, cache and execution boundaries.
- `specloop-documentation`: document the new attention sources and compatibility
  limits, and correct the retirement guidance's historical-attention promise.

All other requirements remain in force, particularly attention pause eligibility
and stale status handling, host paths, external browser navigation, the existing
forge boundary, and every Factory requirement. No new capability is required.
