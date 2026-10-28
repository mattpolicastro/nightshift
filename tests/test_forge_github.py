"""Compatibility tests for the staged factual forge interface."""

from dataclasses import FrozenInstanceError, fields
from unittest.mock import Mock

import pytest

from nightshift import queue
from nightshift.forge import Forge, ForgeError
from nightshift.forge.github import GitHubForge
from nightshift.forge.types import CICheck, Comment, Issue, LabelEvent, PullRequest, ResponseText
from nightshift.specloop_intake import Paused


class StructuralFake:
    def list_issues(self, repo: str, label: str) -> list[queue.Issue]: return []
    def issue_labels(self, repo: str, number: int) -> set[str]: return set()
    def relabel_issue(self, repo: str, number: int, *, add: list[str], remove: list[str]) -> None: pass
    def latest_pr_number(self, repo: str, branch: str, *, strict: bool = True) -> int | None: return None
    def pr_feedback_records(self, repo: str, number: int) -> list[tuple[str, dict]]: return []
    def pr_feedback_items(self, repo: str, number: int) -> list[queue.FeedbackItem]: return []
    def open_blockers(self, repo: str, numbers: list[int]) -> list[int]: return numbers


@pytest.mark.scenario("forge-interface/factual-forge-contract/frozen-records-and-structural-implementation")
def test_structural_contract_and_frozen_facts():
    runner = Mock()
    implementations: list[Forge] = [GitHubForge(runner), StructuralFake()]
    for forge in implementations:
        assert isinstance(forge, Forge)
        assert Forge not in type(forge).__mro__
    runner.assert_not_called()
    records: list[Issue | Comment | LabelEvent | CICheck | ResponseText] = [
        Issue("o/r", 7), PullRequest("o/r", 7), Comment(1), LabelEvent(2),
        CICheck(), ResponseText(" \nraw\n")]
    for record in records:
        names = {field.name for field in fields(record)}
        assert not names & {"trusted", "authorized", "actionable", "ready", "consumed"}
        for name in names:
            with pytest.raises(FrozenInstanceError):
                setattr(record, name, None)
    assert Issue("o/r", 7).labels is None
    assert PullRequest("o/r", 7).is_cross_repository is None
    assert ResponseText(" \nraw\n").stdout == " \nraw\n"
    assert queue.Issue("o/r", 7, "title", "body", True).branch == "claude/7"


FEEDBACK_CALLS = [
    ["api", "repos/o/r/issues/7/comments", "--paginate", "--slurp"],
    ["api", "repos/o/r/pulls/7/reviews", "--paginate", "--slurp"],
    ["api", "repos/o/r/pulls/7/comments", "--paginate", "--slurp"],
]
FEEDBACK = '[[{"id":1,"body":" fix ","author_association":"MEMBER","user":{"login":"pat"}}, {"id":2,"body":"ignore","author_association":"NONE"}]]'
TRUSTED = {"id": 1, "body": " fix ", "author_association": "MEMBER", "user": {"login": "pat"}}


@pytest.mark.real_trust
@pytest.mark.scenario("forge-interface/exact-github-operations/helper-delegation")
@pytest.mark.parametrize("method,helper,args,kwargs,replies,expected,calls", [
    ("list_issues", "_list", ("draft",), {}, ['[{"number":7,"title":"Title","body":null}]'],
     [queue.Issue("o/r", 7, "Title", "")],
     [["issue", "list", "--repo", "o/r", "--label", "draft", "--state", "open", "--json", "number,title,body", "--limit", "100"]]),
    ("issue_labels", "labels_of", (7,), {}, ['{"labels":[{"name":"draft"}]}'], {"draft"},
     [["issue", "view", "7", "--repo", "o/r", "--json", "labels"]]),
    ("relabel_issue", "_relabel", (7,), {"add": ["a", "b"], "remove": ["c", "d"]}, [""], None,
     [["issue", "edit", "7", "--repo", "o/r", "--add-label", "a", "--add-label", "b", "--remove-label", "c", "--remove-label", "d"]]),
    ("latest_pr_number", "_latest_pr_number", ("topic",), {}, ['[{"number":9},{"number":7}]'], 9,
     [["pr", "list", "--repo", "o/r", "--head", "topic", "--state", "all", "--json", "number", "--limit", "20"]]),
    ("latest_pr_number", "_latest_pr_number", ("topic",), {"strict": False}, ["[]"], None,
     [["pr", "list", "--repo", "o/r", "--head", "topic", "--state", "all", "--json", "number", "--limit", "20"]]),
    ("pr_feedback_records", "_pr_feedback_records", (7,), {}, [FEEDBACK] * 3,
     [("conversation", TRUSTED), ("review", TRUSTED), ("inline", TRUSTED)], FEEDBACK_CALLS),
    ("pr_feedback_items", "pr_feedback_items", (7,), {}, [FEEDBACK] * 3,
     [queue.FeedbackItem("conversation:1", "**pat:**\n\nfix"),
      queue.FeedbackItem("review:1", "**pat:**\n\nfix"), queue.FeedbackItem("inline:1", "**pat:**\n\nfix")], FEEDBACK_CALLS),
    ("open_blockers", "open_blockers", ([9, 2],), {}, ['{"state":"OPEN"}', '{"state":"CLOSED"}'], [9],
     [["issue", "view", "9", "--repo", "o/r", "--json", "state"],
      ["issue", "view", "2", "--repo", "o/r", "--json", "state"]]),
    ("open_blockers", "open_blockers", ([],), {}, [], [], []),
])
def test_github_forge_emits_current_argv(monkeypatch, method, helper, args, kwargs, replies, expected, calls):
    runner = Mock(side_effect=replies)
    original = getattr(queue, helper)
    returned = []

    def delegate(*args, **kwargs):
        result = original(*args, **kwargs)
        returned.append(result)
        return result

    spy = Mock(side_effect=delegate)
    monkeypatch.setattr(queue, helper, spy)
    monkeypatch.setattr(queue, "_gh", Mock(side_effect=AssertionError("unbound runner")))
    result = getattr(GitHubForge(runner), method)("o/r", *args, **kwargs)
    assert result == expected
    assert result is returned[0]
    helper_kwargs = {"strict": True, **kwargs} if method == "latest_pr_number" else kwargs
    spy.assert_called_once_with("o/r", *args, **helper_kwargs, runner=runner)
    assert runner.call_args_list == [((argv,), {}) for argv in calls]


