"""Compatibility tests for the staged factual forge interface."""

from dataclasses import FrozenInstanceError, fields
import json
from typing import Literal
from unittest.mock import Mock, call

import pytest

from nightshift import queue, specloop_intake as intake
from nightshift.config import Repo
from nightshift.forge import Forge, ForgeError
from nightshift.forge.github import GitHubForge
from nightshift.forge.types import CICheck, Comment, Issue, LabelEvent, PullRequest, ResponseText
from nightshift.specloop_intake import Paused
import test_specloop_intake as proposal_tests
from test_specloop_intake import repo


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


@pytest.mark.real_trust
@pytest.mark.scenario("forge-interface/late-bound-runners/default-runner-remains-patchable")
@pytest.mark.scenario("forge-interface/late-bound-runners/explicit-runner-wins")
@pytest.mark.scenario("forge-interface/core-policy-preservation/revision-and-proposal-delivery-unchanged")
@pytest.mark.scenario("issue-intake/forge-mediated-intake/existing-workflows-retain-transcripts")
@pytest.mark.parametrize("recover", [False, True])
def test_late_bound_runner_still_patchable(monkeypatch, repo, tmp_path, recover):
    # Intake and the backend were imported above, before any transport patch.
    forbidden = Mock(side_effect=AssertionError("unbound transport"))
    transcripts = []
    for attempt in range(3):
        gh, commands = proposal_tests.DraftGh(("spec:draft",)), proposal_tests.Commands()
        if recover:
            gh.prs = [{"number": 8}]

        def dispatch(args):
            # Even a patch during a poll cannot redirect delegated helpers.
            monkeypatch.setattr(queue, "_gh", forbidden)
            return gh(args)

        runner = Mock(side_effect=dispatch)
        monkeypatch.setattr(queue, "_gh", runner if attempt < 2 else forbidden)
        forge = Mock(spec_set=Forge, wraps=GitHubForge(runner))
        constructor = Mock(return_value=forge)
        monkeypatch.setattr(intake, "GitHubForge", constructor)
        runner.assert_not_called()
        rows = intake.propose_pending(
            repo, queue.Labels(), tmp_path / str(attempt),
            **({"runner": runner} if attempt == 2 else {}),
            specloop_runner=commands, git_runner=commands,
        )
        constructor.assert_called_once_with(runner)
        names = [c[0] for c in forge.mock_calls]
        expected = ["list_issues", "list_issues", "issue_for_draft", "latest_pr_number"]
        expected += ["pr_for_recovery"] if recover else ["latest_pr_number", "open_blockers"]
        expected += ["label_event_pages", "repository", "collaborator_response"]
        if not recover:
            expected += ["issue_author", "create_draft_pr"]
        expected += ["pr_state_url", "relabel_pr", "comment_pages", "create_comment", "relabel_issue"]
        assert names == expected
        assert gh.labels == set()
        assert len(gh.prs) == 1
        assert "<!-- nightshift:draft:" + proposal_tests.CHANGE + ":pr-8 -->" in gh.comments[-1]["body"]
        if recover:
            assert rows == [] and commands.calls == []
        else:
            assert rows[0]["delivered"] is True
            assert commands.calls[-1] == [
                "git", "-C", str(tmp_path / "manifest-source"), "push", "origin",
                "abc123:refs/heads/" + proposal_tests.BRANCH,
            ]
        transcripts.append(gh.calls)
    assert transcripts[0] == transcripts[1] == transcripts[2]
    forbidden.assert_not_called()


@pytest.fixture
def proposal_blocker_spy(monkeypatch):
    """Run retained workflows unchanged, while proving the new operation is used."""
    blockers = Mock(wraps=queue.open_blockers)
    forges = []

    def construct(runner):
        forge = Mock(spec_set=Forge, wraps=GitHubForge(runner))
        forges.append(forge)
        return forge

    monkeypatch.setattr(intake, "GitHubForge", construct)
    monkeypatch.setattr(queue, "open_blockers", blockers)
    monkeypatch.setattr(queue, "_gh", Mock(side_effect=AssertionError("unbound transport")))
    yield
    operations = [c for forge in forges for c in forge.open_blockers.call_args_list]
    assert operations
    assert [(c.args, {}) for c in blockers.call_args_list] == operations
    assert all(call.kwargs["runner"] is not None for call in blockers.call_args_list)


