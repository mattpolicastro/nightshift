"""Execute documented commands and check their table/links, not prose wording.

The shared stopped fixture supplies durable confirmation for all four stages;
these checks never perform a live stop or claim the design's reader checks.
"""

import fcntl
import json
import re
import shlex

import pytest

from nightshift import cli, daemon, specloop_job as jobs
from test_specloop_documentation_integrity import DOCS, LINK, REPOSITORY, ROOT
from test_specloop_jobs import (  # noqa: F401 -- shared isolated fixtures
    offline_jobs_cli, release_snapshot, stopped_job,
)


def commands(path):
    return {
        tuple(shlex.split(command))
        for command in re.findall(r"`nightshift (jobs(?: [^`\n]+)?)`", (ROOT / path).read_text())
    }


def invoke(command, job_id):
    return cli.main([job_id if arg == "<id>" else arg for arg in command])


@pytest.mark.scenario(
    "specloop-documentation/stopped-job-release-workflow/operator-releases-a-stopped-hold"
)
@pytest.mark.parametrize("path", ("README.md", "docs/specloop/README.md"))
def test_documented_inventory_stop_release_commands(path, stopped_job, offline_jobs_cli, capsys):
    _, directory, _ = stopped_job
    examples = commands(path)
    workflow = [("jobs",), ("jobs", "stop", "<id>"), ("jobs", "release", "<id>")]
    assert set(workflow) <= examples
    for command in workflow:
        assert invoke(command, directory.name) == 0
        output = capsys.readouterr()
        assert directory.name in output.out and output.err == ""
    # Re-inventory the historical attempt after releasing its intake hold.
    assert invoke(workflow[0], directory.name) == 0
    assert " stopped\n" in capsys.readouterr().out


@pytest.mark.scenario(
    "specloop-documentation/release-recovery-boundaries/operator-understands-remaining-limits"
)
@pytest.mark.scenario(
    "specloop-documentation/current-limitations/fixed-limitations-leave-the-open-list"
)
@pytest.mark.scenario(
    "specloop-documentation/current-limitations/remaining-ledger-gaps-are-explicit"
)
@pytest.mark.parametrize("path", DOCS)
def test_documented_recovery_preserves_evidence_and_links(path, stopped_job, offline_jobs_cli):
    root, directory, receipt_path = stopped_job
    release, = [command for command in commands(path) if command[1:2] == ("release",)]
    receipt = json.loads(receipt_path.read_text())
    # The pause is deliberately present: release must not resume or launch work.
    daemon.PAUSE_FILE.touch()
    before = release_snapshot(root)
    assert invoke(release, directory.name) == 0
    after = release_snapshot(root)
    assert json.loads(receipt_path.read_text()) == {k: v for k, v in receipt.items() if k != "job"}
    assert set(after) - set(before) == {receipt_path.with_suffix(".release.lock")}
    assert {p: v for p, v in after.items() if p in before and p != receipt_path} == {
        p: v for p, v in before.items() if p != receipt_path
    }
    assert jobs.collect(receipt_path, receipt) is None
    assert release_snapshot(root) == after
    assert daemon.PAUSE_FILE.exists()
    targets = {target for _, target in LINK.findall((ROOT / path).read_text())}
    assert f"{REPOSITORY}/issues/34" in targets
    if path == "KNOWN-LIMITATIONS.md":
        assert f"{REPOSITORY}/issues/17" in targets
        assert "README.md#pause-and-job-control" in targets


@pytest.mark.scenario(
    "specloop-documentation/release-command-outcomes/operator-interprets-release-output"
)
@pytest.mark.parametrize("case,row", [
    ("release", 0), ("repeat", 1), ("replacement", 1),
    ("running", 2), ("stopping", 2), ("finishing", 2), ("unverifiable", 2),
    ("syntax", 3),
])
def test_release_outcome_table_matches_cli(case, row, stopped_job, offline_jobs_cli, capsys):
    root, directory, receipt_path = stopped_job
    # Locate the table by its CLI output schema, allowing explanatory prose to change.
    table, = [block for block in (ROOT / "README.md").read_text().split("\n\n")
              if block.startswith("|") and "`<id>: released`" in block]
    rows = [[cell.strip() for cell in line.strip("|").split("|")]
            for line in table.splitlines()[2:]]
    assert len(rows) == 4 and all(len(cells) == 3 for cells in rows)
    command, = [command for command in commands("README.md") if command[1:2] == ("release",)]
    if case in ("repeat", "replacement"):
        assert invoke(command, directory.name) == 0
        if case == "replacement":
            jobs.save(receipt_path, {**json.loads(receipt_path.read_text()), "job": "b" * 32})
    elif case in ("running", "finishing"):
        (directory / "stop.json").unlink()
        if case == "running":
            (directory / "result.json").unlink()
    elif case == "stopping":
        stop = json.loads((directory / "stop.json").read_text())
        jobs.save(directory / "stop.json", {**stop, "state": "stopping"})
    elif case == "unverifiable":
        (directory / "process.json").unlink()
    elif case == "syntax":
        command = command[:-1]
    capsys.readouterr()
    before = release_snapshot(root)
    with (directory / "lock").open() as lifetime:
        if case == "running":
            fcntl.flock(lifetime, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            code = invoke(command, directory.name)
        except SystemExit as exc:
            code = exc.code
    assert code == int(rows[row][2])
    output = capsys.readouterr()
    if code == 0:
        assert output.out.strip() == rows[row][1].strip("`").replace("<id>", directory.name)
        assert output.err == ""
    else:
        assert output.err and output.out == ""
    if case != "release":
        assert release_snapshot(root) == before
