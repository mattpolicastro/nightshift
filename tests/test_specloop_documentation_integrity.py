"""Offline documentation checks; live reference status and readability stay human.

The task explicitly permits additive tests. Diff review and the full pytest
result remain acceptance checks, not claims a test can make about itself.
"""

import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from specloop import seal, tasks
from test_specloop_seal_verify import M, repo  # noqa: F401 -- shared git fixture


ROOT = Path(__file__).resolve().parents[1]
DOCS = ("README.md", "KNOWN-LIMITATIONS.md", "docs/specloop/README.md")
REPOSITORY = "https://github.com/mattpolicastro/nightshift"
LINK = re.compile(r"\[([^\[\]\n]+)\]\(([^\s)]+)\)")

# Checked against first-parent merge subjects through a684e40. Keep the
# offline checks usable in the verifier's extracted source, which has no .git.
# Each entry names a behavior beside its implementation PR, not its issue.
CITATIONS = (
    ("blocked-by:", 29),
    ("latest application", 42),
    ("--idea-file", 35),
    ("source-qualified GitHub ID", 61),
    ("status_error", 32),
    ("verifies process-start identity", 28),
    ("touchpoints", 53),
    ("pytest-xdist", 54),
    ("Base preparation implements", 59),
    ("four drafting rounds", 37),
    ("Before drafting, the harness snapshots", 36),
    ("file.py::function", 60),
    ("overlap feedback", 36),
    ("unconditional ban", 37),
    ("Implementers do not receive the map", 43),
    ("Human-only checks belong", 30),
    ("MODIFIED", 36),
    ("separate read-only review", 57),
    ("The checklist covers all eight items", 62),
    ("mutation_parallel = 2", 27),
    ("selected-test timeout", 44),
    ("Full committed source protects", 48),
    ("docstring annotation was removed", 63),
    ("Two operators add candidates", 64),
)
# Issue identities from the approved source map; this says nothing about their
# current open/closed state. In particular, PRs #36/#37/#54/#62 are not issues.
ISSUES = {4, 5, 6, 8, 10, 11, 12, 14, 17, 21, 24, 31, 33, 34, 46, 50, 51, 58}


@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/references-and-reader-checks-are-reviewable"
)
@pytest.mark.parametrize("path", DOCS)
def test_reference_labels_distinguish_prs_from_issues(path):
    references = []
    merged = {number for _, number in CITATIONS}
    for label, target in LINK.findall((ROOT / path).read_text()):
        if reference := re.fullmatch(r"(PR|issue) #(\d+)", label):
            kind, number = reference.groups()
            endpoint = "pull" if kind == "PR" else "issues"
            assert target == f"{REPOSITORY}/{endpoint}/{number}"
        if target.startswith((f"{REPOSITORY}/pull/", f"{REPOSITORY}/issues/")):
            kind, number = target.removeprefix(f"{REPOSITORY}/").split("/")
            assert label == f"{'PR' if kind == 'pull' else 'issue'} #{number}"
            if kind == "pull":
                assert int(number) in merged, f"Unverified merged PR: {target}"
            else:
                assert int(number) in ISSUES, f"Issue absent from the source map: {target}"
            references.append(target)
    assert references, f"{path} lost its references"


@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/references-and-reader-checks-are-reviewable"
)
@pytest.mark.parametrize("behavior,number", CITATIONS)
def test_guide_keeps_implementation_citations_beside_behavior(behavior, number):
    guide = (ROOT / "docs/specloop/README.md").read_text()
    paragraphs = [" ".join(p.split()) for p in guide.split("\n\n")]
    citation = f"[PR #{number}]({REPOSITORY}/pull/{number})"
    assert any(behavior in p and citation in p for p in paragraphs), (
        f"{behavior!r} needs its adjacent {citation}"
    )


