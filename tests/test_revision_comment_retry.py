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
ERROR_RETRY = "issue-intake/failure-marker-reconciliation/error-comment-retry-does-not-duplicate"
ERROR_READ = "issue-intake/failure-marker-reconciliation/reconciliation-failure-prevents-blind-retry"


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


@pytest.mark.scenario("forge-interface/canonical-daemon-markers/inline-references-do-not-match")
@pytest.mark.parametrize("operation", ["rejection", "delivery"])
@pytest.mark.parametrize("present", [False, True])
def test_github_inline_a_final_b_selects_only_a(remote, operation, present):
    remote.lose_response = False
    marker = ("<!-- nightshift:draft:label-authorization-8:42 -->" if operation == "rejection"
              else "<!-- nightshift:revise:gate -->")
    original = {"id": 81, "body": f"inline {marker} suffix\r\n<!-- nightshift:revise:other -->"}
    remote.comments.append(original.copy())
    if present:
        remote.comments.append({"id": 82, "body": "old\r\n" + marker})
    runner = partial(queue._gh, sleep=lambda _: None)
    if operation == "rejection":
        specloop_intake._comment_label_rejection(REPO, 8, "agent:revise",
            {"id": 42, "actor": {"login": "outsider"}}, runner=runner)
    else:
        deliver()
    assert remote.comments[0] == original and len(remote.comments) == 2
    assert len(remote.creates) == int(not present)
    assert len(remote.patches) == int(present and operation == "delivery")
    if remote.patches:
        assert remote.patches[0][1] == f"repos/{REPO}/issues/comments/82"
    if operation == "delivery":
        assert remote.comments[1]["body"] == f"{queue.SELF_COMMENT_PREFIX} Revision complete\n\n{marker}"
    assert remote.comments[1]["body"].endswith(marker)


@pytest.mark.scenario("forge-interface/canonical-daemon-markers/lost-response-uses-canonical-selection")
@pytest.mark.parametrize("failure", [None, "HTTP 403", "HTTP 503", "pause"])
def test_github_lost_post_response_patches_canonical_a_never_b(remote, monkeypatch, failure):
    marker = "<!-- nightshift:revise:gate -->"
    original = {"id": 81, "body": f"inline {marker} suffix\r\n<!-- nightshift:revise:other -->"}
    remote.comments.append(original.copy())
    raw = " Résumé\r\n '$HOME' `literal` \n "
    body = f"{queue.SELF_COMMENT_PREFIX} {raw}\n\n{marker}"
    waits = []

    def transport(args, **kwargs):
        result = remote.run(args, **kwargs)
        if args[1:3] == ["pr", "comment"] and failure != "pause":
            remote.read_error = failure
        return result

    def guard():
        if failure == "pause" and waits:
            raise specloop_intake.Paused()

    monkeypatch.setattr(queue.subprocess, "run", transport)
    with queue.gh_guard(guard):
        if failure:
            with pytest.raises(specloop_intake.Paused if failure == "pause" else queue.GhError):
                specloop_intake._comment(REPO, 8, "gate", raw, action="revise", kind="pr",
                                        runner=partial(queue._gh, sleep=waits.append))
        else:
            specloop_intake._comment(REPO, 8, "gate", raw, action="revise", kind="pr",
                                    runner=partial(queue._gh, sleep=waits.append))
    assert len(remote.creates) == 1 and len(remote.comments) == 2
    assert remote.comments == [original, {"id": 502, "body": body}]
    assert remote.patches == ([] if failure else [["api", f"repos/{REPO}/issues/comments/502",
        "--method", "PATCH", "-f", f"body={body}"]])
    assert waits == ([2, 2, 8] if failure == "HTTP 503" else [2])
    reads = sum(c[:2] == ["api", COMMENTS] for c in remote.calls)
    assert reads == (1 if failure == "pause" else 4 if failure == "HTTP 503" else 2)


@pytest.mark.scenario(LOST)
@pytest.mark.scenario(ERROR_RETRY)
@pytest.mark.parametrize("round_id", [None, "42"])
@pytest.mark.parametrize("action,kind", [("revise", "pr"), ("draft", "issue"),
                                         ("admit", "pr"), ("publish", "pr"),
                                         ("draft-error", "issue"), ("admit-error", "pr"),
                                         ("revise-error", "pr"), ("publish-error", "pr"),
                                         ("approval", "pr")])
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
@pytest.mark.scenario(ERROR_RETRY)
@pytest.mark.parametrize("action", ["draft", "admit", "publish", "revise", "draft-error",
                                   "admit-error", "publish-error", "revise-error", "approval"])