@pytest.mark.scenario("issue-intake/dependency-gated-drafting/open-blocker-defers-round")
@pytest.mark.scenario("issue-intake/dependency-gated-drafting/all-blockers-closed-allows-round")
@pytest.mark.scenario("issue-intake/dependency-gated-drafting/unreadable-blocker-defers-round")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
@pytest.mark.parametrize("blockers,lookup_failure", [((6,), False), ((5, 6), False), ((6,), True)])
def test_forge_blockers_retain_polling(repo, tmp_path, caplog, proposal_blocker_spy,
                                     armed, blockers, lookup_failure):
    proposal_tests.test_blocked_issue_is_not_drafted_until_blockers_close(
        repo, tmp_path, caplog, armed, blockers, lookup_failure)


@pytest.mark.scenario("issue-intake/dependency-gated-drafting/blocked-poll-preserves-state")
@pytest.mark.scenario("issue-intake/dependency-gated-drafting/rearmed-round-waits")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
@pytest.mark.parametrize("previous,lost_response", [
    (None, False), ("needs_input", False), ("rejected", False), ("drafted", False),
    ("needs_input", True), ("rejected", True), ("drafted", True), ("released", False),
])
def test_forge_blockers_preserve_receipts(repo, tmp_path, monkeypatch, caplog,
                                        proposal_blocker_spy, armed, previous, lost_response):
    proposal_tests.test_blocked_issue_changes_no_labels_or_comments(
        repo, tmp_path, monkeypatch, caplog, armed, previous, lost_response)


@pytest.mark.scenario("issue-intake/dependency-gated-drafting/no-dependencies-allows-round")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
def test_forge_empty_blockers_do_not_read(repo, tmp_path, caplog, proposal_blocker_spy, armed):
    proposal_tests.test_body_without_dependencies_drafts_without_blocker_lookups(
        repo, tmp_path, caplog, armed)


@pytest.fixture
def intake_forge(monkeypatch):
    monkeypatch.setattr(intake, "GitHubForge", Mock(side_effect=AssertionError("unexpected backend")))
    monkeypatch.setattr(queue, "_gh", Mock(side_effect=AssertionError("unexpected transport")))
    return Mock(spec_set=Forge)


@pytest.mark.scenario("forge-interface/late-bound-runners/explicit-runner-wins")
@pytest.mark.scenario("forge-interface/core-policy-preservation/revision-and-proposal-delivery-unchanged")
@pytest.mark.parametrize("recover", [False, True])
def test_proposal_delivery_uses_supplied_forge(intake_forge, recover):
    forge = intake_forge
    forge.comment_pages.return_value = []
    forge.pr_state_url.return_value = {"state": "OPEN", "url": URL}
    receipt = {"round_id": "round", "result": {"outcome": "needs_input", "questions": ["Which base?"]}}
    intake._deliver(Repo("o/r"), queue.Labels(), 7, "change", receipt,
                    {"spec:draft", "agent:ready"}, pr=7 if recover else None, forge=forge)
    if recover:
        body = f"{queue.SELF_COMMENT_PREFIX} Spec draft ready for review: {URL}\n\n<!-- nightshift:draft:change:pr-7 -->"
        expected = [call.pr_state_url("o/r", "7"),
                    call.relabel_pr("o/r", URL, add=["spec:review"], remove=[])]
    else:
        body = (f"{queue.SELF_COMMENT_PREFIX} The spec draft needs human input.\n\nQuestions:\n- Which base?"
                "\n\n<!-- nightshift:draft:change:round -->")
        expected = []
    assert forge.mock_calls == expected + [
        call.comment_pages("o/r", 7), call.create_comment("o/r", 7, "issue", body),
        call.relabel_issue("o/r", 7, add=[] if recover else ["needs-human"],
                           remove=["agent:ready", "spec:draft"]),
    ]


