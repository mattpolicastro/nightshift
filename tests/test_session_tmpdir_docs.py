"""Physical-line structure only; retention meaning and live-host checks need review."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUN_START = "**Run state** lives in "
RUN_END = "The guarded migration uses these build-state mappings:"
GAPS = "## Open gaps"
REPOSITORY = "https://github.com/mattpolicastro/nightshift"
LINK = re.compile(r'\[([^\[\]\\\n]+)\]\(([^\s()\[\]\\<>"\']+)\)')
pytestmark = pytest.mark.scenario(
    "specloop-documentation/session-scratch-retention-guidance/host-validation-remains-explicit"
)


def passage(text, section):
    # Deliberately no Markdown parsing or stripping, including in fences/HTML.
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines)
              if (line.startswith(RUN_START) if section == "run" else line == GAPS)]
    assert len(starts) == 1, "missing or duplicate start boundary"
    start, = starts
    if section == "run":
        ends = [i for i, line in enumerate(lines) if line == RUN_END]
        assert len(ends) == 1, "missing or duplicate end boundary"
        end, = ends
    else:
        ends = [i for i, line in enumerate(lines) if i > start and line.startswith("## ")]
        assert ends, "missing following heading boundary"
        end = ends[0]
    assert end > start, "reversed boundaries"
    return "\n".join(lines[start:end])


def inline_links(text):
    links = []
    for line in text.split("\n"):
        offset = 0
        while candidate := re.search(r"[\[\]]", line[offset:]):
            start = offset + candidate.start()
            match = LINK.match(line, start)
            assert match and (start == 0 or line[start - 1] not in "\\!"), (
                f"ambiguous inline link: {line}"
            )
            links.append(match.groups())
            offset = match.end()
    return links


def check_links(document, text):
    links = inline_links(text)
    for label, target in links:
        reference = re.fullmatch(r"(issue|PR) #([1-9][0-9]*)", label)
        github = re.fullmatch(re.escape(REPOSITORY) + r"/(issues|pull)/([1-9][0-9]*)", target)
        if reference or target.startswith((f"{REPOSITORY}/issues", f"{REPOSITORY}/pull")):
            assert reference and github, (label, target)
            kind, number = github.groups()
            assert label == f"{'issue' if kind == 'issues' else 'PR'} #{number}"
        url = urlsplit(target)
        if url.scheme or url.netloc:
            assert url.scheme in ("http", "https") and url.netloc, target
        else:
            destination = document.parent / unquote(url.path) if url.path else document
            assert destination.exists(), f"broken local destination: {target}"
    return links


@pytest.mark.scenario(
    "specloop-documentation/session-scratch-retention-guidance/operator-distinguishes-scratch-from-evidence"
)
@pytest.mark.parametrize("path,section", [
    ("docs/specloop/README.md", "run"), ("KNOWN-LIMITATIONS.md", "gaps"),
])
def test_session_scratch_passage_references(path, section):
    document = ROOT / path
    links = check_links(document, passage(document.read_text(), section))
    assert ("issue #116", f"{REPOSITORY}/issues/116") in links


@pytest.mark.parametrize("section,start,end", [
    ("run", RUN_START + "`state/`", RUN_END), ("gaps", GAPS, "## Next"),
])
@pytest.mark.parametrize("before,after", [("", ""), ("```\n", "\n```"), ("<!--\n", "\n-->")])
def test_boundaries_are_literal_physical_lines(section, start, end, before, after):
    body = f"{start}\n  {end}\n### Nested\nbody"
    text = f"outside\n{before}{body}\n{end}{after}\n## Later\noutside"
    assert passage(text, section) == body


@pytest.mark.parametrize("section,text", [
    ("run", RUN_END),
    ("run", RUN_START),
    ("run", f"{RUN_START}\n{RUN_START}\n{RUN_END}"),
    ("run", f"{RUN_START}\n{RUN_END}\n{RUN_END}"),
    ("run", f"{RUN_END}\n{RUN_START}"),
    ("run", f" {RUN_START}\n{RUN_END}"),
    ("run", f"{RUN_START}\n{RUN_END} "),
    ("run", f"{RUN_START}\n{RUN_END}\n```\n{RUN_START}\n```"),
    ("run", f"{RUN_START}\n{RUN_END}\n<!--\n{RUN_END}\n-->"),
    ("gaps", "## Next"),
    ("gaps", GAPS),
    ("gaps", f"{GAPS}\n{GAPS}\n## Next"),
    ("gaps", f"## Earlier\n{GAPS}"),
    ("gaps", f" {GAPS}\n## Next"),
    ("gaps", f"{GAPS} \n## Next"),
    ("gaps", f"{GAPS}\n  ## Indented"),
    ("gaps", f"{GAPS}\n## Next\n<!--\n{GAPS}\n-->"),
])
def test_ambiguous_or_missing_boundaries_are_refused(section, text):
    with pytest.raises(AssertionError):
        passage(text, section)


@pytest.mark.parametrize("text", [
    "[label](file.md", "[label]file.md)", "[label](file name.md)",
    "[label](file(name).md)", "[[label]](file.md)", "[label](file.md\n)",
    "[la\nbel](file.md)", "[label]\n(file.md)", "[label][reference]",
    "[reference]: file.md", "[label](<file.md>)", '[label](file.md "title")',
    r"[label](file\(name\).md)", r"\[label](file.md)", "![label](file.md)",
    "[](file.md)", "[label]()", "[label](file.md)]", "[label](file[1].md)",
    "[valid](file.md) [broken](", "[unclosed", "orphan]",
])
def test_ambiguous_link_syntax_is_refused(text):
    with pytest.raises(AssertionError):
        inline_links(text)


def test_single_line_links_resolve_locally_without_freezing_labels(tmp_path):
    document = tmp_path / "guide.md"
    document.touch()
    (tmp_path / "other file.md").touch()
    text = ("Any wording ([first label](other%20file.md)), [second](#anchor).\n"
            f"[issue #116]({REPOSITORY}/issues/116) [PR #42]({REPOSITORY}/pull/42)")
    assert check_links(document, text) == [
        ("first label", "other%20file.md"), ("second", "#anchor"),
        ("issue #116", f"{REPOSITORY}/issues/116"), ("PR #42", f"{REPOSITORY}/pull/42"),
    ]
    with pytest.raises(AssertionError, match="broken local destination"):
        check_links(document, "[arbitrary label](missing.md)")


@pytest.mark.parametrize("label,target", [
    ("issue #116", f"{REPOSITORY}/pull/116"),
    ("PR #116", f"{REPOSITORY}/issues/116"),
    ("issue #115", f"{REPOSITORY}/issues/116"),
    ("arbitrary label", f"{REPOSITORY}/issues/116"),
    ("issue #116", f"{REPOSITORY}/issues/not-a-number"),
    ("issue #116", f"{REPOSITORY}/issues/116/extra"),
    ("issue #116", f"{REPOSITORY}/issues/116?query=1"),
    ("issue #116", "https://example.com/issues/116"),
    ("issue #116", "README.md"),
])
def test_issue_and_pr_labels_agree_with_urls(label, target):
    with pytest.raises(AssertionError):
        check_links(ROOT / "README.md", f"[{label}]({target})")
