## Context

OpenSpec operations currently live in `specloop/openspec.py`, but consumers
also construct change paths, inspect archives, collect design checks and
choose context files. This is the first engine step of #184, tracked by #187;
it does not implement the later spec-kit adapter. The repository has no
`AGENTS.md` in this checkout or its inspected parent directories.

The important compatibility boundaries are observable: exact npx argv,
validator exception classification, draft prompt text and confinement,
committed-head checklist collection, expected-head finish guards, archive
retry/status reads, revision identity, PR wording and saved run state.
Existing specs governing these boundaries remain unchanged.

## Goals / Non-Goals

Introduce one small typed format seam, about 120 lines excluding tests, and
otherwise make substitutions. Keep default and explicit OpenSpec selection
equivalent. Accept the future format name in the trusted manifest and report
its unavailability through ordinary admission refusal.

Do not implement format discovery, a plugin registry, conversion, spec-kit
behavior, alternate binaries, state migrations, new retry policy, or semantic
Markdown parsing. Leave `specloop/openspec.py`, `specloop/prompts.py`,
`nightshift/` and all rendered prompt wording unchanged. Format selection is
not a change to the provider adapter or forge adapter.

## Decisions

### Interface and default implementation

Define `SpecFormat(Protocol)` in `specloop/spec_format.py`, with exactly these
members; `repo` and `work` are `Path`, and `change` and `head` are strings:

| Member | Return / OpenSpec behavior |
| --- | --- |
| `change_dir(repo, change)` | `Path`; delegate to `openspec.change_dir` |
| `tasks_path(change)` | `str`; `openspec/changes/<change>/tasks.md` |
| `tasks_paths_at(repo, head, change)` | `list[str]`; committed task candidates in legacy selection order |
| `validate(repo, change)` | `list[str]`; delegate to `openspec.validate` |
| `scenarios(repo, change)` | `dict[str, str]`; delegate to `openspec.scenarios` |
| `human_checks(repo, head, change)` | `list[str]`; extracted `design_human_checks` implementation |
| `context_files(repo, change)` | `list[tuple[Path, str]]`; existing source paths and context-relative destinations |
| `drafter_root(change)` | `str`; `openspec/changes/<change>/`, including trailing slash |
| `finish(work, change, all_tasks_done)` | `dict`; delegate to `openspec.archive` with the supplied boolean |
| `pr_title(change)` | `str`; `<change>: OpenSpec change via specloop` |
| `pr_kind` | `str` property/value; `OpenSpec change` |

Expose `OPENSPEC: SpecFormat` and `get(m: Manifest) -> SpecFormat`. OpenSpec
selection returns that singleton. `"speckit"` raises the existing `SpecError`
class with exactly `spec-kit format is not available yet`. Import/re-export
the existing `SpecError` and `ValidatorUnavailable` identities; do not create
parallel exception classes. Delegating methods resolve `openspec` attributes
on each call, never cache bound functions during import or construction.

Add the defaulted `Manifest.format: str = "openspec"` at the end of the
dataclass with the seam, so old positional construction and type checking
continue to work. The manifest task subsequently wires parsing to
`run.get("format", "openspec")`. Do not add unrelated manifest validation or
a state-format discriminator. Explicit `"speckit"` parses and survives template
overrides; selection, not TOML loading, establishes adapter availability.

### Exact path and content rules

`tasks_paths_at` preserves `_tasks_path`'s Git invocation and selection:
inspect the live path and archive subtree at `head`; return `[live]` if live
exists, otherwise exact-change archive task paths in reverse lexical order.
Match only `openspec/changes/archive/\d{4}-\d{2}-\d{2}-<escaped-change>/tasks.md`
with a full match. If none match, return `[live]` so the subsequent read fails
as before. The first element therefore equals the old helper's result,
including live-over-archive precedence and newest-name selection; introduce
no ambiguity error or filesystem-based fallback.

