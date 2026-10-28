## Context

`revise_pending` in `nightshift/specloop_intake.py` uses the latest authorized
label event as a spec-stage `round_id`. Rejected and `needs_input` results add
`needs-human` and retain `agent:revise`. Clearing the hold can select another
snapshot under that same event. `_comment` currently identifies both results
with `<!-- nightshift:revise:<change>:<round> -->` and PATCHes the first comment.

Build snapshots already have UUID round IDs. Ordinary failed/crashed keyed
attempts retain their snapshot; replacement selection after a delivered refusal
or explicit stopped-job release creates a new build round. This distinction
must survive the delivery fix. A delivery attempt is not a subprocess job ID.

The current `_save_revision_snapshot` already persists selected feedback before
dispatch, preserves cumulative consumption and unknown fields, and clears
round-local fields. `_collect` retains the receipt when saving a result.
`_finish_delivery` records completion only after notification and label work,
with existing pause and receipt coordination. These boundaries are sufficient;
no job-store or Forge interface extension is needed.

The #126 seam is already available: `_comment` uses `Forge.comment_pages`,
`create_comment` and `update_comment`. `queue._gh` reconciles ambiguous creates
with a terminal marker regex that already accepts an additional colon-delimited
suffix. Its transport and pause guards remain unchanged.

## Goals / Non-Goals

Goals are to preserve each newly produced same-round result in a separate PR
comment, retry one saved result without duplicate creates, and recover older
receipts using their original markers. The fix must work with synchronous test
runners and durable background jobs, including daemon restart.

Non-goals are new label cleanup, fresh-event synthesis, changes to selection,
automatic retry of held work, revised trust rules, build-key deduplication,
transport retries, Gitea enrollment, job schemas or live service automation.
Rejections keep `agent:revise` and add `needs-human` exactly as today. Existing
success and empty-feedback label behavior also stays unchanged.

## Decisions

### 1. Give replacement results a durable delivery identity

Use optional receipt field `delivery_attempt`, a UUID encoded as 32 lowercase
hexadecimal characters. Absence means the implicit initial attempt. This keeps
first-attempt and historical markers byte-for-byte compatible and avoids
rewriting existing build marker assertions.

In `_save_revision_snapshot`, before replacing fields, compare the previous and
new `change` and non-null `round_id`. When both identities match, generate a
fresh `uuid.uuid4().hex` for the replacement's `delivery_attempt`, regardless of
whether the earlier receipt was delivered. Clear the previous attempt field as
part of clearing round-local state; never inherit it into a replacement. A
different logical round uses the implicit initial attempt. A legacy snapshot
without a round ID retains its unqualified marker when recovered; normal new
selection supplies a round ID and therefore already has a distinct marker.

Save the chosen identity atomically with the new snapshot before `_start`, for
both synchronous and background execution. Empty-feedback replacement results
use the same snapshot rule even though they dispatch no command. Do not generate
an identity during comment rendering, collection, acknowledgement or polling.
Do not derive it from the result body, timestamp, job ID or feedback hash:
separately selected attempts can legitimately have identical output.

Retries of a retained snapshot keep its existing field, including ordinary
keyed crash redispatch through `_start`. Existing selection decides whether
work is a replacement; this change does not alter that decision. In particular,
an undelivered refusal with unchanged feedback is still recovered as today,
while the existing changed-feedback replacement path receives a fresh suffix.

Validate a present field in `_validate_revision_feedback`: require a string
matching `[0-9a-f]{32}` and a nonempty string `round_id`. Invalid metadata fails
before receipt replacement, command dispatch or revision completion delivery,
including its completion-comment and label writes. It must not silently fall
back to the initial marker, reset history or mark the receipt delivered.
Preserve the existing receipt error path: `_each` forwards the validation error
to `on_error`, and the daemon's existing item-failure threshold may invoke
`report_failure` to post the stage-error comment and add `needs-human`. Those
failure-reporting writes remain permitted under the existing pause guards;
they neither deliver the malformed revision result nor remove `agent:revise`.
Do not change the threshold, failure marker namespace or daemon behavior.
Missing metadata is valid compatibility state. Preserve the existing feedback
schema version, consumed IDs and all other unknown fields.
An unsuccessful snapshot save leaves the old durable receipt untouched and
dispatches nothing.

