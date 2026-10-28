"""Full committed source determines mutation eligibility."""

import json
import textwrap
import tokenize
from dataclasses import replace

import pytest

from specloop import evidence, manifest, seal, verify


M = manifest.parse({
    "run": {"repo": "/unused", "change": "example", "branch": "main"},
    "roles": {"implement": "p", "review": "p"},
    "profiles": {"p": {"adapter": "codex", "model": "m"}},
    "paths": {"allowed": ["src/**"]},
    "verify": [{"id": "test", "run": ["true"]}],
    "gates": {"mutation_steps": ["test"], "mutation_paths": ["src/**"], "mutation_count": 100},
})


def candidates(source, path="src/example.py"):
    return [(path, line, text) for line, text in enumerate(source.splitlines(), 1)]


def select(source, *, path="src/example.py", seed=1, count=100, mutation_skip=()):
    return evidence.generate(candidates(source, path), count, seed,
                             sources={path: source}, mutation_skip=mutation_skip)


@pytest.fixture
def committed_project(tmp_path):
    """Create isolated Git objects; never use the checkout as an analysis source."""
    repo = tmp_path / "repo"
    repo.mkdir()
    seal.git(repo, "init", "-q")
    empty_tree = seal.git(repo, "mktree", input=b"").decode().strip()
    initial = seal.git(repo, "commit-tree", empty_tree, "-m", "initial",
                       env=seal.IDENTITY).decode().strip()

    def create(before, after):
        base = seal.commit_files(repo, initial, before, "base")
        head = seal.commit_files(repo, base, after, "head")
        return repo, base, head

    return create


