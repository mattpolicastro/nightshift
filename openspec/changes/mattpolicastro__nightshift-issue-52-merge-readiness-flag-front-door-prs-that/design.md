## Context

`publish_pending` in `nightshift/specloop_intake.py` reads admitted runs, captures `(change, head, pr)` plus human checks before publication, recovers lost publish responses at `human_checks`, and returns immediately for a delivered receipt. Publication comments use a durable round marker. `tests/test_specloop_base_publication.py` exercises that boundary through real local Git and CLI state.

`specloop.loop.section_evidence` emits `evidence` events containing `section`, `passed`, `score`, `timeouts`, `uncovered`, and `survived`. It does not currently emit an explicit `unmeasured` or skip-reason field. Successful gates also save `evidenced` and `evidence_summary` in `state.json`. A null score can mean either unmeasured evidence or legitimate skipped mutation; null alone cannot decide readiness.

`nightshift.land` independently requires green checks and synchronization with current `main`. Intake must not import or invoke it, as required by `reviewed-pr-landing/Operator entry point`. The incident described in the proposal remains relevant, but the user's redraft answer replaces merge-parent provenance with simple advisory CI checks. The original `test_ready_requires_ci_against_current_base` is superseded by a test that readiness does not query base provenance; current-base tests remain owned by landing.

No `AGENTS.md` exists in this checkout or its ancestors. Repository README and the applicable existing specifications were read. No existing requirement needs removal or modification: the new capability adds a distinct stage after successful publication delivery.

## Goals / Non-Goals

Goals: deterministic, conservative assessment from existing local run evidence and ordinary GitHub responses; one explanation of all blockers; bounded polling; restart-safe delivery; preservation of publication, pause, revision authorization, and worker contracts. All executable changes belong in `nightshift/specloop_intake.py`.

Non-goals: merge or finish actions, branch updates, continuous same-head CI monitoring, Actions or checkout logs, merge-ref provenance, per-attempt execution receipts, evidence reruns, changes to run-state producers, label creation, or a guarantee that a labelled PR can be merged without review.

## Decisions

### Publication remains a separate completed operation

Add small private helpers in intake, with injected runners and pure assessment functions. Suggested boundaries are `_readiness_evidence`, `_readiness_human_checks`, `_readiness_reasons`, and `_reconcile_merge_readiness`; the names are internal, not a CLI contract.

Invoke reconciliation from the existing delivered-receipt branch of `publish_pending`, only when status is `paused` with reason `human_checks`, the admission is successful, and the matching publication receipt records `published` and `delivered`. Do not assess in the same invocation that delivers the publication comment: the next normal poll observes the post-publication state. A refused, unmatched, or absent publication receipt is not eligibility evidence.

Read the PR once with `gh pr view <number> --repo <repo> --json state,labels,headRefName,headRefOid,isCrossRepository,url,body`. Require an open same-repository PR, exact recorded URL, and `spec/<recorded change>` branch. Closed, forked, or wrong-identity PRs receive no readiness writes. Keep publication's pre-delivery revision/needs-human guards unchanged; do not apply the old `agent:revise` early return to the new assessment, where that label is a blocker.

Successful assessment or readiness transport failure must not reset publication's `delivered`, replace its marker, invoke `publish` again, or modify the run. Route readiness errors through the existing item error path without making them publication refusals. Continue processing other admissions.

### Evidence comes from local run state

Use `specloop.state.Run.for_change(change, require_existing=True)` to locate `state.json`, `events.jsonl`, `manifest.toml`, and `repo.git`; do not use the repository enrollment template to locate evidence. Verify saved change/PR identity against the status row. Read committed `openspec/changes/<change>/tasks.md` at the status head through bounded read-only Git and parse it with `specloop.tasks.parse` to enumerate expected sections. Missing or malformed local state is an explicit evidence blocker, never an empty successful run. Ignore unknown state fields and unrelated well-formed event types.

Select the last `evidence` event in append order for each expected section, including revision sections. Earlier failed or timed-out attempts do not poison a later valid result. Validate field types (booleans are not numeric scores/counts), a finite score in `[0,1]` when present, nonnegative integer timeouts, and empty uncovered scenarios. An ordinary measured pass requires `passed: true`, a numeric score, zero timeouts, and that section in saved `evidenced`. A supplied `unmeasured: true` field always blocks; current-format failed/null events likewise cannot establish measurement.

