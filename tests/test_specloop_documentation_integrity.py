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



@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/references-and-reader-checks-are-reviewable"
)
@pytest.mark.parametrize("path", DOCS)
def test_reference_labels_distinguish_prs_from_issues(path):
    references = []
    for label, target in LINK.findall((ROOT / path).read_text()):
        if reference := re.fullmatch(r"(PR|issue) #(\d+)", label):
            kind, number = reference.groups()
            endpoint = "pull" if kind == "PR" else "issues"
            assert target == f"{REPOSITORY}/{endpoint}/{number}"
        if target.startswith((f"{REPOSITORY}/pull/", f"{REPOSITORY}/issues/")):
            kind, number = target.removeprefix(f"{REPOSITORY}/").split("/")
            assert label == f"{'PR' if kind == 'pull' else 'issue'} #{number}"
            references.append(target)
    assert references, f"{path} lost its references"


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
