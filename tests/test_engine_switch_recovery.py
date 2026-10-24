"""Worker retirement uses durable evidence even with unhealthy enrollment."""

import json

import pytest

from manifest_fixtures import manifest_text
from nightshift import daemon, outcomes, queue
from nightshift.config import Config, Repo
from test_specloop_isolation import Pipeline, poll


@pytest.fixture
def switched(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")
    manifest = tmp_path / "template.toml"
    manifest.write_text(manifest_text(tmp_path / "source"))
    pipeline = Pipeline(Repo("owner/switched", engine="specloop", manifest=manifest))
    monkeypatch.setattr(queue, "_gh", pipeline.gh)
    monkeypatch.setattr(daemon.specloop_intake, "run_git", pipeline.command)
    return pipeline


def working(pipeline, number, tmp_path, *, survives=False):
    pipeline.issue(number)
    pipeline.issues[number]["labels"] = [{"name": "agent:working"}, {"name": "keep-me"}]
    worktree = tmp_path / f"work-{number}"
    if survives:
        worktree.mkdir()
        (worktree / "diff.txt").write_text("unfinished work")
    claim = queue.Claim(pipeline.repo.name, number, f"claude/{number}", str(worktree),
                        "yesterday", phase="reviewing")
    claim.write()
    return claim


def labels(pipeline, number):
    return {label["name"] for label in pipeline.issues[number]["labels"]}


@pytest.mark.scenario("config-resilience/worker-switch-reconciliation/switch-recovers-working-issues")
def test_engine_switch_recovers_working_issues(switched, tmp_path, monkeypatch):
    orphan = working(switched, 1, tmp_path)
    orphan.clear()
    missing = working(switched, 2, tmp_path)
    surviving = working(switched, 3, tmp_path, survives=True)
    worker = Repo("owner/worker", "true")
    cfg = Config(repos=[switched.repo, worker])
    recovered = []

    def reconcile(name, labels):
        assert name == worker.name  # Destructive reconciliation never sees switched claims.
        return [queue.Reconciliation(queue.Repair.RESUMABLE, 9, "worker work")]

    monkeypatch.setattr(queue, "reconcile", reconcile)
    monkeypatch.setattr(daemon, "_recover", lambda cfg, repo, path, number:
                        recovered.append((repo.name, number)))
    monkeypatch.setattr(queue, "claim", lambda *a: pytest.fail("worker claim for specloop"))
    original = switched.gh

    def journal_before_write(args):
        if args[:2] in (["issue", "edit"], ["issue", "comment"]):
            journal = daemon.STATE_DIR / "engine-switch/owner__switched" / f"{args[2]}.json"
            saved = json.loads(journal.read_text())
            assert saved["version"] == 1 and saved["marker"] and not saved["done"]
        return original(args)

    monkeypatch.setattr(queue, "_gh", journal_before_write)
    health = daemon.RepoHealth()
    daemon.startup(cfg, {worker.name: tmp_path}, health)
    assert not health.failures and recovered == [(worker.name, 9)]
    assert daemon.claim_next(Config(repos=[switched.repo]), {}) is None
    assert labels(switched, 1) == labels(switched, 2) == {"agent:ready", "keep-me"}
    assert labels(switched, 3) == {"needs-human", "keep-me"}
    comment, = switched.issues[3]["comments"]
    assert all(value in comment["body"] for value in
               (surviving.phase, surviving.branch, surviving.worktree, "inspection"))
    assert (tmp_path / "work-3/diff.txt").read_text() == "unfinished work"
    assert not missing.path.exists() and not surviving.path.exists()
    assert all(t["state"] == "needs_decision" and "interrupted worker recovery" in t["reason"]
               for t in outcomes.tasks())
    assert len(outcomes.tasks()) == 3
    daemon.startup(Config(repos=[switched.repo]), {}, health)
    assert len(switched.issues[3]["comments"]) == 1 and len(outcomes.tasks()) == 3


@pytest.mark.scenario("config-resilience/worker-switch-reconciliation/switch-recovers-working-issues")
@pytest.mark.parametrize("first_survives", [False, True])
@pytest.mark.parametrize("next_work", ["missing", "surviving", "orphan"])
def test_later_worker_attempt_gets_separate_recovery(switched, tmp_path, first_survives, next_work):
    working(switched, 1, tmp_path, survives=first_survives)
    cfg = Config(repos=[switched.repo])
    health = daemon.RepoHealth()
    daemon.startup(cfg, {}, health)
    path = daemon.STATE_DIR / "engine-switch/owner__switched/1.json"
    previous = json.loads(path.read_text())
    previous["future_field"] = "retained evidence"
    path.write_text(json.dumps(previous))

    # Switch back to worker and rearm through the ordinary claim interface.
    switched.issues[1]["labels"] = [{"name": "agent:ready"}, {"name": "keep-me"}]
    _, claim = queue.claim(switched.repo.name, tmp_path, runner=switched.gh)
    if next_work == "surviving":
        worktree = tmp_path / "switched-1"
        worktree.mkdir()
        (worktree / "diff.txt").write_text("new unfinished work")
    elif next_work == "orphan":
        claim.clear()
    daemon.startup(cfg, {}, health)  # Switch to specloop again.

    assert not health.failures and not claim.path.exists()
    target = "needs-human" if next_work == "surviving" else "agent:ready"
    assert labels(switched, 1) == {target, "keep-me"}
    current = json.loads(path.read_text())
    assert current["done"] and current["id"] != previous["id"]
    if next_work == "orphan":
        assert current["claim"] is None
    else:
        assert current["claim"]["started_at"] == claim.started_at
    assert json.loads((path.parent / "completed" / f"1-{previous['id']}.json").read_text()) == previous
    outcome, = outcomes.tasks()
    assert outcome["engine_switch_id"] == current["id"] and outcome["state"] == "needs_decision"
    comments = switched.issues[1]["comments"]
    assert len(comments) == int(first_survives) + int(next_work == "surviving")
    if next_work == "surviving":
        assert current["marker"] in comments[-1]["body"]
        assert (worktree / "diff.txt").read_text() == "new unfinished work"
    if first_survives:
        assert (tmp_path / "work-1/diff.txt").read_text() == "unfinished work"
    daemon.startup(cfg, {}, health)
    assert outcomes.tasks() == [outcome] and json.loads(path.read_text()) == current


@pytest.mark.scenario("config-resilience/worker-switch-reconciliation/unknown-recovery-state-is-retained")
@pytest.mark.parametrize("state", ["stale-search", "same-claim", "new-claim-no-working", "read-failure"])
def test_completed_recovery_checks_current_evidence(switched, tmp_path, state):
    claim = working(switched, 1, tmp_path)
    daemon._reconcile_engine_switch(switched.repo, queue.Labels())
    path = daemon.STATE_DIR / "engine-switch/owner__switched/1.json"
    before = path.read_bytes(), outcomes.tasks(), list(switched.calls)
    if state != "stale-search":
        if state != "same-claim":
            claim.started_at = "later"
        claim.write()
        if state != "new-claim-no-working":
            switched.issues[1]["labels"] = [{"name": "agent:working"}]

    def gh(args):
        if args[:2] == ["issue", "list"]:
            return json.dumps([switched.issues[1]])  # Includes stale search hits.
        if state == "read-failure" and args[:2] == ["issue", "view"]:
            raise queue.GhError("labels unavailable", transient=True)
        return switched.gh(args)

    if state == "stale-search":
        daemon._reconcile_engine_switch(switched.repo, queue.Labels(), runner=gh)
        assert not claim.path.exists()
    else:
        with pytest.raises((ValueError, queue.GhError)):
            daemon._reconcile_engine_switch(switched.repo, queue.Labels(), runner=gh)
        assert queue.in_flight(switched.repo.name) == ([claim], [])
    assert path.read_bytes() == before[0] and outcomes.tasks() == before[1]
    assert not any(c[:2] in (["issue", "edit"], ["issue", "comment"])
                   for c in switched.calls[len(before[2]):])


@pytest.mark.scenario("config-resilience/worker-switch-reconciliation/recovery-does-not-need-a-manifest")
@pytest.mark.parametrize("manifest", ["absent", "missing", "invalid"])
def test_recovery_without_healthy_manifest(switched, tmp_path, manifest):
    claim = working(switched, 1, tmp_path)
    switched.issue(7)
    switched.approved(8)
    if manifest == "invalid":
        switched.repo.manifest.write_text("[broken")
    else:
        switched.repo.manifest.unlink()
        if manifest == "absent":
            switched.repo = Repo(switched.repo.name, engine="specloop")
    health = daemon.RepoHealth()
    poll([switched], health)
    assert labels(switched, 1) == {"agent:ready", "keep-me"} and not claim.path.exists()
    assert health.failures == {switched.repo.name: 1}
    assert not any(c[:2] in (["specloop", "propose"], ["specloop", "admit"])
                   for c in switched.calls)
    assert labels(switched, 7) == {"spec:draft"}
    assert switched.prs[8]["labels"] == [{"name": "spec:approved"}]


@pytest.mark.scenario("config-resilience/worker-switch-reconciliation/unknown-recovery-state-is-retained")
@pytest.mark.parametrize("failure", ["claim", "list", "labels", "comments"])
def test_unknown_recovery_retains_evidence_and_isolates_intake(switched, tmp_path, failure):
    claim = working(switched, 1, tmp_path, survives=True)
    if failure == "claim":
        claim.path.write_text("{unreadable")
    before = claim.path.read_bytes()
    switched.issue(7)
    switched.ready(9, daemon.STATE_DIR)
    switched.approved(8)
    independent = Pipeline(Repo("owner/other", engine="specloop", manifest=switched.repo.manifest))
    independent.issue(7)
    original = switched.gh

    def unavailable(args):
        if ((failure == "list" and args[:2] == ["issue", "list"])
                or (failure == "labels" and args[:3] == ["issue", "view", "1"])
                or (failure == "comments" and args[:2] == [
                    "api", "repos/owner/switched/issues/1/comments"])):
            raise queue.GhError("required read failed", transient=True)
        return original(args)

    switched.gh = unavailable
    health = daemon.RepoHealth()
    poll([switched, independent], health)
    assert health.failures == {switched.repo.name: 1}
    assert claim.path.read_bytes() == before
    assert (tmp_path / "work-1/diff.txt").read_text() == "unfinished work"
    assert labels(switched, 1) == {"agent:working", "keep-me"}
    assert labels(switched, 7) == {"spec:draft"}
    assert switched.prs[8]["labels"] == [{"name": "spec:approved"}]
    assert any(c[:2] == ["specloop", "publish"] for c in switched.calls)
    assert any(c[:2] == ["specloop", "propose"] for c in independent.calls)


@pytest.mark.scenario("config-resilience/durable-switch-recovery/partial-recovery-retries-safely")
def test_outcome_failure_keeps_claim_and_journal(switched, tmp_path, monkeypatch):
    claim = working(switched, 1, tmp_path, survives=True)
    with monkeypatch.context() as patch:
        patch.setattr(outcomes, "record", lambda *a, **k: None)
        with pytest.raises(OSError, match="outcome not persisted"):
            daemon._reconcile_engine_switch(switched.repo, queue.Labels())
    assert claim.path.is_file() and labels(switched, 1) == {"needs-human", "keep-me"}
    path = daemon.STATE_DIR / "engine-switch/owner__switched/1.json"
    journal = json.loads(path.read_text())
    assert journal["claim"]["worktree"] == claim.worktree and not journal["done"]
    journal["future_field"] = "preserve"
    path.write_text(json.dumps(journal))
    writes = [c for c in switched.calls if c[:2] in (["issue", "edit"], ["issue", "comment"])]
    daemon._reconcile_engine_switch(switched.repo, queue.Labels())
    assert not claim.path.exists() and len(outcomes.tasks()) == 1
    assert json.loads(path.read_text())["future_field"] == "preserve"
    assert [c for c in switched.calls if c[:2] in (["issue", "edit"], ["issue", "comment"])] == writes


@pytest.mark.scenario("config-resilience/worker-switch-reconciliation/unknown-recovery-state-is-retained")
@pytest.mark.parametrize("old_claim", [False, True])
@pytest.mark.parametrize("current_working", [False, True])
def test_unfinished_recovery_cannot_retire_a_new_claim(
    switched, tmp_path, monkeypatch, old_claim, current_working,
):
    previous = working(switched, 1, tmp_path)
    if not old_claim:
        previous.clear()
    with monkeypatch.context() as patch:
        patch.setattr(outcomes, "record", lambda *a, **k: None)
        with pytest.raises(OSError, match="outcome not persisted"):
            daemon._reconcile_engine_switch(switched.repo, queue.Labels())
    path = daemon.STATE_DIR / "engine-switch/owner__switched/1.json"
    journal = json.loads(path.read_text())
    journal["future_field"] = "retain this unfinished attempt"
    path.write_text(json.dumps(journal))

    # Worker startup clears the released claim; a later worker takes the issue.
    queue.reconcile(switched.repo.name, runner=switched.gh)
    _, claim = queue.claim(switched.repo.name, tmp_path, runner=switched.gh)
    worktree = tmp_path / "switched-1"
    worktree.mkdir()
    (worktree / "diff.txt").write_text("new unfinished work")
    if not current_working:
        switched.issues[1]["labels"] = [{"name": "agent:ready"}, {"name": "keep-me"}]
    before = path.read_bytes(), claim.path.read_bytes(), outcomes.tasks(), labels(switched, 1)
    switched.calls.clear()
    switched.issue(7)
    switched.ready(9, daemon.STATE_DIR)
    independent = Pipeline(Repo("owner/other", engine="specloop", manifest=switched.repo.manifest))
    independent.issue(7)
    health = daemon.RepoHealth()

    daemon.startup(Config(repos=[switched.repo]), {}, health)
    poll([switched, independent], health)

    assert health.failures == {switched.repo.name: 2}
    assert (path.read_bytes(), claim.path.read_bytes(), outcomes.tasks(), labels(switched, 1)) == before
    assert (worktree / "diff.txt").read_text() == "new unfinished work"
    assert not any(c[:2] in (["issue", "edit"], ["issue", "comment"]) and c[2] == "1"
                   for c in switched.calls)
    assert labels(switched, 7) == {"spec:draft"}
    assert not any(c[:2] == ["specloop", "propose"] for c in switched.calls)
    assert any(c[:2] == ["specloop", "publish"] for c in switched.calls)
    assert any(c[:2] == ["specloop", "propose"] for c in independent.calls)


def recovery_path():
    return daemon.STATE_DIR / "engine-switch/owner__switched/1.json"


def recovery_writes(pipeline):
    return [c for c in pipeline.calls
            if c[:2] in (["issue", "edit"], ["issue", "comment"]) and c[2] == "1"]


@pytest.mark.scenario("config-resilience/durable-switch-recovery/lost-transition-response-recovers")
@pytest.mark.scenario("config-resilience/durable-switch-recovery/partial-recovery-retries-safely")
@pytest.mark.parametrize("operation,applied,survives", [
    ("comment", False, True), ("comment", True, True), ("edit", False, True),
    ("edit", True, True), ("edit", False, False), ("edit", True, False),
])
def test_remote_write_failure_recovers_on_restart(
    switched, tmp_path, monkeypatch, survives, operation, applied,
):
    claim = working(switched, 1, tmp_path, survives=survives)
    # Keep the released issue available for assertions across later polls.
    monkeypatch.setattr(daemon.specloop_intake, "propose_pending", lambda *a, **k: None)
    original = switched.gh

    def failing(args):
        if args[:2] == ["issue", operation]:
            if applied:
                original(args)
            raise queue.GhError("write response lost", transient=True)
        return original(args)

    health = daemon.RepoHealth()
    switched.gh = failing
    poll([switched], health)
    assert health.failures == {switched.repo.name: 1}
    assert claim.path.exists() and not outcomes.tasks()
    saved = json.loads(recovery_path().read_text())
    assert not saved["done"] and saved["claim"]["worktree"] == claim.worktree
    if operation == "comment":
        assert labels(switched, 1) == {"agent:working", "keep-me"}
        assert not any(c[:2] == ["issue", "edit"] for c in recovery_writes(switched))
    elif applied:
        assert "agent:working" not in labels(switched, 1)

    switched.gh = original
    monkeypatch.setattr(queue, "reconcile", lambda *a, **k: pytest.fail("discarded switch evidence"))
    daemon.startup(Config(repos=[switched.repo]), {}, daemon.RepoHealth())
    poll([switched], health)
    assert not health.failures and not claim.path.exists()
    assert json.loads(recovery_path().read_text())["done"]
    outcome, = outcomes.tasks()
    assert outcome["engine_switch_id"] == saved["id"] and outcome["state"] == "needs_decision"
    writes = recovery_writes(switched)
    assert sum(c[:2] == ["issue", "edit"] for c in writes) == 1
    assert len(switched.issues[1]["comments"]) == int(saved["action"] == "hold")
    assert sum(c[:2] == ["issue", "comment"] for c in writes) == int(saved["action"] == "hold")


@pytest.mark.scenario("config-resilience/durable-switch-recovery/partial-recovery-retries-safely")
@pytest.mark.parametrize("stage", ["intent", "commented", "transitioned", "done"])
def test_journal_write_failure_resumes_from_durable_evidence(switched, tmp_path, monkeypatch, stage):
    claim = working(switched, 1, tmp_path, survives=True)
    original = daemon.specloop_job.save

    def failing(path, data):
        progress = next((key for key in ("done", "transitioned", "commented") if data.get(key)), "intent")
        if progress == stage:
            raise OSError("journal write failed")
        original(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(daemon.specloop_job, "save", failing)
        with pytest.raises(OSError, match="journal write failed"):
            daemon._reconcile_engine_switch(switched.repo, queue.Labels())
    assert claim.path.exists() == (stage != "done")
    assert bool(outcomes.tasks()) == (stage == "done")
    if stage == "intent":
        assert not recovery_path().exists() and not recovery_writes(switched)
    else:
        saved = json.loads(recovery_path().read_text())
        assert not saved["done"] and saved["claim"]["branch"] == claim.branch
        saved["future_field"] = "keep"
        original(recovery_path(), saved)
    if stage == "commented":
        assert labels(switched, 1) == {"agent:working", "keep-me"}
    before = outcomes.tasks()
    daemon.startup(Config(repos=[switched.repo]), {}, daemon.RepoHealth())
    assert not claim.path.exists() and json.loads(recovery_path().read_text())["done"]
    assert len(outcomes.tasks()) == 1
    if before:
        assert outcomes.tasks() == before  # No second outcome event after cleanup/save failure.
    if stage != "intent":
        assert json.loads(recovery_path().read_text())["future_field"] == "keep"
    assert [c[1] for c in recovery_writes(switched)] == ["comment", "edit"]
    assert (tmp_path / "work-1/diff.txt").read_text() == "unfinished work"


@pytest.mark.scenario("config-resilience/durable-switch-recovery/partial-recovery-retries-safely")
@pytest.mark.parametrize("conflict", ["agent:ready", "agent:revise", "agent:done", "removed", "rearmed"])
def test_human_label_change_is_preserved(switched, tmp_path, monkeypatch, caplog, conflict):
    claim = working(switched, 1, tmp_path, survives=True)
    original = switched.gh

    def human_change(args):
        result = original(args)
        if args[:2] == ["issue", "comment"]:
            switched.issues[1]["labels"] = [{"name": "keep-me"}]
            if conflict != "removed":
                switched.issues[1]["labels"].append({"name": conflict})
        return result

    if conflict == "rearmed":
        # The label transition was recorded, but the local outcome failed.
        with monkeypatch.context() as patch:
            patch.setattr(outcomes, "record", lambda *a, **k: None)
            with pytest.raises(OSError, match="outcome not persisted"):
                daemon._reconcile_engine_switch(switched.repo, queue.Labels())
        switched.issues[1]["labels"] = [{"name": "agent:working"}, {"name": "keep-me"}]
    else:
        switched.gh = human_change
    health = daemon.RepoHealth()
    writes = list(recovery_writes(switched))
    poll([switched], health)
    assert health.failures == {switched.repo.name: 1} and "conflicting labels" in caplog.text
    expected = "agent:working" if conflict == "rearmed" else conflict
    assert labels(switched, 1) == ({"keep-me"} if conflict == "removed" else {"keep-me", expected})
    assert claim.path.exists() and not outcomes.tasks()
    assert not json.loads(recovery_path().read_text())["done"]
    assert not any(c[:2] == ["issue", "edit"] for c in recovery_writes(switched)[len(writes):])


@pytest.mark.scenario("config-resilience/durable-switch-recovery/partial-recovery-retries-safely")
def test_pending_recovery_ignores_stale_lists(switched, tmp_path, monkeypatch):
    claim = working(switched, 1, tmp_path, survives=True)
    with monkeypatch.context() as patch:
        patch.setattr(outcomes, "record", lambda *a, **k: None)
        with pytest.raises(OSError, match="outcome not persisted"):
            daemon._reconcile_engine_switch(switched.repo, queue.Labels())
    original = switched.gh
    writes = recovery_writes(switched)

    def stale(args):
        if args[:2] == ["issue", "list"]:
            return json.dumps([{**switched.issues[1], "labels": [{"name": "agent:working"}]}])
        if args[:2] == ["api", "repos/owner/switched/issues/1/comments"]:
            return "[[]]"  # Already acknowledged comment omitted by a stale list.
        return original(args)

    daemon._reconcile_engine_switch(switched.repo, queue.Labels(), runner=stale)
    assert not claim.path.exists() and json.loads(recovery_path().read_text())["done"]
    assert recovery_writes(switched) == writes and len(outcomes.tasks()) == 1


@pytest.mark.scenario("config-resilience/durable-switch-recovery/partial-recovery-retries-safely")
def test_human_hold_prevents_release_after_intent_save(switched, tmp_path, monkeypatch, caplog):
    claim = working(switched, 1, tmp_path)
    original = daemon.specloop_job.save

    def human_hold(path, data):
        original(path, data)
        switched.issues[1]["labels"] = [{"name": "needs-human"}, {"name": "keep-me"}]

    health = daemon.RepoHealth()
    with monkeypatch.context() as patch:
        patch.setattr(daemon.specloop_job, "save", human_hold)
        poll([switched], health)
    poll([switched], health)
    assert health.failures == {switched.repo.name: 2} and "conflicting labels" in caplog.text
    assert labels(switched, 1) == {"needs-human", "keep-me"}
    assert claim.path.exists() and not outcomes.tasks() and not recovery_writes(switched)
    saved = json.loads(recovery_path().read_text())
    assert saved["action"] == "release" and not saved["done"]


@pytest.mark.scenario("config-resilience/durable-switch-recovery/partial-recovery-retries-safely")
@pytest.mark.parametrize("boundary", ["intent", "comments-read", "comment", "edit"])
def test_pause_between_recovery_operations_retains_intent(switched, tmp_path, monkeypatch, boundary):
    claim = working(switched, 1, tmp_path, survives=True)
    original_gh, original_save = switched.gh, daemon.specloop_job.save

    def pause_after_read_or_write(args):
        result = original_gh(args)
        if (args[:2] == ["issue", boundary] or
                (boundary == "comments-read" and args[:2] == [
                    "api", "repos/owner/switched/issues/1/comments"])):
            daemon.PAUSE_FILE.touch()
        return result

    def pause_after_intent(path, data):
        original_save(path, data)
        if boundary == "intent":
            daemon.PAUSE_FILE.touch()

    with monkeypatch.context() as patch:
        patch.setattr(daemon.specloop_job, "save", pause_after_intent)
        switched.gh = pause_after_read_or_write
        poll([switched], daemon.RepoHealth())
    saved = json.loads(recovery_path().read_text())
    assert not saved["done"] and claim.path.exists() and not outcomes.tasks()
    writes = recovery_writes(switched)
    assert [c[1] for c in writes] == {
        "intent": [], "comments-read": [], "comment": ["comment"], "edit": ["comment", "edit"],
    }[boundary]
    switched.calls.clear()
    poll([switched], daemon.RepoHealth())
    assert not switched.calls  # A fully paused poll does no external recovery work.
    daemon.PAUSE_FILE.unlink()
    switched.gh = original_gh
    poll([switched], daemon.RepoHealth())
    assert not claim.path.exists() and json.loads(recovery_path().read_text())["done"]
    assert [c[1] for c in writes + recovery_writes(switched)] == ["comment", "edit"]
    assert len(switched.issues[1]["comments"]) == len(outcomes.tasks()) == 1
