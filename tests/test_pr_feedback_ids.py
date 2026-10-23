"""Identified REST feedback, strict reads, and the unchanged worker boundary."""

import json
from dataclasses import FrozenInstanceError

import pytest

from nightshift import queue
from test_revise import PrGh
from test_specloop_spec_revision import CHANGE, poll, setup  # noqa: F401

pytestmark = pytest.mark.real_trust
SOURCES = ("issues/8/comments", "pulls/8/reviews", "pulls/8/comments")


def record(identity=1, body="Change λ\n\nKeep 🦉", **extra):
    return {"id": identity, "body": body, "user": {"login": "reviewer"},
            "author_association": "COLLABORATOR", **extra}


class FeedbackGh:
    def __init__(self):
        self.pages = {endpoint: [[record()]] for endpoint in SOURCES}
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        assert args[0] == "api" and args[2:] == ["--paginate", "--slurp"]
        payload = self.pages[args[1].removeprefix("repos/owner/sample/")]
        if isinstance(payload, Exception):
            raise payload
        return payload if isinstance(payload, str) else json.dumps(payload)


@pytest.mark.scenario("pr-revision/identified-feedback/all-feedback-sources-retain-identity")
def test_sources_pages_identity_and_rendering():
    gh = FeedbackGh()
    for source in SOURCES:
        first = record(state="CHANGES_REQUESTED", path="src/λ.py", line=4, original_line=2)
        second = record(2, state="COMMENTED", path="src/λ.py", line=None, original_line=9)
        gh.pages[source] = [[first], [], [first, second]]
    items = queue.pr_feedback_items("owner/sample", 8, runner=gh)
    body = "Change λ\n\nKeep 🦉"
    assert items == [
        queue.FeedbackItem("conversation:1", f"**reviewer:**\n\n{body}"),
        queue.FeedbackItem("conversation:2", f"**reviewer:**\n\n{body}"),
        queue.FeedbackItem("review:1", f"**reviewer (changes requested):**\n\n{body}"),
        queue.FeedbackItem("review:2", f"**reviewer:**\n\n{body}"),
        queue.FeedbackItem("inline:1", f"**reviewer on `src/λ.py:4`:**\n\n{body}"),
        queue.FeedbackItem("inline:2", f"**reviewer on `src/λ.py:9`:**\n\n{body}"),
    ]
    assert [call[1] for call in gh.calls] == [f"repos/owner/sample/{s}" for s in SOURCES]
    with pytest.raises(FrozenInstanceError):
        items[0].id = "changed"


def test_raw_history_retains_non_actionable_bodies_and_creation_times():
    gh = FeedbackGh()
    for source in SOURCES:
        key = "submitted_at" if source.endswith("reviews") else "created_at"
        gh.pages[source] = [[record(i, body, **{key: f"2026-10-20T0{i}:00:00Z"},
                                   updated_at="2026-10-22T00:00:00Z")
                             for i, body in enumerate(("", "  ", queue.SELF_COMMENT_PREFIX, "Act"), 1)]]
        gh.pages[source][0] += [record(5, author_association="NONE"),
                                record(6, author_association=None)]
        missing = record(7)
        del missing["author_association"]
        gh.pages[source][0].append(missing)
    raw = queue._pr_feedback_records("owner/sample", 8, runner=gh)
    assert len(raw) == 12
    for source, item in raw:
        key = "submitted_at" if source == "review" else "created_at"
        assert item[key] == f"2026-10-20T0{item['id']}:00:00Z"
        assert item["updated_at"] == "2026-10-22T00:00:00Z"
        assert queue._feedback_id(source, item) == f"{source}:{item['id']}"
    assert [item.id for item in queue.pr_feedback_items("owner/sample", 8, runner=gh)] == [
        "conversation:4", "review:4", "inline:4",
    ]


