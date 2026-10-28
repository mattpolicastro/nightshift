## Context

The landed `nightshift/forge/__init__.py` declares `Forge`, whose methods return
existing queue objects, dictionaries, paginated dictionaries and `ResponseText`.
`github.py` preserves the existing GitHub command transcripts. `http.py` supplies
`GiteaTransport`, JSON helpers, classified `GiteaHTTPError`, guarded dispatch and
GET-only retries. Neither prerequisite needs editing. Existing GitHub policy
and intake/revision/readiness requirements remain authoritative for the
currently wired front door. A structurally compatible standalone Gitea
backend does not by itself make that front door ready for Gitea enrollment.

## Goals / Non-Goals

Goals: implement the existing front-door interface without changing callers;
make silent Gitea failures observable; exclude untrusted and bot-authored idea
text; exercise actual HTTP serialization through an in-process opener; supply
contracts reusable against a disposable Gitea repository.

Non-goals: daemon/configuration wiring, new config keys, membership discovery,
landing, publication policy, merging, changes to the plugin or build engine,
and installing a live-service CI job. No changes to README outside its module
layout and adjacent staged-boundary explanation, or to
`nightshift/forge/{__init__,types,http,github}.py`, existing tests or type baseline.
In particular do not edit `docs/specloop/prepare.md`, `specloop/loop.py`,
`specloop/manifest.py`, `specloop/verify.py`, or
`tests/test_dependency_snapshots.py`, which belong to concurrent work.

## Decisions

### Implementation files and ownership

Only these implementation files may be created or extended by implementing tasks:

- `nightshift/forge/gitea.py`: adapter, private normalization/pagination helpers.
- `tests/fake_gitea.py`: stateful fake opener and response/failure fixtures.
- `tests/test_forge_gitea.py`: focused offline behavior and regression tests.
- `tests/contract/conftest.py`: fake/live contract selection and scoped fixtures.
- `tests/contract/test_gitea_contract.py`: shared observable API contracts.
- `README.md`: only the module-layout table and adjacent staged-boundary
  explanation, distinguishing the implemented backend from deferred enrollment.

Capability deltas remain in this change for the harness to archive at finish.
They are drafting artifacts, not implementation files. Task 1.7 does not apply
specs or edit `openspec/**`. It corrects README's obsolete claim that a Gitea
backend remains future work, as required by the Module layout guidance delta.
Preserve the existing module entries and core-policy explanation; add a
`forge/gitea.py` entry for standalone front-door operations over `forge/http.py`.
State that the backend is implemented while Gitea enrollment/daemon integration
and landing migration remain future work. Do not imply live compatibility has
been established by offline tests.

For this documentation edit and any structural verification, select the unique
physical line exactly `## Layout` through, but excluding, the next column-zero
line beginning `## `. Missing or duplicate boundaries block the edit/check.
Within it, module rows begin literally `| ` followed by a backtick; module paths
are the balanced backtick spans in the first pipe-delimited cell. Refuse
unbalanced spans or duplicate module paths. Treat fences, HTML and indentation
literally, without semantic parsing. Verify named paths resolve, retain the
existing layout test, and do not introduce exact explanatory-prose assertions.

Seven tasks separate transport/fake foundation, label safety, identity/evidence (including feedback reads),
PR operations, CI/provenance, comment mutation, and contract execution. Each
targets at most approximately 300 changed lines including tests, extending the
shared files only for its concern. Reuse table-driven fixtures and common
normalizers; do not cut failure assertions to meet a line estimate.

### Construction and error boundaries

Use `GiteaForge(base_url, token, *, trusted_logins, bot_login,
wip_prefix="WIP: ", opener=None, guard=None, timeout=30)`. It owns a
`GiteaTransport` created with those transport arguments. Construction makes no
request; freeze a copy of the allowlist. Require nonempty string logins and a
nonempty prefix; reject invalid constructor inputs with value-free diagnostics.
Use casefolded login comparison consistently, including bot exclusion. Token
and arbitrary response bodies must never enter adapter-generated diagnostics.

Use `/api/v1` API paths beneath the transport's configured origin/prefix.
Encode owner, repository and branch/path components independently; queries are
separate mappings. Never dispatch to URLs returned by Gitea. Public operation
failures from the transport become `ForgeError` retaining `transient`; malformed
or ambiguous evidence produces nontransient `ForgeError`. Do not introduce
backend retries. `issue_labels` raises on unreadable evidence rather than
returning an empty set. `latest_pr_number(strict=False)` may return None only
for genuine transport failures; `open_blockers` retains unreadable numbers in
input order. Empty blocker input does not read anything.

