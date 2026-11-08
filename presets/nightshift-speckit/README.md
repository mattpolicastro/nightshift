# Nightshift spec-kit preset

Authoring assets for [issue #185](https://github.com/mattpolicastro/nightshift/issues/185)
in [issue #184](https://github.com/mattpolicastro/nightshift/issues/184).
Requires spec-kit >=1.3.2, a spec-kit project, Git and a supported host agent.

Spec-kit's Bash template resolver uses the first `python3` on `PATH` and requires
PyYAML in that interpreter. If the system Python lacks it, set
`SPECKIT_PYTHON_EXECUTABLE` to the CLI's own interpreter (for a `uv tool` install,
use `export SPECKIT_PYTHON_EXECUTABLE="$(uv tool dir)/specify-cli/bin/python"`).
Otherwise, `/speckit.specify` stops with
`Error: PyYAML is required to resolve preset template composition` before creating the feature.

The inspected baseline is v1.3.2; future-version compatibility is untested.
Installing this preset installs authoring assets only: it does not enroll a
repository or configure a build. Start with the
[native drafting and ordinary admission workflow](../../docs/specloop/README.md#native-spec-kit-drafting).
For approved features, follow the
[explicit native build guide](../../docs/specloop/README.md#native-spec-kit-builds)
with a trusted [`format = "speckit"` manifest](../../examples/specloop/speckit.toml)
kept outside the target repository. Set `[paths] frozen` to include `specs/**`
and `.specify/**` plus workflow and steering protections. Explicit `nightshift admit`
runs feasibility and structural validation; `--skip-feasibility` is an optional
operator-requested bypass of feasibility only. `nightshift run`
builds and `nightshift publish` publishes. After human checks, `nightshift finish`
sets `**Status**: Complete`, archives nothing, pushes and marks ready without merging.
Native daemon intake support remains future work.

## Installation

From a Nightshift checkout:

```sh
specify preset add --dev ./presets/nightshift-speckit
```

For another project, point `--dev` at this checkout's preset directory.
Alternatively, substitute the URL placeholder:

```sh
specify preset add --from <release-archive-url>
```

Use a preset release archive with `preset.yml` at its package root, not the whole
Nightshift source archive. No published archive is claimed.
[preset.yml](preset.yml) uses JSON-compatible YAML flow syntax: spec-kit's YAML
loader accepts it, and offline tests use standard `json.loads` without a dependency.
It replaces [spec](templates/spec-template.md) and [tasks](templates/tasks-template.md)
templates and wraps [specify](commands/speckit.specify.md); no plan override or scripts.

## Issue-first authoring

Open the issue, then invoke `/speckit.specify 42 Add a gate` or `/speckit.specify #42 Add a gate`.
Expect `specs/042-add-a-gate/spec.md` on `spec/042-add-a-gate`. With the Git extension
before-specify hook enabled, the wrapper supplies `GIT_BRANCH_NAME`; the hook creates
the branch. Otherwise, create/select that exact branch before invoking specify.
Invalid input, conflicting explicit overrides, existing directories and branch
failures refuse creation. The title supplies the slug and initial description as text.

Follow the [eight house rules](../../docs/specloop/speckit.md): issue-derived identity,
open the issue first, stable AS labels, task citations, separate human checks,
flow-forward records, frozen implementation paths and resolved approval questions.

## Baseline and validation

Versioned upstream sources: [scaffold manifest](https://github.com/github/spec-kit/blob/v1.3.2/presets/scaffold/preset.yml),
[spec template](https://github.com/github/spec-kit/blob/v1.3.2/templates/spec-template.md),
[tasks template](https://github.com/github/spec-kit/blob/v1.3.2/templates/tasks-template.md),
[specify command](https://github.com/github/spec-kit/blob/v1.3.2/templates/commands/specify.md),
[Git hook](https://github.com/github/spec-kit/blob/v1.3.2/extensions/git/commands/speckit.git.feature.md)
and [core directory resolution](https://github.com/github/spec-kit/blob/v1.3.2/docs/overview/core.md).

Static checks cover manifest paths, labels, citations and links, not host execution.
Matt's design.md checklist remains pending: install in a scratch spec-kit 1.3.2 project
in Claude Code or Codex; verify branch, path, composed templates and `.specify/feature.json`;
record versions, hook setup and results on #184. Live-reference/readability review is also Matt's.
