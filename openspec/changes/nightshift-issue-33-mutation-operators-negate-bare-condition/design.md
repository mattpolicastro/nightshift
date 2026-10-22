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
usually the last statement in the immediate `if` suite. Both new operators
cover this guard: negating the condition reverses entry, while dropping the
return lets execution continue after the enclosing `if`. The return must end
a branch whose enclosing statement list continues after that `if`.

## Goals / Non-Goals

Generate useful single-line mutants for bare Python conditions and guard-clause
returns, without changing sampling or evidence interpretation. Exercise the new
behavior using complete source fixtures, temporary Git repositories and local
verification stubs, independent of GitHub, launchd, Paseo or model services.

Do not change selection priorities, counts, seeds, scoring, classification,
execution, concurrency, preparation, timeout budgets or evidence formats. Do not
introduce configuration, a general control-flow graph, reachability analysis,
multi-line mutants or new non-Python behavior. Arbitrary truthiness expressions
and returns outside the guard-clause rule are ineligible for the new operators.

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

### A droppable return ends a guard branch with following code

Use AST statement-list membership to find an `ast.Return` that is the last
statement directly in an `if`, `elif` or `else` branch, and require a later
statement after the owning `if` statement in its immediate enclosing statement
list. The rule applies in normal and async functions, including guards inside
nested branch, loop, exception-handler, `finally` and match-case suites. Earlier
statements in the guard branch, such as `log(...)`, do not disqualify the return.

Python represents an `elif` chain as nested `ast.If` nodes in `orelse`. Use the
actual keyword tokens to associate `elif` branches and the final `else` with the
chain's initial `if`; check for a following sibling after that whole statement.
An ordinary nested `if` written inside an `else` remains a separate statement:
it needs a following sibling in that `else` suite. Do not climb other enclosing
suites looking for a continuation. Another clause, comments, blank lines or code
inside a nested definition do not count as a following sibling statement.

The return must occupy one physical line with only indentation before its
`return` keyword and only whitespace or a trailing comment after its AST span.
Exclude semicolon-separated forms, inline suites, explicit continuations and
multiline expressions. Replace the span from `return` through the expression
with `pass`, retaining leading indentation and the exact trailing whitespace and
comment. For example:

```python
def choose(blocked, fallback):
    if blocked:
        log("blocked")
        return None  # short circuit
    return fallback
```

The guard return has a `drop-return` candidate whose replacement is
`        pass  # short circuit`; the final function return has none. A bare
`return` ending the guard branch is handled the same way. The resulting source
must compile. Removing the return allows control to reach `return fallback`;
this also works when the guard return is the branch's only statement.

Exclude returns followed by another statement in their own branch, returns
directly in function/loop/try/handler/finally/match-case suites, and returns in
branches of an `if` that is last in its enclosing statement list. A later outer
statement does not qualify a return inside a different nested scope. This is a
syntactic guard rule, without reachability analysis or a general control-flow
graph. Existing operators on excluded return lines remain eligible.

### Tests establish behavior without changing the executor

Put all new tests and helpers in `tests/test_specloop_evidence.py`, which is not
one of the concurrent-work test files. Add scenario markers for the delta IDs.

- `test_bare_condition_is_negated`: parameterize `if` and `while`, ordinary and
  parenthesized names, spacing/comments, indentation and Unicode. Assert exact
  operator identity and line replacement, compile each mutated source and use
  bounded fixtures to demonstrate changed branch/loop behavior. Add negative
  cases for unsupported forms and strings.
- `test_early_return_is_dropped`: parameterize bare and expression returns with
  guard branches in `if`/`elif`/`else`, asserting `pass`, exact preserved formatting
  and compilability. Cover a sole return and logging before the return; demonstrate
  fall-through to a later return or side effect. Add normal/async functions,
  nested guards, chain ownership, non-guard returns, same-branch following code,
  unchanged committed context and unsupported-layout cases.
- `test_gate_measures_a_guard_clause_change`: create base/head commits where the
  production diff adds only `if blocked:` and its indented `return None` before
  an unchanged `return fallback`. Use a mutation budget of two and no matching
  exclusions; each added line has exactly one candidate. Keep `gather`, Git
  diff/source loading, `generate`, selection and
  `mutate` real. Stub only `verify.run_extracted` to load the private fixture
  source and check the function for blocked and unblocked inputs, returning
  receipts from actual assertion outcomes, using a non-`None` fallback. The
  baseline passes; both condition negation and return dropping fail the behavioral
  assertions independently. Assert exactly two generated mutants, one
  `negate-condition` and one `drop-return` with exact before/after text on the
  guard return line, both killed, an empty skip reason, measured evidence, score
  `1.0` and matching persisted evidence for both mutants. Do not stub `generate`
  or inject hand mutants; condition negation alone cannot satisfy this test.

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
- The syntactic guard rule does not prove the continuation is reachable or
  behaviorally different. It deliberately excludes non-guard early exits and
  distinguishes `elif` chains from ordinary nested `if` statements; focused
  ownership tests guard against accidental widening of the operator.
- Negating a loop can produce nontermination. Existing timeout behavior remains
  responsible for it; generator tests use bounded loops and do not change budgets.
- A dropped return may be equivalent, for example when later execution produces
  the same result. Unit tests cannot establish prevalence on real diffs.
  Human PR checklist: inspect `evidence.json` and summaries from the next three
  real gates that select either new operator, review surviving/equivalent cases,
  and record whether equivalent mutants are consuming an excessive share of the
  sample. Raise follow-up work if needed; do not tune scoring or selection here.
  This observation is not an implementing-agent task or a CI completion claim.
