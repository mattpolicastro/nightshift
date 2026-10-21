import json
import subprocess

import pytest

from specloop import cli, messages, seal, serve, state, tasks
from specloop.state import Run


TASKS = """\
## 1. Core

- [x] 1.1 First task
- [ ] 1.2 Stuck task
- [ ] 1.3 Future task
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path / "home")
    # Status must not load the credentials used by execution contexts.
    def no_credentials(*args, **kwargs):
        pytest.fail("status tried to load credentials")

    monkeypatch.setattr(cli, "load_env", no_credentials)
    monkeypatch.setattr(seal, "credential_values", no_credentials)
    return state.ROOT


@pytest.fixture
def admitted(home, tmp_path):
    def make(change):
        source = tmp_path / change
        task_file = source / "openspec" / "changes" / change / "tasks.md"
        task_file.parent.mkdir(parents=True)
        task_file.write_text(TASKS)

        def git(*args):
            return subprocess.run(
                ["git", "-C", str(source), *args], check=True, capture_output=True, text=True
            ).stdout.strip()

        git("init", "-q", "-b", f"spec/{change}")
        git("add", ".")
        git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "tasks")
        head = git("rev-parse", "HEAD")
        run = Run.for_change(change)
        run.save()
        run.manifest_path.write_text(f"""
