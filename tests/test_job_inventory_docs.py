"""Execute operator examples against local fixtures without freezing prose."""

import pytest

from nightshift import cli, daemon, specloop_job as jobs
from test_jobs_cli import compact_offline, local_state, inventory_rows  # noqa: F401
from test_specloop_compaction import delivered, snapshot
from test_specloop_documentation_integrity import DOCS, LINK, REPOSITORY, ROOT
from test_stopped_job_release_docs import commands


@pytest.mark.scenario(
    "specloop-documentation/job-inventory-view-guidance/operator-selects-a-view-and-cleanup"
)
@pytest.mark.parametrize("path", DOCS)
@pytest.mark.parametrize("identity", ["missing", "replacement"])
def test_documented_views_and_paused_cleanup(path, identity, compact_offline, monkeypatch, capsys):
    root = compact_offline
    receipt_path, receipt, old, idea = delivered(root)
    if identity == "replacement":
        _, _, current, _ = delivered(root, serial=2)
    else:
        jobs.save(receipt_path, {k: v for k, v in receipt.items() if k != "job"})
    examples = commands(path)
    listing, = [command for command in examples if len(command) == 1]
    history, = [command for command in examples if command[1:2] == ("--all",)]
    compact, = [command for command in examples if command[1:2] == ("compact",)]
    assert f"{REPOSITORY}/issues/103" in dict(LINK.findall((ROOT / path).read_text())).values()
    pause = root / "pause"
    monkeypatch.setattr(daemon, "PAUSE_FILE", pause)
    pause.touch()
    before = snapshot(root)
    assert cli.main(listing) == 0
    output = capsys.readouterr()
    count = 2 if identity == "replacement" else 1
    assert output.err == "" and output.out == (
        f"No active background jobs.\nHidden historical jobs: {count} (use --all to show).\n"
    )
    assert cli.main(history) == 0
    output = capsys.readouterr()
    rows = inventory_rows(output.out)
    assert rows == [f"{serial:032x}  proposals  o/r#8  delivered" for serial in range(1, count + 1)]
    assert output.err == "" and snapshot(root) == before
    assert cli.main(compact) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.endswith(f"Compacted: {count - 1}; skipped: 1.\n")
    assert f"{old.name}  proposals  o/r#8  skipped:" in output.out
    after = snapshot(root)
    assert {p: data for p, data in after.items() if p.is_relative_to(old) or p == idea} == {
        p: data for p, data in before.items() if p.is_relative_to(old) or p == idea
    }
    assert after[receipt_path] == before[receipt_path] and pause.exists()
    if identity == "replacement":
        assert not (current / "log").exists()
    assert cli.main(history) == 0
    output = capsys.readouterr()
    assert inventory_rows(output.out) == rows and output.err == ""
    assert snapshot(root) == after
