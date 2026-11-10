## Context

`nightshift/cli.py` currently calls `outcomes.refresh()` before
`specloop_attention.snapshot()`. Worker refresh has a 40-second shared network
budget, eight seconds per subprocess, four concurrent checks and 32 selected
records. Specloop status has a separate eight-second limit. Both snapshots live
in `~/.nightshift/outcomes.sqlite3`. `--offline` avoids both remote reads.

The plugin executes the version-1 attention command with a 60-second online or
15-second offline timeout and a 2 MiB output cap. `server/reader.ts` projects only
worker tasks; it currently drops the CLI's specloop entries. Its cache shares an
in-flight promise and throttles refreshes to 30 seconds. `client/main.tsx` loads
offline first, refreshes online, and refreshes each active minute and on foreground.
Factory has separate readers, state, timers and actions and must remain untouched.

The existing `attention`, `worker-retirement`, `host-paths`, `nightshift-cli`,
`forge-interface`, `paseo-runs-page` and relevant `specloop-documentation`
requirements were reviewed. There is no AGENTS.md in this checkout or its ancestor
directories. The #79 Factory landing implementation is already present.

## Goals / Non-Goals

- Make labelled front-door escalations visible without a daemon history record.
- Distinguish confirmed empty results from unavailable or incomplete repositories.
- Keep version 1, pause compatibility, bounded reads and offline snapshots usable.
- Preserve historical evidence and all Factory behavior.

Do not modify daemon comments, labels, escalation/retry policy, Factory components,
forge adapters or `server/reader.ts`. Do not add dismiss, resolve, retry or landing
controls to attention. Gitea discovery is outside this GitHub attention slice:
report such enrollment explicitly as unavailable rather than silently skipping it
or falling back to GitHub. This is a display limitation, not a dispatch change.

## Decisions

### 1. A separate collector inside the allowed outcomes module

Add `outcomes.front_door_snapshot(cfg, *, offline=False, runner=None, clock=None)`
and a private repository collector in `nightshift/outcomes.py`. Read `cfg.repos`,
`cfg.forge_for(repo)` and `cfg.labels.needs_human` from this attention-only caller.
Retain `record`, `tasks`, `refresh`, `attention` and `render` for older
callers and their direct API tests. The new collector never calls those historical
selection or reconciliation functions.

For implicit GitHub and explicit GitHub-kind enrollment, use the existing
deadline-bound `_read_github` runner. A paginated `gh api` GET of
`repos/<owner>/<repo>/issues` with `state=open`, the URL-encoded configured label
and `per_page=100` returns both issues and PRs; distinguish PRs by `pull_request`.
Use `--paginate --slurp` and validate every page and every selected row. Do not use
the intake helper's fixed 100-PR limit. Reject malformed state, labels, number,
title, kind or URL instead of treating them as absence. Fetch conversation
comments for each selected item through its paginated issues-comments endpoint;
PR reviews and inline review comments are not escalation delivery channels.
No GitHub writes, worker refresh, receipt scans or new authorization queries occur.
Direct argv remains confined to `outcomes.py`, already allowed by the forge scan.

Each entry is `{repo, number, kind, title, url, summary}`, where `kind` is `issue`
or `pr`, and `summary` is a string or null. Identity is `(repo, kind, number)`;
sort repositories by configured name and entries by number then kind for stable
display. Validate HTTPS GitHub URLs against the exact repository and
`/issues/<number>` or `/pull/<number>` respectively, without credentials, query or
fragment. Opening entries never changes labels. Closure or label removal removes
an entry only after a complete successful repository read.

### 2. Comment recognition is a physical-line rule

Use existing daemon output from `specloop_intake._comment` and `_deliver`, without
changing it. Split bodies on LF, remove one terminal CR from each line, and remove
only trailing empty lines. The first physical line must begin exactly with
`**nightshift** — ` followed by one of these complete headlines:

- `The spec draft needs human input.`
- `Admission refused. Human input is needed.`
- `Revision needs human input.`
- `Publication refused. Human input is needed.`
- `Specloop <stage> failed. Human input is needed.`, with stage a nonempty ASCII
  `[A-Za-z0-9_-]+` token.

