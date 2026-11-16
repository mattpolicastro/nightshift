## Context

The attention branch of `nightshift/cli.py` currently refreshes worker outcomes
before reading `specloop_attention.snapshot()`. Historical GitHub refresh uses
a 40-second shared deadline and eight-second request limits. Specloop status has
its own eight-second limit. Both use `~/.nightshift/outcomes.sqlite3`.

The plugin's `server/attention.ts` executes the CLI and delegates cache lifecycle
to `server/reader.ts::createReader`, whose projection only exposes worker tasks.
The page loads offline first, then online, each active minute and on foreground.
Factory already supplies pause detail and actions, including #79's Land support.
That dependency is present in this checkout and remains a rollout prerequisite.

Relevant attention, worker-retirement, documentation, host-path, CLI, forge,
migration and Factory requirements were reviewed. No AGENTS.md was found in this
checkout or its ancestor directories.

## Goals / Non-Goals

Show open front-door escalations and compact Factory references; distinguish
confirmed empty listings from unavailable reads; preserve version-1 field types,
pause eligibility, historical APIs and bounded refreshes.

Do not change Factory components, list/filter behavior, actions, daemon escalation,
labels, retry policy or Slack configuration. Do not migrate/delete history or
change forge adapters. Existing GitHub attention boundaries remain: every
enrollment gets a status, but non-GitHub enrollment reports unsupported/stale
without querying a same-named GitHub repository. This is not new forge support.

## Decisions

### 1. Collect listings separately from summaries

Add `outcomes.front_door_snapshot(cfg, *, offline=False, runner=None, clock=None)`
and private collection helpers in `nightshift/outcomes.py`. Read `cfg.repos`,
`cfg.forge_for(repo)` and `cfg.labels.needs_human`. Retain historical `record`,
`tasks`, `refresh`, `attention` and `render` unchanged for their callers.

For implicit or explicit GitHub-kind enrollment, use the deadline-bound
`_read_github` seam with literal `gh api` GET arguments. Fully paginate
`repos/<owner>/<repo>/issues?state=open&labels=<encoded-label>&per_page=100`
with `--paginate --slurp`; this endpoint returns both issues and PRs, distinguished
by `pull_request`. Validate all pages and required row fields; a failed or
malformed page is a failed listing, never an empty successful listing. Do not
reuse intake's fixed 100-PR limit. There are no remote writes or receipt reads.

Each entry contains `repo`, positive integer `number`, `kind` (`issue` or `pr`),
`title`, `url`, nullable `summary`, nullable `summary_checked_at`, boolean
`summary_stale`, and nullable `summary_error`/`summary_error_code`. Sort repositories
by configured name and entries by number then kind. Validate links as exact
HTTPS github.com repository paths ending in `/issues/<number>` or
`/pull/<number>`, without credentials, query or fragment.

Commit each complete listing before enriching its summaries. Reuse cached summary
fields only for matching item identities in that repository/forge/label cache.
New items start with unavailable summaries until checked. Closure or label removal
removes an entry on a successful complete listing, even if a remaining item's
comments fail. Listing failures preserve previous membership and listing time.

### 2. Recognize escalation comments by physical lines

Fetch all pages of each item's conversation comments through the issues-comments
endpoint; PR reviews and inline comments are not escalation delivery channels.
Split bodies on LF, remove one terminal CR per line and only trailing empty lines.
The first physical line must be exactly `**nightshift** — ` plus one of:

- `The spec draft needs human input.` with action `draft`.
- `Admission refused. Human input is needed.` with action `admit`.
- `Revision needs human input.` with action `revise`.
- `Publication refused. Human input is needed.` with action `publish`.
- `Specloop <stage> failed. Human input is needed.` with action `<stage>-error`;
  stage is a nonempty ASCII `[A-Za-z0-9_-]+` token.

The final remaining line must be exactly
`<!-- nightshift:<action>:<identity> -->`, with the matching action and a nonempty
identity containing no whitespace or angle brackets. Colons in existing round
and attempt suffixes are allowed. Do not trim indentation or interpret Markdown
fences, HTML blocks or quoted examples. Mismatched physical boundaries refuse
summary evidence; this is display recognition, not an authorization mechanism.

Select the qualifying comment with greatest parsed `updated_at`, falling back
to `created_at` only when updated time is absent, then numeric comment ID.
Updates count because delivery comments are upserted. Ordinary discussion and
success comments never displace an escalation. Malformed recency on a qualifying
comment makes only that item's enrichment fail. Render its first physical line
as plain text, including the literal prefix, capped at 400 characters including
a trailing ellipsis on truncation.

A successful complete comment read replaces the summary (null when no comment
qualifies), advances `summary_checked_at` and clears its error. A failed,
oversized, incomplete or deferred comment read retains the item's prior summary
and summary timestamp, marks `summary_stale` and supplies a safe diagnostic.
Without a retained summary, display “Summary unavailable”. Null after a successful
read simply means no recognized escalation comment and needs no warning.
Neither case hides the item or rolls back a successful listing.

