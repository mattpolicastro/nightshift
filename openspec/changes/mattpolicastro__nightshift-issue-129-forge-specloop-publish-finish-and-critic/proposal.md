## Why

Issue #129 completes the explicit engine publication path in wave 5 of #122,
after #128 supplies trusted forge configuration. Publish, finish and critic
context still assume GitHub despite the available Gitea adapter. Operators
need Gitea PRs without weakening origin checks, workflow protection or existing
GitHub behavior.

## What Changes

- Select the engine forge from manifest `[run] github`: an omitted host or
  `github.com` retains byte-identical `gh` argv; another host must uniquely match
  a configured Gitea URL host.
- Validate Gitea checkout origins against scheme, hostname, effective port,
  configured URL prefix and repository before any push or API write.
- Preserve git publication and existing base preparation. Refuse publication
  of changes under `.gitea/workflows/`, `.github/workflows/` or `.forgejo/` on
  either forge. Reconcile an existing PR before creating a WIP-prefixed Gitea PR.
- Finish by archiving and pushing as before, then removing the Gitea WIP title
  prefix with PATCH and verifying the resulting title by GET.
- Collect critic PR files through forge-specific readers. Gitea uses 30-second
  socket timeouts and starts no request after 120 seconds; a slowly arriving
  response can overrun both numbers. Preserve partial evidence and warnings.
- Update affected passages in `README.md`, `KNOWN-LIMITATIONS.md` and
  `docs/specloop/README.md`, including credential setup, daemon restrictions,
  title-edit races, timeout limits and the pending first real publish check.

Out of scope: daemon execution or authorization changes, `nightshift/land.py`,
landing/arming/plugin migration, native Gitea draft state, title conflict
detection, durable publication transactions, and changes to `nightshift/forge/`
or its HTTP read-deadline behavior. No service rollout is implied.

## Capabilities

- **ADDED `specloop-forge`:** manifest selection, full Gitea origin validation,
  workflow refusal, draft publication and verified WIP completion.
- **MODIFIED `spec-critic`:** backend-specific context completeness and Gitea
  request scheduling/socket limits. Replace arbitrary explicit GitHub-host
  routing with the manifest selection contract.
- **MODIFIED `specloop-documentation`:** explicit forge command guidance and
  scoped limitations; qualify existing GitHub-only publication and disabled
  Gitea statements as daemon restrictions.

All other requirements remain, particularly `publish-base-sync`, PR adoption
and partial-finish status in `specloop-interface`, `forge-interface` daemon
holds, and existing `gitea-forge`/`gitea-http` contracts. This draft changes only
files in this change directory; the task list describes later implementation.
