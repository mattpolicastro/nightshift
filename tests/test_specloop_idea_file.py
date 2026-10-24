"""Durable drafting inputs read by a detached CLI after polling and restart."""

import json
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest

from nightshift import daemon, specloop_intake, specloop_job
from nightshift.config import Config
from test_engine_routing import DraftGh
from test_specloop_background import CLI, background, eventually
from test_specloop_intake import BRANCH, calls, idea_file


@pytest.mark.scenario("issue-intake/durable-drafting-payload/delayed-background-read")
@pytest.mark.scenario(
    "specloop-documentation/front-door-guidance/input-and-status-remain-usable"
)
def test_delayed_idea_read_survives_polls_and_restart(background, monkeypatch):
    root, repo, children = background
    monkeypatch.setattr(daemon, "STATE_DIR", Path("relative state with spaces"))
    # Read only after release, from a different working directory than the daemon.
    reader = '''
os.chdir(root / "reader cwd")
args = sys.argv[1:]
idea = Path(args[args.index("--idea-file") + 1]).read_text(encoding="utf-8")
(root / "read-idea.txt").write_text(idea, encoding="utf-8")
'''
    (root / "reader cwd").mkdir()
    (root / "specloop").write_text(f"#!{sys.executable}\n" + CLI.replace(
        'result = json.loads', reader + '\nresult = json.loads'))
    (root / "cli-result.json").write_text(json.dumps({
        "version": 1, "outcome": "drafted", "branch": BRANCH, "commit": "abc123",
    }))
    gh = DraftGh(("spec:draft",))
    gh.comments[0]["body"] = "Original λ\n\n" + "complete thread 🦉\n" * 70_000 + "END"
    expected = "Add a gate\n\nPlease draft it.\n\n" + gh.comments[0]["body"]

    def run(args, **kwargs):
        assert args[0] == "git"
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(specloop_intake, "run_git", run)
    health = daemon.RepoHealth()
    poll = partial(daemon.poll_specloop, Config(repos=[repo]), health, runner=gh)
    poll()
    eventually(lambda: (root / "started").exists())
    path = daemon.STATE_DIR / "proposals/owner__sample/7.json"
    saved = path.read_bytes()
    receipt = json.loads(saved)
    assert "result" not in receipt
    assert receipt["idea"] == expected
    directory = path.parent / "7.jobs"
    args = json.loads((directory / receipt["job"] / "request.json").read_text())["args"]
    request_path = directory / receipt["job"] / "request.json"
    request = json.loads(request_path.read_text())
    request_mtime = request_path.stat().st_mtime_ns
    payload = idea_file(args)
    assert payload.parent == directory.resolve()
    assert "relative state with spaces" in str(payload)
    assert all("Original λ" not in arg for arg in args)
    assert not (root / "read-idea.txt").exists()
    gh.comments[0]["body"] = "Edited after launch"
    for _ in range(3):
        poll()
    specloop_job._children.clear()
    poll()
    assert path.read_bytes() == saved
    assert [p.resolve() for p in directory.glob("idea-*.txt")] == [payload]
    assert len((root / "started").read_text().splitlines()) == 1
    assert not gh.prs and not calls(gh, "issue", "edit")
    assert not (root / "read-idea.txt").exists()

    (root / "release").touch()
    for child in children:
        child.wait(timeout=5)
    assert not (root / "timed-out").exists()
    assert (root / "read-idea.txt").read_bytes() == expected.encode("utf-8")
    assert payload.read_bytes() == expected.encode("utf-8")
    poll()
    poll()
    assert json.loads(path.read_text())["delivered"] is True
    assert json.loads(request_path.read_text()) == {**request, "args": args[:-2]}
    assert request_path.stat().st_mtime_ns == request_mtime
    assert (request_path.parent / "result.json").is_file()
    assert not payload.exists()
    assert not health.failures
    assert len((root / "started").read_text().splitlines()) == 1
    assert gh.labels == set() and len(gh.prs) == 1
    assert gh.pr_url in gh.comments[-1]["body"]
    assert len(gh.comments) == 2
    assert len(calls(gh, "pr", "create")) == len(calls(gh, "issue", "comment")) == 1
    assert len(calls(gh, "issue", "edit")) == 1
    assert "Closes #7" in calls(gh, "pr", "create")[0][-1]
    assert calls(gh, "pr", "edit")[0][-2:] == ["--add-label", "spec:review"]
