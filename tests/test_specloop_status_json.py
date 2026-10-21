import json
import subprocess
from datetime import datetime, timezone

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

    assert cli.main(["status", "--json", *(["context"] if named else [])]) == 0
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

    assert cli.main(["status", "--json"]) == 0
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
    assert cli.main(["status", "--json"]) == 0
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
    assert cli.main(["status", "--json"]) == 0
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

    assert cli.main(["status", "--json", "published"]) == 0
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

    assert cli.main(["status", "--json"]) == 0
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

    assert cli.main(["status", "--json"]) == 0
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

    assert cli.main(["status", "--json"]) == 0
    before = json.loads(capsys.readouterr().out)["runs"][0]["context"]
    assert before["summary"] == "The page needs links."
    assert before["repo"] == str(cli._manifest(run).repo)
    assert before["task"]["label"] == "1.2"
    assert before["detail"] == "Failed twice: x"
    assert [e["text"] for e in before["recent"]] == ["paused: stuck", "task 1.1 landed"]

    # Infrastructure failures can emit reviews without a structured result.
    run.event(kind, section=1, task=second.key, disposition="timeout", **output_fields)
    assert cli.main(["status", "--json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["runs"][0]["context"] == before


@pytest.mark.scenario("run-context/context-in-status/broken-context-does-not-break-status")
def test_missing_proposal_does_not_break_context(admitted, capsys):
    admitted("missing-proposal")
    admitted("healthy", proposal="## Why\n\nThe page needs links.\n")
    assert cli.main(["status", "--json"]) == 0
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
    assert cli.main(["status", "--json"]) == 0
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
    assert cli.main(["status", "--json"]) == 0
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
    assert cli.main(["status", "--json"]) == 0
    context = json.loads(capsys.readouterr().out)["runs"][0]["context"]
    assert context["task"] == (None if status == "done" else {
        "label": "1.2", "section": "Core", "text": "x" * 300,
    })
    assert context["detail"] == (("First line\n" + "d" * 1600)[:1500] if status == "paused" else "")