@pytest.mark.parametrize("previous_round", [None, "4"])
def test_distinct_round_is_created_then_reconciled(remote, previous_round, action):
    remote.lose_response = False
    deliver(action=action, round_id=previous_round)
    original = remote.comments[0].copy()
    remote.lose_response = True
    deliver(action=action, round_id="42")
    assert len(remote.creates) == len(remote.comments) == 2
    assert remote.comments[0] == original
    assert remote.comments[1]["body"].endswith(f"<!-- nightshift:{action}:gate:42 -->")
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


@pytest.mark.scenario(ERROR_RETRY)
@pytest.mark.scenario(ERROR_READ)
@pytest.mark.parametrize("stage", ["draft", "admit", "revise", "publish"])
@pytest.mark.parametrize("interruption", [None, "HTTP 403", "HTTP 503", "pause", "label"])
def test_error_comment_retry_does_not_duplicate(remote, monkeypatch, stage, interruption):
    kind = "issue" if stage == "draft" else "pr"
    labels, waits = [], []
    paused = False
    label_error = interruption == "label"

    def transport(args, **kwargs):
        if args[1:3] == [kind, "edit"]:
            remote.calls.append(args[1:])
            assert remote.comments and remote.patches  # Comment delivery precedes labeling.
            if label_error:
                return subprocess.CompletedProcess(args, 1, "", "HTTP 403: label forbidden")
            labels.append(args[-1])
            return subprocess.CompletedProcess(args, 0, "", "")
        return remote.run(args, **kwargs)

    def sleep(seconds):
        nonlocal paused
        waits.append(seconds)
        paused = interruption == "pause"

    def guard():
        if paused:
            raise specloop_intake.Paused()

    monkeypatch.setattr(queue.subprocess, "run", transport)
    remote.read_error = interruption if interruption in ("HTTP 403", "HTTP 503") else None
    report = partial(specloop_intake.report_failure, Repo(REPO), queue.Labels(), 8,
                     stage, kind, RuntimeError("overdue attempt"), runner=partial(queue._gh, sleep=sleep))
    with queue.gh_guard(guard):
        if interruption == "pause":
            with pytest.raises(specloop_intake.Paused):
                report()
            assert remote.calls[-1] == remote.creates[0]
        else:
            errors = report()
            assert bool(errors) == bool(interruption)
    assert len(remote.creates) == len(remote.comments) == 1
    assert labels == ([] if interruption else ["needs-human"])
    assert waits == ([2, 2, 8] if interruption == "HTTP 503" else [2])
    if interruption:
        if interruption != "label":
            assert not remote.patches and not any(c[1] == "edit" for c in remote.calls)
        remote.read_error, paused, label_error = None, False, False
        with queue.gh_guard(guard):
            assert report() == []
        assert labels == ["needs-human"]
    assert len(remote.creates) == len(remote.comments) == 1
    assert remote.patches[-1][1] == f"repos/{REPO}/issues/comments/501"
    assert remote.comments[0]["body"].endswith(f"<!-- nightshift:{stage}-error:8 -->")


@pytest.mark.scenario(ERROR_READ)
@pytest.mark.parametrize("body", ["worker comment", "<!-- nightshift:unknown:gate -->",
                                "<!-- nightshift:draft:gate --> trailing text"])
def test_unmarked_retry_policy_unchanged(remote, body):
    waits = []
    queue._gh(["issue", "comment", "8", "--repo", REPO, "--body", body], sleep=waits.append)
    assert len(remote.creates) == 2 and not remote.patches
    assert remote.calls == remote.creates and waits == [2]


@pytest.mark.scenario(ERROR_RETRY)
@pytest.mark.parametrize("action", ["draft-error", "admit-error", "revise-error", "publish-error", "approval"])
def test_different_actions_keep_distinct_comments(remote, action):
    remote.lose_response = False
    deliver(action=action.removesuffix("-error") if action != "approval" else "admit")
    original = remote.comments[0].copy()
    remote.lose_response = True
    deliver(action=action)
    assert len(remote.creates) == len(remote.comments) == 2
    assert remote.comments[0] == original
    assert remote.patches[0][1] == f"repos/{REPO}/issues/comments/502"
