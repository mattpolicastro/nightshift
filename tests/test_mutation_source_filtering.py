"""Full committed Python source determines mutation eligibility."""

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