### 3. Use a small bounded last-good cache

Add one `front_door_snapshot` table keyed by repository, selected forge identity
(kind/name/URL where configured) and configured label. Store entries with their
summary metadata, listing `checked_at`, and nullable listing error/code. Do not
alter historical tables or columns. Only enrolled keys enter the response; a
changed forge or label does not borrow a different key's data.

Use one monotonic 40-second deadline for all GitHub listing and comment reads.
Each subprocess gets `min(8 seconds, remaining time)`. Use at most four concurrent
repository reads and no unbounded executor queue. Attempt listings before
enrichment so a long thread cannot consume another repository's listing turn.
Within each phase use stable repository/item order and stop starting requests at
the deadline. Full pagination must finish within the same bounded call.
Repositories whose listing was not completed retain their last good listing
marked stale; items whose enrichment was not completed retain summaries marked
stale. Specloop retains its separate eight-second read; plugin limits stay intact.

No 32-repository cap, durable attempt order, attempt identities or newer-started
wins protocol is introduced. Short SQLite transactions commit complete listings,
then individual summary results; no transaction spans network work. Summary
updates apply only to items still present in that cache key. Concurrent CLI
invocations use ordinary committed cache writes, without an ordering guarantee
between independent refreshes; the plugin already shares its in-flight read.

Use bounded safe messages with codes `read_failed`, `timeout`,
`invalid_response`, `budget_exhausted`, `unsupported_forge` and `not_checked`;
never persist raw credential-bearing stderr for display. Repository `stale` is
true for a listing error or any item enrichment error. Repository error fields
report the listing failure first, otherwise a summary-failure diagnostic;
entry fields identify affected summaries. Listing `checked_at` advances on a
successful listing even when summary enrichment fails. Recovery clears errors at
the successful operation's boundary.

Offline reads fetch no GitHub or status data, advance no timestamps and retain
stored errors. A cold repository is `not_checked`, with null listing time and a
visible stale diagnostic. Return `[]` before opening the new cache when
`cfg.repos` is empty; this avoids incidental state creation for empty-enrollment
read callers, including `test_reads_never_migrate`.

### 4. Compose version-1 CLI output and preserve historical APIs

In `nightshift/cli.py` replace attention's historical calls with the new provider.
Retain `version`, `offline`, `tasks`, `stale`, `specloop` and `specloop_error`
with their existing types. Legacy `tasks` and `stale` are always empty arrays.
Add `repositories`: an array of
`{repo, label, entries, checked_at, stale, error, error_code}`.
The internal forge cache key need not cross the display boundary.

Return 1 for any current repository/listing/summary or specloop failure, otherwise
0 even when human waits exist. Configuration and usage behavior remains unchanged.
Historical worker errors affect neither output nor exit status. Text shows
title, Issue/PR kind, link, optional summary and visible stale diagnostics even
for repositories without entries. Preserve the existing offline banner.

In `nightshift/specloop_attention.py::render`, show one logical line per pause:
`[STALE ]<change> — <reason> · See Factory: <change> [· <pr>]`.
Retain the existing count/error lines, pause selection, reason/PR metadata,
allowance validation, alias invocation and snapshot behavior. The JSON specloop
entries do not change.

The attention delta explicitly supersedes only the worker-results preservation
clause. Worker-retirement deltas supersede historical attention projection and
attention-triggered reconciliation promises, preserving historical storage,
explicit `outcomes.refresh()` resolution/stale behavior, mirror utilities and
durable leftover-claim recovery.

### 5. Adapt the existing plugin reader

Keep `server/reader.ts::createReader` as the sole cache lifecycle. In the allowed
`plugins/nightshift/server/attention.ts`, wrap its execute seam with a small
display adapter: validate the CLI version/mode and new fields, project the new
display payload, and pass a normalized legacy envelope with empty `tasks` and
`stale` to `createReader`. Combine its returned cache metadata with the most
recent successfully validated display projection. Publish that projection only
after validation, and clear it on disposal; failed reads keep the prior projection
and the reader's original fetched time/error. Do not create a second throttle,
in-flight promise, fallback cache or reader lifecycle.

Extend `shared/attention.ts` additively with repository and specloop display
fields. Accept CLI exit 1 as structured partial/stale data and expose safe current
source errors instead of the reader's worker-outcome wording. Retain transport
fallback wording. A legacy version-1 producer lacking `repositories` shows a
small front-door-unavailable/update diagnostic and no worker rows; supplied
malformed fields fail through existing fallback. No broader compatibility layer
is needed.

Preserve the production installation check, configured executable/cwd/PATH,
literal argv, 15/60-second timeouts, 2 MiB cap, 30-second throttle, shared loads,
redaction, URL safety and child cleanup. Validate escalation destinations against
the exact repository, kind and number before exposing them. Only display fields
cross RPC; comments remain plain text and local paths/raw errors stay private.
Legacy reader unit tests remain applicable; production adapter tests exercise
the new fields through that same lifecycle.