Accept `passed: true`, `score: null`, zero timeouts, and saved completion as measured-not-required only when the section's saved successful summary contains the exact line `mutation: skipped (no mutable lines added under mutation_paths)`. Do not equate other skip reasons (no mutation steps or no section base) with that exemption. This covers normal test-only gates and cosmetic sections with no mutable lines without demanding execution receipts that should not exist.

For a test-only section without an evidence event, permit the original diff exception only with positive local proof: compare its saved `section_base` with the next section's saved start (or the current run head for the final section), require those commits to exist and be ordered ancestors, and require a nonempty changed-path set entirely under `tests/`, apart from the harness-owned change's `tasks.md`. Reject renames crossing that boundary and do not treat a tasks-only diff as a test-only proof. No absent baseline, unsupported layout, integrated production-file change, or Git failure grants the exception. A recorded failed, unmeasured, uncovered, or timed-out latest event cannot be overridden by this fallback. This conservative path comparison may block a test-only section spanning upstream production changes; it does not reconstruct integration merges or run evidence again.

Missing required events, missing timeout counts, malformed JSONL (including a partial final line), ambiguous/null evidence without an allowed exemption, and disabled evidence for a production section all block with section-specific reasons. Never scan `receipts.json`, `evidence.json`, `mutation-runs.json`, attempt directories, or checkout logs. This is a stricter label assessment, not a change to existing evidence scoring or publication eligibility.

### Human checks and pending revision

Find the checklist by a line rule, not Markdown semantics. The body must contain exactly one line equal to `## Checks a human must make` at column 0. Its section is the following lines up to the next line starting with `#` at column 0, or the end of the body. Zero or several such lines block as unknown. In the section, blank lines are ignored; a section whose only nonblank line is `(none)` passes; every other nonblank line must be a list item starting `- [ ] ` or `- [x] `, and any other content blocks as unknown. There is no fence, HTML, indentation or other Markdown handling. Whatever the line rule cannot establish blocks, which is safe because the label is advisory and `nightshift.land` re-checks at merge. Do not infer emptiness from an unreadable body. Both checked and unchecked list items are checks: ticking a pre-merge item does not make this advisory rule's list empty.

Permit a remaining check only when it cites exactly one known scenario ID in backticks and that scenario, in the run head's committed change specs, contains the exact standalone annotation `<!-- nightshift:post-deploy -->`. Find the scenario by a line rule: the block starts at the line `#### Scenario: <name>` whose name slugs (lowercase, every run of other characters replaced by `-`) to the cited scenario slug under the cited capability and requirement, and ends at the next line starting with `#` at column 0. The annotation must be an exact line inside that block. A scenario found zero or several times blocks; no natural-language inference from a mention of deployment and no annotation accepted solely from the PR body. Unmapped items, prose other than the empty sentinel, or an unmarked scenario block. Do not edit checklist text, mark boxes, or change coverage exemptions. This annotation is an optional convention for future specs; it does not require edits to existing specs.

At assessment time, the configured revision label means revision pending, regardless of actor (readiness does not authorize that request). Also block on a matching build-revision receipt under `state_dir/revisions/<owner__repo>/<number>.json` with `command: revise` and incomplete delivery, an attached uncollected/stopped job, or no terminal result. A fully delivered terminal receipt without the trigger does not block. Malformed revision state is unknown/pending. Read existing state only; never collect jobs, consume feedback, migrate revision receipts, release work, or import the landing module.

### CI is a single advisory snapshot

When assessment is due, make one logical call through the existing guarded GitHub runner to `gh pr checks <pr> --repo <repo> --json name,bucket,state`. Require a JSON array of valid nonempty string fields; green means at least one `bucket: pass` and every bucket is `pass` or `skipping`, matching landing's green rule. Empty, skipped-only, pending, failing, cancelled, malformed, or unavailable checks are blockers. The existing string-returning runner raises on nonzero exit (including pending exit 8); handle that as CI not established, never as green. Do not alter `queue._gh` or import `land.ci_is_green`, which has a different subprocess-result schema.

