## Why

The #24 guard added `if blocked:` and `return None`, but neither line matched
an existing mutation operator. The evidence gate reported `no mutable lines
added under mutation_paths` even though hand mutants were killed by the tests.
The generator needs operators for these control-flow forms so small logic
changes can produce behavioral evidence.

blocked-by: #21

## What Changes

- Add a Python bare-condition operator: `if x:` becomes `if not x:` and
  `while x:` becomes `while not x:`.
- Add a Python early-return operator that replaces a standalone, single-line
  `return` or `return <expr>` with `pass` when a later statement exists in the
  same immediate block. Preserve indentation and trailing comments.
- Feed both operators into the existing candidate pool, retaining committed
  source analysis, exclusions, one-mutant-per-line spreading and seeded sampling.
- Add the three requested regressions in `tests/test_specloop_evidence.py`,
  together with eligibility and compatibility cases. Preserve existing assertions;
  explain any fixture or fake setup changes individually in the implementation PR.

Implementation scope is mutant generation in `specloop/evidence.py` and tests in
`tests/test_specloop_evidence.py`. Selection policy, scoring, execution, timeouts,
manifest settings, non-Python operators and other production files are explicitly
out of scope. The sole return in a guard's `if` body remains terminal under the
same-block rule; condition negation supplies the mutant for the #24 regression.

## Capabilities

- `mutation-selection`: add requirements for Python bare-condition negation,
  nonterminal return replacement, compatibility with existing candidate rules,
  and measurement of a guard-only diff. This extends the existing capability;
  no existing requirement is modified or removed.
- Preserve every existing requirement in `mutation-selection` and
  `parallel-evidence-mutants`, including protected prose, configured exclusions,
  analysis failures, equivalence handling, deterministic selection, scoring and
  execution behavior. No delta to `parallel-evidence-mutants` is needed.