**Guard origin takes precedence over exception class.** Wrap the injected guard
with an observer that records the exception object in request-local context and
immediately re-raises that same object. Reset this context for every transport
call (including reconciliation and retries; use a context variable/token so
nested calls cannot leave stale origin evidence). Every handler that translates,
suppresses or reconciles `GiteaHTTPError` first checks whether it is that recorded
guard exception; if so it re-raises unchanged. This requires no transport edits
or private transport attribute access. Other guard exception classes naturally
propagate without classification. Broad exception handlers are permitted only
in the observer to record and re-raise, never to initiate reconciliation.

Reconciliation catches only genuine `GiteaHTTPError`, before translation to
`ForgeError`. A transient failure or a 2xx response that cannot be decoded is
an ambiguous write outcome. A definite 4xx/redirect rejection does not trigger
reconciliation. Tests use guard-raised `GiteaHTTPError`, `ForgeError`, `OSError`
and an ordinary pause exception before writes, reads, retries and reconciliation;
assert object identity, zero subsequent dispatch and no fallback success.

### Pagination and FakeGitea

Each list endpoint starts at `page=1, limit=50`, retaining its own fixed query
and incrementing page by one. A nonempty page, including a short one, never
proves completion. Continue to an empty terminal page. Do not follow `Link`
URLs or infer completion from a missing Link header. Refuse malformed pages,
conflicting duplicate IDs, a repeated nonempty page, or failure to terminate
within 1,000 pages. If provided total/next metadata contradicts termination,
refuse rather than return a partial result. This policy belongs in `gitea.py`,
not `GiteaTransport.get_page`. Use the same rule for nested review comments and
the `statuses` array inside combined-status envelopes. Read all pages before
returning an operation's result; sort timeline events by positive numeric ID
to present chronological order, deduplicating identical events.

`FakeGitea` implements the trusted urllib opener interface (`add_handler`,
`open`) and context-managed responses with code, headers, bytes and close.
It records method/path/query/body and owns repository/issue/PR/comment state;
no sockets, server thread or subprocess. Tests inject it into the real
transport. Add route behavior with the corresponding adapter task, not a
complete service emulator in task 1.1. Support short intermediate pages,
later-page failures, duplicate names, ignored label/head filters, silently
ignored label writes and writes accepted before a lost response. Unexpected
routes fail tests. Never record or print real credentials.

### Labels and timeline

There is no begin-poll API in Forge. Therefore every `list_issues` and
`list_open_prs` invocation reads every repository-label page and builds a fresh
name-to-ID map; label mutations also refresh the map. Reject duplicate names
anywhere in that map, malformed IDs/names and any requested missing name before
listing candidates or writing. No cross-poll label cache or label creation.

List issues with `state=open` and locally match the resolved label ID; exclude
rows identifying PRs. List open PRs similarly. Never send `labels` or `label`
query parameters, including IDs, and do not rely on the fake's filtering.
Read issue labels from `/repos/{o}/{r}/issues/{n}/labels`.

For relabeling, reject overlapping add/remove names, preserve unrelated labels,
and perform targeted changes in deterministic remove-then-add order. DELETE
`.../labels/{id}` for each present removal; POST `.../labels` with
`{"labels": [id]}` for each absent addition. After each successful write,
read all issue-label pages and confirm that specific requested membership.
Silent non-application raises and stops further writes. A guard or HTTP failure
stops the operation; no write is replayed. Already satisfied operations may
skip writes after reading current labels. PR relabeling uses the same issue
label endpoints. A failed read-back never becomes success.

Label events come exclusively from `.../issues/{n}/timeline`. The contract
fixture defines label rows with `type="label"`, positive `id`, `label.name`,
`user.login`, `removed` boolean and `created_at`. Normalize `removed=false/true`
to `event="labeled"/"unlabeled"`, with `actor.login`, association and label.
Ignore non-label rows; refuse incomplete or unknown label-action evidence,
rather than inferring actions or actors from prose. This explicit wire shape
must be checked against the chosen live version; alternative documented shapes
need explicit fixtures and validation, never permissive guessing.

### Trust and ordinary reads

Only the injected allowlist determines association: `OWNER` for a listed login
other than the bot, `NONE` otherwise. Ignore server associations, ownership,
permissions and organization membership. Apply this to issue authors, comments,
reviews, inline feedback and event actors. Preserve required camelCase keys
for view results and snake_case keys for REST-shaped records. Missing authors
are untrusted, not synthetic owners. `issue_for_draft` omits bot comments even
if the allowlist includes the bot or its body has no daemon prefix.