### 2. Extend only revision delivery's marker argument

Add optional `delivery_attempt: str | None = None` to `_comment`. Only revision
delivery passes the saved field; all other callers retain current defaults.
The marker formats are:

| Receipt identity | Exact marker |
| --- | --- |
| Legacy, no round | `<!-- nightshift:revise:<change> -->` |
| Round, implicit initial attempt | `<!-- nightshift:revise:<change>:<round> -->` |
| Same-round replacement attempt | `<!-- nightshift:revise:<change>:<round>:attempt-<uuid> -->` |

Thus the marker key is `(change, round, attempt)`, with the initial attempt
encoded by the omitted suffix. Round IDs themselves never change for delivery;
the `--round-id` value and keyed result validation remain untouched.

Emit exactly one selected marker on the final physical line of the outgoing
comment. Lookups continue to use the complete literal token, including ` -->`,
anywhere in a stored body, as existing delivery and transport do. Never search
by round prefix, fall back from an attempt marker to a bare round marker, or
append a legacy alias to the new comment. These are literal byte/string rules,
not Markdown or HTML parsing; fences and indentation have no interpretation.
If new attempt metadata cannot produce a valid marker, block delivery rather
than guess. Do not introduce a new parser or relax the existing transport regex.

The old and new complete markers cannot match each other by prefix because the
closing delimiter is part of the match. An old comment remains unchanged when
a new attempt is posted, while retries update only the matching attempt's
comment. Preserve the existing all-pages lookup and no-blind-POST behavior on
read failure, and route calls through the guarded Forge runner.

### 3. Preserve lifecycle contracts and identify the exact assertion migration

Existing authorization, eligibility, successful-result validation, immutable
inputs, consumption timing, stopped holds, release and pause checks still run
in their existing order. Failed/rejected results consume no selected IDs. A
newer label event remains governed by existing event-bound success delivery.
The implementation adds no rejection label removal and no extra label reads.

The `Spec-stage revision` delta explicitly changes the comment replacement
contract. In `tests/test_specloop_spec_revision.py`, only these conflicting
expectations need migration:

- `test_rejected_revision_explained_without_push`: after the successful second
  proposal, expect two comments and preserve the original refusal body/ID.
- `test_answer_after_lost_needs_human_response_retries_propose`: likewise expect
  two comments and assert the revised commit in the second comment, retaining
  all dispatch, feedback, label and branch assertions.

No existing assertion is deleted to hide a regression. These one-comment and
overwritten-body expectations are replaced because they encode the defect;
their behavioral checks remain and the first result's preservation is added.
All unaffected assertions, including exact first-attempt build markers and
legacy markers, remain unchanged. The `Revision comment retry reconciliation`
delta extends the accepted formats while keeping its existing guarantees.
Other requirements in `pr-revision`, `paused-job-delivery`, `forge-interface`
and `specloop-documentation` remain in force. In particular, stable build round
identity is unchanged: a new build round still initially uses exactly its saved
round marker, and ordinary retries retain it.

### 4. Keep recovery guidance and implementation scope narrow

Add a short `Revision re-runs` bullet to `KNOWN-LIMITATIONS.md` beside revision
recovery limits, citing issue #137. Explain that same-round replacement results
now get distinct comments but rejection does not remove `agent:revise`. To start
a fresh round, resolve the questions/contradictions, remove `agent:revise`, clear
`needs-human`, and have a trusted actor add `agent:revise` again. Reapplying an
already-present label creates no new event. This is not stopped-job release and
does not bypass pause, authorization or other eligibility gates.

