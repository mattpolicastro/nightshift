import json
import threading

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


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Tracked(Recorder):
    """A recorder that measures reads in flight and can run a hook per path."""

    def __init__(self, responses, hooks=None):
        super().__init__(responses)
        self.hooks, self.lock, self.active, self.peak = hooks or {}, threading.Lock(), 0, 0

    def __call__(self, args):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if args[1] in self.hooks:
                self.hooks[args[1]]()
            return super().__call__(args)
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture
def stamps(monkeypatch):
    counter = iter(f"2026-11-15T00:{n // 60:02d}:{n % 60:02d}+00:00" for n in range(3600))
    monkeypatch.setattr(outcomes, "now", lambda: next(counter))
    monkeypatch.setattr(outcomes.subprocess, "run",
                        lambda *a, **k: pytest.fail("injected runner bypassed"))


def snap(cfg, responses, **kwargs):
    return {r["repo"]: r for r in outcomes.front_door_snapshot(cfg, runner=Recorder(responses), **kwargs)}


def entry_map(status):
    return {e["number"]: e for e in status["entries"]}


ESC = escalation("Revision needs human input.", "revise")
HEAD = f"{PREFIX}Revision needs human input."


@pytest.mark.scenario("attention/escalation-comment-summary/failed-summary-does-not-hide-listing-changes")
def test_sibling_comment_failures_do_not_hide_listing_changes(stamps):
    """attention/escalation-comment-summary/failed-summary-does-not-hide-listing-changes"""
    cfg = Config(repos=[Repo("o/r")])
    first = snap(cfg, {listing("o/r"): [[row("o/r", 1, "one"), row("o/r", 2, "two")]],
                       comments("o/r", 1): [[ESC]], comments("o/r", 2): [[]]})["o/r"]
    kept_at = entry_map(first)[1]["summary_checked_at"]
    assert entry_map(first)[1]["summary"] == HEAD and not first["stale"]
    malformed = {**ESC, "updated_at": "later"}
    second = snap(cfg, {
        listing("o/r"): [[row("o/r", 1, "one"), row("o/r", 3, "new", pr=True),
                          row("o/r", 4, "four"), row("o/r", 5, "five")]],
        comments("o/r", 1): RuntimeError("ghp_SECRET stderr"),
        comments("o/r", 3): [[{**ESC, "body": ESC["body"] + "x" * outcomes.COMMENT_BYTES_LIMIT}]],
        comments("o/r", 4): [[malformed]],
        comments("o/r", 5): [[ESC]]})["o/r"]
    items = entry_map(second)
    assert sorted(items) == [1, 3, 4, 5]  # #2 closed and is gone; #3 is new
    assert second["checked_at"] > first["checked_at"] and second["stale"]
    assert second["error_code"] == "read_failed" and "SECRET" not in json.dumps(second)
    assert items[1]["summary"] == HEAD and items[1]["summary_checked_at"] == kept_at
    assert items[1]["summary_stale"] and items[1]["summary_error_code"] == "read_failed"
    for cold in (3, 4):  # cold failures display as unavailable, never hidden
        assert items[cold]["summary"] is None and items[cold]["summary_checked_at"] is None
        assert items[cold]["summary_error_code"] == "invalid_response"
    assert items[5]["summary"] == HEAD and not items[5]["summary_stale"]
    # A later successful read clears the error with the same listing.
    third = snap(cfg, {listing("o/r"): [[row("o/r", n, "t", pr=n == 3) for n in (1, 3, 4, 5)]],
                       **{comments("o/r", n): [[]] for n in (1, 3, 4)},
                       comments("o/r", 5): [[ESC]]})["o/r"]
    assert not third["stale"] and third["error"] is None
    assert all(e["summary_error"] is None and not e["summary_stale"] for e in third["entries"])
    assert entry_map(third)[1]["summary"] is None  # successful read, no qualifying comment


