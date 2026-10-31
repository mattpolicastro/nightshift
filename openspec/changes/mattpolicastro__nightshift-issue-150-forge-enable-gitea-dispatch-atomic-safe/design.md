## Context

The current adapter already validates labels, projects allowlist association,
checks bot ownership before PATCH, and reconciles a single marked create.
The gaps are in transition ordering and the evidence consumed by core helpers.
`daemon._gitea_held` currently blocks every stage; `startup` and `poll_specloop`
would otherwise run GitHub-only worker recovery. Four intake entry points
construct GitHubForge unconditionally. No AGENTS.md is present in this clone;
README.md, CLAUDE.md, the affected code/tests and current capability contracts
inform this design. The prerequisite wire fixes are already represented here.

## Goals / Non-Goals

Enable eligible Gitea drafting, admission, both revision stages, publication,
readiness and existing delivery recovery using the configured bot adapter.
Retain issue-author/feedback trust, latest label-event authorization, head
binding, pause/stop controls, manifest health and all existing receipt formats.
Keep GitHub runner construction, payloads and assertions except the one
explicitly approved inline-marker fixture migration.

Do not implement persistent comment/PR creation state, reservations, fsync'd
files, `comment_create_root` or `STATE_DIR/gitea-comment-creates`. Do not bypass
`needs-human` to recover a receipt. No engine, landing, arming, plugin, transport
retry/backoff, preflight scheduling, status-query or membership changes.

The runtime edit boundary is `nightshift/forge/gitea.py`, the specified daemon
hold/forwarding/guard wiring and transient condition, and these intake sites:
the four stage signatures/default construction sites, `_label_actor_association`,
`_comment_label_rejection`, `_comment`, and necessary imports. Queue edits are
limited to the shared predicate and reconciliation candidate selection. No
other intake lifecycle helper or receipt code is changed.

## Decisions

### 1. Additions precede removals

In `nightshift/forge/gitea.py:GiteaForge.relabel_issue`, reverse the two groups
to additions then removals; `relabel_pr` continues to delegate. Resolve the
complete label map and read current labels as today; retain sorted unique names,
overlap refusal, unrelated labels and verification after each successful write.
All requested additions must be confirmed before the first DELETE. A failed
write or read-back stops immediately without replaying the write in that call.

For a source-to-destination transition starting with the source present, a
failure leaves source only, both, or destination only, never neither (absent
concurrent external removal). Lost accepted POST/read-back leaves the source;
lost accepted DELETE leaves the destination. Add-only and remove-only behavior
does not gain an artificial destination or an all-label replacement operation.

`needs-human` is deliberately a hold even when the source trigger remains.
Existing early skips and pending-receipt reconciliation stay unchanged. A person
inspects the refusal and remote effects, resolves the problem, then removes
`needs-human`; a retained authorized trigger permits normal eligibility and
receipt processing again. This alone need not start a fresh round. For a fresh
draft/admission/refused-revision round use the existing edited-input or trusted
remove-and-reapply rules. Never promise unattended refusal completion through
`needs-human` or require completed work to rerun.

### 2. Bot-only, canonical marker evidence

Keep `GiteaForge.comment_pages` factual: its complete unpaginated listing still
contains humans for drafting, feedback, landing and unchanged contracts. Add
`GiteaForge.bot_comment_pages(repo, number)` for daemon marker consumers; it
filters by casefolded `bot_login` before marker validation, retains raw bodies,
validates bot marker structure and refuses duplicate canonical bot markers.
Reuse this selection in adapter upserts. Human marker bodies, including malformed
marker strings, neither match, block marker validation, get edited, nor count
as reconciliation. Invalid underlying comment records still fail the complete
listing; malformed bot marker evidence and duplicate bot matches still refuse.
Direct PATCH retains its fresh ownership check. Draft/feedback reads continue
using factual comments so trusted human input is not accidentally filtered out.

In `nightshift/queue.py`, add a shared pure predicate
`_has_final_marker(body, marker)`. Normalize CRLF to LF, split on LF, discard
only trailing empty strings, and compare the last remaining line exactly with
`marker`. Do not strip spaces, tabs or indentation; whitespace-only trailing
lines prevent a match. Fences and HTML have no special meaning. Inline tokens
never match. This is a selection rule, not a replacement for Gitea's stricter
existing outgoing marker validator: retain all outgoing malformed-input
refusals, including its single-trailing-newline bound.

Use that predicate in `_comment_label_rejection` and `_comment` in
`nightshift/specloop_intake.py`, choosing `bot_comment_pages` only for Gitea,
and in the existing candidate selection in `queue._reconcile_intake_comment`.
Limit queue edits to that predicate and selection; keep outgoing retry opt-in,
argv, guards and backoff unchanged. No generic Forge protocol or GitHub backend
change is needed. Canonical daemon bodies remain `{prefix} {body}\n\n{marker}`.

Regression: bot comment B contains inline marker A but ends with B. Requesting
A must neither suppress a rejection nor PATCH B. Test A both absent and present
as another canonical comment. Also exercise production GitHub `_gh`: POST A
is accepted with a lost response, retry lists B and canonical A, and PATCHes
only A. On Gitea test core helpers and adapter reconciliation, human copies,
malformed human marker strings, duplicate bot markers and ownership changes.