Implementation may edit only `nightshift/specloop_intake.py`,
`tests/test_specloop_spec_revision.py` for the two migrations above, new focused
test files `tests/test_revision_result_attempts.py` and
`tests/test_revision_result_markers.py`, and `KNOWN-LIMITATIONS.md`.
Existing test fixtures may be imported without editing their defining files.
The harness owns archiving and spec updates; implementers must not edit
`openspec/**`.

Do not edit these concurrent-work files: `docs/specloop/prepare.md`,
`specloop/loop.py`, `specloop/manifest.py`, `specloop/verify.py`, or
`tests/test_dependency_snapshots.py`. Also leave `nightshift/queue.py`,
`nightshift/forge/**` and `nightshift/specloop_job.py` unchanged: their existing
interfaces and transport are sufficient.

### 5. Verify with local fakes; leave the live cycle to a human

Use existing spec/build fixtures and recording runners. The required
`test_rerun_round_posts_new_result` keeps the same event and round while selecting
another spec attempt, covers `rejected` and `needs_input`, and verifies distinct
comments, current contradictions/questions, the untouched earlier comment and
unchanged refusal labels/consumption. Include identical result text to prove
the attempt is not a content hash. Exercise both synchronous and background
collection paths using existing job helpers.

Cover identity persistence before dispatch, failure to save, successive
same-round replacements and a later new event returning to the initial format.
Use bounded parametrization and existing fixtures to keep each task within
about 300 changed lines.

For malformed metadata, assert no revision command, completion comment,
completion label mutation or receipt mutation. Separately exercise the existing
daemon failure threshold with a recording runner: the stage-error comment and
`needs-human` escalation must still occur, retaining `agent:revise`, receipt
bytes and consumption history. Exercise pause before escalation to prove the
failure-reporting exception does not bypass its guards. Reuse the existing
daemon test helpers without editing daemon code or its existing tests.

`test_old_round_markers_still_recognised` covers unqualified, event-derived and
UUID-round markers against saved receipts without the new field. Verify that
restart resumes the same comment and does not invent an attempt identity.
Test new suffixes through actual `queue._gh` retry/reconciliation with an
injected subprocess runner and no sleeping: a remotely accepted POST followed
by a lost response must find a later-page match and PATCH only that ID. Include
an earlier round-only comment to catch prefix/fallback matching and failed
reconciliation reads to forbid blind POSTs.

Retain existing authorization, pause, stop and build-replay regressions. Add a
focused attempt-qualified pause/restart case that retains the result and suffix
without a new command or premature `delivered` flag. Run the focused tests with
each code/test task and `uv run pytest` for final acceptance; passing tests are
required, not claimed by this draft.

The documentation task is an ordinary local edit/review and existing docs
checks, not a new rich-text parser or exact-prose test. No live GitHub/Gitea,
daemon, provider or host credentials are required for automated validation.

Human checklist for the implementation PR: on the next live rejected revision,
confirm the new reason appears on the PR and the earlier result remains visible;
record the comment link. Check the documented remove/clear/re-add sequence on
the next fresh request and verify it produces a new label event. CI cannot
prove this hosted interaction, and the checklist stays pending until performed.

## Risks / Trade-offs

- The implicit initial attempt keeps old markers and most assertions intact,
  at the cost of two encodings for attempt identity. Tests must distinguish
  missing metadata from malformed metadata and forbid fallback matching.
- More same-round results mean more PR comments. This is intentional history;
  delivery retries still reconcile one comment per saved attempt.
- Missing receipts or restoring an older backup can lose delivery identity.
  This change offers no retrospective reconstruction or new exactly-once
  guarantee beyond the existing transport reconciliation boundary.
- The reported production symptom differs from this checkout's upsert behavior.
  Tests assert separate visible results and preservation, covering both skipped
  delivery and overwritten-history failures.
- No compare-and-remove primitive is available for rejection cleanup. Keeping
  label behavior unchanged avoids adding that race; it does not claim to fix
  pre-existing transport ambiguity in other label operations.
- A late write or unavailable remote read can still leave delivery pending.
  Existing pause guards and error recovery are retained rather than expanded.
