## Context

`specloop/cli.py:status` discovers sorted run names with `state.json`. `_status_json` loads state, manifest, branch head, tasks, context, and actions inside a single outer exception handler. An exception can discard all accumulated entries; `TypeError`, `AttributeError`, and subprocess timeouts can also escape the current handler. `_status_context` already degrades optional display context independently.

`specloop/state.py:Run._load` constructs a new `RunState` when state is absent, which admission needs but status must not mistake for a successfully read run. Unknown state fields are deliberately ignored for compatibility with older supervisors.

Archive lookup already exists in `_tasks_path`, shared by status and `_finish`. Existing tests cover archived tasks and finish retries. This change preserves those behaviors and adds the requested explicit regression names; it does not reimplement archive recovery.

Consumer shape matters. `nightshift/specloop_client.py` rejects nonzero exits and top-level errors. `nightshift/specloop_attention.py` validates status and pause fields, and `plugins/nightshift/shared/factory.ts` requires an existing status enum, numeric task counts, and a string PR. Returning only `{change, error}` or inventing `status: "error"` would still invalidate the entire Factory snapshot.

No `AGENTS.md` was present in the checkout or its ancestor directories. Repository guidance was read from `README.md`, `CLAUDE.md`, and the engine documentation referenced there.

## Goals / Non-Goals

Goals are to keep healthy repositories observable after another run throws, identify the failing run in JSON, preserve healthy entries exactly, and keep status read-only. All specified behavior must be testable with temporary run directories, local Git repositories, and injected failures, without agents, credentials, GitHub, launchd, or Paseo.

Non-goals are run repair, persistent error states, new automatic retries, transactional finish, changes to the text interface, new consumer production code, and proving that every damaged directory will remain discoverable. Existing discovery continues to skip directories without `state.json`; a state file lost after discovery is a per-run failure.

## Decisions

1. **Use strict state reads only where requested.** Add keyword-only `require_existing=False` to `Run.__init__` and `Run.for_change` in `specloop/state.py`, and retain it for reloads. `_load` reads `state.json` directly, catching only `FileNotFoundError` for the default new-state behavior. With `require_existing=True`, absence raises; malformed JSON, malformed known structures, and permission errors always propagate. JSON status opts into strict reads. Existing callers keep admission semantics and unknown fields continue to be ignored. Do not catch corruption and manufacture a default running state.

2. **Put the recovery boundary around one complete run.** Extract the current successful entry construction into `_status_entry(name)` in `specloop/cli.py`. `_status_json` catches `Exception` around each call, including state construction, manifest/roles reads, Git operations, task parsing, and action generation. Append either the completed entry or a fresh error entry exactly once, then continue in sorted order. Do not catch `BaseException` (`KeyboardInterrupt` and `SystemExit` must propagate). Keep the existing optional-context fallback; a missing proposal alone is not a core run error.

3. **Use a compatible, explicitly synthetic error row.** For a failed run, return the following fields, with `error` containing the exception class and message, bounded to 500 characters (the class supplies a nonempty fallback). Do not attempt more disk reads to enrich the row or reuse partial values:

   ```json
   {
     "change": "broken-change",
     "status": "paused",
     "pause": {"reason": "status_error", "task": "", "detail": "Run status could not be read. Inspect specloop status --json for details.", "auto_resumes_remaining": 0},
     "tasks": {"done": 0, "total": 0},
     "head": "",
     "pr": "",
     "context": {"summary": "Run status could not be read.", "repo": "", "branch": "", "task": null, "detail": "Run status could not be read. Inspect specloop status --json for details.", "recent": [], "github": ""},
     "error": "FileNotFoundError: ..."
   }
   ```

   `paused` and zero counts are compatibility placeholders, not claims about execution or completed work; `error` makes that distinction explicit. Never persist this synthetic pause. Omit `action` entirely, so consumers cannot offer a mutation based on unreadable state. Keep raw exception details in `error`; existing Factory projection drops that extra field and displays generic context. Healthy rows do not gain an `error` field.

4. **Separate per-run errors from command failure.** All-run queries return one `{version: 1, supervisor: {state: ...}, runs: [...]}` document and exit 0 even if every discovered run fails. A named admitted run that fails also returns that envelope with one error row and exit 0. Validate the named change and check for absent state before entering per-run collection: an unsafe name or a named change with no state still produces the existing top-level error and exit 1. If a state file disappears after that check or after all-run discovery, strict loading turns it into an error row. Put discovery and supervisor lookup inside the document-level JSON error boundary; their exceptions yield one top-level version-1 error document and exit 1. An absent runs directory still means an empty successful list. Never mix diagnostic prose into stdout.

5. **Preserve archive and read-only semantics.** Continue to resolve task paths from the actual branch head through `_tasks_path`: live tasks win; otherwise select the existing date-prefixed archive for that exact change. A successful archive read reports real counts and head, the saved `human_checks` pause and PR, and the existing finish/abort actions, with no `error`. If both live and archived tasks are unreadable, use the error row. Status never saves state, appends events, acquires a mutating lock, changes refs, loads credentials, pushes, or invokes `finish`.

6. **Exercise failures through the CLI and existing consumers.** Replace the old all-or-nothing expectation in `test_unreadable_run_returns_one_error_document`. Add `test_status_reports_broken_run_and_keeps_others` in `tests/test_specloop_status_json.py`, with healthy runs from distinct temporary repositories before and after the broken name. Parameterize malformed state JSON/structures, missing or invalid manifests, missing Git refs/tasks, permission errors, and Git timeouts. Check exact healthy entries, ordering, exit codes, valid single-document output, and unchanged state/events/refs. Add state loading tests alongside existing unit tests, including strict absence, default admission, unknown fields, and unhandled control-flow exceptions at the status boundary.

   Add `test_status_after_partial_finish` using the local Git and archive stub patterns in `tests/test_specloop_act.py`: invoke the real finish transition, fail the external push after the archive commit, then query named and all-run JSON with another healthy run present. No network or installed OpenSpec CLI is required for this regression. Preserve existing finish-retry coverage.

   Add compatibility tests through `nightshift.specloop_client.call`, the attention parser, and the injected executor in `plugins/nightshift/tests/factory.test.ts`; the mixed list must be accepted, healthy rows must survive, and the error row must offer no action. Production consumer files stay unchanged. Run the focused Python tests and plugin tests, then `uv run pytest` as the definition-of-done gate.

## Risks / Trade-offs

- Catching `Exception` at the individual-run boundary also contains programming defects. The nonempty diagnostic keeps them visible while preserving unrelated rows; control-flow exceptions still terminate normally.
- Synthetic paused/zero-count fields satisfy existing readers but can be misread by consumers that ignore both `error` and context. This is an explicit compatibility compromise; no persisted pause or execution state changes, and no action is offered.
- Existing consumers discard the raw extra error field; the generic context keeps the row understandable without requiring a new UI or exposing raw Git diagnostics there.
- This handles exceptions, not hung I/O, arbitrary invalid values that never raise, every possible discovery race, or a filesystem failure affecting the whole runs root. Existing Git timeouts and document-level error handling remain relevant.
- CI cannot prove every way a real run directory can be damaged. The pull request's human checklist is to inspect an already damaged or safely copied real run alongside a healthy repository, confirm daemon polling and Paseo Factory refresh still show healthy work, and confirm the damaged row has no action buttons. Do not deliberately damage a live run. Host integration and visual presentation are the only checks requiring the actual application; they are not implementing-agent tasks.