`repository` reads repository facts without making authorization decisions.
The two membership methods required structurally by Forge raise a fixed
nontransient unsupported-operation `ForgeError` without HTTP dispatch. They
must not fabricate membership or return positive membership responses. Current
GitHub actor-resolution code remains unchanged; wiring Gitea to it is deferred.

Use issue detail and comment endpoints for `issue_author`, `issue_for_draft`
and blockers. Normalize issue state to uppercase and preserve title/body and
label objects. `pr_feedback_records` reads conversation comments, PR reviews,
and each review's comments through Gitea's nested review-comment endpoint.
Normalize into the existing `conversation`, `review`, `inline` record shapes;
preserve stable source-qualified IDs, timestamps, review state, path and line.
Only allowlisted non-bot records enter feedback results. Deduplicate identical
records, refuse conflicting IDs and partial reads, retain blank trusted records
for legacy baselines. `pr_feedback_items` uses the existing formatting and
instruction filtering semantics without dispatching a GitHub queue helper.
Consumption, marker namespace selection and workflow authorization stay in core.

### PR operations and drafts

`latest_pr_number` lists `/repos/{o}/{r}/pulls` with `state=all` and no `head`
query; filter exact `head.ref` locally, taking the greatest positive PR number
across all pages, including closed/merged PRs. No match returns None only after
a complete read. Never rely on server ordering or the ignored head filter.

Implement each `pr_for_*` view from a shared strict detail normalizer, exposing
the fields its GitHub counterpart returns: uppercase state (MERGED when the
merged flag is true), labels, `headRefName`, `headRefOid`, `isCrossRepository`,
URL, title and body as appropriate. Compare actual head/base repository IDs
for cross-repository evidence; missing evidence is refused. `pr_state_url` and
`relabel_pr` accept positive numbers, exact branch names, or a same-origin,
same-repository PR URL under the configured prefix; resolve to a number before
API dispatch and refuse foreign or ambiguous URLs. Never GET a supplied URL.

Draft recognition is case-sensitive `title.startswith(wip_prefix)`; expose
`isDraft` with view facts. `create_draft_pr` POSTs title/body/head/base, prepending
the prefix only when absent, and returns the created `html_url` in
`ResponseText.stdout`. Do not send a GitHub draft flag. Do not strip a prefix
from factual titles. Failure never leads to another POST in the same call.

### Exact SHA status and commit provenance

Expose `checks_for_sha(repo, sha) -> ResponseText` on GiteaForge in addition to
the unchanged Forge methods. Require a full 40-hex SHA, not a branch name.
Read `/repos/{o}/{r}/commits/{sha}/status` with page/limit, validate the returned
`sha`, `state`, `total_count` and complete `statuses` array. Across pages require
consistent SHA, combined state and count; inconsistent snapshots are refused.
Do not fetch branch tips, merge refs, Actions jobs or logs. Only a proven empty
status set (count zero) produces JSON `[]` (not registered). Missing or malformed
status evidence is an error, not absence. For registered statuses return one
check named `Gitea combined status`, with `state` retaining the combined value:

| Combined value | bucket |
| --- | --- |
| success | pass |
| pending | pending |
| failure, error, warning | fail |
| skipped | skipping |
| anything else | refuse |

`pr_checks(repo, number)` reads that PR's current head SHA once and delegates
to `checks_for_sha`; it does not reuse a cached branch/head snapshot. This extra
read is confined to the standalone backend; the existing GitHub readiness
request budget and daemon code are unchanged. Future enrollment must explicitly
address passing a caller-observed SHA through the interface to retain that budget.

`commit(repo, head)` uses the commit detail endpoint and returns the SHA,
`parents: [{"sha": ...}]` and nested commit author/committer dates needed by
current consumers. Validate the full requested SHA and matching returned SHA;
preserve every parent, including zero parents and merge commits. Missing parents
is malformed evidence, never a root commit. This supplies facts, not a landing
or provenance verdict.

### Bot-owned comments and deterministic markers

Keep `comment_pages` complete, including human comments, with author identity.
For create/upsert recognition, normalize CRLF to LF only for matching, split
into lines, and allow one trailing newline. The final line must full-match
`<!-- nightshift:(?:(?:draft|admit|publish|revise)(?:-error)?|approval):[^\s<>]+ -->`.
There must be exactly one line beginning literally `<!-- nightshift:` anywhere
in the body. A body containing that literal token but failing these rules is
refused before writes. Plain bodies with no token are unmarked. Match an
existing marker by the same exact whole-line rule; malformed candidates with
the sought token cause refusal, not an absent result. Fences, indentation and
HTML context have no semantic meaning; body bytes are never rewritten for
matching. The bot-author check is mandatory regardless of marker placement.

