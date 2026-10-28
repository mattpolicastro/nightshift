"""Compatibility tests for the staged factual forge interface."""

from dataclasses import FrozenInstanceError, fields
import json
import subprocess
from typing import Literal
from unittest.mock import Mock, call

import pytest

from nightshift import daemon, outcomes, queue, specloop_intake as intake
from nightshift.config import Config, Repo
from nightshift.forge import Forge, ForgeError
from nightshift.forge.github import GitHubForge
from nightshift.forge.types import CICheck, Comment, Issue, LabelEvent, PullRequest, ResponseText
from nightshift.specloop_intake import Paused
import test_specloop_intake as proposal_tests
from test_specloop_intake import repo
import test_specloop_approval as approval_tests
import test_specloop_spec_revision as spec_revision_tests
from test_specloop_build_revision import BuildCommands
from test_revision_feedback_state import LegacyGh, legacy_receipt, CUTOFF
from merge_readiness_fixtures import local_run
from test_merge_readiness_polling import polling
import test_merge_readiness_polling as polling_tests
from test_specloop_publication import setup as publication_setup
import test_specloop_publication as publication_tests
import test_engine_switch_recovery as recovery_tests
from test_engine_switch_recovery import switched


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


@pytest.mark.real_trust
@pytest.mark.scenario("forge-interface/late-bound-runners/default-runner-remains-patchable")
@pytest.mark.scenario("issue-intake/forge-mediated-intake/existing-workflows-retain-transcripts")
@pytest.mark.parametrize("stage", ["approval", "spec", "build"])
def test_approval_and_revision_bind_each_poll(monkeypatch, repo, tmp_path, stage):
    forbidden = Mock(side_effect=AssertionError("unbound transport"))
    for attempt in range(3):
        state = tmp_path / str(attempt)
        if stage == "approval":
            gh, commands = approval_tests.ApprovalGh(), approval_tests.Commands()
            poll, directory = intake.admit_pending, "admissions"
        else:
            gh = spec_revision_tests.RevisionGh()
            commands = BuildCommands(gh) if stage == "build" else spec_revision_tests.Commands(gh)
            poll, directory = intake.revise_pending, "revisions"
            spec_revision_tests.seed_consumed(state)
        path = state / directory / "owner__sample/8.json"

        def dispatch(args):
            monkeypatch.setattr(queue, "_gh", forbidden)
            if stage != "approval" and args[:2] == ["pr", "comment"]:
                receipt = json.loads(path.read_text())
                assert receipt["feedback_ids"] == ["conversation:102"]
                assert receipt["consumed_feedback_ids"] == ["conversation:101", "conversation:102"]
            return gh(args)

        runner = Mock(side_effect=dispatch)
        monkeypatch.setattr(queue, "_gh", runner if attempt < 2 else forbidden)
        forge = Mock(spec_set=Forge, wraps=GitHubForge(runner))
        constructor = Mock(return_value=forge)
        monkeypatch.setattr(intake, "GitHubForge", constructor)
        poll(repo, queue.Labels(), state, specloop_runner=commands, git_runner=commands,
             **({"runner": runner} if attempt == 2 else {}))
        constructor.assert_called_once_with(runner)
        expected = ["list_open_prs", "pr_for_admission" if stage == "approval" else "pr_for_revision",
                    "label_event_pages", "repository", "collaborator_response"]
        if stage != "approval":
            expected += ["pr_feedback_items"]
        expected += ["comment_pages", "create_comment"]
        if stage != "approval":
            expected += ["label_event_pages"]
        assert [c[0] for c in forge.mock_calls] == expected + ["relabel_pr"]
        receipt = json.loads(path.read_text())
        assert receipt["delivered"] and receipt["head"] == approval_tests.HEAD
        assert len(gh.comments) == 1
        assert gh.labels == (set() if stage == "approval" else {"spec:review"})
        if stage != "build":
            action = "admit" if stage == "approval" else "revise"
            assert next(c for c in commands.calls if c[0] == "git") == [
                "git", "-C", str(tmp_path / "manifest-source"), "fetch", "origin",
                f"+refs/heads/{approval_tests.BRANCH}:refs/nightshift/{action}/{approval_tests.CHANGE}",
            ]
    forbidden.assert_not_called()