@pytest.mark.real_trust
@pytest.mark.scenario("forge-interface/core-policy-preservation/authorization-and-comments-unchanged")
@pytest.mark.scenario("forge-interface/late-bound-runners/explicit-runner-wins")
@pytest.mark.parametrize("relationship", ["OWNER", "COLLABORATOR", "MEMBER", None])
def test_intake_authorization_protocol_fake(intake_forge, relationship):
    forge = intake_forge
    event = {"id": 2, "event": "labeled", "label": {"name": "draft"}, "actor": {"login": "helper"}}
    forge.label_event_pages.return_value = [[dict(event, id=99, actor={"login": "owner"})], [event]]
    forge.repository.return_value = {"owner": {
        "login": "helper" if relationship == "OWNER" else "owner", "type": "User" if relationship == "OWNER" else "Organization"}}
    forge.collaborator_response.return_value = ResponseText(
        f"HTTP/2.0 {204 if relationship == 'COLLABORATOR' else 404}\r\n\r\n")
    forge.organization_member_response.return_value = ResponseText(
        f"HTTP/1.1 {204 if relationship == 'MEMBER' else 404}\n\n")
    forge.comment_pages.return_value = [[]]
    for _ in range(2):
        events = intake._latest_label_events("o/r", 7, {"draft"}, forge=forge)
        assert events == {"draft": event}
        assert intake._authorize_label_events("o/r", 7, events, forge=forge) is (relationship is not None)
        if relationship is None:
            body = forge.create_comment.call_args.args[-1]
            assert "label-authorization-7:2" in body
            forge.comment_pages.return_value = [[{"body": body, "author_association": "NONE"}]]
    names = [c[0] for c in forge.mock_calls]
    lookups = ["label_event_pages", "repository"]
    if relationship != "OWNER":
        lookups += ["collaborator_response"]
    if relationship in ("MEMBER", None):
        lookups += ["organization_member_response"]
    assert names == (lookups + ["comment_pages", "create_comment"] + lookups + ["comment_pages"]
                     if relationship is None else lookups * 2)
    forge.reset_mock()
    assert intake._label_events("o/r", 7, {"draft"}, forge=forge) == {"draft": 2}
    assert intake._approval_event("o/r", 7, "draft", forge=forge) == 2
    for association in ("OWNER", "MEMBER", "COLLABORATOR", "NONE", None):
        forge.issue_author.return_value = {"author_association": association, "body": BODY}
        assert intake._issue_author_trusted("o/r", 7, forge=forge) is (association in queue.TRUSTED_ASSOCIATIONS)


@pytest.mark.scenario("forge-interface/core-policy-preservation/authorization-and-comments-unchanged")
@pytest.mark.parametrize("pages", [None, {}, [None], [[None]], [[{"body": 42}]]])
def test_rejection_fake_rejects_malformed_history(intake_forge, pages):
    intake_forge.comment_pages.return_value = pages
    with pytest.raises(RuntimeError, match="malformed comment history"):
        intake._comment_label_rejection("o/r", 7, "draft", {"id": 2}, forge=intake_forge)
    assert intake_forge.mock_calls == [call.comment_pages("o/r", 7)]


@pytest.mark.scenario("forge-interface/core-policy-preservation/authorization-and-comments-unchanged")
@pytest.mark.parametrize("event", [None, {"event": "labeled", "label": None},
    {"event": "labeled", "label": {"name": "draft"}, "id": True}])
def test_label_fake_rejects_malformed_evidence(intake_forge, event):
    intake_forge.label_event_pages.return_value = [[event]]
    with pytest.raises(RuntimeError, match="retry next poll"):
        intake._label_events("o/r", 7, {"draft"}, forge=intake_forge)
    assert intake_forge.mock_calls == [call.label_event_pages("o/r", 7)]


@pytest.mark.scenario("forge-interface/core-policy-preservation/authorization-and-comments-unchanged")
@pytest.mark.real_trust
@pytest.mark.parametrize("error", ERRORS)
@pytest.mark.parametrize("operation", ["issue_author", "label_event_pages", "collaborator_response", "comment_pages"])
def test_intake_fake_read_errors_prevent_mutations(intake_forge, error, operation):
    getattr(intake_forge, operation).side_effect = error
    actions = {
        "issue_author": lambda: intake._issue_author_trusted("o/r", 7, forge=intake_forge),
        "label_event_pages": lambda: intake._label_events("o/r", 7, {"draft"}, forge=intake_forge),
        "collaborator_response": lambda: intake._membership_status("o/r", "helper", forge=intake_forge),
        "comment_pages": lambda: intake._comment("o/r", 7, "change", BODY, forge=intake_forge),
    }
    with pytest.raises(type(error)) as caught:
        actions[operation]()
    assert caught.value is error
    assert len(intake_forge.mock_calls) == 1


@pytest.mark.scenario("forge-interface/core-policy-preservation/authorization-and-comments-unchanged")
@pytest.mark.parametrize("response,expected", [
    (ResponseText("HTTP/2 204\n\n"), 204),
    (ResponseText("HTTP/1.1 404 Not Found\r\n\r\n"), 404),
    (queue.GhError("gh: Not Found (HTTP 404)", transient=False), 404),
    (queue.GhError("gh: Not Found (HTTP 404)", transient=True), None),
    (ResponseText("HTTP/2 204\n"), None), (ResponseText("HTTP/2 403\n\n"), None),
    (ResponseText(""), None),
])
def test_membership_fake_keeps_status_parsing_in_core(intake_forge, response, expected):
    if isinstance(response, Exception):
        intake_forge.collaborator_response.side_effect = response
    else:
        intake_forge.collaborator_response.return_value = response
    if expected is None:
        with pytest.raises(RuntimeError):
            intake._membership_status("o/r", "helper", forge=intake_forge)
    else:
        assert intake._membership_status("o/r", "helper", forge=intake_forge) == expected
    assert intake_forge.mock_calls == [call.collaborator_response("o/r", "helper")]


