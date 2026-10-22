## Context

`nightshift/specloop_attention.py::_entries` validates `specloop status --json`
before replacing the SQLite snapshot, but selects pauses using a fixed human
reason set. The daemon deliberately consumes the CLI rather than importing
specloop internals.

`specloop/serve.py::AUTO_RESUME` permits unlimited `usage_limit` resumes and
two `infra` resumes per task. `lift_due` also requires a nonzero `resume_at`.
For infrastructure pauses with no corresponding task state, the supervisor
does not enforce the cap. `TaskState.auto_resumes` records consumed resumes;
the current status pause object exposes only reason, task, and detail.

The user explicitly approved including the status JSON addition in this
change, so it is delivered before the attention consumer update.

## Goals / Non-Goals

Goals: surface pauses requiring intervention, avoid classifying an automatic
wait as human merely because its scheduled time has arrived, keep the CLI
boundary, and test producer and consumer behavior without Paseo or a live
supervisor. Keep the implementation small enough for the tasks below.

Non-goals: change automatic-resume policy, infer supervisor health, execute
resumes, change persisted state, redesign attention output, or change Paseo
sorting, actions, or wording.

## Decisions

### One additive status field

In `specloop/cli.py::_status_json`, add `pause.auto_resumes_remaining` to every
non-null pause for both all-run and named-run queries. Keep `version: 1`, all
existing fields, and `pause: null` for runs without a pause. The new field is a
nonnegative integer or JSON null; null means unlimited, never unknown.

Compute it using the existing `serve.AUTO_RESUME` policy and a read-only
`st.tasks.get(pause.task)` lookup:

| Pause condition | Value |
| --- | --- |
| Reason not in `AUTO_RESUME`, or `resume_at` is zero/unset | `0` |
| Scheduled reason has an unlimited cap | `null` |
| Scheduled capped reason has no matching task state | `null`, matching `lift_due` |
| Scheduled capped reason has task state | `max(0, cap - task.auto_resumes)` |

Thus scheduled infra pauses with counts 0, 1, 2, and 3 report 2, 1, 0, and 0.
Use the policy constant rather than duplicating the cap. Do not call
`RunState.task`, which would create missing task state, or save state or events.
Do not compare the deadline to the current clock: an overdue pause with
remaining resumes still belongs to the supervisor. A stopped supervisor does
not change this classification. No additional timestamp field is needed.

### Selection at the existing CLI boundary

Replace the fixed allowlist in `nightshift/specloop_attention.py::_entries`
with a small predicate over the validated pause. Only runs with status
`paused` and a non-null pause can be selected.

- If `auto_resumes_remaining` is present, select exactly when it is zero.
  Positive integers and null indicate automatic recovery and are omitted.
- If the field is absent, select every reason except `usage_limit`. This
  preserves older usage-limit behavior and existing human reasons while
  surfacing `requested`, unknown reasons, and legacy infra pauses conservatively.
- A present value must be null or a nonnegative integer. Reject booleans,
  negative values, floats, strings, arrays, and objects with `ValueError`.
  Validate before replacing any snapshot, using the existing stale path.

The missing-field fallback may show a legacy infra pause that still has
automatic retries. This is preferable to hiding an exhausted pause, and
upgrading the producer removes the ambiguity. Missing and explicit null
must be distinguished. A new reason with a zero allowance is visible without
editing a list; a new automatic reason with a positive or unlimited allowance
is omitted without editing a list.

Keep output entries as `change`, `reason`, and `pr`, with the existing snapshot
metadata. Do not expose the new field in attention entries or read engine
state files directly. Cached entries are already filtered; offline mode
returns them without launching status or recomputing eligibility.

### Verification and host boundary

Extend `tests/test_specloop_status_json.py` using its isolated admitted-run
fixtures. Cover named and all-run queries, the cap boundary, missing task
state, unscheduled pauses, and unchanged state/event files. Update exact
status-document assertions for the additive field.

Extend `tests/test_specloop_attention.py` using its fake subprocess and temporary
ledger. The two required named tests must check both text and JSON, including
change, reason, and PR link. Parameterized cases cover automatic waits,
non-paused runs, unknown reasons, legacy documents, invalid allowance values,
offline reads, stale retention, and recovery. Add matching pytest scenario
markers. Run targeted tests during each task and `uv run pytest` after the
implementation is complete.

Paseo's worker attention reader and Factory section use different projections;
Factory reads specloop status directly and groups paused runs first. Python
tests cannot establish how these rows look in the installed host. A human
must refresh the Nightshift page with exhausted-infra and requested fixtures
or real runs, check recognizable change/reason or explanation and usable PR
links where present, and record any misleading presentation as follow-up.
This change does not promise that the plugin consumes the new allowance or
that its independent action wording changes.

## Risks / Trade-offs

- Older producers lack enough information to distinguish infra budgets. The
  conservative fallback can produce extra attention rows until upgraded.
- The status field describes scheduling policy, not a guarantee that the
  supervisor is alive or will successfully execute another attempt.
- Unscheduled automatic reasons report zero because the current supervisor
  cannot lift them. Missing task state reports unlimited for scheduled infra
  pauses because that is the existing supervisor behavior; repairing it is
  outside this change.
- Invalid allowance data makes the entire read stale, consistent with existing
  validation. Retaining the previous snapshot avoids silently dropping work.
- Paseo can still need a separate presentation fix; its host check cannot be
  replaced by a green Python suite.
