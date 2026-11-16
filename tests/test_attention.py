import json

import pytest

from nightshift import outcomes
from nightshift.config import Config, ForgeConfig, Labels, Repo

PREFIX = "**nightshift** — "


class Recorder:
    """Answers literal `gh api` reads by path; records every call."""

    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def __call__(self, args):
        self.calls.append(list(args))
        assert args[0] == "api" and args[2:] == ["--paginate", "--slurp"], args
        answer = self.responses[args[1]]
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)


def listing(repo, label="needs-human"):
    return f"repos/{repo}/issues?state=open&labels={label}&per_page=100"


def comments(repo, number):
    return f"repos/{repo}/issues/{number}/comments?per_page=100"


def row(repo, number, title, pr=False):
    kind = "pull" if pr else "issues"
    data = {"number": number, "title": title, "state": "open",
            "html_url": f"https://github.com/{repo}/{kind}/{number}"}
    if pr:
        data["pull_request"] = {"url": "x"}
    return data


def escalation(headline, action, identity="change-1", id=1, updated="2026-11-01T00:00:00Z", **extra):
    body = f"{PREFIX}{headline}\n\ndetails\n\n<!-- nightshift:{action}:{identity} -->"
    return {"id": id, "body": extra.pop("body", body), "updated_at": updated,
            "created_at": "2026-10-01T00:00:00Z", **extra}


@pytest.mark.scenario("attention/front-door-escalations/labelled-issues-and-prs-listed")
def test_listing_pages_kinds_links_and_order_with_custom_label():
    """attention/front-door-escalations/labelled-issues-and-prs-listed"""
    cfg = Config(repos=[Repo("o/zeta"), Repo("o/alpha")], labels=Labels(needs_human="wait on me"))
    run = Recorder({
        listing("o/alpha", "wait%20on%20me"): [[row("o/alpha", 9, "PR nine", pr=True)],
                                               [row("o/alpha", 3, "Issue three")]],
        listing("o/zeta", "wait%20on%20me"): [[]],
    })
    result = outcomes._collect_listings(cfg, run)
    assert [r["repo"] for r in result] == ["o/alpha", "o/zeta"]
    assert result[0] == {"repo": "o/alpha", "label": "wait on me", "error": None, "error_code": None,
                         "entries": [
        {"repo": "o/alpha", "number": 3, "kind": "issue", "title": "Issue three",
         "url": "https://github.com/o/alpha/issues/3"},
        {"repo": "o/alpha", "number": 9, "kind": "pr", "title": "PR nine",
         "url": "https://github.com/o/alpha/pull/9"}]}
    assert result[1]["entries"] == [] and result[1]["error"] is None
    # Listing never reads comments; enrichment is a separate step.
    assert [c[1] for c in run.calls] == [listing("o/alpha", "wait%20on%20me"),
                                         listing("o/zeta", "wait%20on%20me")]


@pytest.mark.parametrize("bad", [
    {"html_url": "https://github.com/o/r/pull/3"},           # wrong kind
    {"html_url": "https://github.com/o/other/issues/3"},     # wrong repo
    {"html_url": "https://u:p@github.com/o/r/issues/3"},     # credentials
    {"html_url": "https://github.com/o/r/issues/3?x=1"},     # query
    {"html_url": "https://github.com/o/r/issues/3#c"},       # fragment
    {"html_url": "http://github.com/o/r/issues/3"},
    {"number": 0}, {"number": True}, {"title": None}, {"state": "closed"},
])
def test_malformed_listing_rows_fail_the_listing_not_empty(bad):
    run = Recorder({listing("o/r"): [[{**row("o/r", 3, "t"), **bad}]]})
    [status] = outcomes._collect_listings(Config(repos=[Repo("o/r")]), run)
    assert status["entries"] is None and status["error_code"] == "invalid_response"