`context_files` returns existing entries in `proposal.md`, `design.md`,
`specs` order, each as `(absolute-or-repo-relative source Path, destination
name)`, preserving the caller's repo path convention. The specs entry is the
directory itself, not a flattened list. `_copy_change` keeps `shutil.copy`
for the two document entries and `copytree(..., dirs_exist_ok=True)` for the
specs entry; omit missing entries just as before. No extra context files.

Move the committed design reader and its parser literally. It uses
`splitlines()`, strips only outer ASCII spaces/tabs, selects exact
`## Human checks` and `### Verification and human rollout checklist`, and
ends an active section at `^(#{1,6})[ \t]+` of the same or shallower depth.
Deeper headings, including nested selected headings, do not narrow the
section. Include only lines matching `- \[ \][ \t]` with nonblank remaining
text. Fences, HTML and indentation have no semantic meaning: a literal
unchecked example stays pending, the safe direction. Keep ordering,
duplicates and normalized line text in the adapter; keep existing-source
preservation and design-addition deduplication in `append_design_checks`.
Established absence returns `[]`; Git/read/decode failure retains the same
diagnostic and blocks readiness rather than implying no checks.

### Call-site substitutions

- `loop.py`: select via the manifest for `Ctx.tasks_path`; delegate the
  existing `design_human_checks(ctx, head)` wrapper; use the adapter in
  `_scenarios_at`, `_gate_context`, `_scenario_text` and `_copy_change`.
  Keep extraction, chmod cleanup, error handling and events where they are.
  `revise.py` obtains its task path from `get(m)` without changing journals,
  locks, commits or replay logic.
- `cli.py`: use the adapter for `problems_with`, PR kind/title, finish and
  draft-ready notification paths. Keep `_tasks_path(repo, head, change)` as a
  compatibility wrapper with an optional adapter argument defaulting to
  `OPENSPEC`; all manifest-bearing finish and JSON/text status callers pass
  `get(m)`. It selects `tasks_paths_at(...)[0]`. Keep expected-head checks
  before task inspection, completed-task checks before archive, and forge
  operations/state transitions in their existing order. Preserve the literal
  archive commit message, including `into openspec/specs`.
- `propose.py`: `in_flight` selects each other run's manifest adapter;
  `lint` and `overlaps` accept an optional adapter defaulting to `OPENSPEC`,
  with production drafting passing `get(m)` as a positional argument so
  existing `*args` test wrappers still work. Use `tasks_path` for task reads,
  `drafter_root` for confinement/diagnostics, and remove only its trailing
  slash for the existing `git add -A <root>` argument. Preserve draft and
  feasibility/critic ordering, overlap policy and commit messages.

The drafting template contains an `openspec/changes/` literal today. Replace
only that path interpolation with an adapter-supplied root; the fully rendered
OpenSpec prompt, including every newline and slash, must be byte-identical.
Pass the new root placeholder in production and in the existing
`tests/test_specloop_task_sizing.py::task_rules` fixture, which formats
`propose.PROMPT` directly; preserve all of that fixture's consumer assertions.
This is substitution, not a prompt wording change. Keep the `openspec/specs/`
instruction and all other prompt text untouched. Reword the non-prompt module
docstring's path example to remove the remaining direct change-path literal.

The source guard scans all four consumer modules for `openspec/changes/`
and direct `openspec.` attribute calls; only `spec_format.py` and
`openspec.py` own those operations. Do not evade the guard by splitting path
literals. Existing `cli.openspec` and `loop.openspec` module aliases may remain
for test compatibility, but production calls and exception references go
through the seam/re-exported classes. Other modules and their existing
OpenSpec-specific prompt contracts are outside this extraction.

### Admission, saved data and tests

At entry to `problems_with`, select the adapter and convert selection's
`SpecError` to `[str(exc)]`. Resolve it before checkout/validator work. Do not
broaden existing catches around OpenSpec validation: `ValidatorUnavailable`
must still reach the version-1 infrastructure error envelope. `nightshift
admit --json` for `"speckit"` returns exit 1 and ordinary `outcome: refused`
with the exact diagnostic among its reasons, no admitted `state.json` and no
validator/agent invocation. Existing partial-admission cleanup still applies.

