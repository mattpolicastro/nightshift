## Context

`specloop/manifest.py` represents all four command kinds with the frozen `Step`
dataclass and parses them through `_steps()`. `template()` already serializes
nested TOML dictionaries. `expand()` performs ordered literal `{name}`
replacement; it is not Python formatting or shell expansion, and unknown
ordinary placeholders remain literal.

`specloop/verify.py:run_extracted()` creates the execution substitution context
and schedules preparation followed by the requested checks. `_run_commands()`
currently supplies only `PATH`, `HOME`, `TMPDIR`, `LANG` and `COPYFILE_DISABLE`
to `Popen`. Acceptance and mutation commands use this same runner. Snapshot
warming also calls it with a directly constructed `Step`, which must retain an
empty environment default.

The landed #134 implementation validates snapshot references in command argv
and creates private snapshot paths before preparation. The landed #128 seal
configuration is independent of command environments. `specloop/evidence.py`
copies steps with `dataclasses.replace()` for timeouts and selects mutation
narrowing by looking for `{mutant_path}` in command argv. Those interfaces and
decisions stay unchanged. No `AGENTS.md` was found in the checkout or its
ancestor directories.

## Goals / Non-Goals

Provide a small, backward-compatible way to configure an individual trusted
command's environment. All four step kinds share parsing and runtime behavior,
values use the existing substitution context, and each command starts from a
clean allowlisted base. Existing tests and spec guarantees remain intact.

Do not add parent-environment imports, environment-file reads, shell evaluation,
recursive formatting, key substitution, agent prompt changes, secret management,
snapshot warm configuration or new sandbox permissions. Do not change evidence
routing, narrowing detection, confirmation, classification or budgets. No
deployment, service restart or private-manifest migration is part of this work.

## Decisions

1. **Append a defaulted field to `Step`.** In `specloop/manifest.py`, add
   `env: dict[str, str] = field(default_factory=dict)` after `timeout`, preserving
   existing positional construction. `_steps()` copies a supplied mapping;
   absent `env` and `{}` both mean no additions. Pass the actual step-kind name
   from `parse()` to `_steps()` for contextual errors. Keep default `expect=0`,
   `timeout=300`, step IDs, ordering and template round trips unchanged.

2. **Validate without coercion.** Accept only a table of string keys and string
   values. Keys must be nonempty and contain neither `=` nor NUL; values must
   contain no NUL. Empty string values are valid. Names are case-sensitive.
   Reject `PATH`, `HOME` and `TMPDIR` even when the configured value equals the
   harness value or is empty. Use a small shared validator in `manifest.py`
   that raises `ValueError` identifying the step kind/ID and `env` field or
   offending key, without including values. Apply the same validation before
   launch in `_run_commands()` for directly constructed steps; also reject a
   NUL introduced by substitution before `Popen`. This prevents bypass through
   the public dataclass without changing its existing constructor usage.

3. **Reuse substitution and placeholder validation.** In `parse()`, apply the
   existing `_snapshot_refs()` validation to environment values as well as
   argv, including aliases expanded through `[vars]`. Extend the existing
   literal `{mutant_path}` detection/defaulting to environment values, rejecting
   nonempty configured overrides when that placeholder is used. At execution,
   compute `{key: expand(value, values)}` using exactly the `values` passed to
   `_run_commands()`: `[vars]` plus harness `src`, `build`, `sha` and private
   `snapshot:<id>` values. Do not expand keys or use environment entries as
   variables for one another. Unknown ordinary placeholders, `$NAME`, `~` and
   shell punctuation retain `expand()`'s current behavior; a `[vars]` value
   already normalized by the parser keeps that existing normalization.

   Environment configuration does not select mutation phases. `{mutant_path}`
   in an environment value receives the path when existing argv-based evidence
   logic supplies it, and the empty default in ordinary/full runs. An env-only
   reference does not enable narrowing. Operators needing narrowing must keep
   the existing argv placeholder. This boundary preserves the existing
   `parallel-evidence-mutants/Narrow then confirm` contract without edits to
   `specloop/evidence.py`.