@pytest.mark.real_trust
@pytest.mark.scenario("forge-interface/core-policy-preservation/revision-and-proposal-delivery-unchanged")
@pytest.mark.parametrize("stage", ["spec", "build"])
def test_revision_forge_recovers_snapshot_without_removing_new_event(monkeypatch, repo, tmp_path, stage):
    gh = spec_revision_tests.RevisionGh()
    commands = BuildCommands(gh) if stage == "build" else spec_revision_tests.Commands(gh)
    path = spec_revision_tests.seed_consumed(tmp_path)
    forge = Mock(spec_set=Forge, wraps=GitHubForge(gh))
    monkeypatch.setattr(intake, "GitHubForge", Mock(return_value=forge))
    gh.fail_comment = True
    with pytest.raises(RuntimeError, match="comment response lost"):
        intake.revise_pending(repo, queue.Labels(), tmp_path, runner=gh,
                              specloop_runner=commands, git_runner=commands)
    saved = json.loads(path.read_text())
    calls = list(commands.calls)
    comment = dict(gh.comments[0])
    gh.feedback[1]["body"] = "Edited after dispatch"
    gh.label_event("agent:revise", "unlabeled")
    gh.label_event("agent:revise", "labeled")
    forge.reset_mock()
    intake.revise_pending(repo, queue.Labels(), tmp_path, runner=gh,
                          specloop_runner=commands, git_runner=commands)
    recovered = json.loads(path.read_text())
    assert recovered == {**saved, "delivered": True}
    assert gh.comments == [comment] and "agent:revise" in gh.labels
    assert [c[0] for c in forge.mock_calls] == [
        "list_open_prs", "pr_for_revision", "label_event_pages", "repository",
        "collaborator_response", "comment_pages", "update_comment", "label_event_pages",
    ]
    assert commands.calls == calls + ([] if stage == "build" else [[
        "git", "-C", str(tmp_path / "manifest-source"), "push", "origin",
        f"{spec_revision_tests.REVISED}:refs/heads/{approval_tests.BRANCH}",
    ]])


@pytest.mark.real_trust
@pytest.mark.scenario("forge-interface/late-bound-runners/default-runner-remains-patchable")
@pytest.mark.scenario("forge-interface/core-policy-preservation/revision-and-proposal-delivery-unchanged")
@pytest.mark.parametrize("saved_time", [False, True])
def test_legacy_feedback_forge_late_binding_and_read_budget(monkeypatch, tmp_path, saved_time):
    forbidden = Mock(side_effect=AssertionError("unbound transport"))
    for attempt in range(3):
        path, gh = tmp_path / str(attempt) / "8.json", LegacyGh()
        receipt = legacy_receipt(path, **({"saved_at": CUTOFF} if saved_time else {}))

        def dispatch(args):
            monkeypatch.setattr(queue, "_gh", forbidden)
            return gh(args)

        runner = Mock(side_effect=dispatch)
        monkeypatch.setattr(queue, "_gh", runner if attempt < 2 else forbidden)
        forge = Mock(spec_set=Forge, wraps=GitHubForge(runner))
        constructor = Mock(return_value=forge)
        monkeypatch.setattr(intake, "GitHubForge", constructor)
        migrated = intake._initialize_revision_feedback(
            path, receipt, "owner/sample", 8, "current-head",
            **({"runner": runner} if attempt == 2 else {}))
        constructor.assert_called_once_with(runner)
        assert forge.mock_calls == ([] if saved_time else [call.commit("owner/sample", "current-head")]) + [
            call.pr_feedback_records("owner/sample", 8)]
        assert gh.calls == ([] if saved_time else [["api", "repos/owner/sample/commits/current-head"]]) + [
            ["api", "repos/owner/sample/issues/8/comments", "--paginate", "--slurp"],
            ["api", "repos/owner/sample/pulls/8/reviews", "--paginate", "--slurp"],
            ["api", "repos/owner/sample/pulls/8/comments", "--paginate", "--slurp"],
        ]
        assert migrated == json.loads(path.read_text())
        assert all(migrated[key] == value for key, value in receipt.items())
        assert migrated["consumed_feedback_ids"] == sorted(
            f"{kind}:{i}" for kind in ("conversation", "review", "inline") for i in (1, 2))
        forge.reset_mock()
        assert intake._initialize_revision_feedback(path, migrated, "owner/sample", 8, "new-head", forge=forge) == migrated
        assert forge.mock_calls == []
    forbidden.assert_not_called()