No `main` lookup, Actions run lookup, parent check, workflow log download, branch update, or CI wait loop is performed. The comment says: `Advisory snapshot for <full head SHA>. Recheck CI and current main when landing; this label does not authorize merge.` Do not emit the superseded `base moved since CI; update the branch` diagnosis without measuring it.

### Head binding and poll budget

Require a nonempty PR head equal to the status run head before adding `merge:ready`. Store readiness separately at `state_dir/merge-readiness/<owner__repo>/<number>.json`, using atomic `_save`. Bind it to change, PR URL, publication round identity (use the existing tuple for legacy receipts), observed PR head, run head, label, reasons, and delivery state. Preserve unknown fields; invalid known fields must not be silently reset.

On each eligible poll, read the PR once. A complete record with the same identity/head and its expected label present, with the opposite label absent, is cached: no checks call, evidence/spec read, comment lookup, label write, or comment write. This applies to `merge:blocked` as well as `merge:ready`. An unlabelled PR, missing/inconsistent labels, new publication identity, or different observed head requires a fresh assessment with at most one checks query. Removing both readiness labels explicitly requests reassessment on the next eligible poll; no new command is needed.

On a head change, remove `merge:ready` before evaluating or delivering any replacement. If the new head differs from the run, add `merge:blocked` and include that mismatch alongside all other determinable blockers. If it matches a newly published run and every condition passes, it may receive a new ready assessment. Do not keep an old ready label because a subsequent checks/evidence read failed. If even the PR/head read fails, perform no speculative writes and retry on the next poll.

The poll budget refers to logical PR/check reads, not existing transport retries or comment pagination during delivery. Reconciliation of incomplete writes uses the one fresh PR read to inspect actual labels and uses comment reads as necessary; it never assumes a persisted delivery intention is a completed effect. No additional status call is needed beyond `publish_pending`'s existing batch read. Outside `human_checks`, this feature makes no readiness queries or writes. Head-change removal is therefore eventual on the next eligible unpaused poll, not a webhook guarantee.

### Label and comment delivery

Use fixed label names `merge:ready` and `merge:blocked`, provisioned by the operator like existing front-door labels. Never create labels. Remove the opposite label before adding the selected label, leaving unrelated labels alone. Persist the chosen assessment before writes and mark delivery complete only after labels and comment succeed. A missing label definition or write failure leaves delivery incomplete for retry.

Maintain one readiness comment per PR/change, updating it on reassessment; unchanged delivered assessments neither POST nor PATCH. A blocked comment lists every failed/unknown condition with stable ordering: head, CI, evidence by section, revision, human checks. Ready comments explain the measured conditions and retain the advisory sentence. Each update includes the full assessed SHA so history or races cannot make an unqualified claim.

Reuse `_comment` with `action="approval"` and `round_id="merge-readiness"`, producing `<!-- nightshift:approval:<change>:merge-readiness -->`. This deliberately uses the existing approval-marker retry namespace: `queue._gh` already reconciles it across all pages, so no queue edit is needed. It is distinct from numeric approval-event markers and from publication markers. Landing must continue selecting the real publication comment, never the readiness explanation. Retain the existing self-comment prefix so feedback filtering ignores it.

On a lost response, read actual labels and reconcile the same comment marker before retry. Never replay an old-head ready intention after observing a new head. Use existing daemon runner pause guards for every external operation and check pause before recording completed delivery; a pause or stop must not reset publication or mark pending readiness delivered. No additional background job is created.

### Implementation scope and test migration

The necessary overlap with in-flight work is only `nightshift/specloop_intake.py`: keep changes confined to private readiness helpers and the post-delivery `publish_pending` branch, with read-only access to revision receipts. Do not refactor revision dispatch/recovery. Rebase the small integration hunk against the concurrent idempotent-revision work.

