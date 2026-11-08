## Context

This is the documentation/preset slice of #184, implementing #185. The current
engine reads OpenSpec; its `specloop/tasks.py` recognizes three-component scenario
references, and `examples/specloop/scenario_plugin.py` reports the IDs attached to
pytest markers. Neither needs modification for the proposed ID shape. Native
spec-kit parsing, admission checks, writes and finish behavior belong to other
issues. Present these as spec-kit authoring rules, not evidence that the current
engine already enforces them.

`nightshift/specloop_intake.py::_change_name` supplies the existing title-slug
algorithm, but its OpenSpec owner-qualified naming remains unchanged. Issue
numbers belong to repositories on both GitHub and Gitea. The recorded operator
answer permits `presets/**` in trusted manifests copied by newly admitted runs;
there is no outstanding allowlist blocker or operator action in this change.

The upstream reference is spec-kit v1.3.2: `presets/scaffold/preset.yml`,
`templates/spec-template.md`, `templates/tasks-template.md`,
`templates/commands/specify.md`, `extensions/git/commands/speckit.git.feature.md`,
and `docs/overview/core.md`. These were inspected in a local v1.3.2 source
checkout; source inspection is not a live installation test. Link to versioned
upstream sources in the preset README so they remain independently reviewable.

The existing `specloop-documentation/Documentation integrity` requirement forbids
test changes in documentation-only refreshes. Its MODIFIED delta retains the
requirement and both scenarios, adding only this issue's explicit exception.
Its historical migration clause describes the preserved baseline, not new
schema or fixture work for #185.
Other requirements in that capability are preserved; no runtime capability is
modified by instructions describing a future format contract.

## Goals / Non-Goals

Give a feature author eight short, reasoned rules and a preset that expresses
them without scripts. Keep normal spec-kit story/task scaffolding and template
resolution, stable scenario evidence, and issue-derived identity. Keep the work
to two documentation-sized sessions in one section, with tests beside their
artifacts and approximately 300 changed lines or fewer per session.

Do not implement engine support, rename existing OpenSpec changes, install the
preset into this repository, add `.specify/` here, override `plan-template.md`,
or claim that static tests prove host-agent command execution. Do not create or
publish a release archive. The only Python edits in implementation are the three
authorized additive tests; all production additions are Markdown and YAML.

## Decisions

### House rules and concrete examples

Create `docs/specloop/speckit.md` with these exact column-zero level-two headings,
each followed by the rule, its reason, and a short example where useful:

1. `## Feature directory and branch`: `specs/<NNN>-<slug>/` and
   `spec/<NNN>-<slug>`. Canonicalize the positive issue number as decimal, pad to
   at least three digits, never truncate. Lowercase the supplied issue title,
   replace runs outside ASCII `a-z0-9` with `-`, strip boundary hyphens, take the
   first 40 characters, strip trailing hyphens, and use `issue` if empty. Do not
   transliterate Unicode or generate a different semantic short name. Include
   `042-add-a-gate`, `1234-big-feature`, the upstream `^[0-9]{3,}-` rule,
   repository-local numbering on both forges, and acceptable gaps. Explain the
   parallel-draft collision avoided by replacing the sequential scan.
2. `## Open the issue first`: say "open the issue, then specify with its number";
   show `/speckit.specify 42 Add a gate` and `/speckit.specify #42 Add a gate`.
   The remaining argument text is the issue title used for the slug and initial
   feature description; expanded requirements can be added during drafting.
   Explain `SPECIFY_FEATURE_DIRECTORY`, linking upstream `docs/overview/core.md`,
   and why sequential/timestamp configuration cannot obtain an issue number.
3. `## Labelled acceptance scenarios`: use
   `1. **AS1** **Given** ... **When** ... **Then** ...` in each user story,
   with numbering local to the story. Append new labels without renumbering
   committed labels or reusing removed IDs, as `/speckit.converge` does for task
   IDs. Explain `<NNN>-<slug>/us<k>/as<n>`; include the literal
   `042-add-a-gate/us1/as2`, a backticked task citation and a pytest marker example.
   The three components retain existing regex, marker and per-story grouping.
