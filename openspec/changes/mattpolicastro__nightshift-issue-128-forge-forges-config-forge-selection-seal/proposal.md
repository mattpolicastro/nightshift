## Why

The Gitea transport and adapter exist, but enrollment, daemon dispatch, secret
discovery and preflight still assume GitHub. Explicit forge selection needs to
preserve the GitHub execution contract and prevent Gitea enrollment from granting
authority to commands that still operate on GitHub.

This is #128, wave 4 of #122, blocked by #127 and #133. It provides configuration
and checks for a staged Gitea integration, not a claim of complete Gitea workflow
support or a successful live enrollment.

## What Changes

- Parse named `[[forges]]` and optional `repos.forge`; omission retains GitHub.
  Reject casefolded repository-name collisions, while ignoring unknown keys.
- Select the existing Gitea adapter for explicitly enrolled Gitea repositories.
  GitHub intake still constructs `GitHubForge(runner)` itself.
- Refuse daemon startup when any configured human landing-token variable is
  present. Extend seal secret discovery to every forge's two token variables.
- Keep worker engine-switch recovery GitHub-only. An unexpected Gitea journal
  holds that entire repository and escalates through repository health, without
  remote writes or loss of evidence.
- Add read-only Gitea preflight checks for bot identity/non-admin status,
  forbidden-repository denial, complete unique labels, clone credentials and
  base-branch protection; denied protection reads are `unverified`, never green.
- Narrow `land.enrolled_repo` to GitHub-kind rows and make `daemon status` print
  `not queried (gitea)` without querying those rows through GitHub.
- Correct affected passages in `config.example.toml`, `README.md`,
  `KNOWN-LIMITATIONS.md` and `docs/specloop/README.md`.

Explicitly out of scope: changes to `nightshift/forge/`, `specloop/cli.py`,
`nightshift/arm.py`, the plugin, wrappers or plists; landing beyond its enrollment
predicate; status beyond the read-only filter; and intake beyond passing a forge
to existing stages. No Gitea authorization redesign, comment-ownership policy,
durable label-transition journal/recovery, label-write reordering, state-key
migration or new intake behavior. These deferred #122 gaps are documented.

The five-task cap is met by dropping the prior drafts' transition-recovery and
authorization machinery, broad runner-test migration and general command
migration. None of the requested five preflight controls is dropped.

## Capabilities

- New `forge-configuration`: named forge configuration and collision prevention.
- Modified `forge-interface`: daemon selection with unchanged GitHub runner
  seams, guarded Gitea calls, and accurate module-layout guidance.
- Modified `config-resilience`: explicitly GitHub-only switch recovery and a
  repository-wide hold for Gitea journals.
- New `forge-seal`: trusted configuration discovery and literal forge-token scans.
- New `forge-preflight`: independent remote and clone negative controls.
- Modified `reviewed-pr-landing`: only GitHub enrollment authorizes GitHub landing.
- Modified `nightshift-cli`: explicit unqueried status for Gitea rows.

`issue-intake/Forge mediated intake`, all `pr-revision` requirements, standalone
Gitea adapter contracts and unaffected requirements remain unchanged. The
late-bound-runner delta allows an explicit forge while retaining default GitHub
construction. The two recovery deltas and module-layout delta replace claims
that would otherwise conflict with this scope.