Do not edit `KNOWN-LIMITATIONS.md`, `README.md`, `docs/specloop/README.md`, `docs/specloop/command-json.md`, `nightshift/specloop_job.py`, `specloop/cli.py`, `specloop/revise.py`, or `specloop/revision_store.py`. Do not edit these in-flight test files: `tests/revision_result_fixtures.py`, `tests/test_revise_idempotency.py`, `tests/test_revise_round_cli.py`, `tests/test_revision_crash_subprocess.py`, `tests/test_revision_feedback_consumption.py`, `tests/test_revision_feedback_recovery.py`, `tests/test_revision_job_identity.py`, `tests/test_revision_replay_delivery.py`, `tests/test_revision_snapshot_retry.py`, `tests/test_revision_store.py`, `tests/test_specloop_background.py`, `tests/test_specloop_build_revision.py`, `tests/test_specloop_jobs.py`, or `tests/test_specloop_label_authorization_recovery.py`.

New tests and shared readiness fakes belong in new `tests/merge_readiness_fixtures.py` and `tests/test_merge_readiness_*.py` files. Existing non-conflicting publication fixture/fake setup may be adapted to supply real PR heads, checks responses, and local run data; keep existing assertion meanings intact except the following authorized migrations in `tests/test_specloop_base_publication.py` (line numbers refer to this checkout):

- Lines 158–159: replace global no-label assertions with exact absence of publication-stage label effects and separate assertions for the readiness label/effects on assessment polls.
- Line 302: require exactly one publication-marker comment, plus exactly one independently counted readiness comment once assessed. Preserve publication body, marker, round, and accepted-comment identity checks.
- Lines 315–316: replace zero-total-GitHub-calls with zero repeated publication effects and the exact cached readiness head-read budget, with no repeated checks, labels, or comment mutations.

Keep all other assertions. Where old fake collections represent publication comments/calls (including the adjacent create/PATCH counts), adapt fixture bookkeeping to maintain explicit publication and readiness collections plus an unfiltered operation log. Never discard readiness operations: assert their exact counts separately in the new integration tests and migrated assertions. Failure injection for publication comments must remain specific to publication delivery so the new stage does not masquerade as a publication failure. Explain each fixture/fake and authorized assertion change in the implementation PR. Do not introduce global skips, weaken assertions to lower bounds, or disable readiness to make integration tests pass.

### Validation and human checklist

Use offline fixtures and temporary local run repositories for evidence, spec annotations, head changes, label transitions, polling counts, malformed data, pause, restart, and ambiguous GitHub responses. Exercise the production `queue._gh` retry behavior with a fake subprocess, not only a successful fake runner. Required named regressions are `test_ready_pr_is_labelled_once`, `test_new_head_removes_ready_label`, and `test_unmeasured_evidence_blocks_readiness`; add `test_readiness_does_not_query_current_base` for the explicitly revised CI contract.

Run targeted new tests and publication regressions during implementation, then `uv run pytest` and `uv run python scripts/check_types.py` without weakening assertions or growing the type baseline. These are implementation acceptance commands, not claims that this documentation draft changes executable behavior.

Pending human checks, excluded from implementation tasks:

- **Post-deploy:** provision both labels in the enrolled repository and observe one real published run, including CI reads, exactly one separate readiness comment, and label removal after a push with deployed credentials. Offline tests cannot establish GitHub permissions or service timing.
- **Post-deploy:** compare the label and full assessed SHA with the reviewer's independent decision over the next 10 merges; record disagreements and reasons, including stale CI/base and remaining manual work. Continue normal review and landing checks during this observation. This determines whether these rules are useful, not permission to merge on the label alone.

## Risks / Trade-offs

- Same-head assessments are cached even when CI later finishes or fails, the body changes, or a revision trigger changes. Remove readiness labels to refresh, or let a later publication/head change invalidate the cache. This is the explicit poll-cost trade-off; `land` still performs fresh gates.
- A push can race the single head/check read and label write. The comment pins the observed SHA and a later eligible poll removes stale ready; there is no atomic GitHub label-and-head operation or provenance guarantee. A daemon pause or a run leaving `human_checks` delays reconciliation.
- Null-score skip recognition depends on the existing saved summary line. Unknown formats and missing old timeout fields block conservatively. Test-only fallback uses narrow path rules and may over-block around base integration, avoiding per-attempt attribution machinery.
- Readiness labels and body text can be manually changed. They are signals, not authority, and no trust is inferred from their existence.
- Six tasks target roughly 200–300 changed lines each, including tests. Shared fixtures reduce repetition; evidence handling and retry integration carry the most sizing risk. Keep helpers narrow and avoid unrelated refactors.