4. `## Tasks cite scenarios`: retain `- [ ] T001 [P] [US1] Description with file path`
   (`[P]` optional; story tags for story work), require backticked IDs on every
   user-story task and at least one task per scenario. Setup tasks need no invented
   story ID. Explain harness-owned review and coverage additions as
   `## Phase N: Review round K`, phase N after the existing phases and IDs after
   the largest T number using `T{M+1:03d}` (no truncation). Harness completion uses
   `[X]`. Explain traceability and collision-free appended task identity.
5. `## Human-only checks`: `plan.md` gets `## Human checks`, with `- [ ]` entries
   naming exactly one scenario ID each. Put `<!-- nightshift:post-deploy -->` in
   the applicable scenario body for post-deploy checks, matching today's OpenSpec
   `design.md` convention. Explain why human/live checks are not agent tasks or
   claimed CI coverage. Do not provide a plan template override.
6. `## Flow-forward persistence`: feature directories are immutable delivered
   records, with harness task ticks and final `**Status**: Complete` as the
   lifecycle writes. Nothing is archived or merged into a consolidated spec;
   this says nothing about merging the feature's Git PR. The overarching spec is
   `.specify/memory/constitution.md`. A later behavior change is a new feature
   naming superseded features under `## Assumptions`. Explain retained history
   and stable references; drafting edits before approval remain possible.
7. `## Frozen paths`: implementing agents cannot edit `specs/**` or `.specify/**`;
   the harness alone updates `tasks.md` and the Status line. Distinguish authoring
   from implementation. Explain protection against agents changing acceptance
   criteria or grading themselves; enforcement is separate work.
8. `## No [NEEDS CLARIFICATION] at approval`: unresolved markers, including
   `[NEEDS CLARIFICATION: ...]`, become drafter questions returned to the issue,
   not an approved build. Explain why unresolved intent cannot be implemented
   autonomously. This is guidance, not a new validator in this change.

### Preset package and YAML without a new dependency

Create exactly these five files:

| File under `presets/nightshift-speckit/` | Contract |
| --- | --- |
| `preset.yml` | Scaffold field names; schema version string `1.0`; preset ID `nightshift-speckit`, name `Nightshift spec-kit`, version `1.0.0`, description, author `Matt Policastro`, repository URL and MIT license; `requires.speckit_version: ">=1.3.2"`. |
| `templates/spec-template.md` | Replacement spec scaffold with branch `spec/<NNN>-<slug>`, Created, Status Draft, Input, three prioritized user-story blocks, independent tests, labelled acceptance lists in every block, edge cases, functional requirements, optional entities, success criteria, and Assumptions for supersession. |
| `templates/tasks-template.md` | Compact replacement retaining Format, paths, setup/foundation, per-story phases and final polish/dependencies guidance. Every example story task has a file path and a backticked scenario ID; Format states universal story-task citation and complete scenario coverage. Include harness-only append/tick guidance and stable IDs. |
| `commands/speckit.specify.md` | Markdown preamble followed by exactly one `{CORE_TEMPLATE}`; `strategy: wrap` in frontmatter and the manifest entry. No copied core workflow, script override or executable helper. |
| `README.md` | Prerequisites, installation, issue-first invocation, branch behavior, rule summary/link, source references and explicit live-validation boundary. |

`provides.templates` has exactly three entries: types `template`, `template`,
`command`; names/replaces `spec-template`, `tasks-template`, `speckit.specify`;
files matching the table. Use `strategy: replace` for document templates and
`strategy: wrap` for the command. Do not invent a separate `provides.commands`
schema or `replaces` file paths with `.md` suffixes.

Serialize `preset.yml` using JSON-compatible YAML flow syntax (quoted keys and
strings, arrays/objects, no YAML-only features). JSON is a YAML subset accepted by
spec-kit's YAML loader; Python's standard `json.loads` can therefore establish
validity for this deliberately restricted manifest without adding PyYAML,
changing `pyproject.toml`/`uv.lock`, calling a host YAML tool, or writing a parser.
Explain this choice in the preset README. Normal YAML frontmatter is unchanged.

### Specify preamble ordering and argument handling