### 3. Gitea actor projection and rejection text

In `_label_actor_association`, branch for Gitea before GitHub login validation,
owner shortcuts or membership reads. Use the adapter's existing `_association`
projection on the event actor: a nonblank string
login gets OWNER only when `login.casefold()` exactly belongs to the copied
allowlist and differs from the casefolded bot login. Return None otherwise;
do not trim, prefix-match or borrow event/server association claims. No remote
lookup is needed. GitHub's validation and owner/member/collaborator path stays
byte-for-byte behaviorally unchanged; membership methods remain unsupported.

In `_comment_label_rejection`, Gitea text explains configured allowlist
membership and trusted reapplication, rather than GitHub relationships. Render
only a conservative safe login for display (letters/digits, `_`, `-`, `.`);
other values use the existing unavailable-identity phrase. Display sanitization
never determines authorization. Keep the GitHub text and tests unchanged.
Mark all new authorization and daemon-dispatch tests `pytest.mark.real_trust`;
the default conftest fixture otherwise bypasses issue-author checks.

### 4. Conditional stage forwarding, holds and retries

Add keyword-only `forge: Forge | None = None` to `propose_pending`,
`admit_pending`, `revise_pending`, and `publish_pending`. At each current
construction site use the supplied forge when non-None, otherwise retain the
exact existing `GitHubForge(runner or queue._gh)` construction and its timing.
Only the daemon Gitea branch supplies this keyword, including to the existing
`report_failure` hook; GitHub callers and runner-form tests stay untouched.

Change `_gitea_held` to hold only a direct `.json` engine-switch journal or an
uninspectable location. Missing/empty locations and nested-only journals do
not hold. Both startup and poll must explicitly skip `_reconcile_engine_switch`
for every Gitea repo, even after its hold clears. Keep journal inspection
before forge construction, manifest-dependent work or remote side effects.
Never replay, delete or migrate foreign journals. Clearing a journal removes
only that gate; normal credentials, health, authorization, pause and stop rules
still apply. Healthy other repositories continue after a Gitea failure.

Construct one adapter per eligible Gitea repository/poll with `forge_for`, the
configured bot token and `check_running` guard; catch construction failures as
repository-local health errors. Preflight remains the existing operator
enrollment prerequisite, not a new per-poll API or cached permission flag. Do not claim
`poll_specloop` independently re-runs preflight. A passing preflight is necessary
operational evidence, not authorization for any individual label actor.

The transport already checks before attempts, GET retries and reconciliation.
Add an optional `after_request=None` callback to GiteaForge and wire it only
from daemon `forge_for` for guarded Gitea dispatch. Invoke it after a successful
transport request before returning facts, verifying a write or acknowledging a
receipt. Run it outside the transport-error catch so its exception object,
including GiteaHTTPError, escapes unchanged with no reconciliation. Default
adapter construction adds no guard invocations, preserving existing guard-count
assertions. HTTP transport itself stays unchanged. Pause after an accepted final
write must leave delivery pending and prevent further external operations.

In `item_failed`, broaden only the transient exemption condition from transient
GhError to transient ForgeError (GhError already inherits it); do not change
thresholds or generic/permanent-error policy. Test timeline reads failing for
more than the health threshold while comments/labels remain writable: health
reports the outage, no failure comment/needs-human is added, and triggers and
receipts remain for retry. Normal Gitea permanent failure reporting uses the
same selected adapter. This small condition change is in addition to the
authorized hold/forwarding/guard wiring, not permission for a health refactor.

### 5. Recovery assumptions and request budgets

Reuse existing receipts and lookup-before-create for comments and PRs. The
deduplication claim assumes read-after-write consistency and no concurrent
creator for the same marker/branch. The deployed single-node Gitea commits a
create before responding; the next complete listing can reconcile it. If an
accepted ambiguous POST remains invisible to later listings, a later daemon
poll can create a duplicate comment or PR. This is an accepted limitation,
also applicable after restart; no durable reservation is introduced. Replicated
Gitea or changed consistency requires revisiting this assumption before rollout.
Duplicate bot markers refuse; operator guidance says pause, inspect effects and
resolve duplicates before resuming, without claiming pause retroactively avoids
an accepted request. CI can exercise visible accepted writes, not prove server
consistency. The merge-readiness delta explicitly scopes its no-duplicate claim.

Readiness keeps one core PR read. Gitea `pr_checks` may read the PR once more
before its one logical combined-status snapshot (which may paginate); cached
polls without CI still read only once. Preserve exact-SHA status validation,
advisory wording and every GitHub one-read/check-argv assertion. Test this through
the real Gitea adapter rather than relying only on stage spies. Do not refactor
head capture or promise stronger cross-request consistency in advisory readiness.

### Assertion migration inventory

Only these existing expectations may change; keep every other assertion:

| Existing assertion | Replacement requirement |
| --- | --- |
| `tests/test_forge_gitea.py::test_gitea_label_write_is_read_back` orders DELETEs before POSTs | Verified label mutations: POST/read-back of all additions before DELETE/read-back, same sorted order within groups and same final set. |
| `test_label_failure_stops_remaining_writes` assumes DELETE is the first write in mixed transitions | Verified label mutations: inject DELETE failures after verified additions and assert the successful POST prefix followed by the failing DELETE; preserve all failure classifications and no-later-write assertions. |
| `test_malformed_existing_marker_is_not_absence`, human (`Alice`) rows | Bot owned marked comments: human marker syntax is ignored and cannot block creation; bot rows still refuse. Existing factual human-comment assertions remain. |
| `tests/test_daemon_forges.py::test_gitea_daemon_stages_are_disabled` and its `held`/`check_hold` blanket-hold expectations | Eligible Gitea dispatch: authorized healthy Gitea stages run through their adapter; isolate new fixtures so existing mixed GitHub assertions stay unchanged. |
| `test_gitea_repo_with_engine_switch_journal_is_held`, nested-only and post-removal blanket-hold expectations | Foreign recovery journal hold: only direct JSON or unreadable locations hold; clearing the gate permits otherwise eligible dispatch, never worker recovery. Preserve all direct-journal safety assertions. |
| `tests/test_forge_github.py::test_comment_fake_preserves_literal_marker_and_raw_body`, inline-only `existing=True` case | Core policy preservation: `"prefix " + marker + " suffix"` expects create_comment; add a canonical-final-line case expecting update_comment with unchanged raw outgoing body. This is the single GitHub exception. |

Preserve #165 body-action/complete-comment corrections, #170 timeline/status
exceptions, all `tests/contract/`, default guard counts, GitHub argv and remaining
assertions. The MODIFIED Offline fake and optional live contracts requirement
extends its exceptions with precisely this inventory, rather than relying on
a conflicting newly added requirement elsewhere.

### Documentation and verification

Task 1.5 updates only affected passages in README.md, KNOWN-LIMITATIONS.md,
docs/specloop/README.md, docs/arming.md and docs/landing.md. Correct local stale
hold statements (or add an explicit nearby supersession), authorization wording,
admitted-work health bypass and retained-trigger re-arm guidance. Preserve
terminal human-token landing, GitHub-only Factory Land, status limitations,
frozen wrappers, existing examples/anchors and independent supervisor controls.
Do not edit the plugin README; the engine guide supplies current daemon scope.

Use existing documentation validators. Where a new structural check is needed,
select a unique exact column-zero heading through the next column-zero heading
of equal/shallower depth or EOF using LF-separated physical lines. Missing,
duplicate or ambiguous boundaries fail. For scoped shell examples require exact
unique opening/closing fence lines and reject extra fence tokens; use shlex and
the real parser without dispatch. Fences, HTML and indentation do not hide
physical headings or tokens. Retain existing link validation, fail ambiguous
syntax, and add no prose-exact assertions or second raw `[[repos]]` guide block.

Keep tests with their concern, reuse FakeGitea and existing pipeline/receipt
fixtures, and run focused pytest modules per task. Final acceptance is
`uv run pytest` and `uv run python scripts/check_types.py` with no new baseline
errors. Include the four issue-named tests from the request and the retry,
guard, readiness and outage regressions described above. Five sessions target
roughly 150/300/200/300/300 changed lines respectively; estimates include tests
and are not permission to trim coverage. Documentation is its own final task.

## Risks / Trade-offs

Add-first is a recoverable ordering, not a multi-label transaction: both labels
may be visible, and needs-human intentionally parks the item. Concurrent human
label edits are outside the never-neither invariant. Delayed visibility and
concurrent creates can duplicate; read-after-write consistency is an explicit
deployment assumption, not a CI result. Gitea readiness can observe two PR
snapshots and remains advisory; landing keeps its stronger independent gates.
Dispatch coverage remains the largest task despite moving docs out; fixture
reuse and narrowly scoped hooks keep it near the five-task budget. No sixth
task, new recovery subsystem, live deployment or live credential changes are
part of implementation.

## Human checks

- [ ] On an enrolled real Gitea repository after #170, #165, #128, #135 and #131, run preflight with the daemon clone and bot environment; confirm the forbidden repository really exists, protection policy is adequate and clone credentials identify the bot. Record server/version and single-node read-after-write assumptions; revisit before replicated deployment.
- [ ] Confirm the service sources the selected bot token and every declared land-token variable is absent, including empty values. Confirm the independent supervisor selects the same forge config and has its own bot/git credentials (the deferred #149 check); do not expose token values in evidence.
- [ ] Have an allowlisted human file and label a small `spec:draft` issue; observe drafting, a correctly WIP-prefixed spec PR, review label and issue link on the same forge. Then have a login outside the allowlist apply a trigger and verify refusal with allowlist-specific wording and no triggered work.
- [ ] Exercise the retained-source plus `needs-human` recovery on disposable work: inspect effects, resolve the refusal and remove the hold; distinguish delivery reconciliation from a fresh trusted remove-and-reapply round. Observe daemon pause separately from the independent supervisor and retain any pending live checks as unchecked.