def forbid_execution(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("excluded or unanalyzable source must not start baseline or mutant workers")

    monkeypatch.setattr(verify, "run_steps", unexpected)
    monkeypatch.setattr(evidence, "ThreadPoolExecutor", unexpected)


@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/python-docstrings-and-comments-excluded")
@pytest.mark.parametrize("path", ["src/example.py", "src/example.pyi"])
@pytest.mark.parametrize("scope", ["", "class Example:\n", "def example():\n", "async def example():\n"])
@pytest.mark.parametrize("prefix", ["", "r", "u"])
@pytest.mark.parametrize("quote", ['"""', "'''"])
def test_docstrings_and_comments_are_never_mutated(path, scope, prefix, quote):
    body = (f'{prefix}{quote}True and False 10\n'
            f'prose 20 == 30 with escaped delimiter \\{quote} still prose\n'
            f'more prose 40 <= 50\n'
            f'{quote}; value = 41  # True and False 99\n'
            'enabled = True  # False or True 98\n'
            '# comment 97 != 96\n')
    source = '# preceding comment 95 + 94\n' + scope + textwrap.indent(body, '    ' if scope else '')
    lines = source.splitlines()
    closing = next(n for n, line in enumerate(lines, 1) if '; value = ' in line)
    for seed in range(12):
        mutants = select(source, path=path, seed=seed)
        assert {mut.line for mut in mutants} == {closing, closing + 1}
        for mut in mutants:
            assert mut.before == lines[mut.line - 1]
            expected = (mut.before.replace('value = 41', 'value = 42') if mut.line == closing
                        else mut.before.replace('enabled = True', 'enabled = False'))
            assert mut.after == expected


@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/python-docstrings-and-comments-excluded")
@pytest.mark.scenario("mutation-selection/committed-source-context/stable-selection-and-exact-replacement")
@pytest.mark.parametrize("docstring", [
    '"é True 10"', "'é False 20'", 'u"é True 30"', 'r"é False 40"',
    '("é True 50" "False 60")', '"é escaped \\" True 70"',
    '("é True 80"\n     # False 90\n     "False 100")',
])
def test_single_line_and_concatenated_docstrings_preserve_adjacent_code(docstring):
    # Non-ASCII identifiers and prose exercise AST byte offsets versus regex
    # character offsets, including code on the same line as a docstring.
    source = f'def café(): {docstring}; return 31  # é True 99\n'
    for seed in range(12):
        mutants = select(source, seed=seed)
        assert len(mutants) == 1
        mut = mutants[0]
        assert mut.line == len(source.splitlines())
        assert mut.before == source.splitlines()[-1]
        assert mut.after == mut.before.replace('return 31', 'return 32')


@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/python-runtime-strings-retain-their-meaning")
@pytest.mark.parametrize("source, expected", [
    ('text = "# 10"\n', 'text = "# 11"'),
    ('text = "// 20"\n', 'text = "// 21"'),
    ('text = "/* 30 */"\n', 'text = "/* 31 */"'),
    ('text = "é # 40"  # False 90\n', 'text = "é # 41"  # False 90'),
    ('text = """\n# 50\n"""\n', '# 51'),
    ("text = r'''\n// 60\n'''\n", '// 61'),
    ('text = """\n* 70\n"""\n', '* 71'),
    ('text = """\n/* 80 */\n"""\n', '/* 81 */'),
    ('value = None\n"not a docstring 90"\n', '"not a docstring 91"'),
    ('def f():\n    value = None\n    "not a docstring 100"\n', '    "not a docstring 101"'),
    ('b"not a docstring 110"\n', 'b"not a docstring 111"'),
    ('f"not a docstring 120"\n', 'f"not a docstring 121"'),
])
def test_runtime_strings_remain_mutable(source, expected):
    # A physical line beginning with # or * can be inside a runtime string,
    # so Python prefix filtering must be deferred until full-source analysis.
    diff = ('+++ b/src/example.py\n@@ -0,0 +1 @@\n'
            + ''.join('+' + line + '\n' for line in source.splitlines()))
    lines = evidence.added_lines(diff, M.mutation_paths)
    for seed in range(6):
        mutants = evidence.generate(lines, 100, seed, sources={"src/example.py": source})
        assert len(mutants) == 1
        assert mutants[0].after == expected
        assert mutants[0].before == source.splitlines()[mutants[0].line - 1]


@pytest.mark.scenario("mutation-selection/committed-source-context/unchanged-context-determines-eligibility")
@pytest.mark.parametrize("path", ["src/example.py", "src/example.pyi"])
def test_gather_uses_committed_context_across_hunks(committed_project, tmp_path, monkeypatch, path):
    before = ('"""Unchanged opening\n'
              'first prose 10\n'
              'unchanged prose\n'
              'last prose 20\n'
              '"""\n'
              'value = 30  # unchanged prose 40\n')
    after = before.replace('first prose 10', 'first prose 11').replace(
        'last prose 20', 'last prose 21').replace('value = 30', 'value = 31')
    repo, base, head = committed_project({path: before}, {path: after})
    diff = seal.git(repo, "diff", "--unified=0", base, head).decode()
    assert diff.count('@@ -') == 3 and '+"""' not in diff
    # Valid but misleading local code: each changed prose line is executable
    # in the worktree, while the committed executable line is in a docstring.
    dirty = 'value = None\nfirst = 11\nunchanged = None\nlast = 21\n"""\nvalue = 31\n"""\n'
    (repo / path).parent.mkdir(parents=True)
    (repo / path).write_text(dirty)
    reads, analyzed, executed = [], [], []
    real_analyze = evidence._python_protected_spans

    def analyze(source_path, source):
        analyzed.append((source_path, source))
        return real_analyze(source_path, source)

    def git(source_repo, *args):
        reads.append(args)
        return seal.git(source_repo, *args)

    def mutate(source_repo, revision, mutants, effective, out):
        assert source_repo == repo and revision == head and effective == M
        executed.extend(replace(mut) for mut in mutants)
        for mut in mutants:
            mut.result = "killed"
        return True

    monkeypatch.setattr(evidence, "git", git)
    monkeypatch.setattr(evidence, "_python_protected_spans", analyze)
    monkeypatch.setattr(evidence, "mutate", mutate)
    for section in (1, 7):
        ev = evidence.gather(repo, base, head, [], set(), M, tmp_path / f"out-{section}", section)
        assert [(mut.path, mut.line, mut.before, mut.after) for mut in ev.mutants] == [
            (path, 6, 'value = 31  # unchanged prose 40', 'value = 32  # unchanged prose 40')]
        assert ev.score == 1.0
    assert len(executed) == 2
    assert reads == [("diff", "--unified=0", base, head), ("show", f"{head}:{path}")] * 2
    assert analyzed == [(path, after)] * 2
    assert (repo / path).read_text() == dirty


@pytest.mark.scenario("mutation-selection/committed-source-context/stable-selection-and-exact-replacement")
@pytest.mark.parametrize("seed", [0, 1, 7, 42])
@pytest.mark.parametrize("count", [1, 3, 100])
def test_filtering_precedes_stable_sampling_and_preserves_exact_replacement(seed, count):
    source = ('"""True 10 == 20"""\n'
              '# False 30\n'
              'skip = 99  # configured exclusion\n'
              'result = x <= 4 and y != False  # True 99\n'
              'café = 10  # unicode before comment 20\n'
              'limit = 100\n')
    lines = candidates(source)
    sources = {"src/example.py": source}
    mutants = evidence.generate(lines, count, seed, sources=sources, mutation_skip=("configured",))
    assert mutants == evidence.generate(lines, count, seed, sources=sources, mutation_skip=("configured",))
    assert mutants == evidence.generate(lines[3:], count, seed, sources=sources)
    assert len(mutants) == min(count, 3)
    assert len({(mut.path, mut.line) for mut in mutants}) == len(mutants)
    allowed = {
        4: {
            'result = x < 4 and y != False  # True 99',
            'result = x <= 4 or y != False  # True 99',
            'result = x <= 4 and y == False  # True 99',
            'result = x <= 4 and y != True  # True 99',
            'result = x <= 5 and y != False  # True 99',
        },
        5: {'café = 11  # unicode before comment 20'},
        6: {'limit = 101'},
    }
    for mut in mutants:
        assert mut.before == source.splitlines()[mut.line - 1]
        assert mut.after in allowed[mut.line]


@pytest.mark.scenario("mutation-selection/committed-source-context/analysis-failure-cannot-become-a-skip")
def test_missing_source_mapping_is_an_error():
    with pytest.raises(ValueError, match=r"src/example\.py"):
        evidence.generate([("src/example.py", 1, 'value = 1')], 1, 1,
                          sources={}, mutation_skip=("value",))


@pytest.mark.scenario("mutation-selection/committed-source-context/analysis-failure-cannot-become-a-skip")
@pytest.mark.parametrize("failure", ["read", "decode", "parse", "tokenize"])
@pytest.mark.parametrize("configured_skip", [False, True])
def test_analysis_errors_propagate_without_partial_evidence(
        committed_project, tmp_path, monkeypatch, failure, configured_skip):
    bad_path = "src/broken.py"
    bad_source = 'value = 2\n' if failure != "parse" else 'value = (\n'
    # Git orders the valid candidate first: a later failure must abort the
    # whole operation, even after another file was successfully analyzed.
    files = {"src/a_valid.py": 'value = 1\n', bad_path: bad_source}
    repo, base, head = committed_project({}, files)
    forbid_execution(monkeypatch)
    real_tokenize = tokenize.generate_tokens

    def tokens(readline):
        source = readline.__self__.getvalue()
        if failure == "tokenize" and source == bad_source:
            raise tokenize.TokenError("cannot tokenize", (1, 0))
        return real_tokenize(readline)

    def git(source_repo, *args):
        if args == ("show", f"{head}:{bad_path}"):
            if failure == "read":
                raise RuntimeError("object unavailable")
            if failure == "decode":
                return b'\xff'
        return seal.git(source_repo, *args)

    monkeypatch.setattr(evidence.tokenize, "generate_tokens", tokens)
    monkeypatch.setattr(evidence, "git", git)
    m = replace(M, mutation_skip=("value",) if configured_skip else ())
    out = tmp_path / "out"
    with pytest.raises(ValueError, match=r"src/broken\.py"):
        evidence.gather(repo, base, head, [], set(), m, out, 1)
    assert not (out / "evidence.json").exists()


@pytest.mark.scenario("mutation-selection/committed-source-context/exclusions-leave-no-mutable-lines")
@pytest.mark.parametrize("kind", ["automatic", "configured", "combined"])
@pytest.mark.parametrize("uncovered", [False, True])
def test_exhausted_candidates_skip_execution_but_retain_coverage(
        committed_project, tmp_path, monkeypatch, kind, uncovered):
    source = ('"""True 10\nFalse 20\n"""\n# comment 30\n' if kind != "configured" else '')
    if kind != "automatic":
        source += 'value = 40  # explicit exclusion\n'
    repo, base, head = committed_project({}, {"src/example.py": source})
    forbid_execution(monkeypatch)
    m = replace(M, mutation_skip=("explicit exclusion",), coverage_exempt={"manual": "human check"})
    wanted = ["covered", "manual"] + (["missing"] if uncovered else [])
    out = tmp_path / "out"
    ev = evidence.gather(repo, base, head, wanted, {"covered"}, m, out, 1)
    assert ev.skipped == "no mutable lines added under mutation_paths"
    assert ev.mutants == [] and ev.score is None and not ev.unmeasured and not ev.baseline_failed
    assert ev.covered == ["covered"] and ev.uncovered == (["missing"] if uncovered else [])
    assert ev.passed(1.0) is (not uncovered)
    saved = json.loads((out / "evidence.json").read_text())
    assert saved["skipped"] == ev.skipped and saved["score"] is None and saved["mutants"] == []
    assert saved["covered"] == ev.covered and saved["uncovered"] == ev.uncovered


@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/non-python-comment-lines-excluded")
def test_added_lines_retains_non_python_prefix_and_declaration_exclusions():
    lines = ['# True 10', '// True 20', '/* True 30', '* True 40',
             'import module', 'from module import value', 'using X = Y;',
             'template<class T>', 'static_assert(true);', 'value = 50']
    for path in ('src/example.cpp', 'src/example.tsx'):
        diff = f'+++ b/{path}\n@@ -0,0 +1,10 @@\n' + ''.join('+' + line + '\n' for line in lines)
        assert evidence.added_lines(diff, M.mutation_paths) == [(path, 10, 'value = 50')]
    diff = '+++ b/src/example.py\n@@ -0,0 +1,10 @@\n' + ''.join('+' + line + '\n' for line in lines)
    assert evidence.added_lines(diff, M.mutation_paths) == [
        ('src/example.py', n, lines[n - 1]) for n in (1, 2, 3, 4, 10)]


def non_python_added_lines(source, path):
    diff = (f'+++ b/{path}\n@@ -0,0 +1,{len(source.splitlines())} @@\n'
            + ''.join('+' + line + '\n' for line in source.splitlines()))
    return evidence.added_lines(diff, M.mutation_paths)


@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/non-python-comment-lines-excluded")
@pytest.mark.parametrize("suffix", ["cc", "js", "jsx", "tsx", "css", "other"])
def test_non_python_blocks_exclude_bare_prose_before_sampling(suffix):
    path = f"src/example.{suffix}"
    source = ('  # True 10\n'
              '\t// False 20\n'
              ' * True 30\n'
              ' /* opening 40\n'
              'bare prose 50 == 60 and False\n'
              '\n'
              'more prose 70 <= 80\n'
              'closing prose 90 */ \t\n'
              'value = 100;\n'
              '/** one line 110 */\n'
              'single = 115;\n'
              '/* another block 120\n'
              'bare prose 130\n'
              ' */\n'
              'limit = 140;\n')
    lines = non_python_added_lines(source, path)
    eligible = [(path, 9, 'value = 100;'), (path, 11, 'single = 115;'), (path, 15, 'limit = 140;')]
    for seed in range(6):
        for count in (1, 100):
            mutants = evidence.generate(lines, count, seed, sources={path: source})
            assert mutants == evidence.generate(eligible, count, seed, sources={path: source})
            assert len(mutants) == min(count, 3)
            for mut in mutants:
                assert mut.before == source.splitlines()[mut.line - 1]
                assert mut.after == {9: 'value = 101;', 11: 'single = 116;', 15: 'limit = 141;'}[mut.line]


@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/non-python-comment-lines-excluded")
@pytest.mark.parametrize("path", ["src/example.cc", "src/example.tsx"])
def test_empty_block_does_not_absorb_code_before_the_next_block(path):
    source = ('/**/\n'
              'value = 10;\n'
              '/* supported block\n'
              'bare prose 20\n'
              '*/\n'
              'limit = 30;\n')
    mutants = evidence.generate(non_python_added_lines(source, path), 100, 1,
                                sources={path: source})
    assert sorted((mut.line, mut.before, mut.after) for mut in mutants) == [
        (2, 'value = 10;', 'value = 11;'),
        (6, 'limit = 30;', 'limit = 31;'),
    ]


@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/non-python-tracking-stays-line-level")
@pytest.mark.parametrize("source, eligible_lines", [
    ('value; // prose 10\n', {1}),
    ('value; /* prose 10 */\n', {1}),
    ('value /* prose 10 */;\n', {1}),
    ('{/* prose 10 */}\n', {1}),
    ('value; /* opening 10\nbare prose 20\n*/\n', {1, 2}),
    ('/* opening 10\nbare prose 20\nvalue = 30;\n', {2, 3}),
    ('/* opening 10\nbare prose 20\n*/ value = 30;\nvalue = 40;\n*/\n', {2, 4}),
    # A single non-whitespace character after */ still makes a mixed boundary.
    ('/* opening 10\nbare prose 20\n*/;\nvalue = 40;\n*/\n', {2, 4}),
    ('/* opening 10\nbare prose 20\nclosing 30 */}\t\n*/\n', {2, 3}),
    ('/* opening 10\nbare prose 20\nclosing 30 */ value = 40;\n*/\n', {2, 3}),
    ('/* prose 10 */ value = 20;\nvalue = 30;\n*/\n', {2}),
    ('/* prose 10 */ /* second opening\nbare prose 20\n*/\n', {2}),
    ('/* opening 10\nbare prose 20\n*/ // trailing comment 30\n*/\n', {2}),
    ('value = "unterminated 10\npattern = / 20\ntext = ` ${ 30\n', {1, 2, 3}),
])
@pytest.mark.parametrize("path", ["src/example.cc", "src/example.tsx"])
def test_unsupported_non_python_forms_add_no_protected_range(source, eligible_lines, path):
    lines = non_python_added_lines(source, path)
    mutants = evidence.generate(lines, 100, 1, sources={path: source})
    assert {mut.line for mut in mutants} == eligible_lines
    # A subsequent supported block is still recognized after a terminated,
    # rejected form. Keep the unmatched-opener fixture at EOF.
    if '*/' in source:
        following = source + '/* supported\nbare prose 50\n*/\nvalue = 60;\n'
        mutants = evidence.generate(non_python_added_lines(following, path), 100, 1,
                                    sources={path: following})
        assert {mut.line for mut in mutants} == eligible_lines | {len(following.splitlines())}


@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/non-python-comment-lines-excluded")
@pytest.mark.scenario("mutation-selection/committed-source-context/unchanged-context-determines-eligibility")
@pytest.mark.parametrize("path", ["src/example.cc", "src/example.js"])
@pytest.mark.parametrize("change_code", [False, True])
def test_gather_uses_committed_block_boundaries_across_hunks(
        committed_project, tmp_path, monkeypatch, path, change_code):
    before = ('/* unchanged opening\n'
              'first prose 10\n'
              'unchanged prose\n'
              'last prose 20\n'
              '*/\n'
              'value = 30;\n')
    after = before.replace('prose 10', 'prose 11').replace('prose 20', 'prose 21')
    if change_code:
        after = after.replace('value = 30', 'value = 31')
    repo, base, head = committed_project({path: before}, {path: after})
    diff = seal.git(repo, "diff", "--unified=0", base, head).decode()
    assert diff.count('@@ -') == (3 if change_code else 2)
    assert '/*' not in diff and '*/' not in diff
    # Local code occupies the committed prose lines, and a local block wraps
    # the committed code line, so both directions of eligibility differ.
    dirty = '\nfirst = 11;\n\nlast = 21;\n/*\nvalue = 31;\n*/\n'
    (repo / path).parent.mkdir(parents=True)
    (repo / path).write_text(dirty)
    executed = []
    forbid_execution(monkeypatch)

    def mutate(source_repo, revision, mutants, effective, out):
        assert source_repo == repo and revision == head and effective == M
        executed.extend(mutants)
        for mut in mutants:
            mut.result = "killed"
        return True

    if change_code:
        monkeypatch.setattr(evidence, "mutate", mutate)
    ev = evidence.gather(repo, base, head, [], set(), M, tmp_path / "out", 1)
    if change_code:
        assert [(mut.path, mut.line, mut.before, mut.after) for mut in ev.mutants] == [
            (path, 6, 'value = 31;', 'value = 32;')]
        assert executed == ev.mutants and ev.score == 1.0
    else:
        assert ev.mutants == [] and ev.score is None
        assert ev.skipped == "no mutable lines added under mutation_paths"
    assert (repo / path).read_text() == dirty


@pytest.mark.scenario("mutation-selection/extension-operator-selection/dart-operator-directions")
@pytest.mark.scenario("mutation-selection/extension-operator-selection/suffix-dispatch-and-token-boundaries")
@pytest.mark.parametrize("suffix", ["dart", "ts", "swift", "kt", "c", "cpp"])
def test_c_family_operators_for_dart_files(suffix):
    cases = [("true", "false", "true"), ("false", "true", "false"),
             ("a&&b", "a||b", "&&"), ("a||b", "a&&b", "||"),
             ("a==b", "a!=b", "=="), ("a!=b", "a==b", "!="),
             ("a<b", "a<=b", "<"), ("a<=b", "a<b", "<="),
             ("a>b", "a>=b", ">"), ("a>=b", "a>b", ">="),
             ("!ready", "ready", "!"), ("!(ready)", "(ready)", "!"),
             ("!!ready", "!ready", "!"), ("! \t_ready", " \t_ready", "!"),
             ("!$ready", "$ready", "!"), ("!é", "é", "!")]
    blocked = ["trueValue false_ _true $false true$ étrue falseé true0 0false",
               "a===b a!==b a<=>b a<<b a>>b a>>>b a->b a=>b a->=b",
               "a<<=b a>>=b a=<b a!<b a>!=b a=!=b a!==b",
               "a&&&b a||||b a&&=b a||=b", "value! $! )! ]! }! !42 !",
               "42 a + b a - b True False a and b a or b",
               "if ready:\n    return None"]
    source = "\n".join(before for before, _, _ in cases) + "\n" + "\n".join(blocked)
    path = f"src/example.{suffix}"
    mutants = select(source, path=path)
    assert sorted((m.line, m.before, m.after, m.op) for m in mutants) == [
        (n, before, after, op) for n, (before, after, op) in enumerate(cases, 1)]
    assert [op for op, _, _ in evidence.C_FAMILY_OPERATORS] == [case[2] for case in cases[:11]]


@pytest.mark.scenario("mutation-selection/extension-operator-selection/suffix-dispatch-and-token-boundaries")
@pytest.mark.parametrize("suffix", ["js", "tsx", "cc", "DART"])
def test_unsupported_suffixes_keep_exact_legacy_selection(suffix):
    source = 'text = "True"\nvalue = 10\na < b\n!ready\ntrue\n'
    mutants = select(source, path=f"src/example.{suffix}", seed=7)
    assert [(m.line, m.op, m.after) for m in mutants] == [
        (3, "<", "a <= b"), (1, "True", 'text = "False"'), (2, "int", "value = 11")]


@pytest.mark.scenario("mutation-selection/extension-operator-selection/python-operators-remain-unchanged")
@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/python-docstrings-and-comments-excluded")
@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/python-runtime-strings-retain-their-meaning")
@pytest.mark.scenario("mutation-selection/control-flow-candidate-compatibility/mixed-operators-retain-stable-spreading")
@pytest.mark.parametrize("path", ["src/example.py", "src/example.pyi"])
@pytest.mark.parametrize("seed,expected", [
    (0, [(3, "False", "other = True"), (12, "True", "        return False"),
         (11, "negate-condition", "    if not ready:  # True 99"), (7, "-", "sub = a + b"),
         (4, "<=", "compare = a < b"), (5, "and", "logic = a or b"),
         (9, "int", 'text = "# 21"'), (6, "+", "add = a - b"), (2, "True", "flag = False"),
         (13, "int", "    return 8"), (8, "int", "number = 11")]),
    (7, [(9, "int", 'text = "# 21"'), (13, "int", "    return 8"),
         (5, "and", "logic = a or b"), (12, "drop-return", "        pass"),
         (11, "negate-condition", "    if not ready:  # True 99"), (6, "+", "add = a - b"),
         (3, "False", "other = True"), (2, "True", "flag = False"),
         (8, "int", "number = 11"), (4, "<=", "compare = a < b"), (7, "-", "sub = a + b")]),
])
def test_python_operators_unchanged(path, seed, expected):
    source = ('"""True 90"""\nflag = True\nother = False\ncompare = a <= b\n'
              'logic = a and b\nadd = a + b\nsub = a - b\nnumber = 10\ntext = "# 20"\n'
              'def f(ready):\n    if ready:  # True 99\n        return True\n    return 7\n')
    for count in (1, 3, 100):
        mutants = select(source, path=path, seed=seed, count=count)
        assert mutants == [evidence.Mutant(path, n, op, source.splitlines()[n - 1], after)
                           for n, op, after in expected[:count]]


@pytest.mark.scenario("mutation-selection/c-family-quote-line-exclusion/quoted-lines-are-excluded")
@pytest.mark.scenario("mutation-selection/c-family-comment-line-exclusion/existing-skips-and-empty-pools-are-preserved")
@pytest.mark.scenario("mutation-selection/c-family-comment-line-exclusion/prefixes-and-complete-blocks-are-protected")
@pytest.mark.scenario("mutation-selection/control-flow-candidate-compatibility/existing-exclusions-cover-new-operators")
@pytest.mark.scenario("mutation-selection/control-flow-candidate-compatibility/mixed-operators-retain-stable-spreading")
@pytest.mark.parametrize("suffix", ["dart", "ts", "swift", "kt", "c", "cpp"])
def test_c_family_exclusions_precede_sampling(suffix):
    excluded = ['true; s = "text";', "false; s = 'text';", 'true; s = `text`;',
                'true; // "', "false; /* ' */", r'true; \"',
                'val s = """${"""true"""}"""', ' \t// true', '  /* false */', ' * a<b',
                '/* opening', 'true a<b', '*/', '/**/', '/** true */', 'cosmetic = true;',
                'if ready:', '    return None']
    source = '\n'.join(excluded + ['return true && a<b;', '!ready;'])
    path = f"src/example.{suffix}"
    lines = candidates(source, path)
    for seed in (0, 1, 7):
        for count in (1, 100):
            mutants = evidence.generate(lines, count, seed, sources={path: source},
                                        mutation_skip=("cosmetic",))
            assert mutants == evidence.generate(lines[-2:], count, seed, sources={path: source})
            assert len(mutants) == min(count, 2)
            assert len({(m.path, m.line) for m in mutants}) == len(mutants)
            for mut in mutants:
                assert mut.before == source.splitlines()[mut.line - 1]
                assert mut.op in {"true", "&&", "<", "!"}


@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/non-python-tracking-stays-line-level")
@pytest.mark.parametrize("source,eligible", [
    ('value; // true\n', {1}), ('value; /* true */\n', {1}),
    ('value /* true */;\n', {1}), ('{/* true */}\n', {1}),
    ('value; /* true\nfalse\n*/\n', {1, 2}), ('/* true\nfalse\na<b\n', {2, 3}),
    ('/* true\nfalse\n*/ a<b\na>b\n*/\n', {2, 4}),
    ('/* true\nfalse\nend a<b */}\n*/\n', {2, 3}),
    ('/* true */ /* second\nfalse\n*/\n', {2}),
    ('/* true\nfalse\n*/ // a<b\n*/\n', {2}),
    ('true; "unterminated\na/b<c\nfalse; ` ${\n', {2}),
])
@pytest.mark.parametrize("suffix", ["dart", "ts", "swift", "kt", "c", "cpp"])
def test_c_family_unsupported_blocks_remain_line_level(source, eligible, suffix):
    path = f"src/example.{suffix}"
    mutants = evidence.generate(non_python_added_lines(source, path), 100, 1, sources={path: source})
    assert {m.line for m in mutants} == eligible


@pytest.mark.scenario("mutation-selection/control-flow-candidate-compatibility/existing-exclusions-cover-new-operators")
@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/non-python-comment-lines-excluded")
@pytest.mark.parametrize("suffix", ["dart", "ts", "swift", "kt", "c", "cpp"])
def test_c_family_added_line_exclusions(suffix):
    path = f"src/example.{suffix}"
    source = ('# true\n// true\n/* true */\n* true\nimport true\nfrom true\n'
              'using true\ntemplate true\nstatic_assert(true);\nreturn true;\n')
    diff = (f'+++ b/{path}\n@@ -0,0 +1,10 @@\n'
            + ''.join('+' + line + '\n' for line in source.splitlines())
            + f'+++ b/outside.{suffix}\n@@ -0,0 +1 @@\n+true\n')
    lines = evidence.added_lines(diff, M.mutation_paths)
    assert lines == [(path, 10, 'return true;')]
    assert evidence.generate(lines, 100, 1, sources={path: source}) == [
        evidence.Mutant(path, 10, "true", 'return true;', 'return false;')]


@pytest.mark.scenario("mutation-selection/c-family-quote-line-exclusion/other-lines-remain-eligible")
@pytest.mark.scenario("mutation-selection/c-family-comment-line-exclusion/prefixes-and-complete-blocks-are-protected")
@pytest.mark.scenario("mutation-selection/c-family-comment-line-exclusion/existing-skips-and-empty-pools-are-preserved")
@pytest.mark.scenario("mutation-selection/prose-is-never-mutated/non-python-comment-lines-excluded")
@pytest.mark.parametrize("suffix,context", [
    ("dart", 'var s = "${ready ? "true" : "false"}";'),
    ("kt", 'val s = """${"""true"""}"""'), ("ts", 'const ratio = a / b;'),
    ("swift", 'let ratio = a / b'), ("c", 'int ratio = a / b;'), ("cpp", 'auto ratio = a / b;'),
])
@pytest.mark.parametrize("mode", ["mutable", "automatic", "configured"])
@pytest.mark.parametrize("uncovered", [False, True])
def test_c_family_committed_context_and_empty_pools(
        committed_project, tmp_path, monkeypatch, suffix, context, mode, uncovered):
    path = f"src/example.{suffix}"
    before = context + '\n/* unchanged\nold true\nunchanged\nold a<b\n*/\nflag = false;\n'
    after = before.replace('old ', 'new ')
    if mode != "automatic":
        after = after.replace('flag = false;', 'flag = true;')
    repo, base, head = committed_project({path: before}, {path: after})
    diff = seal.git(repo, "diff", "--unified=0", base, head).decode()
    assert diff.count('@@ -') == (2 if mode == "automatic" else 3)
    assert f'+{context}\n' not in diff and '/*' not in diff and '*/' not in diff
    (repo / path).parent.mkdir(parents=True)
    dirty = 'true\ntrue\ntrue\ntrue\n/*\ntrue\ntrue\n*/\n'
    (repo / path).write_text(dirty)
    forbid_execution(monkeypatch)
    executed = []

    def mutate(source_repo, revision, mutants, effective, out):
        assert mode == "mutable", "empty pools must not start mutation execution"
        assert source_repo == repo and revision == head
        executed.extend(mutants)
        for mut in mutants:
            mut.result = "killed"
        return True

    monkeypatch.setattr(evidence, "mutate", mutate)
    m = replace(M, mutation_skip=("flag",) if mode == "configured" else ())
    out = tmp_path / "out"
    ev = evidence.gather(repo, base, head, ["covered", "missing"] if uncovered else ["covered"],
                         {"covered"}, m, out, 1)
    expected = ([evidence.Mutant(path, 7, "true", "flag = true;", "flag = false;", "killed")]
                if mode == "mutable" else [])
    assert ev.mutants == executed == expected
    assert ev.skipped == ("" if expected else "no mutable lines added under mutation_paths")
    assert ev.score == (1.0 if expected else None) and not ev.unmeasured and not ev.baseline_failed
    assert ev.covered == ["covered"] and ev.uncovered == (["missing"] if uncovered else [])
    assert ev.passed(1.0) is (not uncovered)
    saved = json.loads((out / "evidence.json").read_text())
    assert saved["score"] == ev.score and saved["skipped"] == ev.skipped
    assert (repo / path).read_text() == dirty


@pytest.mark.scenario("mutation-selection/control-flow-candidate-compatibility/analysis-failures-still-propagate")
@pytest.mark.scenario("mutation-selection/c-family-comment-line-exclusion/existing-skips-and-empty-pools-are-preserved")
@pytest.mark.parametrize("path,source", [("src/example.dart", 'true; "skip"'),
                                        ("src/example.py", 'if skip:'),
                                        ("src/example.pyi", 'if skip:')])
def test_extension_analysis_failures_precede_exclusions(tmp_path, monkeypatch, path, source):
    forbid_execution(monkeypatch)
    for sources in ({}, {path: source}) if path.endswith((".py", ".pyi")) else ({},):
        with pytest.raises(ValueError, match=path):
            evidence.generate(candidates(source, path), 1, 1, sources=sources, mutation_skip=("skip",))

    def git(repo, *args):
        if args[0] == "diff":
            return f'+++ b/{path}\n@@ -0,0 +1 @@\n+{source}\n'.encode()
        raise RuntimeError("missing committed source")

    monkeypatch.setattr(evidence, "git", git)
    with pytest.raises(ValueError, match=path):
        evidence.gather(tmp_path, "base", "head", [], set(), replace(M, mutation_skip=("skip",)),
                        tmp_path / "out", 1)
    assert not (tmp_path / "out/evidence.json").exists()
