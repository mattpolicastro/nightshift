## Context

`propose_pending` in `nightshift/specloop_intake.py` joins the title, body and trusted comment bodies with blank lines. It retains that idea in the proposal receipt. `revise_pending` combines the saved idea (or PR title/body fallback) with current review feedback before invoking `specloop propose --from-branch`. Both currently put the text in `--idea`.

`_start` either calls an injected synchronous runner or delegates to `specloop_job.start`. The latter writes a request beneath `<receipt-stem>.jobs/<job-id>/` and launches a child that may outlive the poll or daemon. `specloop/cli.py` already accepts `--idea-file` and reads it before drafting; no CLI extension is necessary.

Existing intake, engine-routing and spec-revision tests inspect `--idea` directly. Background tests already provide a controllable local fake CLI and simulated daemon restart. These boundaries can prove the change without GitHub, launchd, a provider or the host application.

## Goals / Non-Goals

Goals:

- Make process argument size independent of issue and review text length.
- Preserve complete text, including Unicode, blank lines and a leading dash.
- Keep each file available and unchanged until asynchronous consumers have read it, including across polls and daemon restarts.
- Keep the change within `nightshift/specloop_intake.py` and relevant tests.

Non-goals:

- Change trust rules, eligibility, dependency gating, prompt composition or delivery semantics.
- Remove the existing receipt `idea` field, migrate old receipts, change the job protocol, or change the CLI's public `--idea` support.
- Add cleanup, size limits, truncation, new configuration, or changes to admission and build-stage revision.

## Decisions

### Prepare the file at the common intake launch boundary

Extend private `_start(path, receipt, args, *, runner, launcher)` with an optional keyword `idea: str | None = None`. Both `propose_pending` and the spec-stage branch of `revise_pending` supply `idea=idea` and omit `--idea` from their argument lists. Before either runner branch executes, `_start` writes the supplied string and appends `--idea-file` and its absolute path. Other callers omit the keyword and retain their existing arguments.

Use `(path.parent / f"{path.stem}.jobs" / f"idea-{uuid.uuid4().hex}.txt").resolve()` as the path. Create the parent with `parents=True, exist_ok=True`, and write with `encoding="utf-8"`, without adding a newline or changing the assembled text. Close the file before dispatch. This is the intake job directory: payload files sit beside the launcher's UUID subdirectories, whose allocation stays private to `specloop_job.start`. This layout avoids a launcher API change and works for synchronous runners too.

For example, initial issue 7 uses `proposals/owner__sample/7.jobs/idea-<uuid>.txt`; revision PR 8 uses `revisions/owner__sample/8.jobs/idea-<uuid>.txt`. The CLI receives one absolute path argument even if it contains spaces. The persisted background request contains that same path, not the idea text.

### Retain immutable invocation inputs

Allocate a fresh filename for every actual drafting launch, including a new answer/review round or retry that starts a new job. Do not overwrite an earlier invocation's file or unlink it when `_start` returns. Existing running-job collection and completed-result delivery bypass `_start`, so repeated polls do not create payloads or launch another draft. Retain files alongside the existing job history; cleanup is outside this change.

Keep the proposal receipt's `idea` and `input` values unchanged. Spec revisions still recover the original idea from receipts, including receipts created before this change. No new receipt field is required because the durable job request records the file path.

If directory creation or writing fails, propagate the error through existing intake error handling before invoking the runner or launcher. Do not fall back to argv, report a draft result, or perform successful-draft delivery. Existing daemon failure reporting continues to apply. A failed write may leave an unreferenced partial file; it cannot be consumed because dispatch happens only after a successful close.

### Verify behavior at existing boundaries

- In `tests/test_specloop_intake.py`, add the exact regression name `test_long_issue_thread_drafts_via_idea_file`. Construct at least 1 MiB of assembled title/body/trusted-comment text with distinct beginning/end sentinels, Unicode and multiple comments. At the runner boundary, assert exact UTF-8 file content, an absolute path under the issue's job directory, one `--idea-file`, no `--idea`, and no embedded payload in argv; return a drafted result and assert normal delivery. Do not depend on a platform-specific `ARG_MAX` failure.
- Cover an issue title beginning with `--`, preservation of comment order and trust filtering, re-armed rounds with distinct immutable files, and an injected write error that prevents dispatch. Update existing assertions in this file and `tests/test_engine_routing.py` to read the file while preserving their content and lifecycle checks.
- In new `tests/test_specloop_idea_file.py`, add the delayed-background-read test with a local fake CLI that reads the file after its release checkpoint. Importing fixtures and helpers from `tests/test_specloop_background.py` is allowed; do not edit that file, which the #8 kill-switch build is extending. Exercise a relative state directory containing spaces, return from the first poll, clear in-memory child tracking as in existing restart tests, poll again, then release the reader. Assert it receives the original complete text and delivery occurs once. Use existing bounded waits and process teardown.
- In `tests/test_specloop_spec_revision.py`, inspect the file instead of `--idea` and cover large review feedback, the original-receipt and PR-fallback paths, unchanged `--from-branch`, and a later feedback round retaining the previous payload unchanged.
- Mark tests with the scenario IDs from this change. Run the focused tests with each implementation task and finish with `uv run pytest` green.

Human checklist: none. All new behavior is observable through local files, injected runners and the existing controlled subprocess fixture; no host-application or live-service check is required.

## Risks / Trade-offs

- Files add disk usage to an already retained job history, and receipts still duplicate the original idea. Keeping them avoids races with detached readers; retention policy is separate work.
- File creation introduces a filesystem failure point. Failing before dispatch preserves the existing error/retry flow instead of silently losing text.
- An absolute path is necessary because background execution may use a different working directory. Unique filenames prevent later rounds from replacing an earlier consumer's input.
- This removes payload-size pressure from argv, not downstream model context or memory limits. It does not promise that arbitrarily large ideas can be drafted by a provider.
