"""Offline documentation checks; live reference status and readability stay human.

The task explicitly permits additive tests. Diff review and the full pytest
result remain acceptance checks, not claims a test can make about itself.
"""

import re
import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from specloop import seal, tasks
from nightshift import cli, daemon, specloop_intake, specloop_job
from test_specloop_compaction import delivered, snapshot
from test_specloop_jobs import offline_jobs_cli  # noqa: F401
from test_specloop_seal_verify import M, repo  # noqa: F401 -- shared git fixture


ROOT = Path(__file__).resolve().parents[1]
DOCS = ("README.md", "KNOWN-LIMITATIONS.md", "docs/specloop/README.md")
REPOSITORY = "https://github.com/mattpolicastro/nightshift"
LINK = re.compile(r"\[([^\[\]\n]+)\]\(([^\s)]+)\)")


@pytest.mark.scenario(
    "specloop-documentation/configuration-resilience-guidance/operator-repairs-enrollment"
)
@pytest.mark.parametrize("path", ("README.md", "docs/specloop/README.md"))
def test_enrollment_guidance_explains_holds_repair_and_surviving_work(path):
    text = " ".join((ROOT / path).read_text().split())
    for guidance in (
        "regular-file", "Symlinks to regular files", "FIFOs and devices",
        "`run.change` and `run.branch` are optional", "intake supplies both per change",
        "repository-local health", "Healthy repositories continue",
        "triggers, receipts and jobs retained, without `needs-human` or an agent session",
        "Repair the file at the same path to retry on a later poll",
        "requires a daemon restart", "Admitted runs continue publication and build-stage revisions",
        "manifest and recorded branch", "successful result", "stopped-job holds still apply",
        "unpaused startup and polls", "no claim or surviving worktree return to `agent:ready`",
        "`needs_decision`", "preserved for human inspection", "phase and location",
        "`agent:working` becomes `needs-human`", "Recovery does not resume or delete",
    ):
        assert guidance.lower() in text.lower(), f"{path}: missing guidance: {guidance}"


@pytest.mark.scenario(
    "specloop-documentation/configuration-resilience-guidance/operator-understands-names-and-limits"
)
def test_naming_guidance_distinguishes_compatibility_from_migration():
    for path in DOCS:
        text = " ".join((ROOT / path).read_text().split())
        assert "`owner/widget..js`" in text and "unsupported" in text
        assert "Recorded legacy identities remain" in text
        assert f"[issue #13]({REPOSITORY}/issues/13)" in text
        if path != "KNOWN-LIMITATIONS.md":
            assert "`owner__sample-issue-7-add-a-gate`" in text
            assert "`spec/owner__sample-issue-7-add-a-gate`" in text
    limits = " ".join((ROOT / "KNOWN-LIMITATIONS.md").read_text().split())
    for boundary in ("not automatically migrated or renamed", "global name collisions",
                     "both proposal receipt loss and a title change", "transport retry risks are unchanged",
                     "do not reread labels", "cannot guarantee exactly-once", "human rearms"):
        assert boundary in limits



@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/references-and-reader-checks-are-reviewable"
)
@pytest.mark.scenario(
    "specloop-documentation/git-timeout-guidance/operator-understands-timeout-limits"
)
@pytest.mark.parametrize("path", (*DOCS, "docs/specloop/command-json.md"))
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
    assert f"{REPOSITORY}/issues/9" in references
    assert f"{REPOSITORY}/issues/7" in references


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
@pytest.mark.scenario(
    "specloop-documentation/keyed-revision-recovery-guidance/recovery-limits-remain-reviewable"
)
@pytest.mark.scenario(
    "specloop-documentation/worker-retirement-guidance/existing-installation-is-checked-before-merge"
)
@pytest.mark.parametrize("path", (*DOCS, "docs/specloop/command-json.md"))
def test_relative_documentation_links_resolve_including_heading_fragments(path):
    document = ROOT / path
    links = 0
    for _, target in LINK.findall(document.read_text()):
        url = urlsplit(target)
        if url.scheme or url.netloc:
            continue
        destination = document.parent / unquote(url.path) if url.path else document
        assert destination.resolve().is_relative_to(ROOT)
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


def guide_table(schema):
    blocks = (ROOT / "docs/specloop/README.md").read_text().split("\n\n")
    table, = [block for block in blocks if block.startswith(schema)]
    return dict(tuple(cell.strip() for cell in row.strip("|").split("|"))
                for row in table.splitlines()[2:])


@pytest.mark.scenario("specloop-documentation/deadline-guidance/operator-handles-overdue-work")
@pytest.mark.scenario("specloop-documentation/git-timeout-guidance/operator-understands-timeout-limits")
@pytest.mark.scenario("specloop-documentation/active-job-status-guidance/operator-reads-job-status")
def test_documented_command_budgets_match_dispatch(monkeypatch):
    from test_stopped_job_release_docs import commands

    for path in DOCS:
        assert ("jobs", "--all") in commands(path)
    budgets = guide_table("| Command | Budget (seconds) |")
    git_budget = int(budgets.pop("daemon intake `git`"))
    assert {key.strip("`"): int(value) for key, value in budgets.items()} == (
        specloop_job.COMMAND_TIMEOUT_SECONDS
    )
    calls = []
    monkeypatch.setattr(specloop_intake, "run_git", lambda args, **kw: calls.append((args, kw)))
    specloop_intake._bounded_run(["git", "fetch"], capture_output=True)
    assert calls == [(["git", "fetch"], {"timeout": git_budget, "capture_output": True})]


@pytest.mark.scenario("specloop-documentation/delivered-retention-guidance/operator-understands-retained-artifacts")
@pytest.mark.parametrize("cleanup", [None, *DOCS])
def test_documented_artifacts_match_compaction(tmp_path, monkeypatch, offline_jobs_cli, capsys, cleanup):
    path, receipt, directory, idea = delivered(tmp_path)
    artifacts = guide_table("| Delivered attempt artifact | After compaction |")
    targets = {name: idea if name == "owned `--idea-file` payload" else directory / name.strip("`")
               for name in artifacts}
    before = {name: (target.read_bytes(), target.stat().st_mtime_ns) for name, target in targets.items()}
    if cleanup is None:
        assert specloop_job.compact_delivered(path, receipt) == []
    else:
        from test_stopped_job_release_docs import commands

        command, = [command for command in commands(cleanup) if command[1:2] == ("compact",)]
        pause = tmp_path / "pause"
        monkeypatch.setattr(daemon, "PAUSE_FILE", pause)
        pause.touch()
        assert cli.main(command) == 0
        output = capsys.readouterr()
        assert output.err == "" and output.out.endswith("Compacted: 1; skipped: 0.\n")
        assert json.loads(path.read_text()) == receipt and pause.exists()
        compacted = snapshot(tmp_path)
        assert cli.main(command) == 0
        output = capsys.readouterr()
        assert output.err == "" and output.out.endswith("Compacted: 0; skipped: 1.\n")
        assert snapshot(tmp_path) == compacted
    for name, action in artifacts.items():
        target = targets[name]
        if action == "removed":
            assert not target.exists()
        elif action == "retained":
            assert (target.read_bytes(), target.stat().st_mtime_ns) == before[name]
        else:
            assert action == "redacted" and target.stat().st_mtime_ns == before[name][1]
            original = json.loads(before[name][0])
            assert json.loads(target.read_text()) == dict(
                stage="proposals", args=original["args"][:-2],
                started_at=None, timeout_seconds=None, deadline_at=None,
            )
