## Context

The checkout already contains #123's `nightshift/forge/__init__.py` docstring
and `nightshift/forge/http.py`. The latter is a standalone Gitea transport and
is not used or changed here. No `AGENTS.md` exists in this checkout; the root
README, repository instructions in `CLAUDE.md`, current code and capability
specifications inform this plan.

`queue._gh` accepts `list[str]` without a leading `gh` and returns stdout. It
owns subprocess dispatch, transient retries, marked-comment reconciliation and
the context-local `gh_guard`. Queue helpers already own several command shapes.
Intake owns authorization, receipt transitions, marker selection and readiness
decisions. Daemon closures check pause before and after each logical call and
install the guard for nested transport retries.

Extraction must include indirect paths: `report_failure`, issue-author reads,
legacy feedback initialization, actor membership checks, rejection comments,
ordinary comment upserts, proposal PR creation and engine-switch recovery.
Moving only the seven advertised runner-resolution sites would leave gaps.

## Goals / Non-Goals

Goals are a structural `Forge` interface, a compatible `GitHubForge`, factual
frozen records, late runner resolution and tests preventing new GitHub argv
construction outside the permitted boundary. Observable output, failures,
side-effect ordering, saved state and GitHub request transcripts remain the
same.

Implementation may edit only `nightshift/forge/__init__.py`, new
`nightshift/forge/types.py`, new `nightshift/forge/github.py`,
`nightshift/specloop_intake.py`, `nightshift/daemon.py`, `nightshift/queue.py`,
new `tests/test_forge_github.py`, new `tests/test_forge_boundary.py`, and the
module-layout passage in root `README.md`.

Do not edit `nightshift/land.py`, `nightshift/factory_land.py`,
`nightshift/arm.py`, `nightshift/outcomes.py`, `nightshift/preflight.py`,
`specloop/cli.py`, `specloop/critic.py`, `nightshift/forge/http.py`, config,
plugins, existing tests or the type-error baseline. In particular avoid the
concurrent change's `docs/specloop/README.md`, `examples/specloop/dart.toml`,
`specloop/evidence.py`, `specloop/manifest.py`,
`tests/test_mutation_prose_annotations.py` and
`tests/test_mutation_source_filtering.py`. The root layout table is sufficient
documentation scope.

No backend selection/configuration, Gitea implementation, caching, added reads,
pagination changes or retries are introduced. Existing readiness receipt reuse
and CI-blocked reassessment remain unchanged; “no caching” means no new cache.
Git pushes remain on the current git runner with unchanged argv, timeouts,
leases and guards. Specloop publication internals remain outside this seam.

## Decisions

### Contract, records and errors

Define `Forge` as a `typing.Protocol`, with the named methods below and no
generic `run(argv)` or `api(endpoint)` escape hatch. `GitHubForge` satisfies it
structurally. Keep the package initializer independent of the concrete backend
and queue at runtime to avoid an import cycle; type-only references can use
`TYPE_CHECKING`. Type annotate migrated internal forge parameters as `Forge`.

Add frozen `Issue`, `PullRequest`, `Comment`, `LabelEvent` and `CICheck`
dataclasses in `types.py`, limited to facts already read: identity, title/body,
labels/state, PR refs/URL/cross-repository status, author or actor identity and
association, event identity/type, comment timestamps/context and check
name/bucket/state as applicable. No record decides trusted, authorized, ready,
actionable or consumed. Preserve missing/null values rather than inventing
evidence. Keep existing dictionaries and page lists where conversion would
change validation or caller contracts; this is not a wholesale response-schema
migration. In particular queue helper records and `FeedbackItem` remain
compatible, and `queue.Issue` keeps its current constructor and branch behavior.
Add a small frozen `ResponseText(stdout: str)` factual record for the few
operations whose current text parsing must remain in core, listed below.

`ForgeError(RuntimeError)` takes `(message, *, transient: bool)`. Make
`queue.GhError` inherit it, preserving its public name, constructor, text,
`transient` attribute and `RuntimeError` compatibility. For direct operations
and exceptions escaping delegated helpers, the backend propagates the original
exception object, including `Paused`; it does not wrap arbitrary exceptions,
classify new failures, retry or edit Gitea's existing error class.
Existing `queue.GhError` catches can remain in this staged migration.