@pytest.mark.scenario("attention/front-door-escalations/complete-listing-removes-resolved-entries")
def test_complete_multi_page_listing_and_safe_failures():
    """attention/front-door-escalations/complete-listing-removes-resolved-entries"""
    cfg = Config(repos=[Repo("o/r")])
    pages = [[row("o/r", n, f"t{n}") for n in range(1, 101)], [row("o/r", 101, "last")]]
    [status] = outcomes._collect_listings(cfg, Recorder({listing("o/r"): pages}))
    assert [e["number"] for e in status["entries"]] == list(range(1, 102))
    # A complete empty result is an empty listing, distinct from a failure.
    [empty] = outcomes._collect_listings(cfg, Recorder({listing("o/r"): [[]]}))
    assert empty["entries"] == [] and empty["error"] is None
    secret = "token ghp_SECRET leaked"
    for answer, code in [(RuntimeError(secret), "read_failed"), ("{not json", "invalid_response"),
                         ([{"number": 1}], "invalid_response"), ({"pages": []}, "invalid_response"),
                         ([[row("o/r", 1, "a")], [row("o/r", 1, "a")]], "invalid_response"),
                         (outcomes._TimedOut(secret), "timeout"),
                         (outcomes._BudgetExhausted(secret), "budget_exhausted")]:
        [failed] = outcomes._collect_listings(cfg, Recorder({listing("o/r"): answer}))
        assert failed["entries"] is None and failed["error_code"] == code
        assert failed["error"] == outcomes.SAFE_ERRORS[code] and "SECRET" not in failed["error"]


@pytest.mark.scenario("attention/front-door-escalations/unsupported-enrollment-is-visible")
def test_non_github_enrollment_is_unsupported_without_querying_github():
    """attention/front-door-escalations/unsupported-enrollment-is-visible"""
    gitea = ForgeConfig(name="tea", kind="gitea", url="https://tea.example",
                        token_env="T", land_token_env="L", bot_login="bot")
    ghe = ForgeConfig(name="ghe", kind="github", url="https://ghe.example")
    cfg = Config(repos=[Repo("o/r", forge="tea"), Repo("o/s", forge="ghe"), Repo("o/t")],
                 forges=[gitea, ghe])
    run = Recorder({listing("o/t"): [[]]})
    result = outcomes._collect_listings(cfg, run)
    assert [(r["repo"], r["entries"], r["error_code"]) for r in result] == [
        ("o/r", None, "unsupported_forge"), ("o/s", None, "unsupported_forge"), ("o/t", [], None)]
    assert [c[1] for c in run.calls] == [listing("o/t")]


@pytest.mark.scenario("attention/escalation-comment-summary/most-recent-escalation-supplies-summary")
def test_latest_qualifying_escalation_supplies_summary_across_pages():
    """attention/escalation-comment-summary/most-recent-escalation-supplies-summary"""
    entry = {"repo": "o/r", "number": 7}
    pages = [
        [escalation("The spec draft needs human input.", "draft", id=1,
                    updated="2026-11-05T00:00:00Z"),  # older comment, updated later
         escalation("Admission refused. Human input is needed.", "admit", id=2,
                    updated="2026-11-02T00:00:00Z")],
        [escalation("Revision needs human input.", "revise", "c:round:2", id=3,
                    updated="2026-11-03T00:00:00Z"),
         escalation("Publication refused. Human input is needed.", "publish", id=4,
                    updated="2026-11-04T00:00:00Z"),
         {"id": 9, "body": "thanks, looking now <b>bold</b>", "updated_at": "2026-11-09T00:00:00Z"},
         {"id": 10, "body": f"{PREFIX}Published.\n<!-- nightshift:publish:c -->",
          "updated_at": "2026-11-09T00:00:00Z"}],
    ]
    run = Recorder({comments("o/r", 7): pages})
    result = outcomes._enrich_summary(entry, run, clock=lambda: "T1")
    assert result == {"summary": f"{PREFIX}The spec draft needs human input.",
                      "summary_checked_at": "T1", "summary_stale": False,
                      "summary_error": None, "summary_error_code": None}
    # Timestamp ties break on numeric comment ID; created_at only when updated is absent.
    tie = [[escalation("Specloop verify-2 failed. Human input is needed.", "verify-2-error", id=12,
                       updated="2026-11-05T00:00:00+00:00"),
            escalation("Specloop impl failed. Human input is needed.", "impl-error", id=11,
                       updated="2026-11-05T00:00:00Z"),
            {**escalation("Revision needs human input.", "revise", id=99, updated=None),
             "created_at": "2026-11-01T00:00:00Z"}]]
    assert outcomes._read_summary("o/r", 7, Recorder({comments("o/r", 7): tie})) == \
        f"{PREFIX}Specloop verify-2 failed. Human input is needed."
    # Plain text, capped at 400 characters including the ellipsis.
    long_stage = "s" * 500
    capped = outcomes._read_summary("o/r", 7, Recorder({comments("o/r", 7): [[escalation(
        f"Specloop {long_stage} failed. Human input is needed.", f"{long_stage}-error")]]}))
    assert len(capped) == 400 and capped.endswith("…") and capped.startswith(PREFIX)


