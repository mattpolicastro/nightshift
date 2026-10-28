"""Normal-operation assertions shared verbatim by fake and selected live cases."""

import json

import pytest

from contract.conftest import owned_issue
from nightshift.forge import Forge
from nightshift.queue import FeedbackItem, Issue


pytestmark = [pytest.mark.real_trust, pytest.mark.scenario(
    "gitea-forge/offline-fake-and-optional-live-contracts/fake-exercises-transport-behavior")]


@pytest.mark.scenario("gitea-forge/standalone-adapter/construction-and-interface")
@pytest.mark.scenario("gitea-forge/allowlist-identity-projection/allowlist-alone-determines-trust")
def test_issue_contract(contract):
    f, repo, e = contract.forge, contract.repo, contract.evidence
    assert isinstance(f, Forge)
    issues = f.list_issues(repo, e["label"])
    assert all(isinstance(issue, Issue) for issue in issues)
    assert e["issue"] in {issue.number for issue in issues}
    assert e["pr"] not in {issue.number for issue in issues}
    assert e["label"] in f.issue_labels(repo, e["issue"])
    assert f.issue_author(repo, e["issue"])["author_association"] == "OWNER"
    assert f.issue_for_draft(repo, e["issue"])["authorAssociation"] == "OWNER"
    comments = [r for p in f.comment_pages(repo, e["pr"]) for r in p]
    for row in comments:
        expected = "OWNER" if row["user"]["login"].casefold() == contract.trusted.casefold() else "NONE"
        assert row["author_association"] == expected
    draft = f.issue_for_draft(repo, e["pr"])
    assert all(r["author"]["login"].casefold() != contract.bot.casefold() for r in draft["comments"])
    assert {r["authorAssociation"] for r in draft["comments"]} == {"OWNER", "NONE"}


@pytest.mark.scenario("gitea-forge/issue-and-feedback-records/complete-identified-feedback")
@pytest.mark.scenario("gitea-forge/complete-endpoint-pagination/short-intermediate-page")
def test_feedback_contract(contract):
    f, repo, e = contract.forge, contract.repo, contract.evidence
    records = f.pr_feedback_records(repo, e["pr"])
    assert {source for source, _ in records} == {"conversation", "review", "inline"}
    assert sorted((source, row["body"].strip()) for source, row in records) == sorted([
        ("conversation", "Change contract"), ("conversation", ""),
        ("review", "Change contract"), ("inline", "Change contract")])
    assert len({(source, r["id"]) for source, r in records}) == len(records)
    assert any(not r["body"].strip() for _, r in records)
    expected = []
    for source, row in records:
        assert row["author_association"] == "OWNER"
        assert row["user"]["login"].casefold() == contract.trusted.casefold()
        assert row["submitted_at" if source == "review" else "created_at"]
        assert row["updated_at"]
        if source == "review":
            assert row["state"] == "COMMENTED"
        if source == "inline":
            assert (row["path"], row["line"]) == ("contract.txt", 1)
        if row["body"].strip():
            suffix = " on `contract.txt:1`" if source == "inline" else ""
            expected.append(FeedbackItem(f'{source}:{row["id"]}',
                f'**{row["user"]["login"]}{suffix}:**\n\nChange contract'))
    assert f.pr_feedback_items(repo, e["pr"]) == expected


def test_pr_evidence_contract(contract):
    f, repo, e = contract.forge, contract.repo, contract.evidence
    for method in ("pr_for_recovery", "pr_for_admission", "pr_for_revision",
                   "pr_for_publication", "pr_for_readiness"):
        view = getattr(f, method)(repo, e["pr"])
        assert view["state"] == "OPEN" and view["isDraft"] is True
        assert view["headRefName"] == "contract-head" and view["headRefOid"].lower() == e["sha"].lower()
        assert view["isCrossRepository"] is False
        assert f.pr_state_url(repo, view["url"]) == {"state": "OPEN", "url": view["url"]}
    assert f.latest_pr_number(repo, "contract-head") == e["pr"]
    assert e["pr"] in {r["number"] for r in f.list_open_prs(repo, e["label"])}
    assert json.loads(f.pr_checks(repo, e["pr"]).stdout) == [
        {"name": "Gitea combined status", "state": "success", "bucket": "pass"}]
    assert f.commit(repo, e["sha"])["sha"].lower() == e["sha"].lower()
    events = [r for p in f.label_event_pages(repo, e["issue"]) for r in p]
    assert [r["id"] for r in events] == sorted({r["id"] for r in events})
    assert all(r["author_association"] == "OWNER" for r in events)


def test_owned_mutations_contract(contract):
    f, repo, e = contract.forge, contract.repo, contract.evidence
    with owned_issue(contract) as (number, run):
        assert f.relabel_issue(repo, number, add=[e["label"]], remove=[]) is None
        assert f.issue_labels(repo, number) == {e["label"]}
        assert f.relabel_issue(repo, number, add=[], remove=[e["label"]]) is None
        assert f.issue_labels(repo, number) == set()
        marker = f"<!-- nightshift:draft:{run} -->"
        for body in ("First\n" + marker, "Updated λ\n" + marker):
            assert f.create_comment(repo, number, "issue", body) is None
        comments = [r for p in f.comment_pages(repo, number) for r in p]
        assert len(comments) == 1 and comments[0]["body"] == body
        assert comments[0]["user"]["login"].casefold() == contract.bot.casefold()