Delegation preserves helper error handling, not unconditional runner-error
propagation. `queue.labels_of` catches `RuntimeError` and returns an empty set;
`queue.open_blockers` catches it per lookup and retains that number as blocked;
`queue._latest_pr_number` returns `None` with `strict=False` and re-raises with
`strict=True`. These handlers also catch `GhError` and `Paused`, both
`RuntimeError` subclasses. GitHubForge returns those fallback values unchanged
without trying to recover suppressed exceptions. Daemon recovery's existing
guarded runner captures label-read errors and its core `read_labels` wrapper
re-raises them after the helper returns; retain that protection separately.

### Operation inventory and exact argv

Use semantic arguments such as repository, item identifier, label, head and
body, never caller-built argv or caller-supplied JSON field selectors. The
following inventory is the minimum complete method surface. In the table,
`R` is the repository string, `N` a decimal item number, `T` the exact PR
number-or-URL string, and `B` an unchanged body. These are individual argv
elements, not shell command strings. Field lists and flags are literal and
ordered. Methods perform no eager work during construction.

| Method | Existing operation / runner-form argv |
| --- | --- |
| `list_issues(R, label)` | Delegate `queue._list(R, label, runner=runner)`; retain `issue list --repo R --label label --state open --json number,title,body --limit 100` |
| `issue_labels(R, N)` | Delegate `queue.labels_of(R, N, runner=runner)` for engine-switch recovery |
| `relabel_issue(R, N, add, remove)` | Delegate `queue._relabel`; additions precede removals, preserving each caller's order |
| `latest_pr_number(R, branch, strict=True)` | Delegate `queue._latest_pr_number` with explicit strictness; intake keeps `strict=True`, all-state lookup and limit 20 |
| `pr_feedback_records(R, N)` | Delegate `queue._pr_feedback_records` with the bound runner |
| `pr_feedback_items(R, N)` | Delegate `queue.pr_feedback_items` with the bound runner |
| `open_blockers(R, numbers)` | Delegate `queue.open_blockers` with the bound runner and unchanged number order |
| `issue_for_draft(R, N)` | `issue view N --repo R --json state,title,body,labels,comments` |
| `issue_author(R, N)` | `api repos/R/issues/N` |
| `list_open_prs(R, label)` | `pr list --repo R --label label --state open --json number --limit 100` |
| `pr_for_recovery(R, N)` | `pr view N --repo R --json state,headRefOid` |
| `pr_for_admission(R, N)` | `pr view N --repo R --json state,labels,headRefName,headRefOid,isCrossRepository,url` |
| `pr_for_revision(R, N)` | `pr view N --repo R --json state,title,body,labels,headRefName,headRefOid,isCrossRepository,url` |
| `pr_for_publication(R, N)` | `pr view N --repo R --json state,labels,headRefName,isCrossRepository,url` |
| `pr_for_readiness(R, N)` | `pr view N --repo R --json state,labels,headRefName,headRefOid,isCrossRepository,url,body` |
| `pr_state_url(R, T)` | `pr view T --repo R --json state,url` |
| `label_event_pages(R, N)` | `api repos/R/issues/N/events --paginate --slurp` |
| `repository(R)` | `api repos/R` |
| `collaborator_response(R, login)` | `api repos/R/collaborators/LOGIN --include`, with the existing `quote(login, safe='')` encoding |
| `organization_member_response(owner, login)` | `api orgs/OWNER/members/LOGIN --include`, encoding both components exactly as today |
| `commit(R, head)` | `api repos/R/commits/head` |
| `comment_pages(R, N)` | `api repos/R/issues/N/comments --paginate --slurp` |
| `pr_checks(R, N)` | `pr checks N --repo R --json name,bucket,state` |
| `relabel_pr(R, T, add, remove)` | `pr edit T --repo R`, followed by existing ordered `--add-label` pairs then `--remove-label` pairs |
| `create_comment(R, N, kind, B)` | `kind comment N --repo R --body B`, with kind issue or pr |
| `update_comment(R, comment_id, B)` | `api repos/R/issues/comments/ID --method PATCH -f body=B` |
| `create_draft_pr(R, branch, base, title, B)` | `pr create --repo R --head branch --base base --draft --title title --body B` |