@pytest.mark.scenario("forge-interface/core-policy-preservation/revision-and-proposal-delivery-unchanged")
def test_approval_head_binding_uses_supplied_forge(intake_forge, tmp_path):
    forge = intake_forge
    forge.comment_pages.return_value = []
    args = (tmp_path, Repo("o/r"), 7, "change", 2)
    labels = queue.Labels()
    assert intake._approval_covers_head(*args, {"headRefOid": "old"}, labels, forge=forge)
    assert intake._approval_covers_head(*args, {"headRefOid": "old"}, labels, forge=forge)
    assert forge.mock_calls == []
    assert not intake._approval_covers_head(*args, {"headRefOid": "new"}, labels, forge=forge)
    assert [c[0] for c in forge.mock_calls] == ["comment_pages", "create_comment", "relabel_pr"]
    forge.relabel_pr.assert_called_once_with("o/r", 7, add=[], remove=["spec:approved"])
    assert "<!-- nightshift:approval:change:2 -->" in forge.create_comment.call_args.args[-1]
    assert json.loads((tmp_path / "approvals/o__r/7.json").read_text()) == {"event": 2, "head": "old"}


@pytest.mark.scenario("forge-interface/readiness-request-budget/cached-and-ci-blocked-polls")
@pytest.mark.parametrize("supplied_forge", [False, True])
@pytest.mark.parametrize("response,reason", [
    ("not JSON", "malformed check results"), (None, "malformed check results"),
    ("null", "malformed check results"), ("{}", "malformed check results"),
    ("[null]", "malformed check results"), ("[{}]", "malformed check results"),
    ('[{"name":"CI","bucket":"pass","state":null}]', "malformed check results"),
    ('[{"name":" ","bucket":"pass","state":"DONE"}]', "malformed check results"),
    ("[]", "checks are not green; require at least one pass and only pass or skipping buckets"),
    ('[{"name":"CI","bucket":"fail","state":"DONE"}]',
     "checks are not green; require at least one pass and only pass or skipping buckets"),
    ('[{"name":"CI","bucket":"pass","state":"DONE"}]', None),
    (OSError("unavailable"), "checks unavailable; green CI could not be established"),
    (queue.GhError("bad JSON upstream", transient=False), "checks unavailable; green CI could not be established"),
    (subprocess.TimeoutExpired("gh", 30), "checks unavailable; green CI could not be established"),
    (Paused(), "paused"),
])
def test_readiness_ci_forge_preserves_classification(monkeypatch, response, reason, supplied_forge):
    monkeypatch.setattr(queue, "_gh", Mock(side_effect=AssertionError("unbound runner")))
    runner = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
    forge = Mock(spec_set=Forge)
    if isinstance(response, Exception):
        forge.pr_checks.side_effect = response
    else:
        forge.pr_checks.return_value = ResponseText(response)
    kwargs = {"forge": forge} if supplied_forge else {"runner": runner}
    if supplied_forge:
        monkeypatch.setattr(intake, "GitHubForge", Mock(side_effect=AssertionError("unexpected backend")))
    if reason == "paused":
        with pytest.raises(Paused) as caught:
            intake._readiness_ci(Repo("o/r"), 7, **kwargs)
        assert caught.value is response
    else:
        assert intake._readiness_ci(Repo("o/r"), 7, **kwargs) == ([] if reason is None else [f"CI: {reason}"])
    assert forge.mock_calls == ([call.pr_checks("o/r", 7)] if supplied_forge else [])
    assert runner.call_args_list == ([] if supplied_forge else [
        call(["pr", "checks", "7", "--repo", "o/r", "--json", "name,bucket,state"])])