@pytest.mark.parametrize("body", [None, "", queue.SELF_COMMENT_PREFIX + " done"])
def test_excluded_bodies_need_no_identity_but_remain_available_to_migration(body):
    gh = FeedbackGh()
    gh.pages = {source: [[record(None, body)]] for source in SOURCES}
    assert queue.pr_feedback_items("owner/sample", 8, runner=gh) == []
    for source, item in queue._pr_feedback_records("owner/sample", 8, runner=gh):
        with pytest.raises(ValueError, match="feedback ID"):
            queue._feedback_id(source, item)


@pytest.mark.scenario("pr-revision/identified-feedback/feedback-read-failure-preserves-request")
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("bad", [
    RuntimeError("unavailable"), "", "not json", {}, [None], [[None]], [[[]]],
    [{"id": 1}], [[record(None)]], [[record(True)]], [[record(0)]],
    [[record(-1)]], [[record("1")]], [[record(1.5)]],
    [[{k: v for k, v in record().items() if k != "id"}]],
    [[record()], [record(body="conflicting edit")]],
    [[record(created_at="old")], [record(created_at="new")]],
])
def test_failed_read_reaches_item_error_path_without_replacing_snapshot(
    setup, tmp_path, source, bad,
):
    repo, gh, commands = setup
    reader = FeedbackGh()
    reader.pages[source] = bad

    def github(args):
        if args[0] == "api" and args[1].removeprefix("repos/owner/sample/") in SOURCES:
            return reader(args)
        return gh(args)

    path = tmp_path / "revisions" / "owner__sample" / "8.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {"change": CHANGE, "event": "older", "feedback": ["Saved instructions"],
                "feedback_state_version": 1,
                "feedback_ids": ["conversation:42"], "consumed_feedback_ids": ["review:10"],
                "result": {"outcome": "empty"}, "delivered": True}
    original = json.dumps(snapshot)
    path.write_text(original)
    errors = []
    assert poll((repo, github, commands), tmp_path,
                on_error=lambda number, error: errors.append((number, error))) == []
    assert len(errors) == 1 and errors[0][0] == 8
    assert isinstance(errors[0][1], (ValueError, RuntimeError))
    assert reader.calls  # Authorization succeeded and the strict read was attempted.
    assert path.read_text() == original
    assert commands.calls == [] and gh.comments == []
    assert "agent:revise" in gh.labels


@pytest.mark.scenario("pr-revision/worker-feedback-compatibility/worker-timestamp-cutoff-remains")
def test_worker_retains_cutoff_trust_latest_pr_and_strings():
    gh = PrGh(numbers=(8, 15, 9))
    for hour in (9, 10, 11):
        for association in ("OWNER", "NONE", None):
            timestamp = f"2026-10-20T{hour:02}:00:00Z"
            item = {**record(body=f"hour {hour}"), "author": {"login": "reviewer"},
                    "authorAssociation": association, "author_association": association,
                    "createdAt": timestamp, "submittedAt": timestamp, "created_at": timestamp}
            gh.comments.append(item)
            gh.reviews.append({**item, "state": "APPROVED"})
            gh.inline.append({**item, "path": "a.py", "line": 3})
    assert queue.pr_feedback("owner/sample", "spec/x", "2026-10-20T10:00:00Z", runner=gh) == [
        "**reviewer:**\n\nhour 11", "**reviewer (approved):**\n\nhour 11",
        "**reviewer on `a.py:3`:**\n\nhour 11",
    ]
    assert next(call for call in gh.calls if call[:2] == ["pr", "view"])[2] == "15"


@pytest.mark.scenario("pr-revision/worker-feedback-compatibility/worker-timestamp-cutoff-remains")
@pytest.mark.parametrize("failed", ["list", "view", "api"])
def test_worker_reads_remain_best_effort(failed):
    gh = PrGh(comments=[{"authorAssociation": "OWNER", "body": "conversation"}],
              inline=[{"author_association": "OWNER", "body": "inline"}])

    def runner(args):
        if args[0] == failed or args[1] == failed:
            raise RuntimeError("unavailable")
        return gh(args)

    blocks = queue.pr_feedback("owner/sample", "spec/x", runner=runner)
    expected = {"list": [], "view": ["**unknown:**\n\ninline"],
                "api": ["**unknown:**\n\nconversation"]}
    assert blocks == expected[failed]