ERRORS = [RuntimeError("unavailable"), queue.GhError("unavailable", transient=True),
          queue.GhError("unavailable", transient=False), Paused("paused")]


@pytest.mark.scenario("forge-interface/forge-failure-compatibility/transport-failure-compatibility")
@pytest.mark.parametrize("error", ERRORS)
@pytest.mark.parametrize("method,args,kwargs", [
    ("list_issues", ("draft",), {}),
    ("relabel_issue", (7,), {"add": ["a"], "remove": []}),
    ("latest_pr_number", ("topic",), {"strict": True}),
    ("pr_feedback_records", (7,), {}),
    ("pr_feedback_items", (7,), {}),
])
def test_escaping_helper_errors_keep_identity(error, method, args, kwargs):
    runner = Mock(side_effect=error)
    message, transient = str(error), getattr(error, "transient", None)
    with pytest.raises(type(error)) as caught:
        getattr(GitHubForge(runner), method)("o/r", *args, **kwargs)
    assert caught.value is error
    assert str(error) == message and getattr(error, "transient", None) is transient
    runner.assert_called_once()
    if isinstance(error, queue.GhError):
        assert isinstance(error, ForgeError) and isinstance(error, RuntimeError)
    if isinstance(error, Paused):
        assert not isinstance(error, ForgeError)


@pytest.mark.scenario("forge-interface/forge-failure-compatibility/delegated-helper-error-fallbacks")
@pytest.mark.parametrize("error", ERRORS)
def test_delegated_helper_fallbacks(error):
    runner = Mock(side_effect=error)
    forge = GitHubForge(runner)
    assert forge.issue_labels("o/r", 7) == set()
    assert forge.open_blockers("o/r", [9, 2, 9]) == [9, 2, 9]
    assert forge.latest_pr_number("o/r", "topic", strict=False) is None
    with pytest.raises(type(error)) as caught:
        forge.latest_pr_number("o/r", "topic", strict=True)
    assert caught.value is error
    assert runner.call_args_list == [((argv,), {}) for argv in [
        ["issue", "view", "7", "--repo", "o/r", "--json", "labels"],
        ["issue", "view", "9", "--repo", "o/r", "--json", "state"],
        ["issue", "view", "2", "--repo", "o/r", "--json", "state"],
        ["issue", "view", "9", "--repo", "o/r", "--json", "state"],
        ["pr", "list", "--repo", "o/r", "--head", "topic", "--state", "all", "--json", "number", "--limit", "20"],
        ["pr", "list", "--repo", "o/r", "--head", "topic", "--state", "all", "--json", "number", "--limit", "20"],
    ]]


def test_constructor_binds_current_runner(monkeypatch):
    first, second, explicit = (Mock(return_value="[]") for _ in range(3))
    monkeypatch.setattr(queue, "_gh", first)
    old = GitHubForge()
    monkeypatch.setattr(queue, "_gh", second)
    new, injected = GitHubForge(), GitHubForge(explicit)
    for runner in (first, second, explicit):
        runner.assert_not_called()
    for forge in (old, new, injected):
        assert forge.list_issues("o/r", "draft") == []
    for runner in (first, second, explicit):
        runner.assert_called_once()
