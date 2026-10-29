import contextlib
import json
import os
import subprocess
from dataclasses import asdict

import pytest

from nightshift.cli import main

from specloop import cli, seal, state, tasks
from specloop.state import Run
from test_specloop_command_json import BRANCH, CHANGE, TASKS, admission, git, template
from test_specloop_status_json import assert_status_error, status_document


@pytest.fixture
def paused(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path / "home")
    # State-only tests; real source synchronization is exercised below.
    monkeypatch.setattr(cli, "_ctx", lambda run: run)
    monkeypatch.setattr(cli, "_sync", lambda ctx: None)

    def no_drive(*args, **kwargs):
        pytest.fail("act must leave execution to serve")

    monkeypatch.setattr(cli.loop, "run", no_drive)
    run = Run.for_change("change")
    ts = run.state.task("task")
    ts.status, ts.attempts, ts.infra, ts.review_invalid = "stuck", 3, 2, 1
    ts.violations = ["outside allowed paths"]
    run.pause("stuck", task="task")
    return run


def act(capfd, run, verb, *extra, code=0):
    assert main(["act", run.state.change, verb, *extra, "--json"]) == code
    result = json.loads(capfd.readouterr().out)
    assert set(result) == {"version", "outcome", "verb", "detail"}
    assert result["version"] == 1
    assert result["outcome"] == ("done" if code == 0 else "refused")
    assert result["verb"] == verb
    assert isinstance(result["detail"], str) and result["detail"]
    run.reload()
    return result


@pytest.mark.scenario("run-actions/act/resume-with-a-note")
@pytest.mark.parametrize("note", ['Keep "quoted" text; $(literal)\nSecond line.', "é" * 2000])
def test_resume_with_note(paused, capfd, note):
    paused.request_pause()
    act(capfd, paused, "resume", "--note", note)
    assert paused.state.status == "running" and paused.state.pause is None
    ts = paused.state.tasks["task"]
    assert ts.note == note
    assert ts.status == "pending"
    assert (ts.attempts, ts.infra, ts.review_invalid, ts.violations) == (0, 0, 0, [])
    assert not paused.pause_requested()
    event = json.loads((paused.dir / "events.jsonl").read_text().splitlines()[-1])
    assert event["event"] == "resumed" and event["note"] == note
    before = (paused.dir / "state.json").read_bytes()
    act(capfd, paused, "resume", "--note", "double tap", code=1)
    assert (paused.dir / "state.json").read_bytes() == before


@pytest.mark.scenario("paseo-runs-page/server-checks-before-acting/argv-not-shell")
@pytest.mark.parametrize("note", [
    "--no-sync", "--json", "-n", "",
    'Keep $(touch /tmp/nope); `whoami` && echo "$HOME" | cat\nsecond line',
])
def test_factory_note_argument_reaches_cli_unchanged(paused, capfd, note):
    # The plugin binds --note=value in one argv element, including option-like
    # notes. Exercise the real CLI parser and resulting persisted task note.
    act(capfd, paused, "resume", f"--note={note}")
    assert paused.state.status == "running" and paused.state.pause is None
    assert paused.state.tasks["task"].note == note


@pytest.mark.scenario("run-actions/act/wrong-verb-refused")
@pytest.mark.parametrize("reason,verb", [
    ("phase_gate", "publish"), ("blocked", "accept"), ("secret", "resume"),
    ("ready_to_publish", "resume"), ("human_checks", "publish"), ("stuck", "unknown"),
])
def test_wrong_verb_refused(paused, capfd, monkeypatch, reason, verb):
    paused.pause(reason, task="section:1")
    before = {p.name: p.read_bytes() for p in paused.dir.iterdir()}

    def unexpected(*args):
        pytest.fail("refused action loaded an execution context")

    monkeypatch.setattr(cli, "_ctx", unexpected)
    result = act(capfd, paused, verb, code=1)
    assert "not allowed" in result["detail"]
    assert {p.name: p.read_bytes() for p in paused.dir.iterdir() if p.name != "lock"} == before


@pytest.mark.parametrize("verb", ["resume", "abort"])
def test_long_note_refused(paused, capfd, verb):
    before = (paused.dir / "state.json").read_bytes()
    assert "2,000" in act(capfd, paused, verb, "--note", "é" * 2001, code=1)["detail"]
    assert (paused.dir / "state.json").read_bytes() == before


@pytest.mark.parametrize("change", ["missing", "../outside", ""])
def test_unknown_or_unsafe_change_refused(paused, capfd, change):
    before = sorted(state.ROOT.rglob("*"))
    assert main(["act", change, "abort", "--json"]) == 1
    result = json.loads(capfd.readouterr().out)
    assert result["version"] == 1 and result["outcome"] == "refused"
    assert result["verb"] == "abort" and result["detail"]
    assert sorted(state.ROOT.rglob("*")) == before


def test_lock_held_refused(paused, capfd):
    before = (paused.dir / "state.json").read_bytes()
    with paused.lock() as mine:
        assert mine
        assert "locked" in act(capfd, paused, "resume", code=1)["detail"]
    assert (paused.dir / "state.json").read_bytes() == before


def test_reload_after_lock_and_hold_through_save(paused, monkeypatch, capfd):
    original_lock, original_save = Run.lock, Run.save

    @contextlib.contextmanager
    def changed_before_lock(run):
        # Another process resumed after for_change loaded the old pause.
        fresh = Run.for_change(run.state.change)
        fresh.state.status, fresh.state.pause = "running", None
        fresh.save()
        with original_lock(run) as mine:
            yield mine

    with monkeypatch.context() as patch:
        patch.setattr(Run, "lock", changed_before_lock)
        act(capfd, paused, "abort", code=1)
    assert paused.state.status == "running"
    paused.pause("stuck", task="task")

    def save_under_lock(run):
        with original_lock(Run.for_change(run.state.change)) as mine:
            assert not mine, "act released the lock before applying the transition"
        original_save(run)

    monkeypatch.setattr(Run, "save", save_under_lock)
    act(capfd, paused, "resume")


def test_gate_resume_and_secret_abort(paused, capfd):
    paused.pause("phase_gate", task="section:2")
    act(capfd, paused, "resume")
    assert paused.state.gates_passed == [2]
    paused.pause("secret", task="task")
    act(capfd, paused, "abort")
    assert paused.state.status == "aborted"
    assert paused.state.tasks["task"].status == "stuck"
    act(capfd, paused, "abort", code=1)