@pytest.mark.scenario("forge-interface/core-policy-preservation/authorization-and-comments-unchanged")
@pytest.mark.parametrize("existing", [False, True])
def test_comment_fake_preserves_literal_marker_and_raw_body(intake_forge, existing):
    marker = "<!-- nightshift:draft:change:round -->"
    intake_forge.comment_pages.return_value = [[], [{"id": 42, "body": "prefix " + marker + " suffix",
        "author_association": "NONE"}] if existing else []]
    intake._comment("o/r", 7, "change", BODY, round_id="round", forge=intake_forge)
    body = f"{queue.SELF_COMMENT_PREFIX} {BODY}\n\n{marker}"
    assert intake_forge.mock_calls == [call.comment_pages("o/r", 7),
        call.update_comment("o/r", 42, body) if existing else call.create_comment("o/r", 7, "issue", body)]


@pytest.mark.real_trust
@pytest.mark.scenario("forge-interface/late-bound-runners/explicit-runner-wins")
@pytest.mark.parametrize("kind", ["issue", "pr"])
def test_intake_explicit_runner_and_failure_order(monkeypatch, kind):
    monkeypatch.setattr(queue, "_gh", Mock(side_effect=AssertionError("unbound runner")))
    runner = Mock(side_effect=['{"author_association":"OWNER"}', "[]", "", ""])
    assert intake._issue_author_trusted("o/r", 7, runner)
    assert intake.report_failure(Repo("o/r"), queue.Labels(), 7, "draft", kind, RuntimeError("oops"), runner=runner) == []
    body = (f"{queue.SELF_COMMENT_PREFIX} Specloop draft failed. Human input is needed.\n\noops\n\n"
            "Resolve the failure, then remove `needs-human` to retry.\n\n<!-- nightshift:draft-error:7 -->")
    assert runner.call_args_list == [call(argv) for argv in [
        ["api", "repos/o/r/issues/7"],
        ["api", "repos/o/r/issues/7/comments", "--paginate", "--slurp"],
        [kind, "comment", "7", "--repo", "o/r", "--body", body],
        [kind, "edit", "7", "--repo", "o/r", "--add-label", "needs-human"],
    ]]


@pytest.mark.scenario("forge-interface/late-bound-runners/explicit-runner-wins")
def test_label_authorization_uses_explicit_runner(monkeypatch):
    monkeypatch.setattr(queue, "_gh", Mock(side_effect=AssertionError("unbound runner")))
    event = {"id": 2, "event": "labeled", "label": {"name": "draft"}, "actor": {"login": "helper"}}
    runner = Mock(side_effect=[json.dumps([[event]]),
        '{"owner":{"login":"owner","type":"Organization"}}',
        "HTTP/2 404\n\n", "HTTP/2 204\n\n"])
    events = intake._latest_label_events("o/r", 7, {"draft"}, runner=runner)
    assert intake._authorize_label_events("o/r", 7, events, runner=runner)
    assert runner.call_args_list == [call(argv) for argv in [
        ["api", "repos/o/r/issues/7/events", "--paginate", "--slurp"],
        ["api", "repos/o/r"],
        ["api", "repos/o/r/collaborators/helper", "--include"],
        ["api", "orgs/owner/members/helper", "--include"],
    ]]


@pytest.mark.scenario("forge-interface/late-bound-runners/explicit-runner-wins")
@pytest.mark.parametrize("kind", ["issue", "pr"])
def test_failure_reporting_reuses_fake_and_labels_last(intake_forge, kind):
    forge = intake_forge
    forge.comment_pages.return_value = []
    args = (Repo("o/r"), queue.Labels(), 7, "draft", kind, RuntimeError("oops"))
    assert intake.report_failure(*args, forge=forge) == []
    assert [c[0] for c in forge.mock_calls] == ["comment_pages", "create_comment", f"relabel_{kind}"]
    for error in ERRORS:
        forge.reset_mock()
        forge.comment_pages.side_effect = error
        if isinstance(error, Paused):
            with pytest.raises(Paused) as caught:
                intake.report_failure(*args, forge=forge)
            assert caught.value is error
        else:
            assert intake.report_failure(*args, forge=forge) == [error]
        assert forge.mock_calls == [call.comment_pages("o/r", 7)]
