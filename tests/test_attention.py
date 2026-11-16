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


REPO_KEYS = {"repo", "label", "entries", "checked_at", "stale", "error", "error_code"}
ENTRY_KEYS = {"repo", "number", "kind", "title", "url", "summary", "summary_checked_at",
              "summary_stale", "summary_error", "summary_error_code"}
PAYLOAD_KEYS = {"version", "offline", "tasks", "repositories", "specloop", "specloop_error", "stale"}


@pytest.fixture
def cli_attention(monkeypatch, capsys):
    """Run `nightshift attention` against a config and recorded GitHub answers."""
    from nightshift import cli

    monkeypatch.setattr(cli.config, "load_env_file", lambda: None)
    monkeypatch.setattr(cli.specloop_attention.specloop_client, "call",
                        lambda *a, **k: {"version": 1, "runs": []})
    state = {"recorder": Recorder({})}
    monkeypatch.setattr(outcomes, "_read_github", lambda args, deadline: state["recorder"](args))

    def run(cfg, responses=None, *, json_output=True, offline=False, code=0):
        monkeypatch.setattr(cli.config, "load", lambda path: cfg)
        state["recorder"] = Recorder(responses or {})
        argv = ["attention", *(["--json"] if json_output else []), *(["--offline"] if offline else [])]
        assert cli.main(argv) == code
        out = capsys.readouterr().out
        if not json_output:
            return out
        payload = json.loads(out)
        assert set(payload) == PAYLOAD_KEYS and payload["version"] == 1
        assert payload["offline"] is offline and payload["tasks"] == [] == payload["stale"]
        for repo in payload["repositories"]:
            assert set(repo) == REPO_KEYS
            assert all(set(e) == ENTRY_KEYS for e in repo["entries"])
        return payload

    run.recorder = lambda: state["recorder"]
    return run


@pytest.mark.scenario("attention/front-door-escalations/labelled-issues-and-prs-listed")
def test_attention_lists_needs_human_issues_and_prs(cli_attention, stamps):
    """attention/front-door-escalations/labelled-issues-and-prs-listed"""
    cfg = Config(repos=[Repo("o/zeta"), Repo("o/alpha")], labels=Labels(needs_human="wait on me"))
    responses = {
        listing("o/alpha", "wait%20on%20me"): [[row("o/alpha", 9, "PR nine", pr=True)],
                                               [row("o/alpha", 3, "Issue three")]],
        listing("o/zeta", "wait%20on%20me"): [[row("o/zeta", 4, "Zeta four")]],
        comments("o/alpha", 3): [[ESC]], comments("o/alpha", 9): [[]], comments("o/zeta", 4): [[]],
    }
    payload = cli_attention(cfg, responses)  # waits alone exit 0
    paths = [c[1] for c in cli_attention.recorder().calls]
    # Only enrolled repositories are read, and GitHub filters to open labelled items.
    assert sorted(paths) == sorted(responses)
    assert all("state=open&labels=wait%20on%20me" in p for p in paths if "comments" not in p)
    listed = [(e["repo"], e["number"], e["kind"], e["title"], e["url"])
              for r in payload["repositories"] for e in r["entries"]]
    assert listed == [
        ("o/alpha", 3, "issue", "Issue three", "https://github.com/o/alpha/issues/3"),
        ("o/alpha", 9, "pr", "PR nine", "https://github.com/o/alpha/pull/9"),
        ("o/zeta", 4, "issue", "Zeta four", "https://github.com/o/zeta/issues/4")]
    assert [r["label"] for r in payload["repositories"]] == ["wait on me"] * 2
    assert payload["repositories"][0]["entries"][0]["summary"] == HEAD
    assert not any(r["stale"] for r in payload["repositories"])
    text = cli_attention(cfg, responses, json_output=False)
    assert "3 issue(s)/PR(s) need you" in text and "STALE" not in text
    for kind, repo, number, title, url in [
            ("Issue", "o/alpha", 3, "Issue three", "https://github.com/o/alpha/issues/3"),
            ("PR", "o/alpha", 9, "PR nine", "https://github.com/o/alpha/pull/9"),
            ("Issue", "o/zeta", 4, "Zeta four", "https://github.com/o/zeta/issues/4")]:
        assert text.count(f"{kind} {repo}#{number} — {title}") == 1 and text.count(url) == 1
    assert HEAD in text
    # A closed or relabelled item drops out on the next complete listing.
    gone = cli_attention(cfg, {**responses, listing("o/zeta", "wait%20on%20me"): [[]]})
    assert gone["repositories"][1]["entries"] == []