The wrapper's instructions run before all embedded core steps, including
`before_specify` hooks. Read the first whitespace-delimited argument token as
`#?[0-9]+`, remove the optional `#`, canonicalize leading zeros, and refuse zero,
missing/malformed numbers or an empty remaining title with an issue-first usage
message before feature files or branches are created. Treat the remaining title
as text, never shell code; it replaces the effective core feature-description
arguments, excluding the issue token. No forge API lookup or inferred number.

Compute the exact directory and branch from that title using the rule above.
Supply `SPECIFY_FEATURE_DIRECTORY=specs/<NNN>-<slug>` explicitly for every relevant
shell/helper invocation and nested hook; do not rely on an `export` in an earlier,
separate tool process persisting. Supply `GIT_BRANCH_NAME=spec/<NNN>-<slug>` to
the existing Git extension before-specify hook when present. These calculated
values take precedence over stale feature state, configured numbering and core
short-name suggestions. A conflicting explicit user override is an error, not
permission to silently select another feature.

The Git hook owns creation when enabled; do not create a branch twice. Without
that hook, require the author to select the exact branch before continuing and
give its expected name on mismatch. The README documents this prerequisite.
After hooks, verify the actual branch still matches before core spec creation;
refuse a mismatch or unavailable Git. Keep the core's resolver, validation,
handoffs and hooks, and its persistence of the actual resolved path in
`.specify/feature.json`. Existing feature directories refuse creation rather than
being overwritten; editing an existing draft is a separate authoring action.
Never fall back to a sequential/timestamp directory or a second spec file.

### Discovery, installation and concurrent changes

Add a single sentence under the unique `## How the engine works` heading linking
`speckit.md`, describing the house rules/preset as authoring support in #184.
Add the `presets/` row under root `README.md`'s unique `## Layout` heading in
task 1.2, alongside creation of the preset directory and its Layout assertion.
Task 1.1 adds only the house-rules document, engine-guide link and their test.
The existing `tests/test_forge_gitea.py::test_gitea_layout_module_paths_resolve`
requires every listed directory to exist, and verification gates each task;
therefore neither the row nor its new assertion may precede preset creation.
Preserve that existing test unchanged. These
are the necessary overlaps with #187; preserve its format-seam wording if it
lands first. Do not globally replace "OpenSpec", rename its examples, or change
its archival guidance. No current claim in `KNOWN-LIMITATIONS.md` is invalidated
by supplying authoring assets, so plan no edit there; preserve #182's changes.
If a newly landed passage is actually made false by these assets, correct only
that passage in the three authorized docs, retaining unrelated limitations.

Avoid all other in-flight paths: `docs/specloop/command-json.md`,
`examples/specloop/self-hosting.toml`, every file under `specloop/`, `nightshift/`
and `plugins/`, and every test file except the three additions in
`tests/test_specloop_documentation_integrity.py`. No fixture changes.

The preset README documents spec-kit 1.3.2 as the inspected baseline, installing
from a Nightshift checkout with `specify preset add --dev ./presets/nightshift-speckit`,
or `specify preset add --from <release-archive-url>`. The latter is an explicitly
substituted URL for a preset release archive with `preset.yml` at its package
root, not a fabricated published asset or the whole Nightshift source archive.
Explain that installing assets does not enable Nightshift's format adapter.

### Automated validation and deterministic line rules

Add exactly the three requested test functions to
`tests/test_specloop_documentation_integrity.py`; no existing assertion, fixture,
parameterization or helper changes. Decorate them with the relevant scenarios.

- `test_speckit_house_rules_doc_lists_every_rule`: check all eight exact physical
  heading lines above, each occurring once, plus `042-add-a-gate/us1/as2`.
  Check the engine guide's requested link within its exact unique heading
  boundaries; this task-1.1 test must not require the preset directory or Layout
  row introduced in task 1.2. Recognize `^#{1,6} ` headings at column zero and end a
  section at the next heading of the same or higher level. Missing, duplicate
  or indented target headings fail; no fence/HTML interpretation hides a line.