@pytest.mark.scenario("attention/escalation-comment-summary/ambiguous-comment-does-not-invent-a-summary")
@pytest.mark.parametrize("body", [
    "see `**nightshift** — Revision needs human input. <!-- nightshift:revise:c -->`",
    f"  {PREFIX}Revision needs human input.\n<!-- nightshift:revise:c -->",
    f"{PREFIX}Revision needs human input.\n  <!-- nightshift:revise:c -->",
    f"{PREFIX}Revision needs human input.\n<!-- nightshift:admit:c -->",
    f"{PREFIX}Revision needs human input.\n<!-- nightshift:revise:c -->\n \n",
    f"{PREFIX}Revision needs human input. \n<!-- nightshift:revise:c -->",
    f"```\n{PREFIX}Revision needs human input.\n<!-- nightshift:revise:c -->\n```",
    f"{PREFIX}Revision needs human input.\n<!-- nightshift:revise:c d -->",
    f"{PREFIX}Revision needs human input.\n<!-- nightshift:revise: -->",
    f"{PREFIX}Specloop a b failed. Human input is needed.\n<!-- nightshift:a b-error:c -->",
    f"{PREFIX}Revision needs human input.\r\r\n<!-- nightshift:revise:c -->",
    f"{PREFIX}Revision needs human input.",
    None,
])
def test_ambiguous_comments_refuse_summary(body):
    """attention/escalation-comment-summary/ambiguous-comment-does-not-invent-a-summary"""
    run = Recorder({comments("o/r", 1): [[{"id": 1, "body": body, "updated_at": "garbage"}]]})
    result = outcomes._enrich_summary({"repo": "o/r", "number": 1}, run, clock=lambda: "T")
    assert result["summary"] is None and result["summary_checked_at"] == "T"
    assert result["summary_error"] is None and result["summary_stale"] is False


@pytest.mark.scenario("attention/escalation-comment-summary/ambiguous-comment-does-not-invent-a-summary")
def test_physical_line_rule_and_safe_summary_failures():
    """attention/escalation-comment-summary/ambiguous-comment-does-not-invent-a-summary"""
    crlf = f"{PREFIX}Revision needs human input.\r\nbody\r\n<!-- nightshift:revise:c:round:1 -->\r\n\r\n\n"
    assert outcomes._escalation_headline(crlf) == f"{PREFIX}Revision needs human input."
    # Malformed recency on a qualifying comment fails only that item's read.
    for meta in [{"updated_at": "yesterday"}, {"updated_at": None, "created_at": None},
                 {"updated_at": "2026-11-01T00:00:00"}, {"id": "7"}]:
        bad = {**escalation("Revision needs human input.", "revise"), **meta}
        run = Recorder({comments("o/r", 1): [[bad]]})
        result = outcomes._enrich_summary({"repo": "o/r", "number": 1}, run)
        assert result == {"summary": None, "summary_checked_at": None, "summary_stale": True,
                          "summary_error": outcomes.SAFE_ERRORS["invalid_response"],
                          "summary_error_code": "invalid_response"}
    failed = outcomes._enrich_summary({"repo": "o/r", "number": 1}, Recorder(
        {comments("o/r", 1): RuntimeError("stderr with ghp_SECRET")}))
    assert failed["summary_error_code"] == "read_failed" and "SECRET" not in failed["summary_error"]
