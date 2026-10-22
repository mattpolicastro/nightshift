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
  `return` or `return <expr>` with `pass` when it ends an `if`, `elif` or `else`
  branch and the enclosing statement list continues after that `if` statement.
  Preserve indentation and trailing comments so execution falls through.
- Feed both operators into the existing candidate pool, retaining committed
  source analysis, exclusions, one-mutant-per-line spreading and seeded sampling.
- Add the three requested regressions in `tests/test_specloop_evidence.py`,
  together with eligibility and compatibility cases. The guard-only regression
  must generate and kill the return-dropping mutant as well as condition negation.
  Preserve existing assertions;
  explain any fixture or fake setup changes individually in the implementation PR.

Implementation scope is mutant generation in `specloop/evidence.py` and tests in
`tests/test_specloop_evidence.py`. Selection policy, scoring, execution, timeouts,
manifest settings, non-Python operators and other production files are explicitly
out of scope. A return followed by more code in its own branch is ineligible;
this operator targets guard-clause fall-through, not unreachable following code.

## Capabilities

- `mutation-selection`: add requirements for Python bare-condition negation,
  guard return replacement, compatibility with existing candidate rules,
  and measurement of a guard-only diff. This extends the existing capability;
  no existing requirement is modified or removed.
- Preserve every existing requirement in `mutation-selection` and
  `parallel-evidence-mutants`, including protected prose, configured exclusions,
  analysis failures, equivalence handling, deterministic selection, scoring and
  execution behavior. No delta to `parallel-evidence-mutants` is needed.
