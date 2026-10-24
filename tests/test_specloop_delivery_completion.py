"""Shared background delivery boundaries retain identity and feedback history."""

import json
from pathlib import Path

import pytest

from nightshift import daemon, specloop_job as jobs
from test_engine_routing import DraftGh
from test_specloop_background import background, delivery_job


IDENTITY = "issue-intake/delivery-identity-for-cleanup/proposal-and-admission-retain-matching-identity"
HISTORY = "issue-intake/delivery-identity-for-cleanup/revision-delivery-preserves-feedback-history"
INCOMPLETE = "issue-intake/delivery-identity-for-cleanup/denied-or-incomplete-delivery-retains-artifacts"
STAGES = [pytest.param("draft", marks=pytest.mark.scenario(IDENTITY)),
          pytest.param("admit", marks=pytest.mark.scenario(IDENTITY)),
          pytest.param("spec_revision", marks=pytest.mark.scenario(HISTORY)),
          pytest.param("build_revision", marks=pytest.mark.scenario(HISTORY))]


def read(path):
    return json.loads(path.read_text())


def finish(job, human=False):
    if human:
        job.result = {"version": 1, "outcome": (
            "needs_input" if job.result["outcome"] == "drafted" else "refused"),
            "questions": ["Which voltage?"], "reasons": ["Clarify voltage"]}
        (job.root / "cli-result.json").write_text(json.dumps(job.result))
    (job.root / "release").touch()
    assert job.children[0].wait(timeout=5) == 0
    assert read(job.result_path) == {"result": job.result}
    jobs._children.clear()  # All following delivery uses restart-safe records.


@pytest.mark.scenario(INCOMPLETE)
@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("human", [False, True])
@pytest.mark.parametrize("interruption", [None, "comment", "label", "pause"])
def test_delivery_matrix(delivery_job, stage, human, interruption):
    job = delivery_job(stage)
    saved = read(job.path)
    saved["future"] = {"keep": [1, 2]}
    jobs.save(job.path, saved)
    request_path = job.directory / "request.json"
    request = read(request_path)
    mtime = request_path.stat().st_mtime_ns
    args = request["args"]
    inputs = {Path(args[i + 1]): Path(args[i + 1]).read_bytes()
              for i, arg in enumerate(args) if arg in ("--idea-file", "--feedback")}
    finish(job, human)
    retained = {job.directory / name: (job.directory / name).read_bytes()
                for name in ("result.json", "process.json", "lock", "control.lock")}
    interrupted = []
    events = list(job.gh.events)

    def boundary(args):
        if args[:3] == ["gh", "pr", "create"]:
            job.gh.prs[-1]["headRefOid"] = job.result["commit"]
        if args[0] != "gh" or interrupted:
            return
        comment = args[2] == "comment"
        label = args[1:3] == ["issue" if stage == "draft" else "pr", "edit"]
        if stage == "draft" and label and args[3] != "7":
            label = False  # The issue label write is the final delivery operation.
        if not (comment or label):
            return
        assert not (job.directory / "cleanup.json").exists()
        current = read(job.path)
        if "revision" in stage:
            expected = set(saved["consumed_feedback_ids"])
            if not human:
                expected.update(saved["feedback_ids"])
            assert set(current["consumed_feedback_ids"]) == expected
        if (interruption == "comment" and comment or interruption in ("label", "pause") and label):
            interrupted.append(True)
            if interruption == "pause":
                daemon.PAUSE_FILE.touch()
            else:
                raise RuntimeError("delivery response lost")

    job.after_call = boundary
    job.poll()
    if interruption:
        assert interrupted and not read(job.path).get("delivered")
        assert read(request_path) == request and request_path.stat().st_mtime_ns == mtime
        assert (job.directory / "log").exists() and not (job.directory / "cleanup.json").exists()
        assert all(path.read_bytes() == data for path, data in inputs.items())
        daemon.PAUSE_FILE.unlink(missing_ok=True)
        job.after_call = lambda args: None
        # Model a failed label transaction with its original trigger application.
        job.gh.labels = {"spec:draft"} if stage == "draft" else {
            "spec:approved" if stage == "admit" else "agent:revise", "spec:review"}
        job.gh.events = events
        jobs._children.clear()
        job.poll()
    receipt = read(job.path)
    assert receipt == {**saved, "result": job.result, "delivered": True, **(
        {"consumed_feedback_ids": sorted(set(saved["consumed_feedback_ids"]) |
                                        (set() if human else set(saved["feedback_ids"])))}
        if "revision" in stage else {})}
    assert read(request_path) == {**request, "args": args[:-2] if "--idea-file" in args else args}
    assert request_path.stat().st_mtime_ns == mtime
    assert read(job.directory / "cleanup.json")["complete"] is True
    assert not (job.directory / "log").exists()
    assert all(path.read_bytes() == data for path, data in retained.items())
    for path, data in inputs.items():
        if path.suffix == ".json":
            assert path.read_bytes() == data  # Build feedback is never an idea deletion target.
        else:
            assert not path.exists()
    assert len(job.children) == 1
    assert len(job.gh.comments) == (2 if stage == "draft" else 1)