The final remaining line must match exactly
`<!-- nightshift:<action>:<identity> -->`, with action `draft`, `admit`, `revise`,
`publish`, or the corresponding `<stage>-error`; identity is nonempty, contains
no whitespace or angle brackets, and permits existing colon-separated round and
attempt suffixes. Headline and action must correspond. Do not trim indentation,
interpret fences/HTML, or search inside a quoted example. Any mismatch or ambiguous
candidate is not accepted as escalation evidence; keep the labelled entry with
null summary if no valid candidate remains. This is display recognition, not proof
of author identity or authority to perform an action.

Choose the qualifying comment with the greatest parsed `updated_at` (falling back
to `created_at` when absent), then numeric comment ID to break ties. Updates count
because the daemon upserts delivery comments. Malformed timestamp/ID metadata on
an otherwise qualifying comment makes the repository read incomplete; do not guess
which comment is latest. Later ordinary discussion or success comments do not
replace an escalation. Return the first physical line as plain text, including its
literal prefix, limited to 400 characters with a trailing ellipsis when shortened.
Do not render Markdown, HTML or embedded links from the comment.

### 3. Repository snapshots are independent and atomic

Add a `front_door_snapshot` SQLite table without altering any historical table or
its columns. Key by repository name, selected forge identity and label; keep JSON
entries, last successful `checked_at`, `attempted_at`, and nullable error/code.
Only currently enrolled identities enter the response. Changing a label or forge
does not reuse another selection's cache; old rows may remain on disk.

A refresh starts one 40-second monotonic deadline across all repositories, list
pagination and comment reads. At most four repository jobs run concurrently;
each subprocess gets `min(8 seconds, remaining budget)`. Select up to 32 repositories
by oldest attempted time, then repository name, recording failed attempts too so
an outage cannot starve peers. Submit only one wave of up to four jobs at a time;
stop issuing reads at the deadline and never allow executor shutdown to wait for
unbounded requests. Specloop retains its separate eight-second status limit and
the plugin's outer timeouts and output cap remain unchanged.

Replace one repository's entries and successful timestamp only after its complete
listing and all necessary comment reads validate. A comment-read failure is not
“no comment.” Failure, pagination truncation, malformed data or deferral preserves
the previous complete snapshot and timestamp with `stale=true` and an error. A
cold failure has empty retained entries, null `checked_at`, and a visible stale
repository row. Success with zero entries clears the old rows and error. Partial
success updates healthy repositories independently. Reads for unsupported forge
kinds are not attempted and produce an explicit stale repository diagnostic.

Use stable error codes `read_failed`, `timeout`, `invalid_response`,
`budget_exhausted`, `unsupported_forge`, and `not_checked`, paired with safe,
bounded diagnostic text. Do not persist credential-bearing subprocess stderr as
display text. Offline reads issue no GitHub or status subprocess, do not advance
timestamps, and retain stored errors. An enrolled repository without a cached
snapshot reports `not_checked`; no enrollment means an empty repository array.
Concurrent CLI invocations must not let an older-started attempt overwrite a
newer-started result; compare persisted attempt identity when committing.

### 4. Version-1 CLI composition and compact pauses

Retain `version`, `offline`, `tasks`, `stale`, `specloop`, and `specloop_error`
with their existing types. `tasks` and `stale` become empty compatibility arrays:
their historical contents are intentionally retired from attention output, not
replaced with objects of a new shape. Add `repositories`, an array of
`{repo, label, entries, checked_at, stale, error, error_code}`. `checked_at` and
errors are nullable. Existing specloop entries and their exact metadata remain
unchanged; a page derives its pointer from `change`, not a new run identifier.

Return 1 if any current repository or specloop snapshot is stale/unavailable,
otherwise 0, including successful reads containing human waits. Preserve existing
configuration/usage errors. Historical worker errors no longer affect this exit.
Text shows entry title, Issue/PR kind, link and optional summary, and repository
diagnostics even when there are no cached entries. Retain the offline banner's
existing wording for compatibility and explain its broadened meaning in docs.
Render each specloop entry on one logical line:
`[STALE ]<change> — <reason> · See Factory: <change> [· <pr>]`.
Keep the existing count and status-error lines. This preserves assertions about
change, reason and PR while avoiding a second run-detail presentation.