@pytest.mark.scenario("attention/independent-repository-snapshots/unreadable-repository-is-stale-not-empty")
def test_unreadable_repository_is_stale_while_healthy_one_updates(stamps):
    """attention/independent-repository-snapshots/unreadable-repository-is-stale-not-empty"""
    cfg = Config(repos=[Repo("o/a"), Repo("o/b")])
    cold = snap(cfg, {listing("o/a"): RuntimeError("down"), listing("o/b"): [[]]})
    assert cold["o/a"]["entries"] == [] and cold["o/a"]["checked_at"] is None
    assert cold["o/a"]["stale"] and cold["o/a"]["error_code"] == "read_failed"
    assert not cold["o/b"]["stale"] and cold["o/b"]["checked_at"]
    warm = snap(cfg, {listing("o/a"): [[row("o/a", 1, "x")]], comments("o/a", 1): [[]],
                      listing("o/b"): [[]]})
    failed = snap(cfg, {listing("o/a"): outcomes._TimedOut("t"),
                        listing("o/b"): [[row("o/b", 2, "y")]], comments("o/b", 2): [[]]})
    assert [e["number"] for e in failed["o/a"]["entries"]] == [1]
    assert failed["o/a"]["checked_at"] == warm["o/a"]["checked_at"]
    assert failed["o/a"]["stale"] and failed["o/a"]["error_code"] == "timeout"
    assert [e["number"] for e in failed["o/b"]["entries"]] == [2] and not failed["o/b"]["stale"]


TRUNCATED = '[[{"number": 1, "title": "a", "state": "open"}], [{"num'


@pytest.mark.scenario("attention/independent-repository-snapshots/incomplete-read-preserves-complete-snapshot")
@pytest.mark.parametrize("later_page, code", [
    (RuntimeError("page 2 failed"), "read_failed"),
    (TRUNCATED, "invalid_response"),
    ([[row("o/r", 1, "a")], [{"number": 5}]], "invalid_response"),
])
def test_incomplete_listing_never_partially_replaces(stamps, later_page, code):
    """attention/independent-repository-snapshots/incomplete-read-preserves-complete-snapshot"""
    cfg = Config(repos=[Repo("o/r")])
    good = snap(cfg, {listing("o/r"): [[row("o/r", 1, "a")], [row("o/r", 2, "b")]],
                      comments("o/r", 1): [[]], comments("o/r", 2): [[ESC]]})["o/r"]
    after = snap(cfg, {listing("o/r"): later_page})["o/r"]
    assert after["entries"] == good["entries"] and after["checked_at"] == good["checked_at"]
    assert after["stale"] and after["error_code"] == code


@pytest.mark.scenario("attention/independent-repository-snapshots/offline-recovery-and-selection-isolation")
def test_offline_recovery_and_selection_isolation(stamps):
    """attention/independent-repository-snapshots/offline-recovery-and-selection-isolation"""
    cfg = Config(repos=[Repo("o/r"), Repo("o/gone")])
    snap(cfg, {listing("o/r"): [[row("o/r", 1, "a")]], comments("o/r", 1): [[ESC]],
               listing("o/gone"): [[]]})
    failed = snap(cfg, {listing("o/r"): RuntimeError("x"), listing("o/gone"): [[]]})["o/r"]
    offline = snap(cfg, {}, offline=True)["o/r"]  # Recorder({}) would fail any read
    assert offline == failed and offline["error_code"] == "read_failed"
    assert offline["entries"][0]["summary"] == HEAD and offline["entries"][0]["summary_checked_at"]
    recovered = snap(cfg, {listing("o/r"): [[]], listing("o/gone"): [[]]})["o/r"]
    assert recovered["entries"] == [] and not recovered["stale"] and recovered["error"] is None
    # Removed, relabelled, re-forged and never-checked selections share nothing.
    ghcom = ForgeConfig(name="hub", kind="github")
    for changed in [Config(repos=[Repo("o/r")], labels=Labels(needs_human="other")),
                    Config(repos=[Repo("o/r", forge="hub")], forges=[ghcom]),
                    Config(repos=[Repo("o/r"), Repo("o/new")])]:
        result = snap(changed, {}, offline=True)
        assert "o/gone" not in result
        fresh = result.get("o/new") or result["o/r"]
        assert fresh["entries"] == [] and fresh["checked_at"] is None
        assert fresh["stale"] and fresh["error_code"] == "not_checked"


@pytest.mark.scenario("attention/independent-repository-snapshots/empty-enrollment-leaves-state-untouched")
def test_empty_enrollment_returns_before_opening_the_cache(monkeypatch):
    """attention/independent-repository-snapshots/empty-enrollment-leaves-state-untouched"""
    monkeypatch.setattr(outcomes, "database", lambda: pytest.fail("cache opened"))
    monkeypatch.setattr(outcomes.subprocess, "run", lambda *a, **k: pytest.fail("subprocess"))
    for offline in (False, True):
        assert outcomes.front_door_snapshot(Config(repos=[]), offline=offline,
                                            runner=lambda a: pytest.fail("read")) == []
    assert not outcomes.DB_PATH.exists()