@pytest.fixture
def admitted(template, monkeypatch, capfd, request):
    path, source = template
    github = getattr(request, "param", "example/repo")
    path.write_text(path.read_text().replace('github = "example/repo"', f'github = "{github}"'))
    assert main(admission(path, "--skip-feasibility")) == 0
    capfd.readouterr()

    def no_drive(*args):
        pytest.fail("act must not drive the run")

    monkeypatch.setattr(cli.loop, "run", no_drive)
    return Run.for_change(CHANGE), source


@pytest.mark.parametrize("reason", ["requested", "blocked"])
@pytest.mark.parametrize("command", ["act", "resume-no-run", "resume"])
def test_resume_imports_source_corrections(admitted, monkeypatch, capfd, reason, command):
    run, source = admitted
    key = tasks.parse(TASKS)[0].key
    run.state.task(key).status = "blocked"
    run.pause(reason, task=key)
    run.request_pause()
    git(source, "switch", BRANCH)
    corrected = TASKS + "\nHuman clarification resolving the pause.\n"
    (source / "openspec" / "changes" / CHANGE / "tasks.md").write_text(corrected)
    git(source, "add", ".")
    git(source, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-qm", "clarify blocked task")
    head = git(source, "rev-parse", BRANCH)
    syncs, driven = [], []
    original_sync = cli._sync

    def sync(ctx):
        with Run.for_change(CHANGE).lock() as mine:
            assert not mine, "source synchronization must hold the driver lock"
        assert Run.for_change(CHANGE).state.status == "paused"
        original_sync(ctx)
        syncs.append(ctx.head())

    def drive(ctx):
        assert ctx.head() == head
        assert ctx.tasks_text(head) == corrected
        assert ctx.run.state.status == "running" and ctx.run.state.pause is None
        assert ctx.run.state.tasks[key].status == "pending"
        assert ctx.run.state.tasks[key].note == "Use the correction"
        assert not ctx.run.pause_requested()
        driven.append(ctx.head())
        return "paused"

    monkeypatch.setattr(cli, "_sync", sync)
    if command == "resume":
        monkeypatch.setattr(cli.loop, "run", drive)
        assert main(["resume", CHANGE, "--note", "Use the correction"]) == 0
    else:
        # admitted's driver stub fails if either deferred command drives inline.
        if command == "act":
            act(capfd, run, "resume", "--note", "Use the correction")
        else:
            assert main(["resume", CHANGE, "--no-run", "--note", "Use the correction"]) == 0
        assert syncs == [head] and driven == []
        monkeypatch.setattr(cli.loop, "run", drive)
        # This is the CLI entry point spawned by the supervisor.
        assert main(["run", CHANGE]) == 0
    assert syncs == driven == [head]


def test_deferred_resume_preserves_diverged_run_branch(admitted, capfd):
    run, source = admitted
    bare = run.dir / "repo.git"
    base = git(bare, "rev-parse", BRANCH)
    landed = seal.commit_files(bare, base, {"out/landed.txt": "landed\n"}, "landed task")
    git(bare, "update-ref", f"refs/heads/{BRANCH}", landed)
    correction = seal.commit_files(source, base, {
        f"openspec/changes/{CHANGE}/tasks.md": TASKS + "\nClarification.\n",
    }, "human correction on a diverged branch")
    git(source, "update-ref", f"refs/heads/{BRANCH}", correction)
    run.pause("requested")

    act(capfd, run, "resume")

    assert run.state.status == "running"
    assert git(bare, "rev-parse", "refs/specloop/source") == correction
    assert git(bare, "rev-parse", BRANCH) == landed
    assert git(bare, "show", f"{BRANCH}:out/landed.txt") == "landed"


def test_resume_sync_error_leaves_pause_intact(admitted, monkeypatch, capfd):
    run, _ = admitted
    run.pause("blocked", task=tasks.parse(TASKS)[0].key)
    run.request_pause()
    before = (run.dir / "state.json").read_bytes()

    def fail(ctx):
        raise RuntimeError("could not update the run branch")

    monkeypatch.setattr(cli, "_sync", fail)
    assert "could not update" in act(capfd, run, "resume", code=1)["detail"]
    assert (run.dir / "state.json").read_bytes() == before
    assert run.pause_requested()


@pytest.mark.parametrize("extra", [[], ["--no-run"]])
def test_direct_resume_locked_run_preserves_pause(admitted, capfd, extra):
    run, _ = admitted
    run.pause("requested")
    before = (run.dir / "state.json").read_bytes()
    with run.lock() as mine:
        assert mine
        assert main(["resume", CHANGE, *extra]) == 1
    assert "being driven elsewhere" in capfd.readouterr().err
    assert (run.dir / "state.json").read_bytes() == before


@pytest.mark.parametrize("command", [
    ["resume", "--no-run"], ["accept", "--no-run"], ["abort"],
    ["publish"], ["finish"], ["waive", "task", "finding"],
])
def test_direct_command_refused_while_act_resumes(paused, monkeypatch, capfd, command):
    def sync(ctx):
        before = {name: (paused.dir / name).read_bytes()
                  for name in ("state.json", "events.jsonl")}
        assert main([command[0], "change", *command[1:]]) == 1
        output = capfd.readouterr()
        assert "being driven elsewhere" in output.err
        for name, contents in before.items():
            assert (paused.dir / name).read_bytes() == contents

    monkeypatch.setattr(cli, "_sync", sync)
    act(capfd, paused, "resume", "--note", "retain this guidance")
    assert paused.state.status == "running"
    assert paused.state.tasks["task"].note == "retain this guidance"


def test_direct_publish_lock_contention_is_a_retryable_error(paused, capfd):
    # A held lock is not a refusal: the daemon escalates refusals to a human
    # and retries errors.
    with paused.lock() as mine:
        assert mine
        assert main(["publish", "change", "--json"]) == 1
    document = json.loads(capfd.readouterr().out)
    assert document["version"] == 1 and "outcome" not in document
    assert "being driven elsewhere" in document["error"]


@pytest.mark.parametrize("command", ["publish", "finish"])
def test_direct_push_holds_lock_against_act_abort(admitted, monkeypatch, capfd, command):
    run, _ = admitted
    bare = run.dir / "repo.git"
    if command == "finish":
        head = git(bare, "rev-parse", BRANCH)
        completed = seal.commit_files(bare, head, {
            f"openspec/changes/{CHANGE}/tasks.md": TASKS.replace("[ ]", "[x]"),
        }, "complete task")
        git(bare, "update-ref", f"refs/heads/{BRANCH}", completed)

        def archive(work, change, all_tasks_done):
            assert all_tasks_done
            directory = work / "openspec" / "changes" / change
            target = directory.parent / "archive" / f"2026-10-20-{change}"
            target.parent.mkdir()
            directory.rename(target)

        monkeypatch.setattr(cli.openspec, "archive", archive)
    run.state.pr = "https://github.com/example/repo/pull/42"
    run.pause("ready_to_publish" if command == "publish" else "human_checks")
    pushes = []

    def push(*args):
        pushes.append(args)
        before = (run.dir / "state.json").read_bytes()
        assert "locked" in act(capfd, run, "abort", code=1)["detail"]
        assert (run.dir / "state.json").read_bytes() == before

    monkeypatch.setattr(cli, "_remote", lambda ctx: "unused")
    monkeypatch.setattr(cli, "_push", push)
    # The fake transport supplies an unchanged base before publication.
    monkeypatch.setattr(cli.loop, "_fetch_publish_base", lambda ctx, remote, **kw: ctx.head())
    monkeypatch.setattr(cli, "_gh", lambda *args: "")
    assert main([command, CHANGE]) == 0
    capfd.readouterr()
    run.reload()
    assert len(pushes) == 1
    assert run.state.status == ("paused" if command == "publish" else "done")
    with run.lock() as mine:
        assert mine, "the direct command must release its lock on completion"
    if command == "publish":
        assert run.state.pause.reason == "human_checks"
        act(capfd, run, "abort")
        assert run.state.status == "aborted"


@pytest.mark.parametrize("command,reason,verb,extra,code", [
    ("resume", "stuck", "resume", ["--no-run"], 1),
    ("accept", "stuck", "resume", ["--no-run"], 1),
    ("abort", "stuck", "resume", [], 0),
    ("waive", "stuck", "resume", ["task", "finding"], 0),
    ("pause", "stuck", "resume", [], 0),
    ("publish", "ready_to_publish", "abort", [], 1),
    ("finish", "human_checks", "abort", [], 1),
])
def test_direct_command_reloads_after_act_wins_race(
        paused, monkeypatch, capfd, command, reason, verb, extra, code):
    paused.pause(reason, task="task")
    original_lock, original_reload = Run.lock, Run.reload
    raced = False
    reloaded = []

    @contextlib.contextmanager
    def act_before_lock(run):
        nonlocal raced
        if not raced:
            raced = True
            # for_change has loaded the direct command's snapshot, but act
            # commits its transition before that command acquires the lock.
            act(capfd, paused, verb, "--note", "new guidance")
        with original_lock(run) as mine:
            yield mine

    def reload_under_lock(run):
        with original_lock(run) as mine:
            assert not mine, "the direct command must lock before reloading"
        reloaded.append(run)
        original_reload(run)

    # The act helper also reloads the fixture after the command; only enforce
    # the locking invariant for the separate direct-command Run instance.
    def reload(run):
        if run is paused:
            original_reload(run)
        else:
            reload_under_lock(run)

    monkeypatch.setattr(Run, "lock", act_before_lock)
    monkeypatch.setattr(Run, "reload", reload)
    assert main([command, "change", *extra]) == code
    assert raced and len(reloaded) == 2  # act and the direct command
    paused.reload()
    if verb == "abort":
        assert paused.state.status == "aborted"
    else:
        assert paused.state.tasks["task"].note == "new guidance"
        assert paused.state.status == {"abort": "aborted", "pause": "paused"}.get(command, "running")
        if command == "waive":
            assert paused.state.tasks["task"].waived == ["finding"]


def test_pause_rechecks_running_state_after_act_abort(paused, monkeypatch, capfd):
    paused.state.status, paused.state.pause = "running", None
    paused.save()
    original_lock = Run.lock
    raced = False

    @contextlib.contextmanager
    def abort_before_lock(run):
        nonlocal raced
        if not raced:
            raced = True
            paused.pause("requested")
            act(capfd, paused, "abort")
        with original_lock(run) as mine:
            yield mine

    monkeypatch.setattr(Run, "lock", abort_before_lock)
    assert main(["pause", "change"]) == 1
    paused.reload()
    assert paused.state.status == "aborted"
    assert not paused.pause_requested()


def test_pause_signals_busy_driver_without_saving_state(paused, capfd):
    paused.state.status, paused.state.pause = "running", None
    paused.save()
    before = (paused.dir / "state.json").read_bytes()
    with paused.lock() as mine:
        assert mine
        assert main(["pause", "change"]) == 0
    assert paused.pause_requested()
    assert (paused.dir / "state.json").read_bytes() == before


@pytest.mark.parametrize("reason", ["stuck", "needs_human"])
def test_accept_lands_candidate_without_driving(admitted, capfd, reason):
    run, _ = admitted
    bare = run.dir / "repo.git"
    head = git(bare, "rev-parse", BRANCH)
    key = tasks.parse(TASKS)[0].key
    ts = run.state.task(key)
    ts.candidate_base = head
    ts.candidate = seal.commit_files(bare, head, {"out/accepted.txt": "accepted\n"}, "candidate")
    run.pause(reason, task=key)
    act(capfd, run, "accept")
    assert run.state.status == "running" and run.state.pause is None
    assert run.state.tasks[key].status == "accepted"
    assert git(bare, "show", f"{BRANCH}:out/accepted.txt") == "accepted"
    assert "- [x]" in git(bare, "show", f"{BRANCH}:openspec/changes/{CHANGE}/tasks.md")


@pytest.mark.parametrize("reason", ["stuck", "needs_human"])
@pytest.mark.parametrize("interruption", ["before_land_save", "after_land"])
@pytest.mark.parametrize("retry_command", ["act", "accept"])
def test_accept_recovers_completed_landing(admitted, monkeypatch, capfd, reason,
                                          interruption, retry_command):
    run, _ = admitted
    bare = run.dir / "repo.git"
    base = git(bare, "rev-parse", BRANCH)
    key = tasks.parse(TASKS)[0].key
    ts = run.state.task(key)
    ts.candidate_base = base
    ts.candidate = seal.commit_files(bare, base, {"out/accepted.txt": "accepted\n"}, "candidate")
    ts.status, ts.pending_review, ts.feedback = "stuck", ts.candidate, "review findings"
    candidate = ts.candidate
    run.pause(reason, task=key)
    pause = asdict(run.state.pause)
    original_land = cli.loop._land

    def stop(*args, **kwargs):
        raise SystemExit("acceptance interrupted")

    def land_then_stop(*args):
        original_land(*args)
        stop()

    with monkeypatch.context() as crash:
        if interruption == "before_land_save":
            crash.setattr(Run, "save", stop)
        else:
            crash.setattr(cli.loop, "_land", land_then_stop)
        with pytest.raises(SystemExit, match="acceptance interrupted"):
            main(["act", CHANGE, "accept", "--json"])
    capfd.readouterr()
    run.reload()
    landed = git(bare, "rev-parse", BRANCH)
    assert landed != base and git(bare, "rev-parse", f"{landed}^") == candidate
    assert run.state.status == "paused" and asdict(run.state.pause) == pause
    assert run.state.tasks[key].status == ("passed" if interruption == "after_land" else "stuck")
    events = (run.dir / "events.jsonl").read_bytes()

    def unexpected_land(*args):
        pytest.fail("retry must finish the transition without landing twice")

    monkeypatch.setattr(cli.loop, "_land", unexpected_land)
    if retry_command == "act":
        act(capfd, run, "accept")
    else:
        assert main(["accept", CHANGE, key, "--no-run"]) == 0
        capfd.readouterr()
        run.reload()
    assert run.state.status == "running" and run.state.pause is None
    ts = run.state.tasks[key]
    assert (ts.status, ts.pending_review, ts.feedback) == ("accepted", "", "")
    assert git(bare, "rev-parse", BRANCH) == landed
    assert git(bare, "show", f"{BRANCH}:out/accepted.txt") == "accepted"
    assert tasks.parse(git(bare, "show", f"{BRANCH}:openspec/changes/{CHANGE}/tasks.md"))[0].done
    assert (run.dir / "events.jsonl").read_bytes() == events
    before = (run.dir / "state.json").read_bytes()
    act(capfd, run, "accept", code=1)
    assert (run.dir / "state.json").read_bytes() == before
    assert git(bare, "rev-parse", BRANCH) == landed


@pytest.mark.parametrize("reason", ["stuck", "needs_human"])
@pytest.mark.parametrize("later_pause", ["requested", "stuck", "needs_human", None])
def test_completed_accept_cannot_clear_later_pause(admitted, capfd, reason, later_pause):
    run, _ = admitted
    bare = run.dir / "repo.git"
    base = git(bare, "rev-parse", BRANCH)
    key = tasks.parse(TASKS)[0].key
    ts = run.state.task(key)
    ts.candidate_base = base
    ts.candidate = seal.commit_files(bare, base, {"out/accepted.txt": "accepted\n"}, "candidate")
    run.pause(reason, task=key)
    assert main(["accept", CHANGE, key, "--no-run"]) == 0
    run.reload()
    assert run.state.tasks[key].status == "accepted"
    if later_pause == "requested":
        assert main(["pause", CHANGE]) == 0
    elif later_pause:
        # Even a subsequent pause with the same reason and task is unrelated.
        run.pause(later_pause, task=key)
    capfd.readouterr()
    landed = git(bare, "rev-parse", BRANCH)
    before = {name: (run.dir / name).read_bytes() for name in ("state.json", "events.jsonl")}
    assert main(["accept", CHANGE, key, "--no-run"]) == 1
    assert git(bare, "rev-parse", BRANCH) == landed
    for name, contents in before.items():
        assert (run.dir / name).read_bytes() == contents


@pytest.mark.parametrize("later_pause", ["requested", "other_task", "running"])
def test_interrupted_accept_requires_matching_pause(admitted, monkeypatch, capfd, later_pause):
    run, _ = admitted
    bare = run.dir / "repo.git"
    base = git(bare, "rev-parse", BRANCH)
    key = tasks.parse(TASKS)[0].key
    ts = run.state.task(key)
    ts.candidate_base = base
    ts.candidate = seal.commit_files(bare, base, {"out/accepted.txt": "accepted\n"}, "candidate")
    run.pause("stuck", task=key)
    original_land = cli.loop._land

    def land_then_stop(*args):
        original_land(*args)
        raise SystemExit("acceptance interrupted")

    with monkeypatch.context() as crash:
        crash.setattr(cli.loop, "_land", land_then_stop)
        with pytest.raises(SystemExit, match="acceptance interrupted"):
            main(["accept", CHANGE, key, "--no-run"])
    run.reload()
    if later_pause == "requested":
        run.pause("requested", task=key)
    elif later_pause == "other_task":
        run.pause("stuck", task="another-task")
    else:
        run.state.status, run.state.pause = "running", None
        run.save()
    landed = git(bare, "rev-parse", BRANCH)
    before = {name: (run.dir / name).read_bytes() for name in ("state.json", "events.jsonl")}
    assert main(["accept", CHANGE, key, "--no-run"]) == 1
    assert git(bare, "rev-parse", BRANCH) == landed
    for name, contents in before.items():
        assert (run.dir / name).read_bytes() == contents


@pytest.mark.parametrize("movement", ["unrelated", "candidate", "unticked", "modified", "later"])
def test_accept_recovery_refuses_other_branch_movement(admitted, capfd, movement):
    run, _ = admitted
    bare = run.dir / "repo.git"
    base = git(bare, "rev-parse", BRANCH)
    key = tasks.parse(TASKS)[0].key
    ts = run.state.task(key)
    ts.candidate_base = base
    ts.candidate = seal.commit_files(bare, base, {"out/accepted.txt": "accepted\n"}, "candidate")
    # Neither a passed state nor a tick alone proves that this candidate landed.
    ts.status = "passed"
    tick = {f"openspec/changes/{CHANGE}/tasks.md": TASKS.replace("[ ]", "[x]")}
    if movement == "unrelated":
        moved = seal.commit_files(bare, base, tick, "other completion")
    elif movement == "candidate":
        moved = ts.candidate
    elif movement == "unticked":
        moved = seal.commit_files(bare, ts.candidate, {}, "no task tick")
    elif movement == "modified":
        moved = seal.commit_files(bare, ts.candidate,
                                  {**tick, "out/accepted.txt": "different\n"}, "different result")
    else:
        landed = seal.commit_files(bare, ts.candidate, tick, "complete task")
        moved = seal.commit_files(bare, landed, {"out/accepted.txt": "later\n"}, "later edit")
    git(bare, "update-ref", f"refs/heads/{BRANCH}", moved)
    run.pause("stuck", task=key)
    before = {name: (run.dir / name).read_bytes() for name in ("state.json", "events.jsonl")}
    act(capfd, run, "accept", code=1)
    assert git(bare, "rev-parse", BRANCH) == moved
    for name, contents in before.items():
        assert (run.dir / name).read_bytes() == contents


def test_accept_without_candidate_refused(admitted, capfd):
    run, _ = admitted
    run.pause("stuck", task=tasks.parse(TASKS)[0].key)
    before = asdict(run.state)
    act(capfd, run, "accept", code=1)
    assert asdict(run.state) == before


def test_direct_accept_preserves_abort_that_wins_lock(admitted, monkeypatch, capfd):
    run, _ = admitted
    bare = run.dir / "repo.git"
    head = git(bare, "rev-parse", BRANCH)
    key = tasks.parse(TASKS)[0].key
    ts = run.state.task(key)
    ts.candidate_base = head
    ts.candidate = seal.commit_files(bare, head, {"out/accepted.txt": "accepted\n"}, "candidate")
    run.pause("stuck", task=key)
    original_lock = Run.lock
    aborted = {}

    @contextlib.contextmanager
    def abort_before_lock(current):
        if not aborted:
            # Use the original lock for act, then let the direct command
            # acquire it with its still-stale candidate snapshot.
            with monkeypatch.context() as patch:
                patch.setattr(Run, "lock", original_lock)
                act(capfd, run, "abort")
            for name in ("state.json", "events.jsonl"):
                aborted[name] = (run.dir / name).read_bytes()
        with original_lock(current) as mine:
            yield mine

    monkeypatch.setattr(Run, "lock", abort_before_lock)
    assert main(["accept", CHANGE, key, "--no-run"]) == 1
    assert "aborted" in capfd.readouterr().err
    assert git(bare, "rev-parse", BRANCH) == head
    for name, contents in aborted.items():
        assert (run.dir / name).read_bytes() == contents


@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/local-origin-compatibility-is-github-only")
def test_publish_and_finish_use_existing_transitions(admitted, tmp_path, monkeypatch, capfd):
    run, source = admitted
    remote = tmp_path / "remote.git"
    git(source, "clone", "-q", "--bare", str(source), str(remote))
    git(source, "remote", "add", "origin", str(remote))
    bare = run.dir / "repo.git"
    head = git(bare, "rev-parse", BRANCH)
    completed = seal.commit_files(bare, head, {
        f"openspec/changes/{CHANGE}/tasks.md": TASKS.replace("[ ]", "[x]"),
    }, "complete task")
    git(bare, "update-ref", f"refs/heads/{BRANCH}", completed)
    run.state.pr = "https://github.com/example/repo/pull/42"
    run.pause("ready_to_publish")
    calls = []

    def gh(*args):
        with Run.for_change(CHANGE).lock() as mine:
            assert not mine
        calls.append(args)
        # Both Python and inherited output must stay off JSON stdout.
        print("GitHub progress")
        os.write(1, b"child progress\n")
        return ""

    def archive(work, change, all_tasks_done):
        assert all_tasks_done
        directory = work / "openspec" / "changes" / change
        target = directory.parent / "archive" / f"2026-10-20-{change}"
        target.parent.mkdir()
        directory.rename(target)

    monkeypatch.setattr(cli, "_gh", gh)
    monkeypatch.setattr(cli.openspec, "archive", archive)
    act(capfd, run, "publish")
    assert run.state.pause.reason == "human_checks"
    assert git(remote, "rev-parse", BRANCH) == completed
    assert calls == []  # Adopt the existing PR.
    assert main(["status", "--json", CHANGE]) == 0
    entry = json.loads(capfd.readouterr().out)["runs"][0]
    assert entry["context"]["github"] == (
        f"https://github.com/example/repo/tree/{BRANCH}/openspec/changes/{CHANGE}"
    )
    act(capfd, run, "finish")
    assert run.state.status == "done" and run.state.pause is None
    assert calls == [("pr", "ready", run.state.pr, "-R", "example/repo")]
    assert "archive/" in git(remote, "ls-tree", "-r", "--name-only", BRANCH)
    assert main(["status", "--json", CHANGE]) == 0
    entry = json.loads(capfd.readouterr().out)["runs"][0]
    assert entry["status"] == "done"
    assert entry["context"]["github"] == (
        f"https://github.com/example/repo/tree/{BRANCH}"
        f"/openspec/changes/archive/2026-10-20-{CHANGE}"
    )


def test_publish_failure_returns_refused_json(admitted, monkeypatch, capfd):
    run, _ = admitted
    run.pause("ready_to_publish")
    before = asdict(run.state)
    monkeypatch.setattr(cli, "_remote", lambda ctx: "unused")
    # The fake transport supplies an unchanged base before the failing push.
    monkeypatch.setattr(cli.loop, "_fetch_publish_base", lambda ctx, remote, **kw: ctx.head())

    def fail(*args):
        raise RuntimeError("push failed")

    monkeypatch.setattr(cli, "_push", fail)
    assert act(capfd, run, "publish", code=1)["detail"] == "push failed"
    assert asdict(run.state) == before


def publication_pr(url, repository="example/repo"):
    return {"html_url": url, "head": {"ref": BRANCH,
            "repo": {"full_name": repository} if repository else None}, "base": {"ref": "main"}}


@pytest.mark.scenario("specloop-forge/manifest-forge-selection/github-stays-compatible")
@pytest.mark.parametrize("fork_count", [0, 30])
@pytest.mark.parametrize("interruption", ["response_lost", "before_save"])
@pytest.mark.parametrize("retry_command", ["act", "publish"])
@pytest.mark.parametrize("admitted", ["example/repo", "github.com/example/repo",
                                     "github.com/Example/Repo"], indirect=True)
def test_publish_recovers_unrecorded_pr(admitted, tmp_path, monkeypatch, capfd, interruption,
                                       retry_command, fork_count):
    run, source = admitted
    github = cli._ctx(run).m.github
    remote = tmp_path / "remote.git"
    git(source, "clone", "-q", "--bare", str(source), str(remote))
    git(source, "remote", "add", "origin", str(remote))
    run.pause("ready_to_publish")
    before = (run.dir / "state.json").read_bytes()
    host = github.split("/")[0] if github.count("/") == 2 else "github.com"
    url = f"https://{host}/example/repo/pull/42"
    created, calls = [], []
    repository = "/".join(github.split("/")[-2:])
    lookup = ("api", f"repos/{repository}/pulls")
    forks = [publication_pr(f"https://{host}/example/repo/pull/{n}", f"fork{n}/repo")
             for n in range(fork_count)]
    pages = 1 + fork_count // 30
    unavailable = False

    def gh(*args):
        calls.append(args[:2])
        if args[:2] == lookup:
            page = int(args[-1].removeprefix("page="))
            host_args = ("--hostname", host) if github.count("/") == 2 else ()
            assert args == (*lookup, *host_args, "--method", "GET", "-f", "state=open",
                            "-f", "base=main", "-f", "per_page=30", "-f", f"page={page}")
            # Fail the final page, after any unrelated PRs have been inspected.
            if unavailable and page == pages:
                raise RuntimeError("lookup unavailable")
            prs = forks + [publication_pr(pr) for pr in created]
            return json.dumps(prs[(page - 1) * 30:page * 30])
        assert args[:2] == ("pr", "create") and "--draft" in args
        assert args[args.index("-R") + 1] == github
        assert not created, "retried creation of a PR that already exists"
        created.append(url)
        if interruption == "response_lost":
            raise RuntimeError("create response lost")
        return url

    def crash_before_save(ctx, *args, **kwargs):
        assert ctx.run.state.pr == url
        raise SystemExit("publication interrupted")

    monkeypatch.setattr(cli, "_gh", gh)
    with monkeypatch.context() as crash:
        if interruption == "before_save":
            crash.setattr(cli.loop.Ctx, "pause", crash_before_save)
            with pytest.raises(SystemExit, match="publication interrupted"):
                main(["act", CHANGE, "publish", "--json"])
            capfd.readouterr()
        else:
            assert "create response lost" in act(capfd, run, "publish", code=1)["detail"]
    assert created == [url]
    assert (run.dir / "state.json").read_bytes() == before
    assert calls == [lookup] * pages + [("pr", "create")]

    # An inconclusive lookup must preserve the pause without trying creation.
    unavailable = True
    assert "lookup unavailable" in act(capfd, run, "publish", code=1)["detail"]
    assert (run.dir / "state.json").read_bytes() == before
    unavailable = False
    if retry_command == "act":
        act(capfd, run, "publish")
    else:
        assert main(["publish", CHANGE, "--json"]) == 0
        assert json.loads(capfd.readouterr().out) == {"version": 1, "outcome": "published", "pr": url}
        run.reload()
    assert run.state.pr == url and run.state.pause.reason == "human_checks"
    assert url in run.state.pause.detail
    assert calls == [lookup] * pages + [("pr", "create")] + [lookup] * (2 * pages)
    assert git(remote, "rev-parse", BRANCH) == git(run.dir / "repo.git", "rev-parse", BRANCH)
    assert main(["status", "--json"]) == 0
    entry = json.loads(capfd.readouterr().out)["runs"][0]
    assert entry["pr"] == url and entry["pause"]["reason"] == "human_checks"
    assert entry["action"]["actions"] == ["finish", "abort"]
    act(capfd, run, "publish", code=1)
    assert len(calls) == 1 + 3 * pages


@pytest.mark.parametrize("repository,head,base", [
    ("contributor/repo", BRANCH, "main"), ("example/other", BRANCH, "main"), (None, BRANCH, "main"),
    ("example/repo", "other-branch", "main"), ("example/repo", BRANCH, "other-base"),
])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("admitted", ["example/repo", "github.com/example/repo"], indirect=True)
def test_publish_ignores_unrelated_pr(admitted, monkeypatch, capfd, repository, head, base, existing):
    """Only the run's repository, head branch and base branch identify its PR."""
    run, _ = admitted
    run.pause("ready_to_publish")
    url = "https://github.com/example/repo/pull/42"
    unrelated = "https://github.com/example/repo/pull/41"
    prs = [publication_pr(unrelated, repository)]
    prs[0]["head"]["ref"] = head
    prs[0]["base"]["ref"] = base
    if existing:
        prs.append(publication_pr(url))
    calls = []

    def gh(*args):
        calls.append(args[:2])
        if args[:2] == ("api", "repos/example/repo/pulls"):
            return json.dumps(prs)
        assert args[:2] == ("pr", "create") and not existing
        return url

    monkeypatch.setattr(cli, "_gh", gh)
    monkeypatch.setattr(cli, "_remote", lambda ctx: "unused")
    monkeypatch.setattr(cli, "_push", lambda *args: None)
    # PR matching uses a fake transport with an unchanged base.
    monkeypatch.setattr(cli.loop, "_fetch_publish_base", lambda ctx, remote, **kw: ctx.head())
    act(capfd, run, "publish")
    assert calls == [("api", "repos/example/repo/pulls")] + ([] if existing else [("pr", "create")])
    assert run.state.pr == url and run.state.pause.reason == "human_checks"
    assert url in run.state.pause.detail and unrelated not in run.state.pause.detail


@pytest.mark.parametrize("failure", ["push", "ready", "ready_response_lost"])
@pytest.mark.parametrize("retry_command", ["act", "finish"])
def test_finish_restarts_from_archive(admitted, tmp_path, monkeypatch, capfd, failure, retry_command):
    run, source = admitted
    bare = run.dir / "repo.git"
    remote = tmp_path / "remote.git"
    git(source, "clone", "-q", "--bare", str(source), str(remote))
    git(source, "remote", "add", "origin", str(remote))
    completed = seal.commit_files(bare, git(bare, "rev-parse", BRANCH), {
        f"openspec/changes/{CHANGE}/tasks.md": TASKS.replace("[ ]", "[x]"),
    }, "complete task")
    git(bare, "update-ref", f"refs/heads/{BRANCH}", completed)
    run.state.pr = "https://github.com/example/repo/pull/42"
    run.pause("human_checks", detail="Check the PR")
    before = asdict(run.state)
    archives, pushes = [], []
    unavailable, draft = True, True
    real_push = cli._push

    def archive(work, change, all_tasks_done):
        assert all_tasks_done
        archives.append(change)
        directory = work / "openspec" / "changes" / change
        target = directory.parent / "archive" / f"2026-10-20-{change}"
        target.parent.mkdir()
        directory.rename(target)

    def push(repo, url, ref):
        pushes.append(git(repo, "rev-parse", ref))
        if unavailable and failure == "push":
            raise RuntimeError("network unavailable")
        real_push(repo, url, ref)

    def gh(*args):
        nonlocal draft
        if args[:2] == ("pr", "view"):
            if unavailable:
                raise RuntimeError("network unavailable")
            return "true" if draft else "false"
        assert args == ("pr", "ready", run.state.pr, "-R", "example/repo")
        if unavailable:
            if failure == "ready_response_lost":
                draft = False
            raise RuntimeError("network unavailable")
        if not draft:
            raise RuntimeError("already marked ready")
        draft = False
        return ""

    monkeypatch.setattr(cli.openspec, "archive", archive)
    monkeypatch.setattr(cli, "_push", push)
    monkeypatch.setattr(cli, "_gh", gh)
    for _ in range(2):
        assert "network unavailable" in act(capfd, run, "finish", code=1)["detail"]
        assert asdict(run.state) == before
        archived = git(bare, "rev-parse", BRANCH)
        assert archived != completed
        assert git(bare, "rev-parse", f"{archived}^") == completed
        assert archives == [CHANGE]
        assert pushes == [archived] * len(pushes)
        assert git(bare, "show", f"{archived}:openspec/changes/archive/2026-10-20-{CHANGE}/tasks.md")
        assert main(["status", "--json"]) == 0
        entry = json.loads(capfd.readouterr().out)["runs"][0]
        assert entry["status"] == "paused" and entry["pause"]["reason"] == "human_checks"
        assert entry["head"] == archived and entry["tasks"] == {"done": 1, "total": 1}
        assert entry["action"]["actions"] == ["finish", "abort"]
        assert entry["context"]["github"] == (
            f"https://github.com/example/repo/tree/{BRANCH}"
            f"/openspec/changes/archive/2026-10-20-{CHANGE}"
        )
        assert main(["status"]) == 0
        output = capfd.readouterr().out
        assert "tasks: 1/1 done" in output and "could not read tasks" not in output

    unavailable = False
    if retry_command == "act":
        act(capfd, run, "finish")
    else:
        assert main(["finish", CHANGE]) == 0
        capfd.readouterr()
        run.reload()
    assert run.state.status == "done" and run.state.pause is None
    assert not draft
    assert git(bare, "rev-parse", BRANCH) == git(remote, "rev-parse", BRANCH) == archived
    assert archives == [CHANGE] and pushes == [archived] * 3
    assert main(["status", "--json", CHANGE]) == 0
    entry = json.loads(capfd.readouterr().out)["runs"][0]
    assert entry["status"] == "done"
    assert entry["context"]["github"] == (
        f"https://github.com/example/repo/tree/{BRANCH}"
        f"/openspec/changes/archive/2026-10-20-{CHANGE}"
    )
    events = [json.loads(line) for line in (run.dir / "events.jsonl").read_text().splitlines()]
    assert [e["head"] for e in events if e["event"] == "finished"] == [archived]
    act(capfd, run, "finish", code=1)
    assert len(pushes) == 3


@pytest.mark.scenario("specloop-interface/status-after-partial-finish/archive-survives-failed-finish-push")
@pytest.mark.scenario("specloop-interface/isolated-run-status-failures/read-failures-leave-run-data-untouched")
@pytest.mark.parametrize("named", [False, True])
def test_status_after_partial_finish(admitted, template, tmp_path, monkeypatch, capfd, named):
    # Keep the additive pause timestamp deterministic in exact status assertions.
    monkeypatch.setattr(state.time, "time", lambda: 1800000000)
    run, source = admitted
    bare = run.dir / "repo.git"
    completed = seal.commit_files(bare, git(bare, "rev-parse", BRANCH), {
        f"openspec/changes/{CHANGE}/tasks.md": TASKS.replace("[ ]", "[x]"),
    }, "complete task")
    git(bare, "update-ref", f"refs/heads/{BRANCH}", completed)
    run.state.pr = "https://github.com/example/repo/pull/42"
    run.pause("human_checks", detail="Check the PR")
    saved_state = (run.dir / "state.json").read_bytes()
    archives, pushes = [], []

    def archive(work, change, all_tasks_done):
        assert all_tasks_done
        archives.append(change)
        directory = work / "openspec" / "changes" / change
        target = directory.parent / "archive" / f"2026-10-20-{change}"
        target.parent.mkdir()
        directory.rename(target)

    def fail_push(repo, url, ref):
        pushes.append(git(repo, "rev-parse", ref))
        raise RuntimeError("network unavailable")

    def no_mutation(*args, **kwargs):
        pytest.fail("status must not mutate run data, load credentials, or retry publication")

    monkeypatch.setattr(cli.openspec, "archive", archive)
    monkeypatch.setattr(cli, "_remote", lambda ctx: "unused")
    monkeypatch.setattr(cli, "_push", fail_push)
    monkeypatch.setattr(cli, "_gh", no_mutation)
    assert "network unavailable" in act(capfd, run, "finish", code=1)["detail"]
    archived = git(bare, "rev-parse", BRANCH)
    assert archived != completed
    assert git(bare, "rev-parse", f"{archived}^") == completed
    archive_path = f"openspec/changes/archive/2026-10-20-{CHANGE}/tasks.md"
    assert cli._tasks_path(bare, archived, CHANGE) == archive_path
    assert f"openspec/changes/{CHANGE}/tasks.md" not in git(
        bare, "ls-tree", "-r", "--name-only", archived
    ).splitlines()
    assert git(bare, "show", f"{archived}:{archive_path}") == TASKS.replace("[ ]", "[x]").strip()
    assert (run.dir / "state.json").read_bytes() == saved_state
    assert run.state.status == "paused" and run.state.pause.reason == "human_checks"

    # Admit a second source repository so all-run status must retain its row.
    healthy_change = "z-healthy"
    healthy_branch = f"spec/{healthy_change}"
    healthy_source = tmp_path / "healthy-source"
    git(source, "clone", "-q", str(source), str(healthy_source))
    healthy_head = seal.commit_files(healthy_source, git(healthy_source, "rev-parse", "HEAD"), {
        f"openspec/changes/{healthy_change}/tasks.md": TASKS,
    }, "healthy change")
    git(healthy_source, "update-ref", f"refs/heads/{healthy_branch}", healthy_head)
    healthy_template = tmp_path / "healthy.toml"
    healthy_template.write_text(template[0].read_text().replace(str(source), str(healthy_source)))
    assert main(["admit", str(healthy_template), "--change", healthy_change,
                     "--branch", healthy_branch, "--skip-feasibility"]) == 0
    capfd.readouterr()
    healthy = status_document(capfd, healthy_change)["runs"][0]
    assert healthy["head"] == healthy_head
    assert healthy["status"] == "running" and "error" not in healthy
    assert healthy["tasks"] == {"done": 0, "total": 1}
    assert healthy["context"]["repo"] == str(healthy_source)

    def snapshot():
        # Includes state/events, directory entries, refs and reflogs in both repos.
        return {
            path.relative_to(tmp_path): (path.stat().st_mtime_ns,
                                         path.read_bytes() if path.is_file() else None)
            for path in tmp_path.rglob("*")
        }

    before = snapshot()
    repositories = (source, bare, healthy_source, Run.for_change(healthy_change).dir / "repo.git")
    refs = [git(repo, "show-ref") for repo in repositories]
    execute = subprocess.run
    unreadable = False

    def read_only_command(argv, *args, **kwargs):
        assert argv[0] == "git" and "-C" in argv, f"unexpected status command: {argv}"
        command = argv[argv.index("-C") + 2]
        assert command in {"rev-parse", "ls-tree", "show"}, f"mutating status command: {argv}"
        if unreadable and command == "show" and argv[-1] == f"{archived}:{archive_path}":
            raise PermissionError("archive read denied")
        return execute(argv, *args, **kwargs)

    with monkeypatch.context() as patch:
        for method in ("save", "event", "lock", "pause"):
            patch.setattr(Run, method, no_mutation)
        for method in ("_ctx", "load_env", "_finish", "_push", "_remote"):
            patch.setattr(cli, method, no_mutation)
        patch.setattr(cli.openspec, "archive", no_mutation)
        patch.setattr(seal, "credential_values", no_mutation)
        patch.setattr(subprocess, "run", read_only_command)
        for unreadable in (False, True):
            entries = status_document(capfd, *([CHANGE] if named else []))["runs"]
            assert [entry["change"] for entry in entries] == (
                [CHANGE] if named else [CHANGE, healthy_change]
            )
            entry = entries[0]
            if unreadable:
                assert_status_error(entry, CHANGE, "PermissionError", since=1800000000)
                assert "archive read denied" in entry["error"]
            else:
                assert "error" not in entry
                assert entry["head"] == archived and entry["tasks"] == {"done": 1, "total": 1}
                assert entry["status"] == "paused" and entry["pr"] == run.state.pr
                assert entry["pause"] == {
                    "reason": "human_checks", "task": "", "detail": "Check the PR",
                    "auto_resumes_remaining": 0,
                    "since": 1800000000,
                }
                assert entry["action"]["actions"] == ["finish", "abort"]
                assert entry["action"]["pause_id"] == "human_checks:"
                assert entry["context"]["github"] == (
                    f"https://github.com/example/repo/tree/{BRANCH}/{archive_path.removesuffix('/tasks.md')}"
                )
            assert entries[1:] == ([] if named else [healthy])
            assert snapshot() == before
    assert [git(repo, "show-ref") for repo in repositories] == refs
    assert archives == [CHANGE] and pushes == [archived]


@pytest.mark.parametrize("archived", [False, True])
def test_finish_still_refuses_unfinished_tasks(admitted, monkeypatch, capfd, archived):
    run, _ = admitted
    bare = run.dir / "repo.git"
    if archived:
        # An older archive must not hide unfinished tasks in the active change.
        head = git(bare, "rev-parse", BRANCH)
        head = seal.commit_files(bare, head, {
            f"openspec/changes/archive/2026-10-19-{CHANGE}/tasks.md": TASKS.replace("[ ]", "[x]"),
        }, "older archive")
        git(bare, "update-ref", f"refs/heads/{BRANCH}", head)
    run.pause("human_checks")
    before = asdict(run.state)

    def unexpected(*args, **kwargs):
        pytest.fail("unfinished tasks must not archive or publish")

    monkeypatch.setattr(cli.openspec, "archive", unexpected)
    monkeypatch.setattr(cli, "_push", unexpected)
    monkeypatch.setattr(cli, "_gh", unexpected)
    act(capfd, run, "finish", code=1)
    assert asdict(run.state) == before


@pytest.mark.parametrize("expect, outcome", [("stuck:task", "done"), ("stuck:another-task", "refused"),
                                             ("phase_gate:section:1", "refused")])
def test_act_refuses_a_pause_other_than_the_one_shown(paused, capfd, expect, outcome):
    # The page sends the pause it showed; a newer pause of the same kind is not it.
    code = main(["act", "change", "resume", f"--expect={expect}", "--json"])
    document = json.loads(capfd.readouterr().out)
    assert document["outcome"] == outcome and code == (0 if outcome == "done" else 1)
    if outcome == "refused":
        assert "changed since it was shown" in document["detail"]
        assert Run.for_change("change").state.pause.reason == "stuck"


def no_push(*args):
    pytest.fail("publishing must not push to a remote it cannot vouch for")


@pytest.mark.parametrize("admitted", [""], indirect=True)
def test_publish_without_github_repository_is_refused_with_its_reason(admitted, monkeypatch, capfd):
    run, source = admitted
    git(source, "remote", "add", "origin", "https://github.com/example/repo.git")
    run.pause("ready_to_publish")
    before = asdict(run.state)
    monkeypatch.setattr(cli, "_push", no_push)
    detail = act(capfd, run, "publish", code=1)["detail"]
    assert "names no GitHub repository" in detail and str(run.dir / "manifest.toml") in detail
    assert asdict(run.state) == before


@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/fork-still-pointing-upstream")
@pytest.mark.parametrize("origin", ["https://github.com/upstream/repo.git", "git@github.com:upstream/repo.git",
                                    "https://github.example.com/example/repo.git"])
def test_publish_to_an_origin_of_another_repository_is_refused(admitted, monkeypatch, capfd, origin):
    run, source = admitted
    git(source, "remote", "add", "origin", origin)
    run.pause("ready_to_publish")
    before = asdict(run.state)
    monkeypatch.setattr(cli, "_push", no_push)
    monkeypatch.setattr(cli, "_gh", no_push)
    detail = act(capfd, run, "publish", code=1)["detail"]
    assert "repoint origin" in detail and "example/repo" in detail
    assert asdict(run.state) == before
    assert main(["publish", CHANGE, "--json"]) == 1
    document = json.loads(capfd.readouterr().out)
    assert document["outcome"] == "refused" and document["reasons"] == [detail]
    run.reload()
    assert asdict(run.state) == before


@pytest.mark.parametrize("origin", ["https://github.com/Example/Repo.git", "git@github.com:example/repo",
                                    "ssh://git@github.com:22/example/repo.git/"])
def test_publish_pushes_to_an_origin_matching_the_manifest(admitted, monkeypatch, capfd, origin):
    run, source = admitted
    git(source, "remote", "add", "origin", origin)
    run.state.pr = "https://github.com/example/repo/pull/42"
    run.pause("ready_to_publish")
    # Validate hosted URLs without contacting them; the fetched base is unchanged.
    monkeypatch.setattr(cli.loop, "_fetch_publish_base", lambda ctx, remote, **kw: ctx.head())
    pushes = []
    monkeypatch.setattr(cli, "_push", lambda repo, url, ref: pushes.append(url))
    act(capfd, run, "publish")
    assert pushes == [origin] and run.state.pause.reason == "human_checks"


@pytest.mark.parametrize("url, repository", [
    ("https://github.com/o/r.git", "github.com/o/r"),
    ("https://token@github.example.com/O/R/", "github.example.com/O/R"),
    ("git@github.com:o/r.git", "github.com/o/r"),
    ("ssh://git@github.com:22/o/r", "github.com/o/r"),
    ("/tmp/remote.git", ""), ("file:///tmp/remote.git", ""), ("../remote.git", ""),
])
def test_remote_repository_is_parsed_from_hosted_urls_only(url, repository):
    assert cli._repository(url) == repository
