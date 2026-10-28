"""Compatibility tests for the staged factual forge interface."""

from dataclasses import FrozenInstanceError, fields
import json
from typing import Literal
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
    def issue_for_draft(self, repo: str, number: int) -> dict: return {}
    def issue_author(self, repo: str, number: int) -> dict: return {}
    def list_open_prs(self, repo: str, label: str) -> list[dict]: return []
    def pr_for_recovery(self, repo: str, number: int) -> dict: return {}
    def pr_for_admission(self, repo: str, number: int) -> dict: return {}
    def pr_for_revision(self, repo: str, number: int) -> dict: return {}
    def pr_for_publication(self, repo: str, number: int) -> dict: return {}
    def pr_for_readiness(self, repo: str, number: int) -> dict: return {}
    def pr_state_url(self, repo: str, target: str | int) -> dict: return {}
    def label_event_pages(self, repo: str, number: int) -> list[list[dict]]: return []
    def repository(self, repo: str) -> dict: return {}
    def collaborator_response(self, repo: str, login: str) -> ResponseText: return ResponseText("")
    def organization_member_response(self, owner: str, login: str) -> ResponseText: return ResponseText("")
    def commit(self, repo: str, head: str) -> dict: return {}
    def comment_pages(self, repo: str, number: int) -> list[list[dict]]: return []
    def pr_checks(self, repo: str, number: int) -> ResponseText: return ResponseText("")
    def relabel_pr(self, repo: str, target: str | int, *, add: list[str], remove: list[str]) -> None: pass
    def create_comment(self, repo: str, number: int, kind: Literal["issue", "pr"], body: str) -> None: pass
    def update_comment(self, repo: str, comment_id: int, body: str) -> None: pass
    def create_draft_pr(self, repo: str, branch: str, base: str, title: str, body: str) -> ResponseText: return ResponseText("")


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


HELPER_CASES = [
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
]

BODY = " \n<!-- marker -->\r\n**café** 'quoted' $HOME `literal` = value\n "
FACTS = {"state": "OPEN", "body": None, "labels": []}
PAGES = [[{"id": 1, "body": BODY}], [], [{"id": 2}]]
RAW = " \r\nnot JSON; parsing belongs in core\n "
URL = "https://github.com/o/r/pull/7"
DIRECT_CASES = [
    ("issue_for_draft", (7,), {}, FACTS,
     ["issue", "view", "7", "--repo", "o/r", "--json", "state,title,body,labels,comments"]),
    ("issue_author", (7,), {}, {"author_association": None}, ["api", "repos/o/r/issues/7"]),
    ("list_open_prs", ("spec:review",), {}, [{"number": 7}, {"number": 9}],
     ["pr", "list", "--repo", "o/r", "--label", "spec:review", "--state", "open", "--json", "number", "--limit", "100"]),
    ("pr_for_recovery", (7,), {}, FACTS,
     ["pr", "view", "7", "--repo", "o/r", "--json", "state,headRefOid"]),
    ("pr_for_admission", (7,), {}, FACTS,
     ["pr", "view", "7", "--repo", "o/r", "--json", "state,labels,headRefName,headRefOid,isCrossRepository,url"]),
    ("pr_for_revision", (7,), {}, FACTS,
     ["pr", "view", "7", "--repo", "o/r", "--json", "state,title,body,labels,headRefName,headRefOid,isCrossRepository,url"]),
    ("pr_for_publication", (7,), {}, FACTS,
     ["pr", "view", "7", "--repo", "o/r", "--json", "state,labels,headRefName,isCrossRepository,url"]),
    ("pr_for_readiness", (7,), {}, FACTS,
     ["pr", "view", "7", "--repo", "o/r", "--json", "state,labels,headRefName,headRefOid,isCrossRepository,url,body"]),
    *[("pr_state_url", (target,), {}, FACTS,
       ["pr", "view", target, "--repo", "o/r", "--json", "state,url"]) for target in ("007", URL)],
    ("label_event_pages", (7,), {}, PAGES, ["api", "repos/o/r/issues/7/events", "--paginate", "--slurp"]),
    ("repository", (), {}, {"owner": {"login": "o", "type": "Organization"}}, ["api", "repos/o/r"]),
    ("collaborator_response", ("a/b +%?é",), {}, ResponseText(RAW),
     ["api", "repos/o/r/collaborators/a%2Fb%20%2B%25%3F%C3%A9", "--include"]),
    ("organization_member_response", ("a/b +%?é",), {}, ResponseText(RAW),
     ["api", "orgs/o%2Fr/members/a%2Fb%20%2B%25%3F%C3%A9", "--include"]),
    ("commit", ("topic/a+b",), {}, {"commit": {"committer": {"date": None}}}, ["api", "repos/o/r/commits/topic/a+b"]),
    ("comment_pages", (7,), {}, PAGES, ["api", "repos/o/r/issues/7/comments", "--paginate", "--slurp"]),
    ("pr_checks", (7,), {}, ResponseText(RAW), ["pr", "checks", "7", "--repo", "o/r", "--json", "name,bucket,state"]),
    *[("relabel_pr", (target,), {"add": ["spec:review", "a b"], "remove": ["old", "stale"]}, None,
       ["pr", "edit", target, "--repo", "o/r", "--add-label", "spec:review", "--add-label", "a b",
        "--remove-label", "old", "--remove-label", "stale"]) for target in ("007", URL)],
    ("relabel_pr", (7,), {"add": [], "remove": []}, None, ["pr", "edit", "7", "--repo", "o/r"]),
    *[("create_comment", (7, kind, BODY), {}, None,
       [kind, "comment", "7", "--repo", "o/r", "--body", BODY]) for kind in ("issue", "pr")],
    ("update_comment", (42, BODY), {}, None,
     ["api", "repos/o/r/issues/comments/42", "--method", "PATCH", "-f", "body=" + BODY]),
    *[("create_draft_pr", ("spec/topic", "release/next", "Title 'é'", BODY), {}, ResponseText(stdout),
       ["pr", "create", "--repo", "o/r", "--head", "spec/topic", "--base", "release/next", "--draft",
        "--title", "Title 'é'", "--body", BODY]) for stdout in (" \n" + URL + "\n", "")],
]
TRANSCRIPTS = HELPER_CASES + [
    (method, None, args, kwargs,
     [expected.stdout if isinstance(expected, ResponseText) else RAW if expected is None else json.dumps(expected)],
     expected, [argv]) for method, args, kwargs, expected, argv in DIRECT_CASES
]


