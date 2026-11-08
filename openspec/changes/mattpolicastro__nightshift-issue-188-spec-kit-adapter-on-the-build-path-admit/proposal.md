## Why

The native spec-kit parser (#186) and format seam (#187) have landed, but
`format = "speckit"` still refuses admission. Approved feature directories need
the existing sealed build, evidence, publication and completion workflow without
conversion to OpenSpec. This is the build-path portion of #184, following the
house rules in `docs/specloop/speckit.md` and current main at `5284294d`.

## What Changes

- Select a thin `SPECKIT` adapter over `specloop/speckit.py`; retain OpenSpec as
  the default and retain its paths, validator, archive and publication behavior.
- Route task operations through the manifest-selected format adapter: OpenSpec
  delegates to unchanged `tasks.py`; native phase/T-labelled operations live in
  `speckit.py`. Never infer format or refuse a file from mixed heading styles.
  Admission validates the committed feature, rejects ambiguous identities and
  phase zero, and reports section/scenario counts. Native review rounds discover
  their own numbering through the adapter and preserve keyed replay.
- Feed native files, story-grouped scenarios, plan human checks and task-labelled
  reviewer notes into the existing build/gate machinery. Keep the existing seal
  and workflow publication refusal.
- Publish with spec-kit labels; complete a fully ticked feature with a Status-only
  harness commit, push, then mark the PR ready. Native finish uses neither a
  clone nor `npx`, archives nothing, and retains retry and expected-head guards.
- Add a trusted-manifest example, offline build/recovery tests and scoped operator
  documentation, including the plugin's Finish table and confirmation guidance.

Out of scope: native drafting or feasibility/critic/prompt redesign; edits to
`specloop/propose.py`, `specloop/feasibility.py`, `specloop/critic.py`,
`specloop/prompts.py`, `specloop/evidence.py` or `nightshift/`; front-door format
support; plugin code; a new state schema; consolidated specifications, automatic
supersession discovery, new dependencies or a different OpenSpec executable.

Move the three obsolete blanket-refusal test cases into new
`tests/test_speckit_adapter.py` before updating their native expectations, and
move the revision adapter case into new `tests/test_speckit_revisions.py` before
wiring its hook. The shared source test file receives only these four removals;
no new tests or rewritten assertions go there. The design explains why this
limited relocation is necessary for a passing suite and how to retain concurrent
changes. All OpenSpec finish/publish assertions and all unaffected assertions
remain. The MODIFIED selection requirement retains every existing scenario
name, qualifying native refusal by invalid feature input.

## Capabilities

Modified capabilities:

- `spec-format`: expose the native adapter, extend the task/revision/completion seam,
  and replace the temporary native-format refusal with selection.
- `speckit-format`: retain offline parser contracts while adding native task
  mutation support and Git-backed completion.
- `specloop-interface`: native admission, section evidence/context, publication
  labels and format-aware revision rounds; unchanged run-state schema.
- `specloop-forge`: qualify finish's archive operation by selected format while
  preserving GitHub/Gitea guards and ready ordering.
- `publish-base-sync`: preserve source-local credential forwarding and retry
  semantics for the native completion commit as well as OpenSpec archives.
- `specloop-documentation`: replace temporary unavailability guidance and explain
  native completion, frozen paths and flow-forward context limits.

No requirements are removed. MODIFIED deltas identify the protocol extension,
temporary restrictions and archive-only wording that must change. Engine routing
compatibility remains untouched and needs no delta: OpenSpec task recognition
and mutation retain their current behavior, including literal native headings.
