## Purpose

Configure isolated environments for trusted prepare, verify, acceptance and
mutation commands while preserving harness-owned values and execution rules.

## ADDED Requirements

### Requirement: Step environment tables

The manifest SHALL accept an optional `env` table on each `[[verify]]`,
`[[prepare]]`, `[[acceptance]]` and `[[mutation_step]]`, exposed as `Step.env`
with an independent empty default. Keys SHALL be nonempty strings without `=`
or NUL; values SHALL be strings without NUL, including empty strings. Parsing
SHALL reject invalid entries without coercion, identify the step and env
setting without printing values, and preserve all existing step fields.

#### Scenario: Tables round trip for all step kinds
- **WHEN** all four step kinds use omitted, empty or populated env tables and a manifest is parsed, templated with run overrides and loaded again
- **THEN** environment entries retain their literal values, omitted and empty tables yield empty mappings, and IDs, argv, ordering, expect and timeout retain their existing values and defaults
- **THEN** existing positional Step construction still works with an independent empty environment

#### Scenario: Invalid tables fail before execution
- **WHEN** any step kind supplies a non-table env, a non-string value, an empty or invalid key, or a NUL-containing value
- **THEN** parsing raises a contextual ValueError before commands run, without coercing values or including them in the diagnostic

### Requirement: Step environment substitution

The runner SHALL expand env values using `manifest.expand()` and the same
execution-time values as the step's argv. It SHALL retain existing literal
substitution semantics, validate snapshot references in env values, and extend
the reserved mutant-path default to literal env references. Environment keys
and sibling values SHALL NOT become substitution variables. Env entries SHALL
NOT change mutation routing or argv-based narrowing decisions.

#### Scenario: Values use the command context
- **WHEN** any of the four step kinds declares env values using manifest variables and `{src}`, `{build}` or `{sha}`, alongside empty values, spaces and literal shell punctuation
- **THEN** test_step_env_applied_with_substitution verifies that its child receives the same replacements as argv, with remaining text preserved and no shell evaluation or host-variable lookup by the harness
- **THEN** unknown ordinary placeholders remain literal, keys are unchanged, and env entries do not interpolate one another

#### Scenario: Snapshot references use private paths
- **WHEN** env values refer directly or through manifest-variable aliases to declared snapshots, or refer to an unknown snapshot
- **THEN** valid references use the same private build path as argv, including the existing empty-cache fallback path, while unknown references fail parsing with an env-specific error
- **THEN** environment paths grant no additional sandbox access and do not alter warming, cloning, fallback or retention behavior

#### Scenario: Mutation context does not select phases
- **WHEN** an env value contains `{mutant_path}` during ordinary execution or when the existing evidence command context supplies a narrowed path
- **THEN** it expands respectively to the empty default or supplied path, and a nonempty configured override is rejected during parsing
- **THEN** an env-only reference does not activate narrowing or confirmation, and existing argv-based scheduling, timeout copies and mutation outcomes remain unchanged

### Requirement: Step environment isolation

The runner SHALL create a fresh environment per command by adding expanded env
entries to its existing fixed base of PATH, HOME, TMPDIR, LANG and
COPYFILE_DISABLE. Explicit entries MAY replace non-reserved defaults. It SHALL
NOT inherit other host variables, mutate parent or manifest environments, or
carry a step's additions into another step or execution. Existing receipts,
ordering, failure handling and sandbox policies SHALL remain unchanged.

#### Scenario: Overrides are confined to one step
- **WHEN** one step sets a custom variable and overrides LANG or COPYFILE_DISABLE, followed by a step without those entries or a separate execution using another build directory
- **THEN** only the configured child receives those overrides, the next child receives original fixed defaults and its own entries, and parent variables absent from the allowlist remain absent
- **THEN** Step.env, Manifest.vars and the parent environment remain unchanged, while build substitutions resolve separately for each execution

#### Scenario: Empty environments preserve existing execution
- **WHEN** existing parsed or directly constructed steps run with omitted or empty env mappings, including the internal snapshot warm step
- **THEN** they receive the original fixed environment and retain command argv, receipt schema, expected-exit rules, ordering, timeouts, process cleanup and stop-on-first-failure behavior

### Requirement: Harness environment ownership

The manifest SHALL reject env entries named exactly PATH, HOME or TMPDIR,
regardless of value. The runner SHALL enforce the same protection for directly
constructed steps before launching the affected command and reject expanded
NUL-containing values before launch. Valid steps SHALL retain the configured
harness tool path and their build-private HOME and TMPDIR.

#### Scenario: Protected variables cannot be replaced
- **WHEN** each step kind attempts to set PATH, HOME or TMPDIR through a parsed env table or a directly constructed Step, including empty or identical values
- **THEN** test_step_env_cannot_override_harness_vars verifies a contextual ValueError and no launch of the affected command
- **THEN** valid neighboring cases retain the harness tool path and build-private HOME and TMPDIR, and a value acquiring NUL during substitution is rejected before Popen