@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/references-and-reader-checks-are-reviewable"
)
def test_review_checklist_stays_in_design_and_pending_work_stays_open():
    change = "nightshift-issue-67-docs-bring-the-specloop-guide-and-known"
    # Also works after OpenSpec archives this change.
    designs = list((ROOT / "openspec/changes").glob(f"**/*{change}/design.md"))
    assert len(designs) == 1
    design = " ".join(designs[0].read_text().split())
    checklist = design.split("Human checks for the implementation PR, never agent tasks:", 1)[1]
    checklist = checklist.split("## Risks", 1)[0]
    for check in (
        "Matt reads the guide once as a new reader",
        "front door through publication and a pause",
        "wording is short and concrete",
        "confirm every cited PR exists and is merged",
        "check the states of cited issues",
        "confirm issue #6 is resolved",
        "current #17 ledger, including #34",
        "Record discrepancies for a docs correction before merge",
        "Local merge history cannot establish current issue state",
        "No live workflow, service restart or actual job stop is needed",
    ):
        assert check in checklist
    implementation = tasks.parse(designs[0].with_name("tasks.md").read_text())
    assert implementation
    for task in implementation:
        assert not re.search(r"\bMatt\b|On GitHub, confirm|reads the guide once", task.text)

    limitations = " ".join((ROOT / "KNOWN-LIMITATIONS.md").read_text().split())
    assert "these gaps remain unresolved" in limitations
    for qualification in (
        "An admission that completes while paused can still build",
        "There is no retry command",
        "can undercount when records are missing or overwritten",
        "automatic JSX style filtering is descoped",
        "do not provide automated intake or publication for other hosts",
    ):
        assert qualification in limitations


def heading_ids(markdown):
    """GitHub heading slugs for the headings used by these local links."""
    ids = set()
    counts = {}
    for heading in re.findall(r"^#{1,6} (.+)$", markdown, re.MULTILINE):
        heading = LINK.sub(r"\1", heading)
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        ids.add(f"{slug}-{count}" if count else slug)
    return ids


@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/scope-and-regression-checks-remain-intact"
)
@pytest.mark.parametrize("path", DOCS)
def test_relative_documentation_links_resolve_including_heading_fragments(path):
    document = ROOT / path
    links = 0
    for _, target in LINK.findall(document.read_text()):
        url = urlsplit(target)
        if url.scheme or url.netloc:
            continue
        destination = document.parent / unquote(url.path) if url.path else document
        assert destination.exists(), f"{path}: broken link {target}"
        if url.fragment:
            assert unquote(url.fragment) in heading_ids(destination.read_text()), (
                f"{path}: missing heading in {target}"
            )
        links += 1
    assert links, f"{path} lost its local links"
    guide = (ROOT / "docs/specloop/README.md").read_text()
    assert "Run `uv run pytest` for implementation acceptance." in guide


@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/scope-and-regression-checks-remain-intact"
)
def test_docs_only_seal_preserves_runtime_and_test_assertions(repo):
    bare, work, base = repo
    protected = {
        "nightshift/queue.py": "def eligible():\n    return False\n",
        "tests/test_queue.py": "def test_eligible():\n    assert eligible() is False\n",
        "docs/unrelated.md": "Unrelated instructions.\n",
    }
    base = seal.commit_files(bare, base, dict.fromkeys(DOCS, "Old docs.\n") | protected, "fixture")
    seal.git(work, "fetch", str(bare), base)
    seal.git(work, "reset", "--hard", base)
    for path in DOCS:
        (work / path).write_text("Updated docs.\n")
    (work / "nightshift/queue.py").write_text("def eligible():\n    return True\n")
    (work / "tests/test_queue.py").write_text("def test_eligible():\n    pass\n")
    (work / "docs/unrelated.md").unlink()
    sealed = seal.seal(bare, work, base, replace(M, allowed=DOCS), "docs update")
    assert sorted(sealed.changed) == sorted(DOCS)
    assert {v.split(":", 1)[0] for v in sealed.violations} == set(protected)
    assert sealed.sha
    for path, original in protected.items():
        assert seal.git(bare, "show", f"{sealed.sha}:{path}").decode() == original
    assert set(seal.git(bare, "diff", "--name-only", base, sealed.sha).decode().splitlines()) == set(DOCS)