@pytest.mark.scenario("attention/independent-repository-snapshots/unreadable-repository-is-stale-not-empty")
def test_attention_unreadable_repository_is_stale_not_empty(cli_attention, stamps):
    """attention/independent-repository-snapshots/unreadable-repository-is-stale-not-empty"""
    cfg = Config(repos=[Repo("o/a"), Repo("o/b")])
    cold = cli_attention(cfg, {listing("o/a"): RuntimeError("ghp_SECRET down"),
                               listing("o/b"): [[]]}, code=1)
    a, b = cold["repositories"]
    assert a["entries"] == [] and a["checked_at"] is None and a["stale"]
    assert a["error_code"] == "read_failed" and "SECRET" not in json.dumps(cold)
    assert b["checked_at"] and not b["stale"]
    warm = cli_attention(cfg, {listing("o/a"): [[row("o/a", 1, "kept")]], comments("o/a", 1): [[ESC]],
                               listing("o/b"): [[]]})
    failing = {listing("o/a"): outcomes._TimedOut("slow"),
               listing("o/b"): [[row("o/b", 2, "fresh")]], comments("o/b", 2): [[]]}
    failed = cli_attention(cfg, failing, code=1)
    a, b = failed["repositories"]
    assert a["entries"] == warm["repositories"][0]["entries"]
    assert a["checked_at"] == warm["repositories"][0]["checked_at"]
    assert a["stale"] and a["error_code"] == "timeout"
    assert [e["number"] for e in b["entries"]] == [2] and not b["stale"]
    assert b["checked_at"] > warm["repositories"][1]["checked_at"]
    text = cli_attention(cfg, failing, json_output=False, code=1)
    assert "STALE o/a — last listed " in text and "Issue o/a#1 — kept" in text
    assert "Issue o/b#2 — fresh" in text
    assert cli_attention(cfg, offline=True, code=1)["repositories"][0] == a
    # A summary failure on a healthy listing is also a current-source failure.
    flaky = {**failing, listing("o/a"): [[]], comments("o/b", 2): RuntimeError("x")}
    result = cli_attention(cfg, flaky, code=1)["repositories"]
    assert not result[0]["stale"]
    assert result[1]["stale"] and result[1]["error_code"] == "read_failed"
    assert outcomes.SUMMARY_UNAVAILABLE in cli_attention(cfg, flaky, json_output=False, code=1)


@pytest.mark.scenario("attention/independent-repository-snapshots/empty-enrollment-leaves-state-untouched")
@pytest.mark.parametrize("offline", [False, True])
def test_attention_empty_enrollment_creates_no_cache(cli_attention, monkeypatch, offline):
    """attention/independent-repository-snapshots/empty-enrollment-leaves-state-untouched"""
    from nightshift import cli
    # Isolate the specloop provider as test_reads_never_migrate does; it has its own store.
    monkeypatch.setattr(cli.specloop_attention, "snapshot", lambda **_: {"entries": [], "error": None})
    monkeypatch.setattr(outcomes, "database", lambda: pytest.fail("cache opened"))
    monkeypatch.setattr(outcomes.subprocess, "run", lambda *a, **k: pytest.fail("subprocess"))
    payload = cli_attention(Config(repos=[]), offline=offline)
    assert payload["repositories"] == [] and cli_attention.recorder().calls == []
    assert not outcomes.DB_PATH.exists()


