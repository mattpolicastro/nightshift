"""Prose cleanup preserves documentation and executable equivalence claims."""

import ast
import io
from pathlib import Path
import tokenize

import pytest

from specloop import evidence, manifest, verify


ROOT = Path(__file__).resolve().parents[1]
MARKER = "mutation-equivalent:"
DOCSTRING_NODES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

# Keep the documentation at every known cleanup site, including #8's job code.
DOCUMENTATION = {
    "nightshift/specloop_job.py": {
        "list_jobs": "Return local attempts and diagnostics, independent of receipts/handles.",
        "control_lock": """Serialize stop admission, finalization and collection across processes.

This is separate from the inherited lifetime lock. Never wait for that
lock or for process termination while holding this short-lived lock.""",
        "request_stop": """Admit stop intent, returning stopping, stopped, or finished.

The control caller must resolve the job directory and supply an ownership
verifier returning the verified process identity (or raising on refusal).
Verification runs under the lock, before accepting intent. Only a stopping
return permits the caller to proceed to signalling/termination checks;
those happen after this lock is released. A retry re-verifies ownership
without replacing the original accepted intent.""",
        "_group_identities": """Remember live witnesses while the wrapper still proves ownership.

SIGTERM may kill the wrapper before an ignoring descendant. A surviving
member with the same start identity and group/session proves continuity
for escalation within this call; a later stop still requires the leader.""",
        "collect": """Return a completed result, or None while the job is live or cancelled.

A dead/failed job is reported through RepoHealth before a later poll may
retry (after the usual GitHub and specloop state checks). Intentional stop
keeps the parent's job reference pending, without failure or automatic retry.""",
    },
    "nightshift/specloop_client.py": {
        "SpecloopError": "An invocation failed or did not speak the version-1 protocol.",
    },
    "specloop/state.py": {
        "Run": """One admitted change: its directory, state and log.

require_existing makes missing state an error, including on reload. By
default, missing state yields new in-memory state for admission.""",
    },
    "specloop/evidence.py": {
        "_python_protected_spans": "Map docstrings and comments to physical lines and character offsets.",
        "_mutate_one": "Run one mutation in a private source and build, retaining its diagnostics.",
        "mutate": "Classify each mutant. False when the unmutated head itself fails.",
    },
}


@pytest.fixture
def package_sources():
    return {
        path.relative_to(ROOT).as_posix(): path.read_text()
        for package in ("nightshift", "specloop")
        for path in sorted((ROOT / package).rglob("*"))
        if path.suffix in (".py", ".pyi")
    }


@pytest.mark.scenario("mutation-selection/prose-equivalence-annotations-removed/prose-markers-removed")
def test_prose_equivalence_annotations_removed(package_sources):
    assert "nightshift/specloop_job.py" in package_sources
    assert DOCUMENTATION.keys() <= package_sources.keys()
    for path, source in package_sources.items():
        tree = ast.parse(source, filename=path)
        lines = source.splitlines()
        documented = {}
        for node in ast.walk(tree):
            if not isinstance(node, DOCSTRING_NODES):
                continue
            doc = ast.get_docstring(node)
            if doc is None:
                continue
            assert "mutation-equivalent" not in doc, (path, getattr(node, "name", "module"))
            # The executor also recognizes the preceding physical line. These
            # obsolete comments sit outside the docstring's AST string value.
            first_line = node.body[0].lineno
            if first_line > 1:
                assert MARKER not in lines[first_line - 2], (path, first_line - 1)
            documented[getattr(node, "name", "module")] = doc
        for name, expected in DOCUMENTATION.get(path, {}).items():
            assert documented.get(name) == expected, (path, name)

    jobs = package_sources["nightshift/specloop_job.py"]
    assert "# mutation-equivalent: This explanatory prose does not control diagnostic handling." not in jobs
    assert (
        "    # Rows sort by stage, repository, numeric item and full ID. Malformed jobs\n"
        "    # remain visible as unknown when their directory context can be recovered;\n"
        "    # errors are returned separately so callers can display good rows and fail.\n"
        "    # A live lock without metadata is starting (or a legacy live job); absent\n"
        "    # process metadata is always exposed as None, never guessed from a PID.\n"
        "    # identity_available also distinguishes failed process-start lookups.\n"
    ) in jobs


@pytest.mark.scenario("mutation-selection/prose-equivalence-annotations-removed/real-code-annotations-retained")
def test_repository_code_annotations_retained(package_sources):
    assert (
        "# mutation-equivalent: No match can start at a span's end, so < and <= reject the same candidates.\n"
        "                if any(m.start() < end and m.end() > start for start, end in protected[path].get(n, ())):"
    ) in package_sources["specloop/evidence.py"]
    assert (
        "# mutation-equivalent: {0,60} -> {1,60} preserves matches: word boundaries between the negation "
        "and edit/chang/touch/modif require at least one separator.\n"
        "NEGATED = re.compile("
    ) in package_sources["specloop/propose.py"]
    assert evidence.EQUIVALENT == MARKER
    assert "line above it starting `mutation-equivalent:`" in package_sources["specloop/loop.py"]


@pytest.fixture(params=["inline", "preceding"])
def annotated_code(request):
    annotation = "# mutation-equivalent: at equality both branches return lower; True and False 10 <= 20"
    condition = "    if value < lower:"
    annotated = (condition + "  " + annotation if request.param == "inline"
                 else "    " + annotation + "\n" + condition)
    return "def clamp(value, lower):\n" + annotated + "\n        return lower\n    else:\n        return value\n"


@pytest.mark.scenario("mutation-selection/prose-equivalence-annotations-removed/real-code-annotations-retained")
def test_real_code_equivalence_path(annotated_code, tmp_path, monkeypatch):
    path = "clamp.py"
    lines = annotated_code.splitlines()
    mutants = evidence.generate(
        [(path, n, line) for n, line in enumerate(lines, 1)], 100, 1,
        sources={path: annotated_code},
    )
    assert len(mutants) == 1  # Mutable words/numbers in either comment are excluded.
    mut = mutants[0]
    assert mut.op == "<"
    assert mut.before == lines[mut.line - 1]
    assert mut.after == mut.before.replace("value < lower", "value <= lower")
    changed = lines.copy()
    changed[mut.line - 1] = mut.after
    mutated_source = "\n".join(changed) + "\n"

    def comments(source):
        return [token.string for token in tokenize.generate_tokens(io.StringIO(source).readline)
                if token.type == tokenize.COMMENT]

    assert comments(mutated_source) == comments(annotated_code)
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / path).write_text(annotated_code)
    m = manifest.parse({
        "run": {"repo": str(tmp_path), "change": "example", "branch": "main"},
        "roles": {"implement": "p", "review": "p"},
        "profiles": {"p": {"adapter": "codex", "model": "m"}},
        "paths": {"allowed": [path]},
        "verify": [{"id": "test", "run": ["must-not-run"]}],
        "gates": {"mutation_steps": ["test"]},
    })

    def unexpected(*args, **kwargs):
        pytest.fail("equivalent code must not run verification commands")

    monkeypatch.setattr(verify, "run_steps", unexpected)
    monkeypatch.setattr(verify, "run_extracted", unexpected)
    out = tmp_path / "out"
    assert evidence._mutate_one(1, mut, baseline, "head", m.verify, m, out) == "equivalent"
    assert (baseline / path).read_text() == annotated_code
    assert not (out / "mutant-1").exists()