@pytest.mark.scenario("forge-interface/late-bound-runners/default-runner-remains-patchable")
@pytest.mark.scenario("forge-interface/readiness-request-budget/cached-and-ci-blocked-polls")
@pytest.mark.parametrize("entry", ["publish", "reconcile"])
@pytest.mark.parametrize("blocker", [None, "human", "CI"])
def test_readiness_each_poll_binds_runner_and_preserves_budget(monkeypatch, polling, tmp_path, entry, blocker):
    e = polling
    if blocker == "human":
        e.gh.pr["body"] = "## Checks a human must make\n- [ ] manual check"
    elif blocker == "CI":
        e.gh.checks = []
    e.poll()
    saved, publication = e.path.read_bytes(), e.publication_path.read_bytes()
    forbidden = Mock(side_effect=AssertionError("unbound transport"))
    status = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({"version": 1, "runs": [e.row]}), ""))
    for attempt in range(3):
        e.gh.operations.clear()

        def dispatch(args):
            monkeypatch.setattr(queue, "_gh", forbidden)
            return e.gh(args)

        runner = Mock(side_effect=dispatch)
        monkeypatch.setattr(queue, "_gh", runner if attempt < 2 else forbidden)
        forge = Mock(spec_set=Forge, wraps=GitHubForge(runner))
        constructor = Mock(return_value=forge)
        monkeypatch.setattr(intake, "GitHubForge", constructor)
        kwargs = {"runner": runner} if attempt == 2 else {}
        if entry == "publish":
            assert intake.publish_pending(e.repo, queue.Labels(), tmp_path, specloop_runner=status, **kwargs) == []
        else:
            intake._reconcile_merge_readiness(e.repo, queue.Labels(), tmp_path, 1, e.row, e.publication, **kwargs)
        constructor.assert_called_once_with(runner)
        assert forge.mock_calls == [call.pr_for_readiness("owner/repo", 1)] + (
            [call.pr_checks("owner/repo", 1)] if blocker == "CI" else [])
        assert runner.call_args_list == [call([
            "pr", "view", "1", "--repo", "owner/repo", "--json",
            "state,labels,headRefName,headRefOid,isCrossRepository,url,body",
        ])] + ([call(["pr", "checks", "1", "--repo", "owner/repo", "--json", "name,bucket,state"])]
               if blocker == "CI" else [])
        assert e.path.read_bytes() == saved and e.publication_path.read_bytes() == publication
    forbidden.assert_not_called()
    assert status.call_args_list == ([call(["specloop", "status", "--json"],
        capture_output=True, text=True, check=False)] * 3 if entry == "publish" else [])


@pytest.mark.scenario("forge-interface/readiness-request-budget/readiness-delivery-unchanged")
@pytest.mark.parametrize("case", ["head", "pending-head", "inconsistent", "incomplete", "ci-green"])
def test_readiness_delivery_through_one_forge(monkeypatch, polling, case):
    e = polling
    forge = Mock(spec_set=Forge, wraps=GitHubForge(e.gh))
    constructor = Mock(return_value=forge)
    monkeypatch.setattr(intake, "GitHubForge", constructor)
    # Retain the existing exact transcripts, receipt, local-state and comment assertions.
    if case in ("head", "pending-head"):
        polling_tests.test_new_head_removes_ready_label(e, case == "pending-head")
    elif case == "inconsistent":
        polling_tests.test_label_or_identity_change_refreshes_assessment(e, "both")
    elif case == "incomplete":
        polling_tests.test_incomplete_same_head_delivery_reuses_intention(e, monkeypatch)
    else:
        polling_tests.test_ci_blocked_assessment_is_reassessed_until_green(e)
    assert constructor.call_count == len(e.calls)
    operations = [c[0] for c in forge.mock_calls]
    assert operations.count("pr_for_readiness") == len(e.calls)
    assert set(operations) <= {"pr_for_readiness", "pr_checks", "relabel_pr",
                               "comment_pages", "create_comment", "update_comment"}


