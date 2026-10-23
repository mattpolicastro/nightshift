## Purpose

Select trusted mutation commands by file and optionally narrow Python tests without changing candidate generation, exclusions, operators or sampling.

## ADDED Requirements

### Requirement: Mutation command definitions

The manifest SHALL accept optional `[[mutation_step]]` definitions with the verify-step shape and defaults as `Manifest.mutation_step`, defaulting to empty. Mutation references SHALL resolve against verify and mutation-only definitions. Mutation-only steps SHALL have unique IDs across verify, prepare and acceptance namespaces and SHALL NOT join those phases or coverage. Existing valid manifests SHALL retain their commands and defaults.

#### Scenario: Definitions round trip
- **WHEN** a manifest defines mutation-only steps with omitted or explicit expect and timeout values and is templated with run overrides and loaded again
- **THEN** argv, IDs, default expect `0`, default timeout `300`, explicit values and mutation references are preserved, and normal verify and coverage selections are unchanged

#### Scenario: Invalid definitions rejected
- **WHEN** mutation-only steps have malformed argv, duplicate or colliding IDs, or mutation references name undefined steps
- **THEN** parsing raises `ValueError` identifying the offending definition or setting before any verification starts

#### Scenario: Placeholder defaults to full outside mutation
- **WHEN** a referenced verify command contains `{mutant_path}` and ordinary verification is requested
- **THEN** its placeholder expands to an empty string without adding mutation-only steps to verification; evidence may override that reserved value only in its own copied manifest
- **THEN** a nonempty configured override of the reserved value is rejected, and manifests without that placeholder retain their existing variables

### Requirement: Ordered mutation routes

The manifest SHALL accept ordered `[[gates.mutation_route]]` tables with nonempty `paths` and `steps` arrays as `Manifest.mutation_routes`, defaulting to empty. Routes SHALL require fallback `mutation_steps`. The first matching route SHALL supply commands in listed order; unmatched files SHALL use fallback declaration order. Routing SHALL preserve mutant selection and existing path-policy semantics.

#### Scenario: First matching route wins
- **WHEN** a mutant matches overlapping routes whose step lists differ
- **THEN** only the first matching route's commands execute in its listed order, and later routes and fallback commands do not run for that mutant

#### Scenario: Unmatched file uses fallback
- **WHEN** a mutant's path matches no configured route
- **THEN** it executes the resolved `mutation_steps`, with verify commands in their original declaration order followed by selected mutation-only commands in declaration order

#### Scenario: Routes validate and round trip
- **WHEN** routes are saved and reloaded through a manifest template, or contain malformed tables, empty arrays, non-string entries, unknown or repeated step IDs, absolute or traversing patterns, backslashes, or no fallback steps
- **THEN** valid routes retain their exact declaration order and entries, and invalid configurations raise `ValueError` naming `gates.mutation_route` and the offending entry

#### Scenario: Globs have explicit semantics
- **WHEN** routes use `*`, `?`, character classes and directory patterns against mixed-case repository-relative paths
- **THEN** matching follows case-sensitive `fnmatchcase`, including `*` crossing `/` and no special additional meaning for `**`, while `mutation_paths`, allowed paths and frozen paths keep their current matching rules

#### Scenario: Legacy manifest is unchanged
- **WHEN** a legacy manifest has neither routes nor mutation-only definitions nor a mutant-path placeholder
- **THEN** it retains its defaults, mutation enablement and skip behavior, fallback verify declaration order, one full baseline, one run per executable mutant, timeout budgets, output locations, evidence JSON shape, classifications and score
- **THEN** the only additional output is the decision receipt, and omitting routes or explicitly supplying an empty route array has the same behavior

### Requirement: Python import selector

`examples/specloop/select_tests.py` SHALL use only the standard library and expose `select_tests(root: Path, mutant_path: str) -> list[str]` plus a CLI with an optional path and `--root`. It SHALL statically inspect Python test imports without executing project code, return sorted unique repository-relative test paths, and print one per line. Empty output SHALL mean the caller runs all tests. Selection SHALL be a performance hint, with full confirmation responsible for survival.

#### Scenario: Importing tests are selected
- **WHEN** fixture tests under `tests/` import a mutated repository-root Python module with `import`, `from`, aliases, imports inside functions or resolvable relative imports
- **THEN** the selector includes the matching `test_*.py` and `*_test.py` files once in sorted order, excludes comments, string lookalikes and unrelated module-name prefixes, and handles a package initializer by including imports of that package or descendants
- **THEN** its CLI and function agree, work with an explicit root outside the current directory, and never execute the inspected imports

#### Scenario: Uncertainty falls back to all tests
- **WHEN** input is empty, unsupported, outside the root or unresolvable, the test tree is absent, no direct imports match, or a scanned file cannot be read or parsed
- **THEN** the selector returns an empty list and the CLI prints nothing with exit zero, allowing the caller to run the full test suite
- **THEN** no transitive dependency graph or dynamic import execution is attempted