@pytest.mark.scenario("attention/bounded-attention-reads/deadline-includes-pagination-and-comments")
def test_shared_deadline_defers_unstarted_reads(stamps):
    """attention/bounded-attention-reads/deadline-includes-pagination-and-comments"""
    names = [f"o/r{n}" for n in range(5)]
    cfg = Config(repos=[Repo(n) for n in names])
    seeded = snap(cfg, {**{listing(n): [[row(n, 1, "old")]] for n in names},
                        **{comments(n, 1): [[ESC]] for n in names}})
    clock, barrier = Clock(), threading.Barrier(4, timeout=5)

    def stall():  # the first four listings start together, then exhaust the budget
        barrier.wait()
        clock.t = outcomes.REFRESH_SECONDS + 1

    run = Tracked({listing(n): [[row(n, 2, "new")]] for n in names},
                  {listing(n): stall for n in names[:4]})
    result = {r["repo"]: r for r in outcomes.front_door_snapshot(cfg, runner=run, clock=clock)}
    assert run.peak == 4 and [c[1] for c in run.calls if "comments" in c[1]] == []
    assert sorted(c[1] for c in run.calls) == sorted(listing(n) for n in names[:4])
    for name in names[:4]:  # completed listings commit; their summaries are deferred
        [item] = result[name]["entries"]
        assert item["number"] == 2 and item["summary_error_code"] == "budget_exhausted"
        assert result[name]["checked_at"] != seeded[name]["checked_at"]
    late = result["o/r4"]
    assert late["entries"] == seeded["o/r4"]["entries"] and late["error_code"] == "budget_exhausted"
    assert late["checked_at"] == seeded["o/r4"]["checked_at"] and late["stale"]


@pytest.mark.scenario("attention/bounded-attention-reads/deadline-includes-pagination-and-comments")
def test_default_runner_bounds_each_subprocess_by_remaining_time(monkeypatch):
    """attention/bounded-attention-reads/deadline-includes-pagination-and-comments"""
    seen = []

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs["timeout"]))
        return outcomes.subprocess.CompletedProcess(argv, 0, "[[]]", "")
    monkeypatch.setattr(outcomes.subprocess, "run", fake_run)
    [status] = outcomes.front_door_snapshot(Config(repos=[Repo("o/r")]))
    assert status["entries"] == [] and not status["stale"]
    assert seen[0][0] == ["gh", "api", listing("o/r"), "--paginate", "--slurp"]
    assert 0 < seen[0][1] <= outcomes.REQUEST_SECONDS
    outcomes._read_github(["api", "x"], outcomes.time.monotonic() + 2)
    assert seen[-1][1] <= 2
    with pytest.raises(outcomes._BudgetExhausted):
        outcomes._read_github(["api", "x"], outcomes.time.monotonic() - 1)
    assert len(seen) == 2


@pytest.mark.scenario("attention/bounded-attention-reads/listings-precede-summary-enrichment")
def test_listings_precede_enrichment_without_a_repository_cap(stamps):
    """attention/bounded-attention-reads/listings-precede-summary-enrichment"""
    names = [f"o/r{n:02d}" for n in range(40)]
    cfg = Config(repos=[Repo(n) for n in names])
    clock = Clock()

    def slow():
        clock.t += 7
    run = Tracked({**{listing(n): [[row(n, 1, "x")]] for n in names},
                   **{comments(n, 1): [[ESC]] for n in names},
                   comments(names[0], 1): outcomes._TimedOut("slow thread")},
                  {comments(names[0], 1): slow})
    result = outcomes.front_door_snapshot(cfg, runner=run, clock=clock)
    paths = [c[1] for c in run.calls]
    assert len(paths) == 80 and all("comments" not in p for p in paths[:40])
    assert all("comments" in p for p in paths[40:]) and run.peak <= 4
    assert [r["repo"] for r in result] == names and all(r["checked_at"] for r in result)
    assert result[0]["entries"][0]["summary_error_code"] == "timeout" and result[0]["stale"]
    assert all(r["entries"][0]["summary"] == HEAD and not r["stale"] for r in result[1:])
