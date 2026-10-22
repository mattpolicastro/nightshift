## Context

`gather` in `specloop/evidence.py` obtains a zero-context diff, restricts added
lines through `added_lines`, and reads complete files from the requested Git
head. `generate(lines, count, seed, *, sources, mutation_skip=())` protects
Python docstrings/comments with AST and token analysis, enumerates textual
operators, shuffles candidates with `random.Random(seed)`, and selects at most
one mutant per physical line. `Mutant` stores path, line, operator, original
line and replacement line. The executor replaces exactly one physical line.

#21 is a prerequisite; its source-context and exclusion implementation and
archived change are already present in this checkout. Its requirements in
`openspec/specs/mutation-selection/spec.md` remain binding. The requirements in
`openspec/specs/parallel-evidence-mutants/spec.md` also remain unchanged.
No AGENTS.md exists in this checkout or its ancestor directories; README.md
points to CLAUDE.md, which was read with the engine README and known limitations.

Existing operators cannot mutate `if blocked:` or `return None`. The latter is
usually the last statement in the immediate `if` suite. This change applies the
requested same-block restriction literally: it adds condition coverage for that
guard, while return dropping requires a later sibling statement.

## Goals / Non-Goals

Generate useful single-line mutants for bare Python conditions and nonterminal
returns, without changing sampling or evidence interpretation. Exercise the new
behavior using complete source fixtures, temporary Git repositories and local
verification stubs, independent of GitHub, launchd, Paseo or model services.

Do not change selection priorities, counts, seeds, scoring, classification,
execution, concurrency, preparation, timeout budgets or evidence formats. Do not
introduce configuration, a general control-flow graph, reachability analysis,
multi-line mutants or new non-Python behavior. Arbitrary truthiness expressions
and final returns are outside the new operators' eligibility.

## Decisions

### Source-aware candidates within the existing generator

Keep the `generate` interface and `Mutant` fields unchanged. Add private Python
control-flow analysis in `specloop/evidence.py`, using the complete committed
source already supplied in `sources`. Analyze each candidate file once per
generation call for the new operators, never per added line or per mutant.
AST and tokens identify executable nodes and physical boundaries; regex matching
alone must not identify control flow inside strings. Reuse parsed source and
offset helpers where practical without changing existing protection behavior.

Produce deterministic per-line candidates with operator names `negate-condition`
and `drop-return`. For each input line, retain the existing operator enumeration
order and append eligible new candidates before the existing shuffle. Retain
the shuffle and selection loop unchanged. When no new candidates apply, the
existing ordered candidate pool and selected output remain unchanged. A larger
pool can change historical selections for the same seed; repeatability for
identical inputs remains mandatory.

Apply `mutation_skip` and protected-span checks before adding candidates. Only
added lines admitted by `mutation_paths` are eligible; unchanged source provides
context, never extra mutation locations. Retain original line numbers and exact
`Mutant.before` text. Construct `after` by changing only the identified code span,
preserving surrounding text. Convert AST UTF-8 byte columns to character offsets
before slicing, as the existing protected-span helper does. Missing source and
parse/tokenization failures still raise a path-identifying error before sampling;
they must not become a successful no-mutants skip.

### Bare conditions are names on ordinary block headers

For `.py` and `.pyi`, recognize `ast.If` and `ast.While` whose test is an
`ast.Name`, whose header occupies one physical line, and whose body begins on a
later line. The actual keyword must be `if` or `while`, excluding `elif` even
though Python represents it as another `ast.If`. Replace the name's source span
with `not ` followed by that exact source span. Thus `if blocked:` becomes
`if not blocked:`, and redundant single-line parentheses remain intact:
`if (blocked):` becomes `if (not blocked):`.

Preserve indentation, keyword spacing, colon spacing, trailing whitespace and
comments. Names may contain Unicode. Exclude already-negated tests, comparisons,
Boolean combinations, constants, attribute access, subscripts, calls, multiline
headers and inline suites from this operator. Their existing operator candidates
are unaffected. Do not infer control flow from string or comment contents or
from non-Python files.

### A droppable return has a later sibling in its immediate suite

Use AST statement-list membership to determine whether an `ast.Return` has a
following statement in the same immediate suite. Apply this rule inside normal
and async functions, including nested branch, loop, exception-handler, `finally`
and match-case suites. Another clause or an enclosing suite is not a later
sibling; comments and blank lines are not statements. A later nested function
definition is a sibling statement, but statements inside that definition are
not siblings of the return.

The return must occupy one physical line with only indentation before its
`return` keyword and only whitespace or a trailing comment after its AST span.
Exclude semicolon-separated forms, inline suites, explicit continuations and
multiline expressions. Replace the span from `return` through the expression
with `pass`, retaining leading indentation and the exact trailing whitespace and
comment. For example:

```python
def choose(fallback):
    return None  # short circuit
    return fallback
```

