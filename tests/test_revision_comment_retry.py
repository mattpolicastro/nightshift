"""Exercise comment delivery through real _gh retries and reconciliation."""

import json
import subprocess
from functools import partial

import pytest

from nightshift import queue, specloop_intake, specloop_job
from nightshift.config import Repo

pytestmark = pytest.mark.real_trust
REPO = "owner/sample"
COMMENTS = f"repos/{REPO}/issues/8/comments"
LOST = "pr-revision/revision-comment-retry-reconciliation/lost-revision-comment-response-reconciled"
READ_FAILS = "pr-revision/revision-comment-retry-reconciliation/revision-reconciliation-read-fails"


class Remote:
    def __init__(self):
        self.comments = []
        self.calls = []
        self.read_error = None
        self.lose_response = True

    @property
    def creates(self):
        return [c for c in self.calls if c[:2] in (["pr", "comment"], ["issue", "comment"])]

    @property
    def patches(self):
        return [c for c in self.calls if "PATCH" in c]

    def run(self, args, **kwargs):
        assert args[0] == "gh", f"unexpected command: {args}"
        command = args[1:]
        self.calls.append(command)
        data, error = {}, ""
        if command[:2] in (["pr", "comment"], ["issue", "comment"]):
            self.comments.append({"id": 501 + len(self.comments),
                                  "body": command[command.index("--body") + 1]})
            if self.lose_response:
                self.lose_response = False
                error = "HTTP 502: comment response lost"
        elif command[:2] == ["api", COMMENTS]:
            assert command[2:] == ["--paginate", "--slurp"]
            # An unrelated first page forces reconciliation to search later pages.
            data = [[{"id": 50, "body": "unrelated feedback"}], self.comments]
            if self.comments and self.read_error:
                error = self.read_error
        elif command[0] == "api" and "PATCH" in command:
            assert command[2:5] == ["--method", "PATCH", "-f"]
            previous = next(c for c in self.comments
                            if command[1] == f"repos/{REPO}/issues/comments/{c['id']}")
            previous["body"] = command[5].removeprefix("body=")
            data = previous
        elif command[:2] == ["pr", "list"]:
            data = [{"number": 8}]
        elif command[:2] == ["pr", "view"]:
            data = {"state": "OPEN", "labels": [{"name": "agent:revise"}],
                    "headRefName": "spec/gate", "headRefOid": "old-head",
                    "isCrossRepository": False}
        elif command[:2] == ["api", f"repos/{REPO}/issues/8/events"]:
            data = [[{"id": 42, "event": "labeled", "label": {"name": "agent:revise"},
                      "actor": {"login": "owner"}}]]
        elif command == ["api", f"repos/{REPO}"]:
            data = {"owner": {"login": "owner", "type": "User"}}
        else:
            assert command == ["pr", "edit", "8", "--repo", REPO,
                               "--remove-label", "agent:revise"]
        return subprocess.CompletedProcess(args, int(bool(error)), json.dumps(data), error)


@pytest.fixture
def remote(monkeypatch):
    remote = Remote()
    monkeypatch.setattr(queue.subprocess, "run", remote.run)
    return remote


def deliver(*, action="revise", kind="pr", round_id=None):
    specloop_intake._comment(REPO, 8, "gate", "Revision complete", action=action,
                            kind=kind, round_id=round_id,
                            runner=partial(queue._gh, sleep=lambda _: None))


@pytest.mark.scenario(LOST)
@pytest.mark.parametrize("round_id", [None, "42"])
@pytest.mark.parametrize("action,kind", [("revise", "pr"), ("draft", "issue"),
                                         ("admit", "pr"), ("publish", "pr")])
def test_lost_response_reconciles_later_page(remote, action, kind, round_id):
    deliver(action=action, kind=kind, round_id=round_id)
    assert len(remote.creates) == len(remote.comments) == len(remote.patches) == 1
    suffix = f":{round_id}" if round_id is not None else ""
    body = remote.creates[0][-1]
    assert body.endswith(f"<!-- nightshift:{action}:gate{suffix} -->")
    assert remote.patches[0] == ["api", f"repos/{REPO}/issues/comments/501",
                                 "--method", "PATCH", "-f", f"body={body}"]
    assert [c[:2] for c in remote.calls] == [
        ["api", COMMENTS], [kind, "comment"], ["api", COMMENTS], remote.patches[0][:2]]


@pytest.mark.scenario(LOST)
@pytest.mark.parametrize("previous_round", [None, "4"])
def test_distinct_round_is_created_then_reconciled(remote, previous_round):
    remote.lose_response = False
    deliver(round_id=previous_round)
    original = remote.comments[0].copy()
    remote.lose_response = True
    deliver(round_id="42")
    assert len(remote.creates) == len(remote.comments) == 2
    assert remote.comments[0] == original
    assert remote.comments[1]["body"].endswith("<!-- nightshift:revise:gate:42 -->")
    assert len(remote.patches) == 1
    assert remote.patches[0][1] == f"repos/{REPO}/issues/comments/502"


@pytest.mark.scenario(READ_FAILS)
@pytest.mark.parametrize("round_id", [None, "42"])
@pytest.mark.parametrize("error,reads", [("HTTP 503: history unavailable", 3),
                                       ("HTTP 403: history forbidden", 1)])
def test_read_failure_preserves_saved_revision_for_recovery(remote, tmp_path, round_id, error, reads):
    path = tmp_path / "revisions" / "owner__sample" / "8.json"
    receipt = {"change": "gate", "head": "old-head", "event": 42, "round_id": round_id,
               "command": "revise", "feedback_state_version": 1,
               "feedback": ["Handle empty input"], "feedback_ids": ["conversation:7"],
               "consumed_feedback_ids": ["conversation:7"],
               "result": {"version": 1, "outcome": "revised", "commit": "landed-commit"}}
    specloop_job.save(path, receipt)
    saved = path.read_bytes()
    remote.read_error = error
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(f'[run]\nrepo = {json.dumps(str(tmp_path))}\n')
    repo = Repo(REPO, engine="specloop", manifest=manifest)
    poll = partial(specloop_intake.revise_pending, repo,
                   queue.Labels(), tmp_path, runner=partial(queue._gh, sleep=lambda _: None))

    with pytest.raises(queue.GhError, match=error) as caught:
        poll()
    assert caught.value.transient is (reads == 3)
    assert len(remote.creates) == len(remote.comments) == 1
    assert not remote.patches
    assert sum(c[:2] == ["api", COMMENTS] for c in remote.calls) == 1 + reads
    assert path.read_bytes() == saved
    assert not any(c[:2] == ["pr", "edit"] for c in remote.calls)

    remote.read_error = None
    assert poll() == [receipt["result"]]
    assert json.loads(path.read_text()) == {**receipt, "delivered": True}
    assert len(remote.creates) == len(remote.comments) == len(remote.patches) == 1
    assert remote.patches[0][1] == f"repos/{REPO}/issues/comments/501"
    assert remote.patches[0][-1] == f"body={remote.creates[0][-1]}"