@pytest.mark.scenario(IDENTITY)
@pytest.mark.parametrize("stage", ["draft", "admit"])
@pytest.mark.parametrize("identity", ["matching", "unrelated", "receiptless", "new_approval"])
def test_existing_delivery_requires_matching_evidence(delivery_job, stage, identity):
    job = delivery_job(stage)
    finish(job)
    saved = read(job.path)
    if stage == "draft":
        job.gh.prs = [{"number": 8, "state": "OPEN", "headRefOid": job.result["commit"]}]
        if identity in ("unrelated", "new_approval"):
            job.result = {"version": 1, "outcome": "needs_input", "questions": ["Unrelated draft"]}
            jobs.finalize(job.directory, {"result": job.result})
    else:
        job.runs.append({"change": saved["change"], "pr": job.gh.pr_url if identity != "unrelated"
                         else "https://github.com/owner/sample/pull/99", "status": "running"})
        if identity != "unrelated":
            job.gh.head = "b" * 40  # Committed recovery keeps the original head and marker.
        if identity == "new_approval":
            job.gh.label_event("spec:approved", "unlabeled")
            job.gh.label_event("spec:approved", "labeled")
    if identity == "receiptless":
        job.path.unlink()
    before = {p: p.read_bytes() for p in job.directory.parent.rglob("*") if p.is_file()}
    job.poll()
    if identity == "matching":
        receipt = read(job.path)
        assert receipt["job"] == saved["job"] and receipt["result"] == job.result
        assert receipt["delivered"] and read(job.directory / "cleanup.json")["complete"]
        if stage == "admit":
            assert receipt["head"] == saved["head"]
            assert f":{saved['approval_event']}:{saved['head']}:admitted -->" in job.gh.comments[-1]["body"]
    else:
        assert all(p.read_bytes() == data for p, data in before.items())
        assert not (job.directory / "cleanup.json").exists()
        if stage == "admit":
            assert "job" not in read(job.path)


@pytest.mark.scenario(IDENTITY)
def test_reopened_old_pr_does_not_deliver_replacement(delivery_job, monkeypatch):
    gh = DraftGh(("spec:draft",))
    old_pr = {"number": 8, "state": "CLOSED", "headRefOid": "b" * 40}
    gh.prs = [old_pr]
    monkeypatch.setattr("test_specloop_background.DraftGh", lambda *args: gh)
    job = delivery_job("draft")
    saved = read(job.path)
    assert saved["replaces_pr"] == {"number": 8, "head": old_pr["headRefOid"]}
    assert job.result["commit"] != old_pr["headRefOid"]
    finish(job)
    # The abandoned PR reopens before the replacement commit is ever pushed.
    old_pr["state"] = "OPEN"
    artifacts = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in job.directory.parent.rglob("*") if p.is_file()}
    job.poll()
    assert read(job.path) == {**saved, "result": job.result}
    assert not (job.directory / "cleanup.json").exists()
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == data for p, data in artifacts.items())
    assert not any(call[0] == "git" and "push" in call for call in job.calls)
    assert gh.prs == [old_pr] and gh.labels == set()
    assert gh.pr_url in gh.comments[-1]["body"]  # Existing-PR recovery still delivers.
    jobs._children.clear()
    job.poll()
    assert read(job.path) == {**saved, "result": job.result}
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == data for p, data in artifacts.items())
    assert len(job.children) == 1 and len(gh.comments) == 2


@pytest.mark.scenario(HISTORY)
@pytest.mark.parametrize("stage", ["spec_revision", "build_revision"])
def test_revision_keeps_newer_feedback_and_trigger(delivery_job, stage):
    job = delivery_job(stage)
    saved = read(job.path)
    finish(job)
    job.gh.feedback.append({"id": 999, "body": "Later feedback", "author_association": "MEMBER",
                            "user": {"login": "reviewer"}, "created_at": "2026-10-21T13:00:00Z"})
    job.gh.label_event("agent:revise", "unlabeled")
    job.gh.label_event("agent:revise", "labeled")
    job.poll()
    receipt = read(job.path)
    assert receipt["event"] == saved["event"] and receipt["feedback"] == saved["feedback"]
    assert receipt["feedback_ids"] == saved["feedback_ids"]
    assert "conversation:999" not in receipt["consumed_feedback_ids"]
    assert "agent:revise" in job.gh.labels
    assert read(job.directory / "cleanup.json")["complete"]
