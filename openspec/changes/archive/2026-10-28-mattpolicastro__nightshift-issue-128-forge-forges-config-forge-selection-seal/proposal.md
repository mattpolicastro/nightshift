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
- Provide `forge_for` to construct the existing adapter for either forge, but
  hold every Gitea repository before daemon recovery or stage dispatch. Gitea
  configuration and read-only preflight are available; unattended execution is
  deferred until core allowlist authorization and durable label transitions exist.
  GitHub intake still constructs `GitHubForge(runner)` itself.
- Refuse daemon startup when any configured human landing-token variable is
  present. Extend seal secret discovery to every forge's two token variables.
- Keep worker engine-switch recovery GitHub-only. An unexpected Gitea journal
  holds that entire repository and escalates through repository health, without
  forge writes or loss of evidence. Explicitly except the Gitea dispatch hold
  from `Health gates new work only`: admitted publication, build revision and
  completion delivery wait too, even without a journal. Ordinary GitHub
  manifest-health failures retain their existing admitted-run bypass.
- Add read-only Gitea preflight checks for bot identity/non-admin status,
  forbidden-repository denial, complete unique labels, clone credentials and
  base-branch protection; denied protection reads are `unverified`, never green.
- Narrow `land.enrolled_repo` to GitHub-kind rows and make `daemon status` print
  `not queried (gitea)` without querying those rows through GitHub.
- Correct affected passages in `config.example.toml`, `README.md`,
  `KNOWN-LIMITATIONS.md` and `docs/specloop/README.md`.

Explicitly out of scope: changes to `nightshift/forge/`, `specloop/cli.py`,
`nightshift/arm.py`, the plugin, wrappers or plists; landing beyond its enrollment
predicate; status beyond the read-only filter; and all intake changes in this
revision. No Gitea authorization redesign, comment-ownership policy,
durable label-transition journal/recovery, label-write reordering, state-key
migration or new intake behavior. These deferred #122 gaps are documented.

The five-task cap is met by deferring Gitea stage forwarding and execution along
with transition-recovery and authorization machinery, broad runner-test migration
and general command migration. This resolves both blocking findings without
enabling unsafe writes: an empty allowlist cannot reach core's owner shortcut,
and admission cannot remove its discovery trigger before recording delivery.
No preflight result or nonempty allowlist enables stages in this change. None of
the requested five preflight controls is dropped.

## Capabilities

- New `forge-configuration`: named forge configuration and collision prevention.
- Modified `forge-interface`: adapter selection with unchanged GitHub runner
  seams, a mandatory Gitea dispatch hold, and accurate module-layout guidance.
- Modified `config-resilience`: explicitly GitHub-only switch recovery and a
  repository-wide hold for Gitea journals and unsupported Gitea dispatch, with an
  explicit exception to the admitted-run health bypass.
- New `forge-seal`: trusted configuration discovery and literal forge-token scans.
- New `forge-preflight`: independent remote and clone negative controls.
- Modified `reviewed-pr-landing`: only GitHub enrollment authorizes GitHub landing.
- Modified `nightshift-cli`: explicit unqueried status for Gitea rows.
- Modified `specloop-documentation`: qualify recovery and admitted-run guidance
  with the Gitea dispatch hold.

`issue-intake/Forge mediated intake`, all `pr-revision` requirements, standalone
Gitea adapter contracts and unaffected requirements remain unchanged. The
`Late bound runners` requirement also remains unchanged: no intake entry-point
signatures or constructor spies are migrated. The recovery, health-gate and
documentation deltas qualify availability without changing GitHub workflow
semantics. A copied manifest cannot bypass missing Gitea authorization and
delivery guarantees or establish workflow identity from a foreign journal.