### 6. Render escalation rows and Factory pointers

In `client/main.tsx`, replace worker groups/counts with repository escalation rows
showing title, kind, repository/number, summary and `openExternalUrl` links.
Show listing and summary diagnostics with their distinct last-success times.
Retained summary text remains visible with a stale notice; cold enrichment failures
say “Summary unavailable”. Keep unavailable repository notices even without rows.

Render pauses as one compact, naturally wrapping change/reason line:
`<change> — <reason> · See Factory below: <change>`.
The exact change name lets the user find a row through existing Factory search.
Do not change Factory props, filters, scroll behavior, controls, timers or RPCs.
A fully checked, error-free empty snapshot may say “Nothing needs your attention”;
cached-only, incomplete or unavailable data must not imply a fresh all-clear.
Ignore legacy worker payloads even when supplied by an older producer.

Test collection with injected runners, clocks and isolated databases. Test the
production adapter and rendered page with mocked SDK/native components, preserving
existing Factory assertions. Keep these Python test names exactly:

- `test_attention_lists_needs_human_issues_and_prs`
- `test_attention_unreadable_repository_is_stale_not_empty`
- `test_attention_points_paused_runs_to_factory`
- `test_attention_no_longer_renders_worker_outcomes`

### 7. Migrate only superseded assertions and guidance

With CLI integration, update the attention portions of
`tests/test_daemon_retirement.py::test_one_shot_recovery_then_public_attention`
and `tests/test_outcomes.py`. Add `repositories` to exact JSON-key expectations,
require empty legacy arrays, and supply isolated listing/comment fixtures for
current-source outage/recovery assertions. Move historical IDs/reasons to direct
`outcomes.tasks()` checks and historical resolution/failure assertions to explicit
`outcomes.refresh()` calls. Compare historical event/observation rows before and
after attention, including an empty recovered listing; do not compare changing
cache database bytes. Preserve all recovery cases, label/journal/claim/worktree/
preserved-file checks, forbidden execution/cleanup guards, exact status argv and
timeout, stale timestamps and offline no-read assertions.

With pause rendering, adjust only worker text/array expectations in
`tests/test_specloop_attention.py`; preserve all allowance, reason/PR, alias,
timestamp, error and offline assertions. Direct historical render/API, mirror
and standalone recovery coverage remains. Keep
`tests/test_migration_activation.py::test_reads_never_migrate`'s unchanged-state
assertion, migration/scheduler guards and parameterization: the empty-enrollment
fast path permits its isolated snapshot stub to remain sufficient; update its
obsolete `outcomes.tasks` stub to the new provider if needed. No skips or xfails.

Update `README.md` and `plugins/nightshift/README.md`, plus only false passages
in `docs/specloop/README.md` and `config.example.toml` under the owner's review
scope amendment. Replace historical-attention and scheduler promises with current
sources, separate listing/summary freshness and retained historical APIs. Preserve
TOML values, enrollment, labels, migration, Factory and unrelated rollout checks.
Documentation checks, if needed, select a unique exact column-zero heading through
the next physical same-or-higher heading matching `^#{1,6} +\S.*$` or EOF;
missing/duplicate boundaries block. Do not semantically parse fences, HTML or
indentation, or require exact explanatory prose.

Acceptance for the implementation is `uv run pytest`, plugin `npm test` and
`npm run typecheck` using the documented isolated plugin build copy. The draft's
validation is OpenSpec strict validation; it does not establish implementation
test success.

Human checklist for the implementation PR, at the next real escalation:

- With Slack off, observe a real drafter escalation on the page within one eligible
  online refresh after the shared throttle, with the correct link and headline.
- On a connected phone, check wrapping, stale messages, link opening and the compact
  pointer, then find the named Factory row. Browser fixtures do not prove this.
- Review changed operator prose and live issue/PR references. Keep unrelated
  retirement rollout checks pending unless independently performed.

## Risks / Trade-offs

- A failed comment thread may leave a stale summary indefinitely, but never hides
  new listing members. Large listings that cannot complete within the budget keep
  their last good membership and an explicit diagnostic.
- Stable bounded collection has no cross-refresh fairness or concurrent-attempt
  ordering guarantee. This accepts a simple display cache in place of a scheduler;
  the 40-second deadline wins over completeness.
- Old clients retain version-1 types but cannot show additive escalation data.
  Deploy CLI/plugin together; historical callers keep their explicit APIs.
- Non-GitHub attention remains visibly unsupported. Future forge discovery is
  separate work, not a fallback to the wrong repository.
- Exact comment recognition can omit future formats. The label still makes the
  item visible; recognizing a display headline grants no authority.
- The plugin's existing database installation check remains. No new setup flow
  or security framework is introduced.