@pytest.mark.scenario("forge-interface/late-bound-runners/default-runner-remains-patchable")
@pytest.mark.scenario("issue-intake/forge-mediated-intake/existing-workflows-retain-transcripts")
@pytest.mark.parametrize("refused", [False, True])
def test_publication_forge_late_binding_and_recovery(monkeypatch, publication_setup, tmp_path, refused):
    repo, gh, commands = publication_setup
    if refused:
        commands.publish_result = {"version": 1, "outcome": "refused", "reasons": ["held"]}
    forbidden = Mock(side_effect=AssertionError("unbound transport"))
    path = tmp_path / "publications/owner__sample/8.json"
    for attempt in range(3):
        def dispatch(args):
            monkeypatch.setattr(queue, "_gh", forbidden)
            result = gh(args)
            if attempt < 2 and (args[:2] == ["pr", "comment"] or "PATCH" in args):
                raise queue.GhError("response lost", transient=True)
            return result

        runner = Mock(side_effect=dispatch)
        monkeypatch.setattr(queue, "_gh", runner if attempt < 2 else forbidden)
        forge = Mock(spec_set=Forge, wraps=GitHubForge(runner))
        constructor = Mock(return_value=forge)
        monkeypatch.setattr(intake, "GitHubForge", constructor)
        errors = []
        intake.publish_pending(repo, queue.Labels(), tmp_path, specloop_runner=commands,
            on_error=lambda n, exc: errors.append(exc), **({"runner": runner} if attempt == 2 else {}))
        constructor.assert_called_once_with(runner)
        receipt = json.loads(path.read_text())
        assert bool(receipt.get("delivered")) is (attempt == 2)
        assert bool(errors) is (attempt < 2)
        body = gh.comments[-1]["body"]
        expected = [call.pr_for_publication(repo.name, 8), call.comment_pages(repo.name, 8),
                    call.create_comment(repo.name, 8, "pr", body) if attempt == 0 else
                    call.update_comment(repo.name, gh.comments[-1]["id"], body)]
        if refused and attempt == 2:
            expected += [call.relabel_pr(repo.name, 8, add=["needs-human"], remove=[])]
        assert forge.mock_calls == expected
        comment_id = gh.comments[-1]["id"]
        argv = [
            ["pr", "view", "8", "--repo", "owner/sample", "--json",
             "state,labels,headRefName,isCrossRepository,url"],
            ["api", "repos/owner/sample/issues/8/comments", "--paginate", "--slurp"],
            ["pr", "comment", "8", "--repo", "owner/sample", "--body", body] if attempt == 0 else
            ["api", f"repos/owner/sample/issues/comments/{comment_id}", "--method", "PATCH", "-f", f"body={body}"],
        ]
        if refused and attempt == 2:
            argv += [["pr", "edit", "8", "--repo", "owner/sample", "--add-label", "needs-human"]]
        assert runner.call_args_list == [call(args) for args in argv]
    forbidden.assert_not_called()
    assert len(commands.publications) == 1
    assert not (tmp_path / "merge-readiness/owner__sample/8.json").exists()


@pytest.fixture
def recovery_forges(monkeypatch):
    forges = []

    def construct(runner):
        forge = Mock(spec_set=Forge, wraps=GitHubForge(runner))
        forges.append(forge)
        return forge

    monkeypatch.setattr(daemon, "GitHubForge", construct)
    return forges


@pytest.mark.scenario("forge-interface/late-bound-runners/default-runner-remains-patchable")
def test_daemon_recovery_binds_each_invocation(monkeypatch, switched, tmp_path, recovery_forges):
    forbidden = Mock(side_effect=AssertionError("unbound transport"))
    for attempt in range(3):
        number = attempt + 1
        claim = recovery_tests.working(switched, number, tmp_path, survives=True)
        runner = Mock(side_effect=switched.gh)
        monkeypatch.setattr(queue, "_gh", runner if attempt < 2 else forbidden)
        daemon._reconcile_engine_switch(switched.repo, queue.Labels(),
                                       **({"runner": runner} if attempt == 2 else {}))
        forge = recovery_forges[-1]
        assert [c[0] for c in forge.mock_calls] == [
            "list_issues", "issue_labels", "comment_pages", "issue_labels",
            "create_comment", "issue_labels", "relabel_issue"]
        assert runner.call_count == 7 and not claim.path.exists()
        assert recovery_tests.labels(switched, number) == {"needs-human", "keep-me"}
        assert json.loads((daemon.STATE_DIR / f"engine-switch/owner__switched/{number}.json").read_text())["done"]
    assert len(recovery_forges) == len(outcomes.tasks()) == 3
    forbidden.assert_not_called()


@pytest.mark.scenario("forge-interface/guarded-daemon-calls/engine-switch-recovery-unchanged")
def test_recovery_preserves_existing_journals_and_order(switched, tmp_path, monkeypatch, recovery_forges):
    recovery_tests.test_engine_switch_recovers_working_issues(switched, tmp_path, monkeypatch)
    assert {c[0] for forge in recovery_forges for c in forge.mock_calls} == {
        "list_issues", "issue_labels", "comment_pages", "create_comment", "relabel_issue"}


