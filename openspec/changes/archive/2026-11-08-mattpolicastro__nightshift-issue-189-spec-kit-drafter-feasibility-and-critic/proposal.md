## Why

#188 supplies native spec-kit build operations, but proposal generation and
pre-admission reviews still describe OpenSpec. Build prompts depend on temporary
OpenSpec-named copies of native files. Feature-sized plans also encounter a
whole-change task-count recommendation intended for smaller OpenSpec changes.

This completes the drafting/review portion of #184 after #188 (approved design
PR #197), preserving the per-task session ceiling while sizing native features
by user story.

## What Changes

- Select native drafting, feasibility and critic wording using `m.format`;
  preserve every existing OpenSpec prompt byte.
- Draft `spec.md`, `plan.md` and initial `tasks.md` from local templates and
  house rules; revise an identified, undelivered feature in place and route
  spec clarification markers through the existing questions outcome.
- Use adapter validation, scenario discovery, task parsing and confinement.
  Exclude native specification references from shared planned-file extraction.
- Review the constitution and explicitly named Complete features. Report
  unacknowledged changes to their guarantees through the existing verdict schema.
- Select native implementation/review/gate/arbiter wording and remove #188's
  context aliases, retaining the original native file names and bytes.
- Recommend six tasks or fewer per user story; report more than eight tasks in
  one story phase as an advisory risk, never the native whole-feature total.
- Correct drafting and support documentation, preserving the OpenSpec drafting
  passage. Preserve existing assertions except the explicitly obsolete native
  alias destinations that this change must migrate.

Out of scope: all `nightshift/` edits, daemon/front-door identity or delivery,
new agents, schemas or state fields, adapters' revision/coverage append mechanics,
new Markdown parsers, automatic history/supersession traversal, template
installation, OpenSpec prompt changes, and changes to the 300-line task ceiling.

The only necessary overlap with #182 is the native-spec-kit context bullet in
`KNOWN-LIMITATIONS.md`: its unsupported-drafting/feasibility claims become false.
Leave its readiness material and all five listed concurrent code/test files
untouched.

## Capabilities

- `spec-drafting`: native artifact and question handling; MODIFIED risk-based
  sizing with the OpenSpec target retained and a native per-story target.
- `spec-critic`: native feasibility/critic read contract; MODIFIED advisory
  sizing scoped by format.
- `spec-format`: MODIFIED native context destinations and drafting task routing;
  native build prompt selection with OpenSpec byte compatibility.
- `in-flight-file-map`: exclude native specification tokens in the shared helper.
- `specloop-documentation`: native drafting guidance and MODIFIED support claims.
- `speckit-preset`: MODIFIED README support status, retaining authoring assets.