The first return has a `drop-return` candidate whose replacement is
`    pass  # short circuit`; the second has none. A bare `return` with a later
sibling is handled the same way. The resulting source must compile. This is a
syntactic rule, not a claim that the original later statement was reachable.

In `if blocked: return None` written as a normal two-line suite, the return is
the last statement of that `if` body even when the function continues after it.
Do not drop it. Likewise, a following `else` or `finally` suite does not make a
return nonterminal in its own suite. This conservative boundary is explicit so
the implementation does not silently broaden the requested operator.

### Tests establish behavior without changing the executor

Put all new tests and helpers in `tests/test_specloop_evidence.py`, which is not
one of the concurrent-work test files. Add scenario markers for the delta IDs.

- `test_bare_condition_is_negated`: parameterize `if` and `while`, ordinary and
  parenthesized names, spacing/comments, indentation and Unicode. Assert exact
  operator identity and line replacement, compile each mutated source and use
  bounded fixtures to demonstrate changed branch/loop behavior. Add negative
  cases for unsupported forms and strings.
- `test_early_return_is_dropped`: parameterize bare and expression returns with
  later siblings, asserting `pass`, exact preserved formatting and compilability.
  Demonstrate changed behavior, such as reaching a later return or side effect.
  Add terminal, nested-suite, unchanged-context and unsupported-layout cases.
- `test_gate_measures_a_guard_clause_change`: create base/head commits where the
  production diff adds only `if blocked:` and its indented `return None` before
  an unchanged `return fallback`. Use a positive mutation budget and no matching
  exclusions. Keep `gather`, Git diff/source loading, `generate`, selection and
  `mutate` real. Stub only `verify.run_extracted` to load the private fixture
  source and check the function for blocked and unblocked inputs, returning
  receipts from actual assertion outcomes. The baseline passes; condition
  negation fails the behavioral assertions. Assert at least one generated
  mutant, an empty skip reason, a killed result, measured evidence, score `1.0`
  and matching persisted evidence. Do not stub `generate` or inject hand mutants.

Keep the integration fixture's added lines free of existing operator matches,
so it fails with the old generator. Exercise excluded paths/lines and committed
context separately. Test repeated seeds and one-per-line selection with a pool
containing both old and new operators; do not give the new operators priority.
Preserve every existing assertion. If fixtures or verification fakes need
adaptation, retain the behavior each assertion checks and explain each setup
change in the implementation PR. Do not weaken a timeout, score, count or
classification assertion to accommodate a different candidate population.

Run focused evidence and mutation-filter tests after their associated changes,
then require `uv run pytest` to pass. Existing sandbox integration tests retain
their macOS requirements; the new tests need no host application or services.

### File boundaries

Implementation edits are restricted to generation code and private helpers in
`specloop/evidence.py` and `tests/test_specloop_evidence.py`. Do not edit
`Evidence`, `gather`, `mutate`, `_mutate_one`, the selection loop, or manifest
parsing. No new dependencies are needed.

To avoid work in flight, do not edit `README.md`, `nightshift/queue.py`,
`nightshift/specloop_client.py`, `nightshift/specloop_intake.py`,
`specloop/loop.py`, `specloop/state.py`, or these test files:
`tests/test_comment_trust.py`, `tests/test_pr_feedback_ids.py`,
`tests/test_revise.py`, `tests/test_revision_comment_retry.py`,
`tests/test_revision_feedback_consumption.py`,
`tests/test_revision_feedback_recovery.py`, `tests/test_revision_feedback_state.py`,
`tests/test_specloop_base_evidence.py`, `tests/test_specloop_base_publication.py`,
`tests/test_specloop_base_sync.py`, `tests/test_specloop_base_sync_recovery.py`,
`tests/test_specloop_build_revision.py`, `tests/test_specloop_label_authorization.py`,
or `tests/test_specloop_spec_revision.py`.

## Risks / Trade-offs

- Adding candidates changes the sampled population on affected diffs even with
  the same seed. Repeatability and spreading remain intact; historical mutant
  identities are not a compatibility guarantee when the operator set grows.
- The same-block restriction excludes many common guard returns. The guard-only
  regression proves condition coverage, not return-dropping coverage for every
  early exit. Extending that boundary would require a separate scope decision.
- Negating a loop can produce nontermination. Existing timeout behavior remains
  responsible for it; generator tests use bounded loops and do not change budgets.
- A dropped return may be equivalent, for example when later execution produces
  the same result. Unit tests cannot establish prevalence on real diffs.
  Human PR checklist: inspect `evidence.json` and summaries from the next three
  real gates that select either new operator, review surviving/equivalent cases,
  and record whether equivalent mutants are consuming an excessive share of the
  sample. Raise follow-up work if needed; do not tune scoring or selection here.
  This observation is not an implementing-agent task or a CI completion claim.