- `test_speckit_preset_manifest_parses`: parse the entire flow-style YAML with
  `json.loads`; assert the schema version, preset ID, three scaffold entries,
  replaces/strategy values, and each file's existence inside the preset root.
  Check the wrapper has exactly one literal `{CORE_TEMPLATE}` and the named
  directory/branch overrides; check the README's two install forms. Invalid
  parsing, missing entries/files or escaped paths fail; no network or install.
  In this task-1.2 test, also assert the new root README Layout row and existing
  `presets/` directory. Select the unique exact `## Layout` physical line through
  the next column-zero `^#{1,6} ` heading of level one or two (or EOF); missing,
  duplicate or indented target headings fail. Require exactly one column-zero
  table row whose first cell, stripped of spaces, is exactly backticked
  `presets/`; missing, duplicate or indented matching rows fail. Use literal
  lines only, without semantic fence/HTML parsing. This adds the assertion here
  rather than changing task 1.1's test or the existing Layout-path assertion.
- `test_speckit_spec_template_labels_scenarios`: select every physical
  `### User Story <positive integer> - ...` block until the next column-zero
  level-one/two/three heading. Require unique story numbers, at least the three
  starter blocks, exactly one `**Acceptance Scenarios**:` line per block, then
  its first nonblank line beginning `1. **AS1** **Given**`. Consume contiguous
  numbered lines until blank/end; each must match
  `N. **ASn** **Given** ... **When** ... **Then** ...` with equal positive N/n
  and no repeated labels in that story. Missing/ambiguous/indented structural
  candidates fail. Scan physical lines only; do not parse fences or HTML.
  Also check the tasks template's Format passage for backticked example IDs
  and verify every physical task line carrying `[USk]` has a backticked
  three-part scenario citation. This validates shipped examples, not arbitrary
  user features or runtime enforcement.

Keep assertions structural and token-based rather than matching explanatory
paragraphs. Run the unchanged Layout-path test at both task gates; task 1.1
keeps the existing directory list valid and task 1.2 supplies the new directory
and its row together. Run the named tests and full `uv run pytest` for implementation
acceptance, preserving existing assertions. Static checks cannot prove prompt
execution order, environment propagation, branch hooks or actual installation.

## Human checks

- [ ] Matt installs with `specify preset add --dev ./presets/nightshift-speckit`
  on a scratch spec-kit 1.3.2 project, then runs `/speckit.specify 42 Add a gate`
  in Claude Code or Codex. Check `spec/042-add-a-gate`,
  `specs/042-add-a-gate/spec.md`, labelled scenarios in every story, effective
  template composition and `.specify/feature.json`; record the tool versions,
  hook setup and result on #184. Covers
  `speckit-preset/issue-first-command-wrapper/select-the-issue-directory-before-core-execution`
  and `speckit-preset/preset-installation/install-from-checkout-or-release`.
- [ ] Matt reads the house rules and preset README once as a feature author,
  checks the #184/#185 and upstream references, and confirms the wording does
  not imply shipped validator/engine support. Record any correction before
  merge. Covers `specloop-documentation/spec-kit-discovery/find-authoring-guidance`
  and `specloop-documentation/documentation-integrity/references-and-reader-checks-are-reviewable`.

These are pre-merge human checks, not implementation tasks or CI results.

## Risks / Trade-offs

- A prompt wrapper cannot mechanically guarantee the host agent propagates env
  variables or follows hook ordering. Keeping the composed core and using its
  documented overrides minimizes divergence; the scratch-project check remains
  necessary and unperformed until Matt records it.
- Issue-derived names prevent distinct issues racing for the next sequential
  number, not concurrent editing of the same issue or shared working tree.
  Existing-directory refusal protects records; isolate parallel drafts in their
  own worktrees. Title edits must not rename an already committed feature.
- JSON-compatible YAML is less idiomatic but avoids a new runtime/development
  dependency and supports a real offline parser within the exact file allowance.
- Replacement templates can drift from upstream. Keep them compact, retaining
  the described core sections and links to the inspected v1.3.2 baseline;
  do not claim compatibility with untested future versions.
- The shared README additions can conflict with #187. Reconcile only those
  additions against the landed text; do not roll back its behavior descriptions
  or touch #182's readiness guidance. The allowlist answer applies to new runs;
  this change never edits external manifests or copies private configuration.