`create_comment` with a valid marker lists all comments before POST: one exact
bot-owned match is updated if necessary, multiple bot matches refuse, human
copies do not count as bot matches and are never edited. With no match, POST
once. Catch only genuine ambiguous `GiteaHTTPError` and re-list all pages: one
bot match with the intended body establishes success; a differing body may
receive one guarded PATCH through the ownership check. No match re-raises the
original failure; a failed reconciliation read propagates. Never POST twice
in one call, even after confirmed absence. Unmarked writes do not reconcile.

`update_comment` GETs `/repos/{o}/{r}/issues/comments/{id}` and verifies bot
authorship before PATCH, even when called directly by core with a human ID.
This prevents existing core substring selection from mutating a human copy;
it may refuse that call rather than silently selecting a different comment.
Preserve guard checks on every read/write and do not retry PATCH in the adapter.
No cross-process exactly-once guarantee is claimed: concurrent creates may
race, and duplicate bot markers cause refusal on the next complete lookup.

### Contract execution and acceptance

Use the same normal-operation contract assertions against FakeGitea by default
and a separately parameterized live fixture. Fake fault tests stay offline.
All new trust tests use `pytest.mark.real_trust` to avoid the root conftest's
legacy trust shortcut, and tests carry the scenario IDs listed in tasks.

Live mode requires `GITEA_CONTRACT_URL`; if absent, live cases skip before
constructing a transport, checking credentials or opening sockets. When URL is
present, missing `GITEA_CONTRACT_TOKEN`, `GITEA_CONTRACT_REPO`,
`GITEA_CONTRACT_BOT_LOGIN` or `GITEA_CONTRACT_TRUSTED_LOGIN` is a setup failure,
not a skip. These are test-only environment variables, not application config.
The token authenticates as the bot. The operator provisions a disposable
repository, branches, labels, trusted/untrusted authors, timeline events,
reviews, commits and status states described by the fixture. Scope mutation
to run-unique issues/PRs/comments and clean up only resources created by that
run. Fixture setup must fail on missing expected evidence, not silently drop
assertions or mutate unrelated fixtures. No automatic server provisioning.

`test_contract_suite_skips_without_url` removes the URL in an isolated pytest
subprocess, forbids opener dispatch, and asserts explicit skips rather than
zero collected tests. Add the seven other named acceptance tests from the issue
to `tests/test_forge_gitea.py`, alongside edge cases identified above.
Acceptance: `uv run pytest` passes with existing assertions preserved and
`uv run python scripts/check_types.py` reports no new errors. Never update the
baseline to absorb errors. This drafting session validates documents; those
commands are implementation acceptance criteria, not claims of work completed.

## Risks / Trade-offs

- Gitea timeline, review and combined-status shapes vary by version. Fake
  coverage validates the stated contract, not actual server compatibility.
  Unknown or incomplete wire evidence refuses operation. A separate direct PR
  owns the live-service CI job and any version-support expansion it reveals.
- Seven approximately 300-line tasks remain tight. Shared normalizers and
  incremental fake routes reduce repetition, but safeguards and test assertions
  must survive implementation; a size estimate is not permission to omit them.
- Empty-page termination costs one more read and pagination is not an atomic
  server snapshot. Refuse detected inconsistencies; do not promise race freedom.
- Allowlist changes require reconstructing the adapter. No membership lookup
  or constructor argument implies Gitea daemon enrollment is available.
- Guard exceptions can share any error class. Origin tracking and identity
  assertions are required for all suppression/reconciliation boundaries.

Human checklist for the PR (not implementing-agent tasks; pending until run):

- Against a disposable instance, record its Gitea version and execute the live
  contract suite with separate bot, trusted and untrusted identities.
- Confirm timeline type/action/actor shape for apply, remove and reapply,
  pagination across server-imposed limits, review/inline endpoints, and exact-SHA
  combined status for absent, success, pending, failure/error/warning and skipped.
- Confirm title-prefix draft behavior, all-state branch lookup, actual label
  mutation/read-back and bot-only comment ownership with a human marker copy.
- Inspect accepted remote effects during a deliberately interrupted comment
  response and confirm recovery; offline lost-response injection is not proof
  of the deployed server/proxy's behavior.
- Verify the separate direct-PR CI job runs the live contracts and records the
  tested version. Missing live evidence remains pending; offline green cannot
  check off these items or establish production readiness.