4. **Merge per command.** Keep the current fixed base in `_run_commands()`:
   `PATH=path` from `tool_path()`, `HOME={build}/home`, `TMPDIR={build}/tmp`,
   `LANG=en_US.UTF-8`, `COPYFILE_DISABLE=1`. For each step construct a new dict
   from that base plus its validated, expanded entries, and pass only that
   dict to `Popen(env=...)`. Explicit `LANG` and `COPYFILE_DISABLE` entries may
   replace their defaults; only the three named harness variables are reserved.
   Never mutate `os.environ`, `Step.env`, `Manifest.vars` or the base dict.
   Leave receipts, logs, expected-exit handling, early stop, process-group
   cleanup, source restrictions and network profiles unchanged. Do not add
   environment fields to receipts or grant filesystem access from env paths.

5. **Test the actual parser and child environment.** Put new tests in
   `tests/test_step_env.py`, leaving existing assertions untouched. Include
   `test_step_env_applied_with_substitution` and
   `test_step_env_cannot_override_harness_vars`, parameterized over the four
   step kinds and three reserved keys as applicable. Use real manifest
   parsing/template reload and a local Python child reporting selected env
   values. A test-local `verify.wrap` identity stub avoids dependence on
   `sandbox-exec`; it tests configuration plumbing, not sandbox enforcement.
   Exercise the shared runner through preparation and requested check tuples,
   with consecutive steps and independent build contexts. Check missing/empty
   mappings, invalid shapes, non-string values, invalid names, NUL, round-trip
   preservation, host-variable exclusion, non-reserved overrides, unchanged
   receipts and no cross-step/parent mutation. Verify snapshot references and
   expansion with declared snapshots and local empty-cache fallback; no APFS
   clone is needed. Supply an execution context with a mutant path to verify
   shared expansion, and check env-only references default empty. Existing
   snapshot, credential and mutation regression tests remain authoritative for
   their unchanged behaviors. Run `uv run pytest` as final acceptance.

6. **Document a bounded example.** Add a short per-step environment passage
   near preparation guidance in `docs/specloop/README.md`, update its two
   step-field lists, and cite issue #135. Explain defaults, validation,
   substitution, non-inheritance, isolation, protected keys, unchanged sandbox
   permissions and argv-based mutation narrowing. Include one TOML fragment
   with uniquely identified definitions for all four kinds, using harmless
   values such as `CACHE_DIR = "{build}/cache"` and a `[vars]` value.

   `tests/test_step_env_docs.py` extracts only the fragment with this physical
   line rule: require exactly one line equal to
   `<!-- step-env-example:start -->` and exactly one equal to
   `<!-- step-env-example:end -->` across the entire file, start before end;
   immediately inside them require exact lines `` ```toml `` and `` ``` ``
   with no leading or trailing whitespace.
   Reject any interior line containing triple backticks or triple tildes.
   Missing, repeated, reversed or indented delimiters and malformed TOML fail
   validation. Do not interpret surrounding fences, HTML or indentation; all
   exact marker lines count regardless of context. Parse the interior with
   `tomllib`, merge with the existing minimal test manifest fixture, then use
   the real manifest parser. Check fields and expansion, not exact prose.
   Include malformed-boundary fixtures so ambiguity always blocks validation.

## Risks / Trade-offs

- An explicit environment can change tool behavior. It comes from the same
  trusted manifest as argv; it imports no ambient credentials and grants no
  sandbox permissions. The protected-key rule applies to the new table, not to
  commands that deliberately change their own environment after launch.
- `Step` is frozen but its mapping is not deeply immutable. Copy on parse and
  fresh runtime dictionaries avoid shared-state changes, including concurrent
  evidence use; no new immutable container or serialization scheme is needed.
- Unknown placeholders retain existing literal behavior. A stricter formatter
  would break the promised parity with `run`; only existing reserved-placeholder
  checks are extended to env values.
- Env-only mutation narrowing is deliberately outside this configuration
  change. Documenting and testing the empty default prevents suggesting that
  an environment entry changes evidence scheduling.
- Both tasks fit one section: approximately 250–300 changed lines for the
  parser/runner and parameterized tests, and under 150 for the guide/example
  check. No host application, live service, APFS probe or hardware check is
  required to establish this feature. CI can prove its configuration behavior.
  Existing documentation-integrity review still includes a readability and
  issue-reference check at PR review; there is no additional operational human
  checklist, and no human-only check is an implementation task.