def history_rows():
    with outcomes.database() as db:
        return ([tuple(r) for r in db.execute("SELECT * FROM events ORDER BY id")],
                [tuple(r) for r in db.execute("SELECT * FROM observations ORDER BY event_id")])


@pytest.mark.scenario("attention/additive-attention-json/worker-history-no-longer-renders")
@pytest.mark.scenario("worker-retirement/historical-evidence-remains-available/historical-attention-remains-readable")
@pytest.mark.scenario("worker-retirement/historical-evidence-remains-available/historical-reconciliation-stays-explicit")
@pytest.mark.scenario("worker-retirement/historical-evidence-remains-available/historical-mirroring-remains-usable")
def test_attention_no_longer_renders_worker_outcomes(cli_attention, monkeypatch):
    """attention/additive-attention-json/worker-history-no-longer-renders

    worker-retirement/historical-evidence-remains-available/historical-attention-remains-readable
    worker-retirement/historical-evidence-remains-available/historical-reconciliation-stays-explicit
    worker-retirement/historical-evidence-remains-available/historical-mirroring-remains-usable
    """
    import importlib.util
    import sys
    from nightshift import mirror

    outcomes.record("w/old", 1, state="awaiting_merge", reason="merge me",
                    pr_url="https://github.com/w/old/pull/2")
    outcomes.record("w/old", 3, state="needs_decision", reason="decide me")
    outcomes.record("w/old", 5, state="needs_decision", reason="stale me")
    with outcomes.database() as db:
        db.execute("INSERT INTO observations(event_id,error) SELECT id,'historical outage' "
                   "FROM events WHERE issue=5")
    history = history_rows()
    before = outcomes.tasks()
    cfg = Config(repos=[Repo("o/r")])
    healthy = {listing("o/r"): [[row("o/r", 7, "current")]], comments("o/r", 7): [[]]}
    for offline in (False, True):
        payload = cli_attention(cfg, healthy, offline=offline)
        assert [e["number"] for e in payload["repositories"][0]["entries"]] == [7]
        text = cli_attention(cfg, healthy, offline=offline, json_output=False)
        assert "w/old" not in text and "task(s) need you" not in text
        assert "historical outage" not in text and "Issue o/r#7 — current" in text
        assert ("Cached outcomes (GitHub and specloop not checked)" in text) is offline
    # Discovery that no longer lists anything, or fails, never reconciles history.
    cli_attention(cfg, {listing("o/r"): [[]]})
    cli_attention(cfg, {listing("o/r"): RuntimeError("down")}, code=1)
    assert history_rows() == history
    assert outcomes.tasks() == before
    assert [(t["issue"], t["state"], t["reason"]) for t in before] == [
        (1, "awaiting_merge", "merge me"), (3, "needs_decision", "decide me"),
        (5, "needs_decision", "stale me")]
    assert [t["issue"] for t in outcomes.attention(before)] == [1, 3, 5]
    assert "STALE w/old#5" in outcomes.render(before)
    # Explicit historical refresh still resolves and preserves under its own rules.
    monkeypatch.setattr(outcomes, "HISTORY_INTERVAL", 0)

    def historical(args):
        if args[0] == "pr":
            return json.dumps({"state": "MERGED"})
        if args[2] == "5":
            raise RuntimeError("still down")
        return json.dumps({"state": "CLOSED", "labels": []})
    refreshed = {t["issue"]: t for t in outcomes.refresh(Labels(), runner=historical)}
    assert refreshed[1]["state"] == refreshed[3]["state"] == "resolved"
    assert refreshed[5]["state"] == "needs_decision" and "still down" in refreshed[5]["error"]
    assert refreshed[5]["checked_at"] is None
    # Attention imports no worker engine and leaves the retained mirror utility in place.
    assert importlib.util.find_spec("nightshift.worker") is None
    assert "nightshift.worker" not in sys.modules and callable(mirror.mirror)