Return decoded dictionaries/lists or current typed helper records for ordinary
JSON reads. Return `ResponseText` containing unchanged stdout for `pr_checks`
and the two membership-response operations so their existing core parsing/error
boundaries stay intact, and for PR creation so the existing `.strip()` and
empty-URL check operate on `.stdout` in core. This frozen result carries facts
without parsing or normalizing them; it is not a generic transport escape hatch.
Mutation return values remain ignored as today.
Do not introduce new empty-success fallbacks for malformed, unavailable or
missing data; preserve the delegated helpers' existing fallbacks above.

The two feedback methods deliberately reuse queue's existing trusted filtering
and formatting. Those decisions stay implemented in queue core, never copied
into `github.py`. Likewise blockers retain queue's fail-closed behavior.

### Late binding and migration boundaries

At each former `gh = runner or queue._gh` site construct
`forge = GitHubForge(runner or queue._gh)` inside the function. The backend
constructor may accept `runner=None`, resolving `queue._gh` at construction,
never in a default argument or module-level singleton. Every delegated queue
helper receives the selected runner explicitly, avoiding its early-bound
default. A backend binds that callable for its lifetime; the next function
invocation constructs a new backend and sees a later monkeypatch.

Preserve existing public and test-used `runner=` signatures. Internal helpers
may accept an optional keyword-only `forge: Forge | None`; if supplied, pass
that object through, otherwise construct it locally from `runner`. This lets
existing direct helper tests run unchanged and permits protocol fakes. Do not
make a forge callable to imitate a runner or expose its runner to core logic.

Migrate `propose_pending`, `admit_pending`, `revise_pending`,
`publish_pending`, `_reconcile_merge_readiness`, `_readiness_ci`,
`_deliver_readiness` and their I/O helpers, including `_deliver`, `_comment`,
`_comment_label_rejection`, `_latest_label_events`, `_label_events`,
`_issue_author_trusted`, `_initialize_revision_feedback`,
`_approval_covers_head` and `report_failure`. Failure reporting chooses the
issue or PR label operation in core. Keep the conditional suppression of an
empty revision label edit; do not turn it into an extra request.

Keep membership status interpretation (including only the existing definitive
404 exception), login validation, owner/collaborator/member decision order,
latest-event selection, `queue.trusted`, marker selection, prefix filtering,
rejection-vs-upsert behavior, receipt updates and readiness judgment in core.
Replace `_membership_status`'s dispatch with the specific forge membership
method while retaining status parsing and error handling. No backend parses
Markdown or HTML. Existing dependency, marker and readiness checklist parsing
is untouched, including malformed-input refusal; do not add a new parser.

In daemon `_reconcile_engine_switch`, create `GitHubForge(gh)` after the
existing guarded closure and use it for list/labels/comments/relabeling. Keep
the closure's validation and captured-error handling around `labels_of`, which
otherwise hides `RuntimeError`. Express its comparison-only list
`args[:2] == ["issue", "view"]` as an equivalent tuple comparison so the
literal-list scanner needs no special exemption. Do not hide real command
construction with tuples or dynamic prefixes.

In `poll_specloop`, keep the guarded closure, including both pause checks and
`with queue.gh_guard(check_running)`. Its wrapped runner must be the runner
passed into `GitHubForge` by intake entry points and error-reporting helpers;
do not bind the underlying runner before the guard. Preserve nested recovery
guards, retry/reconciliation checks and pending receipt behavior.

### Source boundary and documentation

`tests/test_forge_boundary.py::test_no_gh_argv_outside_backends` recursively
parses every `*.py` under `nightshift/` and `specloop/` with `ast.parse`. For
every `ast.List` with a first element that is a literal string `gh`, `issue`,
`pr` or `api`, fail with repository-relative path and line number unless the
path is `nightshift/forge/github.py` or exactly one of:

- `nightshift/queue.py`
- `nightshift/land.py`
- `nightshift/factory_land.py`
- `nightshift/arm.py`
- `nightshift/outcomes.py`
- `nightshift/preflight.py`
- `specloop/cli.py`
- `specloop/critic.py`

The check applies to assigned/returned lists as well as inline call arguments,
including lists with starred tails. Syntax/read failures fail the test. It
does not import modules, invoke gh, scan test fixtures or use path-prefix
exceptions. Fixtures exercise all four literal prefixes, multiline lists,
assignments, allowed exact paths, near-miss paths, ignored comments/strings
and syntax errors. This is a deliberately limited syntactic tripwire, not
whole-program dataflow analysis. The implementation must not evade it.
`factory_land.py` is deferred to #130 along with landing; no migration here.

Add a small root README layout entry naming the protocol/records, GitHub
backend and retained queue transport, with issue #126 as the tracking reference.
Explain that the boundary is staged and does not enable Gitea enrollment.
Do not add Markdown parsing tests or alter the parallel engine guide.

### Verification and task boundaries

`tests/test_forge_github.py::test_github_forge_emits_current_argv` is a
parameterized recording-runner table covering every public operation above.
Each case asserts the entire ordered list of calls, not prefixes or subsets;
include zero-call blocker input, all three feedback endpoints, multiple label
pairs, issue/PR comments, number/URL targets and exact body bytes. Expected
argv are independent literals captured from current code, never generated
from backend constants. Assert delegated helpers are actually called with the
selected runner as well as retaining their transcripts.

Add `test_late_bound_runner_still_patchable` covering imports before patching,
two successive default-runner patches and explicit injection for intake,
readiness and daemon recovery. Test protocol conformance, frozen records,
unchanged identity for direct failures and exceptions escaping helpers,
existing helper fallbacks for `RuntimeError`, `GhError` and `Paused`, strict
versus non-strict lookup, malformed CI classification, pause at dispatch
and during retry/reconciliation, and compatibility of existing policy helpers.
All additions live in the two new test files. Reuse existing fixture helpers
where useful; do not rewrite existing assertions or fixtures.

Run `uv run pytest` and `uv run python scripts/check_types.py`; no new errors
or baseline edits are acceptable. Specifically retain the existing runner-form
assertions in `tests/test_specloop_intake.py`,
`tests/test_specloop_label_authorization*.py`,
`tests/test_specloop_background.py`, `tests/test_specloop_approval.py`,
`tests/test_specloop_base_publication.py`, `tests/test_specloop_publication.py`,
`tests/test_owner_qualified_changes.py`, `tests/test_engine_switch_recovery.py`
and all merge-readiness tests. Existing revision, queue and pause tests remain
part of the full suite. Seven tasks separate the contract, direct operations
and distinct migration concerns; each targets at most about 300 changed lines
including its tests. The boundary scan lands last, after all migrated sites.

## Risks / Trade-offs

- Sharing a PR reader could silently widen fields or change their order. Keep
  separate fixed-field operations, despite their similar bodies.
- Response normalization or moving membership/CI parsing could alter failure
  classification. Preserve raw responses at those seams and existing malformed
  data tests. Frozen records do not justify a schema migration.
- Queue delegation temporarily retains provider-specific code and trust logic
  outside the backend file. The explicit allowlist records that debt; duplicating
  or relocating those helper policies would exceed this extraction.
- A blanket runner-error propagation rule would change blocked-draft handling
  and label-read fallbacks. Tests distinguish errors escaping helpers from
  errors they suppress, and preserve daemon recovery's captured-error re-raise.
- Frozen callable defaults and an unguarded backend could bypass monkeypatches
  or pause checks. Late-construction and retry tests cover those failure modes.
- Exact argv tests cannot prove live GitHub behavior. After deployment, a human
  checks the next real drafting cycle on this repository: the issue becomes a
  spec PR, expected labels flip, and readiness posts after normal approval,
  build and publication. Record the issue/PR and observed outcome in the PR's
  post-deploy checklist. This is a human check, not an implementing-agent task
  or permission to deploy as part of this change. The README addition also
  receives a human readability/reference check; local tests make no live claim.