@pytest.mark.scenario("forge-interface/guarded-daemon-calls/engine-switch-recovery-unchanged")
@pytest.mark.scenario("forge-interface/forge-failure-compatibility/delegated-helper-error-fallbacks")
@pytest.mark.parametrize("response", [*ERRORS, "{}", '{"labels":null}', '{"labels":{}}'])
def test_recovery_label_failure_cannot_authorize_cleanup(switched, tmp_path, recovery_forges, response):
    claim = recovery_tests.working(switched, 1, tmp_path, survives=True)
    before = claim.path.read_bytes()

    def dispatch(args):
        if args[:2] == ["issue", "view"]:
            if isinstance(response, Exception):
                raise response
            return response
        return switched.gh(args)

    runner = Mock(side_effect=dispatch)
    with pytest.raises(type(response) if isinstance(response, Exception) else ValueError) as caught:
        daemon._reconcile_engine_switch(switched.repo, queue.Labels(), runner=runner)
    if isinstance(response, Exception):
        assert caught.value is response
    assert runner.call_count == 2
    assert recovery_forges[0].mock_calls == [
        call.list_issues(switched.repo.name, "agent:working"), call.issue_labels(switched.repo.name, 1)]
    assert claim.path.read_bytes() == before and not outcomes.tasks()
    assert not recovery_tests.recovery_path().exists()
    assert recovery_tests.labels(switched, 1) == {"agent:working", "keep-me"}


@pytest.mark.scenario("forge-interface/guarded-daemon-calls/pause-during-dispatch-and-retry")
@pytest.mark.parametrize("boundary", ["intent", "comments-read", "comment", "edit"])
def test_forge_recovery_pause_retains_intent(switched, tmp_path, monkeypatch, recovery_forges, boundary):
    recovery_tests.test_pause_between_recovery_operations_retains_intent(switched, tmp_path, monkeypatch, boundary)
    assert recovery_forges and any(f.create_comment.called for f in recovery_forges)


@pytest.mark.scenario("forge-interface/guarded-daemon-calls/pause-during-dispatch-and-retry")
@pytest.mark.parametrize("stage", ["label_retry", "comment_retry", "lookup_retry", "patch_retry",
                                  "after_lookup", "after_absent_lookup"])
def test_forge_nested_retries_keep_poll_guard(publication_setup, monkeypatch, tmp_path, stage):
    constructor = Mock(side_effect=GitHubForge)
    monkeypatch.setattr(intake, "GitHubForge", constructor)
    publication_tests.test_pause_stops_real_gh_retries_and_comment_reconciliation(
        publication_setup, monkeypatch, tmp_path, stage)
    assert constructor.called
    assert all(c.args[0] is not queue._gh for c in constructor.call_args_list)


@pytest.mark.scenario("forge-interface/guarded-daemon-calls/pause-during-dispatch-and-retry")
@pytest.mark.parametrize("boundary", ["before", "accepted"])
def test_error_reporting_retains_guard(monkeypatch, publication_setup, boundary):
    repo, gh, commands = publication_setup
    writes = []

    def dispatch(args):
        if args[:2] == ["issue", "comment"]:
            writes.append(args)
            daemon.PAUSE_FILE.touch()
            return ""
        return gh(args)

    def fail_item(*args, on_error, **kwargs):
        if boundary == "before":
            daemon.PAUSE_FILE.touch()
        on_error(7, intake.DeliveryError("held"))

    monkeypatch.setattr(intake, "propose_pending", fail_item)
    constructor = Mock(side_effect=GitHubForge)
    monkeypatch.setattr(intake, "GitHubForge", constructor)
    runner = Mock(side_effect=dispatch)
    daemon.poll_specloop(Config(repos=[repo]), daemon.RepoHealth(), runner=runner, specloop_runner=commands)
    assert constructor.call_count == 1 and constructor.call_args.args[0] is not runner
    assert len(writes) == (boundary == "accepted")
    assert [c.args[0][:2] for c in runner.call_args_list] == [["issue", "list"]] + (
        [["api", "repos/owner/sample/issues/7/comments"], ["issue", "comment"]]
        if boundary == "accepted" else [])