Persist only the existing effective TOML and existing state structure. Do not
rewrite an omitted `format` into legacy manifests or serialize an adapter.
Old code already ignores the new TOML key; new code defaults its absence.
An OpenSpec run's state, task identities, refs and revision journals retain
their old shapes and meanings in both directions. Check this with isolated
saved-manifest/state fixtures; actual deployed resume remains a human check.
The saved-run compatibility scenario carries the existing post-deploy
annotation for that live check; it does not defer the automated tests.

New coverage belongs in `tests/test_spec_format.py`, including the four
requested names: `test_openspec_adapter_matches_legacy_paths`,
`test_manifest_format_defaults_to_openspec`,
`test_manifest_format_speckit_refused_at_admission`, and
`test_no_direct_openspec_path_literals_outside_adapter`. Also cover committed
archive precedence/fallback, context entries, late-bound delegation and error
identity, exact rendered prompts/messages/argv, and compatible saved data.
Use local Git repositories and fakes for validator/agent/forge boundaries;
these tests need no live host, npx download or provider session.

Keep every existing assertion, including the exact npx argv at
`tests/test_specloop_command_json.py:125`, finish/forge retry tests, design
checks, keyed revision and propose tests. Retain legacy helper entry points
and module aliases as above to minimize patch migrations. One expected
migration is
`tests/test_specloop_design_checks.py::test_design_failure_cannot_establish_readiness`:
move its captured Git function and monkeypatch from `loop.git` to
`spec_format.git`, preserving all three failure cases and assertions. Name
this and any further necessary patch-target-only migrations in the PR
description, along with the `task_rules` template-input fixture migration;
direct patches of `specloop.openspec.validate`, `.scenarios`
and `.archive` continue to work unchanged. Run `uv run pytest` and
`uv run python scripts/check_types.py`; do not grow `mypy-baseline.txt`.

### Documentation and concurrent work

Add a Formats paragraph under `docs/specloop/README.md`'s How the engine works,
document `[run] format` and the JSON refusal in `docs/specloop/command-json.md`,
and show `format = "openspec"` in `examples/specloop/self-hosting.toml`.
Explain that only OpenSpec operates today, no migration is required, and the
binary invocation remains unchanged; cite #187 and #184 without inventing a
PR. Root README's existing OpenSpec description remains true; only correct a
passage there if extraction actually makes it false.

Do not edit `KNOWN-LIMITATIONS.md`, `nightshift/specloop_intake.py`,
`tests/merge_readiness_fixtures.py`, `tests/test_merge_readiness_evidence.py`
or `tests/test_specloop_base_publication.py`; concurrent #182 owns them. The
seam fixes no existing limitation and makes none of that file's claims false.
Do not amend unrelated stale documentation. If a documentation check needs
section selection, use a unique exact column-zero heading through the next
physical same-or-higher heading matching `^#{1,6}[ \t]+`; missing or duplicate
starts block validation. Do not parse fences/HTML/indentation. Parse the
whole TOML example with the real parser; no exact-prose tests are required.

## Risks / Trade-offs

- A small extraction can accidentally change failure classification, Git
  argv or slash spelling. Preserve wrappers and late-bound delegation, and
  run the original regression assertions alongside focused adapter tests.
- The list-valued committed-task API leaves room for another format but
  retains OpenSpec's first-candidate behavior, including its live fallback.
  It does not introduce an archive ambiguity policy.
- This is an engine seam only. OpenSpec-specific prompts and front-door
  assumptions remain until later initiative work; accepting the future name
  is not operational spec-kit support.
- Offline fixtures demonstrate schema compatibility, not the state of a
  deployed supervisor or the success of a real resumed run.

## Human checks

- [ ] `spec-format/manifest-format-selection/saved-openspec-runs-remain-compatible`: After deploy, resume one paused OpenSpec run admitted before this change with a manifest lacking `format`; verify its saved head, task progress and pause/revision state remain usable, and record the run identity, code revisions and result on tracking issue #184 with a link from #187. Retain before/after saved manifests and state for old/new reader compatibility inspection; do not run two writers concurrently.
