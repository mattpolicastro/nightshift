## Why

Gitea enrollment currently stops before every daemon stage. Remove-first label
transitions can lose the discovery trigger, core marker lookup can accept a
human copy or an inline reference, and GitHub relationship resolution bypasses
Gitea's configured allowlist. Fixing these contracts enables the existing
front door without changing the build engine or landing authority.

Part of #122. Blocked by #170, #165, #128, #135 and #131. In particular, #165
supplies body-based label actions and complete unpaginated comments; #170
supplies the timeline/status wire-shape corrections. This change builds on
those fixes rather than restoring the obsolete fake-server assumptions.

## What Changes

- Add and verify every destination label before removing any source label.
  A lost `needs-human` addition leaves a safe human hold; it does not introduce
  automatic pending-receipt recovery through that hold.
- Use bot-owned Gitea marker evidence. Share exact final-line matching between
  intake's rejection/delivery lookups and GitHub transport reconciliation.
- Resolve Gitea label actors by casefolded exact allowlist membership, excluding
  the bot, and explain that rule in Gitea rejection comments.
- Pass the selected Gitea adapter through four optional `forge=None` stage
  keywords, lift the unconditional hold, retain foreign-journal holds and
  GitHub-only worker recovery, and exempt transient ForgeError outages from
  item escalation. Preserve pause guards at request and completion boundaries.
- Correct five operator guides and the requirements that currently mandate
  disabled dispatch. Document read-after-write consistency and absence of a
  concurrent creator as assumptions behind create deduplication; delayed
  visibility after an ambiguous POST can duplicate on later daemon polls.
  Explicitly qualify resumed completion delivery and publication-comment
  uniqueness under the same Gitea assumptions, preserving retained work,
  checklists and the prohibition on republishing solely to deliver a comment.
- Permit only the inventoried Gitea assertion migrations and the single
  approved GitHub inline-marker fixture exception. Preserve all other GitHub
  behavior and assertions, unrelated assertions, and `tests/contract/`.

Out of scope: `specloop/`, `nightshift/land.py`, `nightshift/arm.py`, plugin
changes, configuration/schema changes, membership APIs, daemon status queries,
new preflight machinery, persistent create reservations, receipt redesign,
worker recovery on Gitea, native Gitea draft state, automatic merging, and a
readiness refactor to avoid Gitea's second PR read. There are five tasks,
each targeted at approximately 300 changed implementation/test lines or less.

## Capabilities

Modified capabilities:

- `gitea-forge`: add-first transitions, bot evidence, guarded completion, and
  explicit compatibility exceptions.
- `forge-interface`: exact markers, selected stage forwarding, eligible Gitea
  dispatch, transient failures and forge-specific readiness read budgets.
- `issue-intake`: allowlist actor resolution, rejection explanations and the
  consistency assumption for publication-comment recovery.
- `paused-job-delivery`: the same Gitea consistency assumption for resumed
  completion delivery, without changing pause or completed-job handling.
- `config-resilience`: preserve journal holds while admitting eligible Gitea work.
- `merge-readiness`: Gitea CI requests and explicit recovery assumptions.
- `forge-configuration`: enrollment guidance after the hold lifts.
- `gitea-landing`: correct only the guide's disabled-daemon statement.
- `specloop-documentation`: current front-door, recovery and limitation guidance.

No new capability directory outside these deltas is needed. Unmentioned
requirements remain in force.