[run]
repo = {json.dumps(str(source))}
change = "{change}"
branch = "spec/{change}"
[roles]
implement = "test"
review = "test"
[profiles.test]
adapter = "codex"
model = "test"
[paths]
allowed = ["out/**"]
[[verify]]
id = "test"
run = ["true"]
""")
        git("clone", "-q", "--bare", str(source), str(run.dir / "repo.git"))
        return run, head

    return make


@pytest.mark.scenario("specloop-interface/status-json/paused-run-reported")
@pytest.mark.parametrize("named", [False, True])
def test_paused_run_reported(admitted, capsys, named):
    run, head = admitted("paused-change")
    key = tasks.parse(TASKS)[1].key
    detail = 'Failed twice:\nexpected "hello", got "goodbye"'
    run.state.pr = "https://github.com/example/repo/pull/7"
    run.state.task(key).status = "stuck"
    run.state.task(key).attempts = 3
    run.pause("stuck", key, detail)
    (run.dir / "followups.md").write_text("A non-blocking finding\n")

    assert cli.main(["status", "--json", *(["paused-change"] if named else [])]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "version": 1,
        "supervisor": {"state": "not running"},
        "runs": [{
            "change": "paused-change",
            "status": "paused",
            "pause": {"reason": "stuck", "task": key, "detail": detail},
            "tasks": {"done": 1, "total": 3},
            "head": head,
            "pr": "https://github.com/example/repo/pull/7",
            "action": {
                "kind": "action",
                "headline": "Decide how to unstick task 1.2 (3 attempts failed)",
                "why": "Last finding: Failed twice:",
                "command": 'specloop resume paused-change --note "…"   or   specloop accept paused-change',
                "actions": ["resume", "accept", "abort"],
                "pause_id": f"stuck:{key}",
            },
        }],
    }


@pytest.mark.scenario("run-actions/actions-in-status/gate-action-described")
@pytest.mark.parametrize("named", [False, True])
def test_gate_action_described(admitted, capsys, named):
    run, _ = admitted("gate-change")
    run.pause("phase_gate", "section:1", "section 1 is complete")
    slack_message = messages.for_pause(
        "gate-change", "phase_gate", key="section:1", detail="section 1 is complete"
    )

    assert cli.main(["status", "--json", *(["gate-change"] if named else [])]) == 0
    entry = json.loads(capsys.readouterr().out)["runs"][0]
    assert entry["action"] == {
        "kind": slack_message.kind,
        "headline": slack_message.headline,
        "why": slack_message.why,
        "command": slack_message.command,
        "actions": ["resume", "abort"],
        "pause_id": "phase_gate:section:1",
    }
    assert entry["action"]["headline"] == "Review section 1 on the branch"


@pytest.mark.parametrize("reason, verbs", [
    ("gate_evidence", ["resume", "abort"]),
    ("coverage", ["resume", "abort"]),
    ("infra", ["resume", "abort"]),
    ("violation_repeat", ["resume", "abort"]),
    ("review_invalid", ["resume", "abort"]),
    ("requested", ["resume", "abort"]),
    ("stuck", ["resume", "accept", "abort"]),
    ("needs_human", ["resume", "accept", "abort"]),
    ("blocked", ["resume", "abort"]),
    ("ready_to_publish", ["publish", "abort"]),
    ("human_checks", ["finish", "abort"]),
    ("secret", ["abort"]),
    ("acceptance", ["abort"]),
    ("usage_limit", ["abort"]),
    ("unknown_reason", ["abort"]),
])
def test_pause_action_matches_message_and_allowed_verbs(admitted, capsys, reason, verbs):
    run, _ = admitted("paused-change")
    task = tasks.parse(TASKS)[1]
    key = "section:1" if reason == "gate_evidence" else task.key
    detail = "First finding\nMore diagnostic detail"
    run.state.task(task.key).attempts = 3
    run.state.pr = "https://github.com/example/repo/pull/7"
    run.pause(reason, key, detail, resume_at=1800000000)
    before = (run.dir / "state.json").read_bytes()
    slack_message = messages.for_pause(
        "paused-change", reason, label="" if reason == "gate_evidence" else task.label,
        key=key, detail=detail, attempts=0 if reason == "gate_evidence" else 3,
        done=1, pr=run.state.pr, resume_at=1800000000,
    )

    assert cli.main(["status", "--json"]) == 0
    action = json.loads(capsys.readouterr().out)["runs"][0]["action"]
    assert action == {
        "kind": slack_message.kind,
        "headline": slack_message.headline,
        "why": slack_message.why,
        "command": slack_message.command,
        "actions": verbs,
        "pause_id": f"{reason}:{key}",
    }
    assert (run.dir / "state.json").read_bytes() == before


@pytest.mark.scenario("specloop-interface/status-json/unknown-change")
def test_unknown_change(home, capsys):
    assert cli.main(["status", "never-admitted", "--json"]) == 1
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert document["version"] == 1
    assert "never-admitted" in document["error"]
    assert "no such change" in document["error"]
    assert output.err == ""
    assert not home.exists()


def test_empty_status(home, capsys):
    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "version": 1, "supervisor": {"state": "not running"}, "runs": [],
    }


@pytest.mark.scenario("run-actions/actions-in-status/running-run-has-no-action")
def test_multiple_runs_and_change_filter(admitted, monkeypatch, capsys):
    admitted("z")
    admitted("a")
    monkeypatch.setattr(serve, "liveness", lambda: "running (pid 123, last poll 0s ago)")
    assert cli.main(["status", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["supervisor"]["state"] == "running (pid 123, last poll 0s ago)"
    assert [run["change"] for run in document["runs"]] == ["a", "z"]
    for run in document["runs"]:
        assert run["status"] == "running"
        assert run["pause"] is None
        assert "action" not in run
        assert run["pr"] == ""
    assert cli.main(["status", "z", "--json"]) == 0
    assert [run["change"] for run in json.loads(capsys.readouterr().out)["runs"]] == ["z"]


def test_unreadable_run_returns_one_error_document(admitted, capsys):
    run, _ = admitted("broken")
    run.manifest_path.unlink()
    assert cli.main(["status", "--json"]) == 1
    document = json.loads(capsys.readouterr().out)
    assert document["version"] == 1
    assert document["error"]


@pytest.mark.parametrize("status", ["done", "paused", "aborted"])
def test_finished_run_reports_archived_tasks(admitted, tmp_path, capsys, status):
    run, _ = admitted("finished")
    source = tmp_path / "finished"
    changes = source / "openspec" / "changes"
    (changes / "finished" / "tasks.md").write_text(TASKS.replace("[ ]", "[x]"))
    (changes / "archive").mkdir()
    (changes / "finished").rename(changes / "archive" / "2026-10-20-finished")
    for args in [
        ["add", "-A"],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "archive"],
        ["push", "-q", str(run.dir / "repo.git"), "HEAD:refs/heads/spec/finished"],
    ]:
        subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if status != "done":
        run.pause("human_checks", detail="Retry finish after a network failure")
    run.state.status = status
    run.save()
    admitted("other")

    assert cli.main(["status", "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)["runs"]
    assert [e["change"] for e in entries] == ["finished", "other"]
    entry = entries[0]
    assert entry["status"] == status
    assert entry["tasks"] == {"done": 3, "total": 3}
    assert entry["head"] == head
    if status == "paused":
        assert entry["pause"]["reason"] == "human_checks"
        assert entry["action"]["actions"] == ["finish", "abort"]
    else:
        assert "action" not in entry
        if status == "done":
            assert entry["pause"] is None


def test_text_status_is_unchanged(home, capsys):
    assert cli.main(["status", "never-admitted"]) == 0
    assert capsys.readouterr().out == (
        "supervisor: not running\n"
        "never-admitted: no such change (not admitted, or admission was refused)\n"
    )
