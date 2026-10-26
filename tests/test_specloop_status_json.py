import json
import shlex
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nightshift.cli import build_parser, main

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
def admitted(home, tmp_path, monkeypatch):
    # JSON pause timestamps need stable exact-equality expectations.
    event = Run.event

    def timed_event(run, kind, **data):
        data.setdefault("at", 1800000000)
        event(run, kind, **data)

    monkeypatch.setattr(Run, "event", timed_event)

    def make(change, proposal=None, task_text=TASKS, github=""):
        source = tmp_path / change
        task_file = source / "openspec" / "changes" / change / "tasks.md"
        task_file.parent.mkdir(parents=True)
        task_file.write_text(task_text)
        if proposal is not None:
            task_file.with_name("proposal.md").write_text(proposal)

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
github = {json.dumps(github)}
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


@pytest.mark.scenario("state-migration/read-only-fallback/mixed-roots-remain-visible")
def test_status_unions_roots_read_only(admitted, home, capsys):
    from test_state_roots import tree_snapshot

    canonical, _ = admitted("a-canonical", github="example/one")
    old, _ = admitted("z-legacy", github="example/two")
    legacy = state.legacy_root() / "runs"
    legacy.mkdir(parents=True)
    old.dir.rename(legacy / old.dir.name)
    (legacy / canonical.dir.name).symlink_to(canonical.dir, target_is_directory=True)
    before = tree_snapshot(home, state.legacy_root())
    assert main(["status", "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)["runs"]
    assert [entry["change"] for entry in entries] == ["a-canonical", "z-legacy"]
    assert all("error" not in entry for entry in entries)
    for entry in entries:
        assert main(["status", entry["change"], "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["runs"] == [entry]
    assert Run.for_change("z-legacy").dir == legacy / "z-legacy"
    assert tree_snapshot(home, state.legacy_root()) == before


@pytest.mark.scenario("state-migration/read-only-fallback/conflicting-roots-remain-visible")
def test_conflict_and_corrupt_runs_preserve_healthy_reads(admitted, home, capsys):
    from test_state_roots import tree_snapshot

    healthy, _ = admitted("healthy")
    conflict, _ = admitted("conflict")
    corrupt, _ = admitted("corrupt")
    (corrupt.dir / "state.json").write_text("{")
    legacy = state.legacy_root() / "runs/conflict"
    legacy.mkdir(parents=True)
    (legacy / "state.json").write_text('{"change":"conflict","status":"done"}')
    before = tree_snapshot(home, state.legacy_root())
    assert main(["status", "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)["runs"]
    assert [entry["change"] for entry in entries] == ["conflict", "corrupt", "healthy"]
    assert "conflicting run directories" in entries[0]["error"]
    assert "JSONDecodeError" in entries[1]["error"]
    assert "error" not in entries[2]
    assert main(["status", "conflict", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["runs"] == [entries[0]]
    assert main(["status"]) == 0
    assert "conflicting run directories" in capsys.readouterr().out
    assert [run.dir for run in serve.runs()] == [healthy.dir]
    assert "conflicting run directories" in capsys.readouterr().err
    with pytest.raises(ValueError, match="conflicting run directories"):
        Run.for_change("conflict")
    for write in (conflict.save, lambda: conflict.event("test")):
        with pytest.raises(ValueError, match="conflicting run directories"):
            write()
    with pytest.raises(ValueError, match="conflicting run directories"):
        with conflict.lock():
            pytest.fail("conflicting writer acquired a lock")
    assert tree_snapshot(home, state.legacy_root()) == before


@pytest.mark.scenario("state-migration/read-only-fallback/conflicting-roots-remain-visible")
def test_unreadable_run_remains_visible(admitted, monkeypatch, capsys):
    healthy, _ = admitted("healthy")
    unreadable, _ = admitted("unreadable")
    original_stat = Path.stat

    def stat(path, *args, **kwargs):
        if path == unreadable.dir / "state.json":
            raise PermissionError("state metadata denied")
        return original_stat(path, *args, **kwargs)

    original_read = Path.read_text

    def read(path, *args, **kwargs):
        if path == unreadable.dir / "state.json":
            raise PermissionError("state contents denied")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "read_text", read)
    for argv in (["status", "--json"], ["status", "unreadable", "--json"]):
        assert main(argv) == 0
        entries = json.loads(capsys.readouterr().out)["runs"]
        assert entries[-1]["change"] == "unreadable"
        assert "PermissionError" in entries[-1]["error"]
    assert [run.dir for run in serve.runs()] == [healthy.dir]
    assert "state contents denied" in capsys.readouterr().err


@pytest.mark.scenario("nightshift-cli/canonical-build-commands/canonical-tests-retain-output-contracts")
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

    assert main(["status", "--json", *(["paused-change"] if named else [])]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "version": 1,
        "supervisor": {"state": "not running"},
        "runs": [{
            "change": "paused-change",
            "status": "paused",
            "pause": {"reason": "stuck", "task": key, "detail": detail,
                      "auto_resumes_remaining": 0, "since": 1800000000},
            "tasks": {"done": 1, "total": 3},
            "head": head,
            "pr": "https://github.com/example/repo/pull/7",
            "context": {
                "summary": "",
                "repo": str(cli._manifest(run).repo),
                "branch": "spec/paused-change",
                "task": {"label": "1.2", "section": "Core", "text": "Stuck task"},
                "detail": detail,
                "recent": [{
                    "at": datetime.fromtimestamp(
                        json.loads((run.dir / "events.jsonl").read_text())["at"], timezone.utc
                    ).isoformat().replace("+00:00", "Z"),
                    "text": "paused: stuck",
                }],
                "github": "",
            },
            "action": {
                "kind": "action",
                "headline": "Decide how to unstick task 1.2 (3 attempts failed)",
                "why": "Last finding: Failed twice:",
                "command": 'nightshift resume paused-change --note "…"   or   nightshift accept paused-change',
                "actions": ["resume", "accept", "abort"],
                "pause_id": f"stuck:{key}",
            },
        }],
    }


@pytest.mark.scenario("specloop-interface/remaining-automatic-resumes/infrastructure-budget-reported")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("resume_at", [1, 4102444800], ids=["past", "future"])
@pytest.mark.parametrize("consumed, remaining", [(0, 2), (1, 1), (2, 0), (3, 0)])
def test_infrastructure_resume_budget(admitted, capsys, named, resume_at, consumed, remaining):
    run, _ = admitted("infra")
    key = tasks.parse(TASKS)[1].key
    run.state.task(key).auto_resumes = consumed
    run.pause("infra", key, "Worker unavailable", resume_at=resume_at)

    assert main(["status", "--json", *(["infra"] if named else [])]) == 0
    pause = json.loads(capsys.readouterr().out)["runs"][0]["pause"]
    assert pause == {
        "reason": "infra", "task": key, "detail": "Worker unavailable",
        "auto_resumes_remaining": remaining,
        "since": 1800000000,
    }


@pytest.mark.scenario("specloop-interface/remaining-automatic-resumes/infrastructure-budget-reported")
@pytest.mark.parametrize("named", [False, True])
def test_resume_budget_uses_supervisor_policy(admitted, monkeypatch, capsys, named):
    run, _ = admitted("infra")
    key = tasks.parse(TASKS)[1].key
    run.state.task(key).auto_resumes = 1
    run.pause("infra", key, resume_at=1)
    monkeypatch.setitem(serve.AUTO_RESUME, "infra", 5)

    assert main(["status", "--json", *(["infra"] if named else [])]) == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["pause"]["auto_resumes_remaining"] == 4


@pytest.mark.scenario("specloop-interface/remaining-automatic-resumes/unlimited-scheduled-recovery")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("resume_at", [1, 4102444800], ids=["past", "future"])
@pytest.mark.parametrize("reason, has_task", [
    ("usage_limit", True), ("usage_limit", False), ("infra", False),
])
def test_unlimited_scheduled_recovery(admitted, capsys, named, resume_at, reason, has_task):
    run, _ = admitted("unlimited")
    key = tasks.parse(TASKS)[1].key
    if has_task:
        run.state.task(key).auto_resumes = 3
    else:
        # An unrelated task must not supply a budget for the paused task.
        run.state.task(tasks.parse(TASKS)[0].key).auto_resumes = 3
    run.pause(reason, key, resume_at=resume_at)

    assert main(["status", "--json", *(["unlimited"] if named else [])]) == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["pause"]["auto_resumes_remaining"] is None


@pytest.mark.scenario("specloop-interface/remaining-automatic-resumes/no-automatic-recovery")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("has_task", [False, True])
@pytest.mark.parametrize("reason, resume_at", [
    ("requested", 0), ("requested", 1),
    ("unknown_reason", 0), ("unknown_reason", 1),
    ("infra", 0), ("usage_limit", 0),
    ("infra", None), ("usage_limit", None),
])
def test_no_automatic_recovery(admitted, capsys, named, has_task, reason, resume_at):
    run, _ = admitted("human")
    key = tasks.parse(TASKS)[1].key
    if has_task:
        run.state.task(key).auto_resumes = 0
    run.pause(reason, key, resume_at=resume_at or 0)
    if resume_at is None:
        persisted = json.loads((run.dir / "state.json").read_text())
        del persisted["pause"]["resume_at"]
        (run.dir / "state.json").write_text(json.dumps(persisted))

    assert main(["status", "--json", *(["human"] if named else [])]) == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["pause"]["auto_resumes_remaining"] == 0


@pytest.mark.scenario("specloop-interface/remaining-automatic-resumes/status-remains-read-only")
@pytest.mark.parametrize("named", [False, True])
def test_resume_allowance_status_is_read_only(admitted, monkeypatch, capsys, named):
    runs = {}
    key = tasks.parse(TASKS)[1].key
    for change in ["budget", "missing-task", "unpaused"]:
        run, head = admitted(change)
        run.state.pr = "https://github.com/example/repo/pull/7"
        if change == "budget":
            run.state.task(key).auto_resumes = 1
        if change != "unpaused":
            run.pause("infra", key, "Worker unavailable", resume_at=1)
        else:
            run.save()
        runs[change] = (run, head)

    def file_snapshot():
        return {
            path: (path.read_bytes(), path.stat().st_mtime_ns) if path.exists() else None
            for run, _ in runs.values()
            for path in [run.dir / "state.json", run.dir / "events.jsonl"]
        }

    before = file_snapshot()
    loaded = []
    original_for_change = Run.for_change

    def track_load(cls, change, **kwargs):
        run = original_for_change(change, **kwargs)
        loaded.append((run, asdict(run.state)))
        return run

    def no_mutation(*args, **kwargs):
        pytest.fail("status tried to mutate run or task state")

    monkeypatch.setattr(Run, "for_change", classmethod(track_load))
    monkeypatch.setattr(Run, "save", no_mutation)
    monkeypatch.setattr(Run, "event", no_mutation)
    monkeypatch.setattr(state.RunState, "task", no_mutation)

    for names in ([[change] for change in runs] if named else [[]]):
        assert main(["status", "--json", *names]) == 0
        output = capsys.readouterr()
        assert output.err == ""
        document = json.loads(output.out)
        assert document["version"] == 1
        assert document["supervisor"] == {"state": "not running"}
        assert [entry["change"] for entry in document["runs"]] == (names or list(runs))
        for entry in document["runs"]:
            run, head = runs[entry["change"]]
            assert entry["status"] == run.state.status
            assert entry["head"] == head
            assert entry["pr"] == run.state.pr
            assert entry["tasks"] == {"done": 1, "total": 3}
            assert entry["context"]["branch"] == f"spec/{entry['change']}"
            if entry["change"] == "unpaused":
                assert entry["pause"] is None
                assert "action" not in entry
            else:
                assert entry["pause"] == {
                    "reason": "infra", "task": key, "detail": "Worker unavailable",
                    "auto_resumes_remaining": 1 if entry["change"] == "budget" else None,
                    "since": 1800000000,
                }
                assert entry["action"]["pause_id"] == f"infra:{key}"

    assert len(loaded) == len(runs)
    assert all(asdict(run.state) == before_state for run, before_state in loaded)
    assert file_snapshot() == before


@pytest.mark.scenario("run-actions/actions-in-status/gate-action-described")
@pytest.mark.parametrize("named", [False, True])
def test_gate_action_described(admitted, capsys, named):
    run, _ = admitted("gate-change")
    run.pause("phase_gate", "section:1", "section 1 is complete")
    slack_message = messages.for_pause(
        "gate-change", "phase_gate", key="section:1", detail="section 1 is complete"
    )

    assert main(["status", "--json", *(["gate-change"] if named else [])]) == 0
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


@pytest.mark.scenario("run-actions/canonical-command-hints/messages-and-status-agree")
@pytest.mark.parametrize("reason, verbs", [
    ("phase_gate", ["resume", "abort"]),
    ("seal", ["resume", "abort"]),
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

    assert main(["status", "--json"]) == 0
    action = json.loads(capsys.readouterr().out)["runs"][0]["action"]
    assert action == {
        "kind": slack_message.kind,
        "headline": slack_message.headline,
        "why": slack_message.why,
        "command": slack_message.command,
        "actions": verbs,
        "pause_id": f"{reason}:{key}",
    }
    if reason in ("requested", "usage_limit"):
        assert action["kind"] == messages.FYI and action["command"] == ""
    else:
        for suggestion in action["command"].split("   or   "):
            executable, *argv = shlex.split(suggestion)
            assert executable == "nightshift"
            args = build_parser().parse_args(argv)
            assert args.handler is cli.dispatch and args.change == "paused-change"
            assert args.cmd == "status" or args.cmd in verbs
            if args.cmd == "resume":
                assert args.note == ("…" if reason in ("stuck", "blocked", "needs_human") else "")
                assert args.no_run is False
    assert (run.dir / "state.json").read_bytes() == before


@pytest.mark.scenario("specloop-interface/status-json/unknown-change")
@pytest.mark.scenario("specloop-interface/isolated-run-status-failures/unknown-changes-and-document-failures")
def test_unknown_change(home, capsys):
    assert main(["status", "never-admitted", "--json"]) == 1
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert document["version"] == 1
    assert "never-admitted" in document["error"]
    assert "no such change" in document["error"]
    assert output.err == ""
    assert not home.exists()


@pytest.mark.scenario("specloop-interface/isolated-run-status-failures/unknown-changes-and-document-failures")
def test_empty_status(home, capsys):
    assert main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "version": 1, "supervisor": {"state": "not running"}, "runs": [],
    }


@pytest.mark.scenario("run-actions/actions-in-status/running-run-has-no-action")
def test_multiple_runs_and_change_filter(admitted, monkeypatch, capsys):
    admitted("z")
    admitted("a")
    monkeypatch.setattr(serve, "liveness", lambda: "running (pid 123, last poll 0s ago)")
    assert main(["status", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["supervisor"]["state"] == "running (pid 123, last poll 0s ago)"
    assert [run["change"] for run in document["runs"]] == ["a", "z"]
    for run in document["runs"]:
        assert run["status"] == "running"
        assert run["pause"] is None
        assert "action" not in run
        assert run["pr"] == ""
    assert main(["status", "z", "--json"]) == 0
    assert [run["change"] for run in json.loads(capsys.readouterr().out)["runs"]] == ["z"]


def status_document(capsys, *names, code=0):
    assert main(["status", "--json", *names]) == code
    output = capsys.readouterr()
    assert output.err == ""
    # json.loads rejects trailing prose and a second JSON document.
    document = json.loads(output.out)
    assert document["version"] == 1
    if code == 0:
        assert set(document) == {"version", "supervisor", "runs"}
        assert document["supervisor"] == {"state": "not running"}
    else:
        assert set(document) == {"version", "error"}
        assert document["error"]
    return document


def assert_status_error(entry, change, error_class, since=None):
    error = entry["error"]
    assert error.startswith(f"{error_class}:")
    assert 0 < len(error) <= 500
    detail = "Run status could not be read. Inspect nightshift status --json for details."
    assert entry == {
        "change": change,
        "status": "paused",
        "pause": {"reason": "status_error", "task": "", "detail": detail,
                  "auto_resumes_remaining": 0, "since": since},
        "tasks": {"done": 0, "total": 0},
        "head": "",
        "pr": "",
        "context": {"summary": "Run status could not be read.", "repo": "", "branch": "",
                    "task": None, "detail": detail, "recent": [], "github": ""},
        "error": error,
    }
    suggestion = entry["context"]["detail"].split("Inspect ", 1)[1].split(" for details.", 1)[0]
    executable, *argv = shlex.split(suggestion)
    assert executable == "nightshift"
    args = build_parser().parse_args(argv)
    assert args.handler is cli.dispatch and args.cmd == "status" and args.json is True
    assert "action" not in entry


@pytest.mark.scenario("specloop-interface/isolated-run-status-failures/broken-run-keeps-other-repositories-visible")
@pytest.mark.parametrize("failure, error_class", [
    ("state-json", "JSONDecodeError"),
    ("state-object", "AttributeError"),
    ("pause", "TypeError"),
    ("task-state", "AttributeError"),
    ("manifest-missing", "FileNotFoundError"),
    ("manifest-invalid", "TOMLDecodeError"),
    ("manifest-fields", "KeyError"),
    ("roles", "JSONDecodeError"),
    ("ref", "RuntimeError"),
    ("permission", "PermissionError"),
    ("git-timeout", "TimeoutExpired"),
    ("task-parse", "ValueError"),
    ("message", "AttributeError"),
    ("action", "TypeError"),
])
@pytest.mark.scenario(
    "specloop-documentation/front-door-guidance/input-and-status-remain-usable"
)
def test_status_reports_broken_run_and_keeps_others(admitted, monkeypatch, capsys, failure, error_class):
    # Each admission has its own source and bare repository. Create out of order.
    last, _ = admitted("z-healthy", proposal="## Why\n\nLast repository.\n")
    broken, _ = admitted("m-broken", task_text=TASKS.replace("First task", "Broken task"))
    first, _ = admitted("a-healthy", proposal="## Why\n\nFirst repository.\n")
    for run in (first, broken, last):
        run.state.pr = f"https://github.com/example/{run.state.change}/pull/7"
        run.pause("human_checks", detail="Review the published change")
    before = status_document(capsys)["runs"]
    assert before[0]["context"]["repo"] != before[2]["context"]["repo"]
    state_path = broken.dir / "state.json"
    if failure == "state-json":
        state_path.write_text('{"change":')
    elif failure == "state-object":
        state_path.write_text("[]")
    elif failure in ("pause", "task-state"):
        raw = json.loads(state_path.read_text())
        raw["pause" if failure == "pause" else "tasks"] = {} if failure == "pause" else []
        state_path.write_text(json.dumps(raw))
    elif failure == "manifest-missing":
        broken.manifest_path.unlink()
    elif failure == "manifest-invalid":
        broken.manifest_path.write_text("[run")
    elif failure == "manifest-fields":
        broken.manifest_path.write_text("[run]\n")
    elif failure == "roles":
        (broken.dir / "roles.json").write_text("{")
    elif failure == "ref":
        seal.git(broken.dir / "repo.git", "update-ref", "-d", "refs/heads/spec/m-broken")
    elif failure == "permission":
        read_text = Path.read_text

        def denied(path, *args, **kwargs):
            if path == state_path:
                raise PermissionError("state is unreadable")
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", denied)
    elif failure == "git-timeout":
        real_git = cli.git

        def timeout(repo, *args):
            if repo == broken.dir / "repo.git":
                raise subprocess.TimeoutExpired("git", 60)
            return real_git(repo, *args)

        monkeypatch.setattr(cli, "git", timeout)
    elif failure == "task-parse":
        parse = tasks.parse

        def invalid(text):
            if "Broken task" in text:
                raise ValueError("invalid tasks")
            return parse(text)

        monkeypatch.setattr(tasks, "parse", invalid)
    elif failure == "message":
        # Fail late, after all core fields and context have been assembled.
        for_pause = messages.for_pause

        def fail_action(change, *args, **kwargs):
            if change == "m-broken":
                raise AttributeError("cannot construct pause message")
            return for_pause(change, *args, **kwargs)

        monkeypatch.setattr(messages, "for_pause", fail_action)
    else:
        broken.pause("broken_action")
        actions_for_pause = messages.actions_for_pause

        def fail_verbs(reason):
            if reason == "broken_action":
                raise TypeError("cannot construct action")
            return actions_for_pause(reason)

        monkeypatch.setattr(messages, "actions_for_pause", fail_verbs)

    entries = status_document(capsys)["runs"]
    assert [entry["change"] for entry in entries] == ["a-healthy", "m-broken", "z-healthy"]
    assert entries[0] == before[0]
    assert entries[2] == before[2]
    assert "error" not in entries[0] and "error" not in entries[2]
    assert_status_error(entries[1], "m-broken", error_class, since=1800000000)


@pytest.mark.scenario("specloop-interface/isolated-run-status-failures/error-rows-remain-compatible-and-non-actionable")
@pytest.mark.scenario("specloop-interface/isolated-run-status-failures/broken-run-keeps-other-repositories-visible")
@pytest.mark.parametrize("message", ["", 'private path: /repo\n"failed"', "x" * 1000])
@pytest.mark.scenario(
    "specloop-documentation/front-door-guidance/input-and-status-remain-usable"
)
def test_error_row_shape_and_diagnostic(admitted, monkeypatch, capsys, message):
    from nightshift import outcomes, specloop_attention, specloop_client

    for change in ("z-healthy", "m-broken", "a-healthy"):
        run, _ = admitted(change)
        run.state.pr = f"https://github.com/example/{change}/pull/7"
        run.pause("human_checks", detail="Ready for review")
    document = status_document(capsys)
    before = document["runs"]
    assert before[0]["context"]["repo"] != before[2]["context"]["repo"]

    def runner(argv, **kwargs):
        assert argv == ["specloop", "status", "--json"]
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        return subprocess.CompletedProcess(argv, 0, json.dumps(document), "")

    monkeypatch.setattr(outcomes, "now", lambda: "2026-10-21T12:00:00+00:00")
    cached = specloop_attention.snapshot(runner=runner)
    assert cached["error"] is None
    assert len(cached["entries"]) == 3
    real_manifest = cli._manifest

    def fail_manifest(run):
        if run.dir.name == "m-broken":
            raise RuntimeError(message)
        return real_manifest(run)

    monkeypatch.setattr(cli, "_manifest", fail_manifest)
    document = status_document(capsys)
    error = document["runs"][1]
    assert_status_error(error, "m-broken", "RuntimeError", since=1800000000)
    assert error["error"] == f"RuntimeError: {message}"[:500]
    assert document["runs"][0] == before[0]
    assert document["runs"][2] == before[2]
    accepted = specloop_client.call(["status"], runner=runner)
    assert accepted == document
    expected = [
        {"change": "a-healthy", "reason": "human_checks", "pr": before[0]["pr"]},
        {"change": "m-broken", "reason": "status_error", "pr": ""},
        {"change": "z-healthy", "reason": "human_checks", "pr": before[2]["pr"]},
    ]
    assert specloop_attention._entries(accepted) == expected
    checked_at = "2026-10-21T13:00:00+00:00"
    monkeypatch.setattr(outcomes, "now", lambda: checked_at)
    refreshed = specloop_attention.snapshot(runner=runner)
    assert refreshed == {
        "entries": [{**entry, "checked_at": checked_at, "error": None, "stale": False}
                    for entry in expected],
        "checked_at": checked_at, "error": None,
    }
    assert "action" not in refreshed["entries"][1]
    assert specloop_attention.snapshot(offline=True) == refreshed


@pytest.mark.scenario("specloop-interface/isolated-run-status-failures/named-and-entirely-broken-collections")
@pytest.mark.parametrize("named", [False, True])
def test_named_and_entirely_broken_collections(admitted, capsys, named):
    for change in ("z-broken", "a-broken"):
        run, _ = admitted(change)
        run.manifest_path.unlink()
    names = ["z-broken"] if named else []
    entries = status_document(capsys, *names)["runs"]
    assert [entry["change"] for entry in entries] == (names or ["a-broken", "z-broken"])
    for entry in entries:
        assert_status_error(entry, entry["change"], "FileNotFoundError")


@pytest.mark.scenario("specloop-interface/isolated-run-status-failures/unknown-changes-and-document-failures")
@pytest.mark.parametrize("change", ["../escape", "/absolute", ".", "..", "bad/name", ""])
def test_unsafe_named_status(home, monkeypatch, capsys, change):
    def no_run_read(*args, **kwargs):
        pytest.fail("unsafe name reached run collection")

    monkeypatch.setattr(Run, "for_change", no_run_read)
    document = status_document(capsys, change, code=1)
    assert "unsafe change name" in document["error"]
    assert not home.exists()


@pytest.mark.scenario("specloop-interface/isolated-run-status-failures/unknown-changes-and-document-failures")
@pytest.mark.parametrize("stage", ["discovery", "supervisor"])
@pytest.mark.parametrize("error", [PermissionError("unreadable"), TypeError("bad data"),
                                 subprocess.TimeoutExpired("lookup", 60), RuntimeError()])
@pytest.mark.scenario(
    "specloop-documentation/front-door-guidance/input-and-status-remain-usable"
)
def test_document_failures(admitted, monkeypatch, capsys, stage, error):
    admitted("healthy")

    def fail(*args, **kwargs):
        raise error

    if stage == "discovery":
        monkeypatch.setattr(Path, "glob", fail)
    else:
        monkeypatch.setattr(serve, "liveness", fail)
    status_document(capsys, code=1)


@pytest.mark.scenario("specloop-interface/status-after-partial-finish/missing-live-and-archived-tasks")
@pytest.mark.parametrize("named", [False, True])
def test_missing_live_and_archived_tasks(admitted, tmp_path, capsys, named):
    broken, _ = admitted("broken")
    admitted("healthy")
    healthy = status_document(capsys, "healthy")["runs"][0]
    source = tmp_path / "broken"
    for args in [
        ["rm", "openspec/changes/broken/tasks.md"],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "remove tasks"],
        ["push", "-q", str(broken.dir / "repo.git"), "HEAD:refs/heads/spec/broken"],
    ]:
        subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)
    broken.pause("human_checks", detail="Finish was interrupted")
    entries = status_document(capsys, *(["broken"] if named else []))["runs"]
    assert_status_error(entries[0], "broken", "RuntimeError", since=1800000000)
    assert "tasks.md" in entries[0]["error"]
    assert entries[1:] == ([] if named else [healthy])


@pytest.mark.scenario("specloop-interface/isolated-run-status-failures/read-failures-leave-run-data-untouched")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("failure, error_class", [
    ("corrupt", "JSONDecodeError"), ("permission", "PermissionError"),
    ("disappeared", "FileNotFoundError"), ("action", "TypeError"),
])
def test_failed_status_is_read_only(admitted, tmp_path, monkeypatch, capsys, named, failure, error_class):
    broken, _ = admitted("broken")
    healthy, _ = admitted("healthy")
    for run in (broken, healthy):
        run.state.pr = "https://github.com/example/repo/pull/7"
        run.pause("human_checks", detail="Review before finishing")
    healthy_entry = status_document(capsys, "healthy")["runs"][0]
    state_path = broken.dir / "state.json"
    if failure == "corrupt":
        state_path.write_text("{")

    def snapshot():
        # Include directories, all run files, source repos, refs and reflogs.
        return {
            path.relative_to(tmp_path): (path.stat().st_mtime_ns,
                                         path.read_bytes() if path.is_file() else None)
            for path in tmp_path.rglob("*")
        }

    expected = snapshot()
    load = Run._load

    def read(run):
        nonlocal expected
        assert run.require_existing is True
        if run.dir == broken.dir and failure == "disappeared":
            # Simulate another process deleting state after selection. The
            # real strict loader must now fail, without recreating the file.
            state_path.unlink()
            expected = snapshot()
        return load(run)

    monkeypatch.setattr(Run, "_load", read)
    if failure == "permission":
        read_text = Path.read_text

        def denied(path, *args, **kwargs):
            if path == state_path:
                raise PermissionError("state read denied")
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", denied)
    if failure == "action":
        for_pause = messages.for_pause

        def failed_message(change, *args, **kwargs):
            if change == "broken":
                raise TypeError("action read failed")
            return for_pause(change, *args, **kwargs)

        monkeypatch.setattr(messages, "for_pause", failed_message)

    def no_mutation(*args, **kwargs):
        pytest.fail("failed status tried to mutate run data or create an execution context")

    for method in ("save", "event", "lock", "pause"):
        monkeypatch.setattr(Run, method, no_mutation)
    monkeypatch.setattr(state.RunState, "task", no_mutation)
    monkeypatch.setattr(cli, "_ctx", no_mutation)
    monkeypatch.setattr(cli, "_finish", no_mutation)
    execute = subprocess.run

    def read_only_command(argv, *args, **kwargs):
        if argv[0] != "git" or "-C" not in argv:
            pytest.fail(f"unexpected status command: {argv}")
        if argv[argv.index("-C") + 2] not in {"rev-parse", "ls-tree", "show"}:
            pytest.fail(f"mutating status command: {argv}")
        return execute(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", read_only_command)
    entries = status_document(capsys, *(["broken"] if named else []))["runs"]
    assert_status_error(entries[0], "broken", error_class, since=1800000000)
    assert entries[1:] == ([] if named else [healthy_entry])
    assert snapshot() == expected
    if failure == "disappeared":
        assert not state_path.exists()


@pytest.mark.scenario("specloop-interface/isolated-run-status-failures/read-failures-leave-run-data-untouched")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(7)])
def test_status_propagates_control_flow_exceptions(admitted, monkeypatch, capsys, named, error):
    admitted("broken")

    def interrupted(run):
        raise error

    monkeypatch.setattr(Run, "_load", interrupted)
    with pytest.raises(type(error)) as caught:
        main(["status", "--json", *(["broken"] if named else [])])
    assert caught.value is error
    output = capsys.readouterr()
    assert output.out == output.err == ""


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

    assert main(["status", "--json"]) == 0
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
    assert main(["status", "never-admitted"]) == 0
    assert capsys.readouterr().out == (
        "supervisor: not running\n"
        "never-admitted: no such change (not admitted, or admission was refused)\n"
    )


@pytest.mark.scenario("run-context/context-in-status/paused-run-has-context")
@pytest.mark.parametrize("named", [False, True])
def test_paused_run_context_comes_from_branch(admitted, tmp_path, capsys, named):
    # An earlier unfinished task must not take precedence over the paused task.
    run, _ = admitted("context", proposal="## Why\n\nThe page needs links.\n\nMore rationale.\n",
                      task_text=TASKS.replace("[x]", "[ ]"))
    run.pause("stuck", tasks.parse(TASKS)[1].key, "Failed twice: x")
    # Neither a dirty source checkout nor a candidate is the run's branch head.
    source = tmp_path / "context"
    change_dir = source / "openspec" / "changes" / "context"
    (change_dir / "proposal.md").write_text("## Why\n\nWrong summary.\n")
    (change_dir / "tasks.md").write_text(TASKS.replace("Stuck task", "Wrong task"))

    assert main(["status", "--json", *(["context"] if named else [])]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    context = json.loads(output.out)["runs"][0]["context"]
    assert context["summary"] == "The page needs links."
    assert context["repo"] == str(source)
    assert context["branch"] == "spec/context"
    assert context["task"] == {"label": "1.2", "section": "Core", "text": "Stuck task"}
    assert context["detail"] == "Failed twice: x"


@pytest.mark.scenario("run-context/context-in-status/recent-events-in-plain-words")
def test_context_recent_events(admitted, capsys):
    run, _ = admitted("events")
    first, second, _ = tasks.parse(TASKS)
    events = [
        ("resumed", {}),
        ("pause", {"reason": "stuck"}),
        ("arbitrated", {"task": second.key, "output": {"decision": "retry"}}),
        ("gate_reviewed", {"section": 1, "disposition": "ok", "output": {"verdict": "FAIL"}}),
        ("landed", {"task": first.key, "label": first.label}),
        ("attempt_failed", {"task": second.key, "attempt": 1}),
        ("agent_started", {"task": second.key}),
    ]
    for i, (kind, data) in enumerate(events):
        run.event(kind, at=1800000000 + i, **data)

    assert main(["status", "--json"]) == 0
    recent = json.loads(capsys.readouterr().out)["runs"][0]["context"]["recent"]
    assert [e["text"] for e in recent] == [
        "task 1.2 attempt 1 failed", "task 1.1 landed", "section 1 gate review: FAIL",
        "arbiter ruled retry on task 1.2", "paused: stuck",
    ]
    assert [e["at"] for e in recent] == [
        datetime.fromtimestamp(1800000000 + i, timezone.utc).isoformat().replace("+00:00", "Z")
        for i in range(5, 0, -1)
    ]
    run.event("gate_reviewed", at=1800000007, section=1, output={"verdict": "PASS"})
    run.event("resumed", at=1800000008)
    assert main(["status", "--json"]) == 0
    recent = json.loads(capsys.readouterr().out)["runs"][0]["context"]["recent"]
    assert len(recent) == 5
    assert [e["text"] for e in recent[:2]] == ["resumed", "section 1 gate review: PASS"]


@pytest.mark.scenario("run-context/context-in-status/github-link-only-when-published")
@pytest.mark.parametrize("repository", ["owner/repo", "github.com/owner/repo"])
def test_context_github_requires_pr_and_manifest_repository(admitted, capsys, repository):
    for change, pr, github in [
        ("published", "https://github.com/elsewhere/repo/pull/7", repository),
        ("unpublished", "", repository),
        ("no-github", "https://github.com/owner/repo/pull/8", ""),
    ]:
        run, _ = admitted(change, github=github)
        run.state.pr = pr
        run.save()
    assert main(["status", "--json"]) == 0
    links = {r["change"]: r["context"]["github"] for r in json.loads(capsys.readouterr().out)["runs"]}
    assert links == {
        "published": "https://github.com/owner/repo/tree/spec/published/openspec/changes/published",
        "unpublished": "", "no-github": "",
    }


@pytest.mark.scenario("run-context/context-in-status/github-link-only-when-published")
@pytest.mark.parametrize("host", ["github.com", "GitHub.COM", "github.example.com"])
def test_context_github_normalizes_explicit_manifest_host(admitted, capsys, host):
    run, _ = admitted("published", proposal="## Why\n\nThe page needs links.\n",
                      github=f"{host}/Example/Repo")
    run.state.pr = f"https://{host}/Example/Repo/pull/7"
    run.save()

    assert main(["status", "--json", "published"]) == 0
    context = json.loads(capsys.readouterr().out)["runs"][0]["context"]
    assert context["github"] == (
        "https://github.com/Example/Repo/tree/spec/published/openspec/changes/published"
        if host.lower() == "github.com" else ""
    )
    assert context["summary"] == "The page needs links."
    assert context["branch"] == "spec/published"


@pytest.mark.scenario("run-context/context-in-status/recent-events-in-plain-words")
@pytest.mark.parametrize("rewrite", ["split", "renumber"])
def test_recent_failure_keeps_historical_label_after_task_rewrite(admitted, capsys, rewrite):
    run, head = admitted("history")
    first, second, _ = tasks.parse(TASKS)
    run.event("landed", task=first.key, label=first.label)
    run.event("implement", task=second.key, label=second.label, session=1, attempt=1)
    run.event("attempt_failed", task=second.key, attempt=1)
    run.event("arbitrated", task=second.key, output={"decision": "retry"})

    assert main(["status", "--json"]) == 0
    before = json.loads(capsys.readouterr().out)["runs"][0]["context"]["recent"]
    assert [e["text"] for e in before] == [
        "arbiter ruled retry on task 1.2", "task 1.2 attempt 1 failed", "task 1.1 landed",
    ]

    if rewrite == "split":
        subtasks = ["First part", "Second part"]
        run.event("implement", task=second.key, label=second.label, session=2, attempt=2)
        run.event("implemented", task=second.key, disposition="ok",
                  output={"status": "too_big", "summary": "", "subtasks": subtasks})
        text = tasks.split(TASKS, second, subtasks)
        assert second.key not in {t.key for t in tasks.parse(text)}
        next_label = "1.2.1"
    else:
        text = TASKS.replace("1.2 Stuck task", "1.9 Stuck task")
        next_label = "1.9"
    repo = run.dir / "repo.git"
    rewritten = seal.commit_files(repo, head, {"openspec/changes/history/tasks.md": text}, "rewrite tasks")
    seal.git(repo, "update-ref", "refs/heads/spec/history", rewritten, head)
    # Later implementation events must not relabel older failures, either.
    current = tasks.parse(text)[1]
    run.event("implement", task=current.key, label=current.label, session=3, attempt=2)

    assert main(["status", "--json"]) == 0
    entry = json.loads(capsys.readouterr().out)["runs"][0]
    assert entry["head"] == rewritten
    assert entry["context"]["task"]["label"] == next_label
    assert entry["context"]["recent"] == before


@pytest.mark.scenario("run-context/context-in-status/recent-events-in-plain-words")
@pytest.mark.scenario("run-context/context-in-status/broken-context-does-not-break-status")
@pytest.mark.parametrize("kind", ["gate_reviewed", "arbitrated"])
@pytest.mark.parametrize("output_fields", [{}, {"output": None}, {"output": {}}])
def test_review_without_structured_output_preserves_context(admitted, capsys, kind, output_fields):
    run, _ = admitted("review-timeout", proposal="## Why\n\nThe page needs links.\n",
                      github="owner/repo")
    first, second, _ = tasks.parse(TASKS)
    run.event("landed", task=first.key, label=first.label)
    run.pause("stuck", second.key, "Failed twice: x")
    run.state.pr = "https://github.com/owner/repo/pull/7"
    run.save()

    assert main(["status", "--json"]) == 0
    before = json.loads(capsys.readouterr().out)["runs"][0]["context"]
    assert before["summary"] == "The page needs links."
    assert before["repo"] == str(cli._manifest(run).repo)
    assert before["task"]["label"] == "1.2"
    assert before["detail"] == "Failed twice: x"
    assert [e["text"] for e in before["recent"]] == ["paused: stuck", "task 1.1 landed"]

    # Infrastructure failures can emit reviews without a structured result.
    run.event(kind, section=1, task=second.key, disposition="timeout", **output_fields)
    assert main(["status", "--json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["runs"][0]["context"] == before


@pytest.mark.scenario("run-context/context-in-status/broken-context-does-not-break-status")
def test_missing_proposal_does_not_break_context(admitted, capsys):
    admitted("missing-proposal")
    admitted("healthy", proposal="## Why\n\nThe page needs links.\n")
    assert main(["status", "--json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    contexts = {r["change"]: r["context"] for r in json.loads(output.out)["runs"]}
    assert contexts["missing-proposal"]["summary"] == ""
    assert contexts["missing-proposal"]["branch"] == "spec/missing-proposal"
    assert contexts["healthy"]["summary"] == "The page needs links."


@pytest.mark.scenario("run-context/context-in-status/broken-context-does-not-break-status")
@pytest.mark.parametrize("failure", ["events", "git"])
def test_context_failure_is_isolated_to_one_run(admitted, monkeypatch, capsys, failure):
    broken, _ = admitted("broken", proposal="## Why\n\nBroken run.\n")
    admitted("healthy", proposal="## Why\n\nHealthy run.\n")
    if failure == "events":
        (broken.dir / "events.jsonl").write_text('{"incomplete":')
    else:
        real_git = cli.git

        def fail_proposal(repo, *args):
            if repo == broken.dir / "repo.git" and args[0] == "show" and args[1].endswith("proposal.md"):
                raise subprocess.TimeoutExpired("git", 60)
            return real_git(repo, *args)

        monkeypatch.setattr(cli, "git", fail_proposal)
    assert main(["status", "--json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    entries = {r["change"]: r for r in json.loads(output.out)["runs"]}
    if failure == "events":
        # A damaged event line is skipped; the rest of the context survives.
        assert entries["broken"]["context"]["summary"] == "Broken run."
        assert entries["broken"]["context"]["recent"] == []
    else:
        assert entries["broken"]["context"] == {
            "summary": "", "repo": "", "branch": "", "task": None,
            "detail": "", "recent": [], "github": "",
        }
    assert entries["broken"]["tasks"] == {"done": 1, "total": 3}
    assert entries["healthy"]["context"]["summary"] == "Healthy run."


@pytest.mark.parametrize("proposal, expected", [
    ("## What\n\nNo Why section.\n", ""),
    ("## Why\n\n## What\n\nOther section.\n", ""),
    ("## Why\n\nFirst line\ncontinued.\n\nSecond paragraph.\n", "First line continued."),
    ("## Why\n\n" + "x" * 500, "x" * 399 + "…"),
])
def test_context_summary_paragraph_and_limit(admitted, capsys, proposal, expected):
    admitted("summary", proposal=proposal)
    assert main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["context"]["summary"] == expected


@pytest.mark.parametrize("status", ["running", "paused", "done", "aborted"])
def test_context_task_fallback_and_detail_limit(admitted, capsys, status):
    task_text = TASKS.replace("Stuck task", "x" * 350)
    if status == "done":
        task_text = task_text.replace("[ ]", "[x]")
    run, _ = admitted("task", task_text=task_text)
    run.pause("phase_gate", "section:1", "First line\n" + "d" * 1600)
    run.state.status = status
    run.save()
    assert main(["status", "--json"]) == 0
    context = json.loads(capsys.readouterr().out)["runs"][0]["context"]
    assert context["task"] == (None if status == "done" else {
        "label": "1.2", "section": "Core", "text": "x" * 300,
    })
    assert context["detail"] == (("First line\n" + "d" * 1600)[:1500] if status == "paused" else "")