@pytest.mark.real_trust
@pytest.mark.scenario("forge-interface/exact-github-operations/helper-delegation")
@pytest.mark.scenario("forge-interface/exact-github-operations/direct-operation-transcripts")
@pytest.mark.parametrize("method,helper,args,kwargs,replies,expected,calls", TRANSCRIPTS)
def test_github_forge_emits_current_argv(monkeypatch, method, helper, args, kwargs, replies, expected, calls):
    runner = Mock(side_effect=replies)
    original = getattr(queue, helper) if helper else None
    returned = []

    def delegate(*args, **kwargs):
        result = original(*args, **kwargs)
        returned.append(result)
        return result

    spy = Mock(side_effect=delegate)
    if helper:
        monkeypatch.setattr(queue, helper, spy)
    monkeypatch.setattr(queue, "_gh", Mock(side_effect=AssertionError("unbound runner")))
    forge = GitHubForge(runner)
    runner.assert_not_called()
    result = getattr(forge, method)("o/r", *args, **kwargs)
    assert result == expected
    if helper:
        assert result is returned[0]
        helper_kwargs = {"strict": True, **kwargs} if method == "latest_pr_number" else kwargs
        spy.assert_called_once_with("o/r", *args, **helper_kwargs, runner=runner)
    assert runner.call_args_list == [((argv,), {}) for argv in calls]


@pytest.mark.scenario("forge-interface/exact-github-operations/direct-operation-transcripts")
def test_transcript_table_covers_declared_operations():
    covered = {case[0] for case in TRANSCRIPTS}
    for cls in (Forge, GitHubForge):
        assert covered == {name for name, value in vars(cls).items() if not name.startswith("_") and callable(value)}


@pytest.mark.scenario("forge-interface/exact-github-operations/direct-operation-transcripts")
@pytest.mark.parametrize("method,args,kwargs,expected,argv", [
    case for case in DIRECT_CASES if isinstance(case[3], (dict, list))])
@pytest.mark.parametrize("stdout", ["", "not JSON", '{"state":'])
def test_direct_reads_reject_malformed_json(method, args, kwargs, expected, argv, stdout):
    runner = Mock(return_value=stdout)
    with pytest.raises(json.JSONDecodeError):
        getattr(GitHubForge(runner), method)("o/r", *args, **kwargs)
    runner.assert_called_once_with(argv)


ERRORS = [RuntimeError("unavailable"), queue.GhError("unavailable", transient=True),
          queue.GhError("unavailable", transient=False), Paused("paused")]


@pytest.mark.scenario("forge-interface/forge-failure-compatibility/transport-failure-compatibility")
@pytest.mark.parametrize("error", ERRORS)
@pytest.mark.parametrize("method,args,kwargs,expected,argv", DIRECT_CASES)
def test_direct_failures_keep_identity(error, method, args, kwargs, expected, argv):
    runner = Mock(side_effect=error)
    forge = GitHubForge(runner)
    runner.assert_not_called()
    message, transient = str(error), getattr(error, "transient", None)
    with pytest.raises(type(error)) as caught:
        getattr(forge, method)("o/r", *args, **kwargs)
    assert caught.value is error
    assert str(error) == message and getattr(error, "transient", None) is transient
    runner.assert_called_once_with(argv)
    if isinstance(error, queue.GhError):
        assert isinstance(error, ForgeError) and isinstance(error, RuntimeError)
    if isinstance(error, Paused):
        assert not isinstance(error, ForgeError)


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