The `attention/Allowance compatibility and snapshots` delta removes only its
worker-results preservation clause. Existing pause selection, allowance
validation, timestamps, status-error rows and stale/offline behavior remain.
The two `worker-retirement` deltas explicitly retire historical list/projection
promises and attention-triggered historical reconciliation, not historical
storage, claim recovery or utility compatibility. Historical observations change
only through an explicit historical API call, never through the new attention
command.

### 5. Production plugin projection stays inside the allowed files

Add an exported, injectable `createAttentionReader(execute, clock)` in
`server/attention.ts` and use it for the production singleton. Leave the legacy
`server/reader.ts` and its tests intact; duplicating its small cache lifecycle here
avoids editing an out-of-scope file. Validate version, refresh mode, source entry
shape and exact issue/PR URLs before caching. Recognize CLI exit 1 as structured
partial/stale data, not a command failure. Project only display fields and safe
error codes/messages; retain home-directory redaction for display strings.

Extend `shared/attention.ts` with optional `repositories`, `specloop` and
`specloopError` fields so existing typed legacy fixtures continue to compile.
Production snapshots always supply them and empty `tasks`/`stale`; never convert
new entries into legacy worker states. A version-1 producer missing `repositories`
is accepted as legacy/unavailable with a visible “Front-door attention unavailable;
update Nightshift on this host” diagnostic, not an authoritative empty list.
Any supplied malformed new field fails the read rather than taking this fallback.

Preserve configured executable/cwd/PATH, literal argv, the current installation
check (including the outcomes database), 15/60-second execution limits, 2 MiB cap,
30-second cache throttle, shared in-flight promise, transport-error fallback and
child cleanup. A malformed refresh keeps the complete last plugin snapshot and
fetched time with a safe error; a cold transport failure is unavailable.

### 6. Page presentation and validation

`client/main.tsx` renders repository escalation rows with title, explicit Issue/PR
kind, repository/number, optional summary and `openExternalUrl` navigation. Stale
repository messages remain visible even without entries; retained rows inherit
their repository's stale status and last-success time. A fully checked empty
snapshot may say “Nothing needs your attention.” A partial, offline-cold or failed
snapshot must not claim that; show “Attention could not be fully checked.”

Human pauses appear as one compact text row per change:
`<change> — <reason> · See Factory below: <change>`. Natural wrapping is allowed on
phones; do not ellipsize away the change identity. This is an explicit textual
pointer, not a new navigation or run-action control. Factory search may hide a
row, so the exact change name lets the operator find it using existing search.
Do not change Factory props, scroll behavior, filters, disclosures, timers or RPCs.
Do not display worker counts, awaiting-merge groups, history notices or worker rows,
even if a legacy producer supplies them. Use existing host theme tokens.

Test through injected subprocess runners, clocks and isolated SQLite databases,
and through the existing plugin rendered harness approach with mocked SDK/native
components. Preserve unaffected assertions. Migrate assertions that pin worker
output, its exact pre-addition JSON key set or its historical-refresh side effect
as described below, retaining coverage through the appropriate public interface.
Add the four requested Python test names, with plugin rendered tests proving the
actual page also lists escalations, reports cold failures and shows only compact
Factory pointers. Run `uv run pytest`, plugin `npm test` and `npm run typecheck`.

The regression migration belongs with CLI integration in task 1.3, not a separate
test-only follow-up. Its concrete boundaries are:

- In `tests/test_daemon_retirement.py::test_one_shot_recovery_then_public_attention`,
  keep all three surviving/missing/unreadable cases and the daemon one-shot,
  label, durable-outcome, journal, claim-byte, worktree and preserved-file
  assertions. Keep the forbidden execution/destructive-cleanup guards, exact
  status argv/timeout, specloop stale/timestamp checks and offline no-read guards.
- Update its exact JSON key set to the six existing keys plus `repositories`;
  assert `tasks == stale == []` on successful, failed and offline attention
  reads. Move assertions about historical issue IDs and reasons to
  `outcomes.tasks()`. Supply isolated paginated repository/comment responses for
  the new collector, separately from the historical `issue view` runner.
  Assert repository entries, errors and retained timestamps for the corresponding
  healthy/outage/recovery phases; do not merely delete the old stale checks.
