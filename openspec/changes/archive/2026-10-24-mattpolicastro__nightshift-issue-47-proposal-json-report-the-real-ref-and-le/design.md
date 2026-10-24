## Context

`specloop.propose.publish_branch` already checks all source worktrees, fetches
the draft into an unchecked-out branch or the private proposal ref, and
returns its actual location. Ordinary branch names are returned without
`refs/heads/`; private refs are returned in full. `specloop.cli.propose`
currently seeds JSON with `m.branch` and never replaces it with that return
value. Its human notification already uses the correct ref.

`nightshift.specloop_intake._validate` is shared by synchronous `_start` and
background `_collect`, including spec revisions. It rejects a successful
proposal unless `branch == spec/<receipt.change>` and a commit is present.
Both `_deliver` and `revise_pending` already push the recorded commit from
the manifest's source repository to the public branch. That source need not
be `Repo.dir`. Revision first fetches the existing public PR head into
`refs/nightshift/revise/<change>` and checks its identity.

The checkout contains no `AGENTS.md`; `README.md`, `CLAUDE.md`, the relevant
code/tests and existing capability requirements supply the repository
context. #13 and #9 remain the stated implementation dependencies; this
change does not replace their enrollment and revision behavior.

## Goals / Non-Goals

Make successful JSON identify a locally fetchable proposal ref, allow the
daemon to consume either supported spelling, and publish the exact drafted
commit to the existing public branch without changing a reviewer's checkout.
Keep old normal-ref results and receipts usable without migration.

Do not generalize the daemon to arbitrary refs, change branch naming or
fallback selection, move work into the worker clone, change JSON outcomes,
relax any authorization/health/pause/stop/head checks, or change admitted-run
revision and publication. No deployment or live-service action is an agent
task.

## Decisions

1. **Update the successful producer only.** In `specloop/cli.py:propose`, set
   `branch=published_ref` in the final `_outcome(..., outcome="drafted")`
   after `drafting.publish_branch` succeeds. Keep the version-1 envelope,
   commit, diagnostics, notifications and exit codes. A normal result still
   reports the effective branch, including standalone CLI custom branches;
   a fallback reports the full private ref. Questions, rejected drafts and
   failed publication retain their existing behavior and do not claim a
   successfully published fallback.

2. **Accept two exact strings for the recorded change.** In
   `nightshift/specloop_intake.py:_validate`, allow only
   `spec/<receipt['change']>` and
   `refs/specloop/proposals/<receipt['change']>` for `propose/drafted`, still
   requiring a nonempty commit. Use equality against the two complete values,
   not a prefix, basename or generic Git-ref validator. Missing/non-string
   branches, another change's private ref, tags, `HEAD`, refspec expressions
   and `refs/heads/spec/<change>` remain invalid. Apply the same rule through
   `_start` and `_collect`; preserve the returned value in the saved result.
   Other outcomes and commands retain their current validation.

3. **Separate local source from public destination and retain the pinned
   commit.** A consumer fetching proposal output from the manifest source
   uses the reported ref: expand an ordinary branch to `refs/heads/<branch>`
   when a fully qualified fetch source is needed, and pass a private ref
   unchanged. Never construct `refs/heads/refs/specloop/...` or fetch that
   private ref from GitHub. In this checkout, `publish_branch` has already
   fetched the draft into that source repository before returning success;
   the daemon pushes from the same repository, so no redundant daemon fetch
   or new clone is needed. `_deliver` and `revise_pending` retain the explicit
   `<result.commit>:refs/heads/spec/<change>` push refspec. The SHA pins the
   proposal retained at the reported ref and prevents a later local ref move
   from changing a cached result's delivery. Tests must prove that the
   reported ref is fetchable and that its proposal commit reaches the public
   branch while the checked-out local spec branch stays at its old tip.
   The pre-drafting revision fetch remains from the public PR branch: it
   fetches review input, not the later proposal output.

4. **Preserve lifecycle behavior.** Initial delivery keeps its draft PR title,
   issue link, risk text, `spec:review` label and intake-label removal. PR
   lookup/admission continue using the public branch. Replacement drafts keep
   their existing force-with-lease protection; spec revisions keep ordinary
   non-forced pushes and the saved-head check. Successful fallback results
   survive transport failures and restart without redrafting. Revision IDs
   are consumed only after a successful push, before notification, exactly as
   today. Existing manifest health, dependency, actor authorization, pause
   and stopped-job holds remain in force.

5. **Land the consumer before the producer and test without live services.**
   Use two tasks in one section, each targeting fewer than roughly 300 changed
   lines including tests. Put daemon regressions in
   `tests/test_specloop_proposal_delivery.py`, reusing the injected GitHub and
   command fixtures in `tests/test_specloop_intake.py` and
   `tests/test_specloop_spec_revision.py` where useful. Exercise both direct
   results and durable background collection. The required
   `test_daemon_delivers_a_fallback_ref_proposal` uses temporary Git
   repositories and a bare local origin with real Git operations, so it
   cannot pass by merely asserting an argv string. Cover initial delivery
   and spec revision, rejection of unrelated refs, and retry after failure.
   In `tests/test_specloop_command_json.py`, add
   `test_propose_json_reports_real_ref` and update the existing checked-out
   spec test's obsolete branch assertion. Exercise main and linked worktrees,
   a normal branch, a custom CLI branch, one JSON stdout document, ref/commit
   agreement, and unchanged HEAD/index/staged/unstaged/untracked contents.
   Stub agent and GitHub boundaries; no real model, GitHub or host application
   is necessary. Run focused tests per task, then `uv run pytest` and
   `uv run python scripts/check_types.py` after the producer and consumer are
   both present.

## Risks / Trade-offs

- A new CLI paired with an old daemon still rejects fallback results. Landing
  and deploying consumer support before, or together with, the producer avoids
  that mismatch. Deployment itself is outside this implementation.
- `branch` now explicitly means a local source ref on successful proposal
  results. Consumers must not reuse it as the GitHub PR head. Exact matching
  keeps unrelated refs outside the daemon contract while allowing the CLI's
  existing standalone custom branches.
- Keeping SHA-addressed pushes is intentional: fetching/pushing a mutable ref
  tip on every retry could publish another attempt's commit. There is no new
  ref-tip equality gate or receipt migration that could strand previously
  saved successful work. Private-ref cleanup remains outside this change.
- Existing authorization, feedback-consumption, lease and checkout-isolation
  coverage must stay green; this protocol correction does not weaken those
  requirements.

Human checklist for the resulting PR: on the next real Mac Studio revision
round with `spec/<change>` checked out, record the JSON branch and commit,
confirm the daemon accepts the private ref and updates the same GitHub PR at
`spec/<change>`, and confirm the local HEAD, index and review edits remain
untouched. Check revision acknowledgement and label removal. Leave this check
pending until that round actually occurs; temporary repositories and CI do
not prove the deployed launchd/service environment.
