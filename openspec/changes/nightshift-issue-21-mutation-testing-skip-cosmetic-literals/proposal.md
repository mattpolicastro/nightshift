## Why

Mutation gates currently spend their sample on cosmetic literals and prose.
Factory-context section 2 scored 6/10 because four surviving mutants changed
styles that tests correctly did not pin. Docstring mutations also caused
pointless strengthening rounds and `mutation-equivalent` annotations in prose.
After #31, evidence containing only equivalent and timeout results still passes
with a null score, despite demonstrating no test sensitivity.

blocked-by: #31

Admission also follows #8's merge so its `nightshift/specloop_job.py` docstring
annotations are included in the cleanup base.

## What Changes

- Add trusted `[gates] mutation_skip`, an optional list of case-sensitive line
  substrings, defaulting to an empty list. Matching lines produce no mutants.
- Use full committed source context and Python `ast`/`tokenize` to exclude
  docstrings and comments, including trailing comments, before sampling mutants.
  For non-Python files retain today's comment-prefix exclusion and add only
  line-level tracking for block comments with comment-only boundary lines.
- Automatically exclude integer-literal mutations inside JSX inline `style`
  object expressions, including multiline objects and quoted numeric values.
  Use brace counting that ignores single, double and backtick-quoted strings.
- Remove prose-only equivalence annotations from `nightshift/` and `specloop/`,
  including #8's annotations in `nightshift/specloop_job.py`, preserving
  annotations that justify real code.
- Require at least one killed or survived mutant for generated evidence to be
  measured. Preserve no-mutable-lines skips, coverage checks, baseline failure,
  timeout classification and the killed/survived scoring denominator.
- Add host-independent regression tests and retain `uv run pytest` as the
  implementation's completion check.

Out of scope: new mutation operators, regex configuration, automatic judgment
of semantic equivalence, style rendering tests, excluding every style-related
variable by name, changing trusted manifests outside this repository, changing
the worker pool or sandbox, and daemon/plugin behavior. A general C-style or
JS/TS lexer, regex-versus-division detection, template interpolation parsing,
and non-Python inline comment parsing are also out of scope. The implementation
must avoid the concurrent-work files listed in the design; `specloop_job.py`
is explicitly allowed for prose cleanup after #8 merges.

## Capabilities

- **Added: `mutation-selection`** — configurable exclusions, committed-source
  prose filtering, default JSX style filtering, and prose annotation cleanup.
- **Modified: `parallel-evidence-mutants`** — amend Stable evidence results and
  Timeout scoring to incorporate the new selection and measured-evidence rules;
  add accurate reporting for equivalent-only evidence. The previous exception
  permitting equivalent/timeout-only evidence is deliberately replaced because
  an annotation is not evidence that a test detects a defect. All other existing
  requirements remain in force, including `verify-preparation` restrictions.