- Replace its final expectation that attention resolves historical outcomes.
  Compare historical event/observation rows before and after attention, including
  after a repository refresh reports no labelled items, and assert no historical
  mutation. Then call `outcomes.refresh(cfg.labels, runner=...)` explicitly with
  the existing closed-issue fixture and keep the assertion that all historical
  tasks become resolved. Do not compare whole SQLite file bytes: current-source
  snapshot tables legitimately change. Retain the existing preserved-file checks.
- Apply the same separation in `tests/test_outcomes.py`, particularly
  `test_attention_refresh_stale_offline_and_resolution`: preserve historical
  refresh failure, timestamp and resolution coverage through explicit API calls;
  use enrolled repository fixtures for current-source CLI failures. Worker
  deferred checks remain covered through `outcomes.refresh()`, while attention
  tests assert historical errors cannot affect its result. In
  `tests/test_specloop_attention.py`, update worker rows/text and additive JSON
  expectations only, retaining all pause selection, reason/PR, allowance,
  timestamp, timeout, alias and offline assertions. Historical `outcomes.render()`
  assertions remain valid as direct API checks, without requiring that text in CLI
  output. Historical mirror tests and standalone recovery tests remain unchanged.

This is an explicit migration of the superseded attention contract, not permission
to skip, xfail or delete recovery coverage. Use shared fixtures and parameterized
phases to keep task 1.3, including the four named tests and these migrations,
within roughly 300 changed lines. Existing direct historical API tests supply
the unchanged behavior coverage without duplicating their implementations.

Human checklist for the implementation PR, pending the next real escalation:

- With Slack alerts off, observe a real drafter escalation on the page within one
  eligible online refresh (after the shared throttle), with its GitHub link and
  latest escalation headline. Record observation rather than claiming CI proved it.
- On a connected phone, check title/summary wrapping, stale errors, link opening
  and the readability of the Factory pointer, then find that change's existing row.
  Compact rendered fixtures do not substitute for native device validation.

### 7. Keep all active attention guidance consistent

Task 2.3 updates four allowed documents: `README.md`,
`plugins/nightshift/README.md`, `config.example.toml` and
`docs/specloop/README.md`. Limit the extra config and guide edits to attention
and retirement prose. In the example config, replace the promise that historical
outcomes remain available through `nightshift attention` and broaden its
human-pause description to current front-door entries and Factory pointers.
Keep every TOML value and enrollment example unchanged.

In the specloop guide, replace the claim that JSON carries historical worker
tasks with the version-1 contract: empty `tasks`/`stale`, additive `repositories`
and retained `specloop` fields. Explain current-source stale/offline behavior and
historical database/API readability separately. Replace the rollout checklist's
historical-attention check with checks of current attention and retained history
through historical APIs, preserving leftover recovery and independent supervisor
checks. These live checks stay pending; editing their prose does not perform them.
Preserve unrelated enrollment, labels, service prerequisites, migration and
Factory guidance. Documentation consistency can be checked without Paseo; the
real escalation and phone checks above still require a person.

## Risks / Trade-offs

- The 40-second bound wins over completeness. Large repositories or long threads
  may remain stale until a complete read succeeds; the UI must expose that limit.
  Repository-level atomicity avoids publishing a falsely complete partial list.
- `tasks`/`stale` remain schema-compatible but intentionally stop carrying history.
  An old client sees no worker rows and cannot display the new escalation fields;
  update CLI and plugin together. Existing outcome APIs remain independently usable.
- Gitea enrollment is visible as unsupported, not healthy-empty. Adding its bounded
  discovery and trusted display origins requires a separate change to the forge
  and plugin boundaries; this change must never query the wrong GitHub repository.
- Comment recognition may omit unfamiliar or malformed future formats. The labelled
  issue/PR remains visible without a summary; no refusal behavior or labels change.
- Retaining the installation database check preserves current host assertions but
  still requires the existing database before first plugin use. This change does
  not introduce a new installation workflow.
- Keeping a separate production reader avoids expanding the file allowance but
  duplicates cache mechanics; equivalent lifecycle tests guard against divergence.
