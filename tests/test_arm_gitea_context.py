"""Arming context through the real Gitea adapter, critic and recording opener."""

import socket

import pytest

from fake_gitea import FakeGitea, Reply
from nightshift import arm
from nightshift.forge import http
from test_arm import concurrent_checkout
from test_specloop_critic_gitea import ROOT, label, pr


DRAFT = {"id": 9, "name": "spec:draft"}
BODY = ("**Scope:** `docs/shared.md`\n**What to do:** Edit.\n"
        "**Definition of done:** `test_shared`\n**What CI cannot prove:** Appearance.\n")


def issue(number, **extra):
    return {"id": number, "number": number, "body": BODY, "state": "open", "labels": [DRAFT], **extra}


def pages(fake, endpoint, *replies):
    for page, reply in enumerate([*replies, []], 1):
        reply = reply if isinstance(reply, (Reply, Exception)) else Reply(reply)
        fake.route("GET", ROOT + endpoint, reply, reply, reply, page=page)


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("network"))
    monkeypatch.setattr(http, "sleep", lambda _: None)
    fake = FakeGitea()
    fake.repository("org/repo", labels=[DRAFT], issues=[issue(76, labels=[]), issue(77)],
                    pulls=[pr(901, "spec/org__repo-issue-77-work", state="open")])
    pages(fake, "pulls/901/files", [{"filename": "docs/shared.md", "previous_filename": "docs/old.md"}])
    facet = arm._GiteaArmingForge(arm.GiteaForge(
        fake.base_url, "synthetic", bot_login="Robot", trusted_logins=[], opener=fake))
    yield fake, facet, arm.IssueCache(forge=facet)
    assert all(call.timeout == 30 and call.method == "GET" for call in fake.calls)
    assert all(response.closed for response in fake.responses)


@pytest.mark.scenario("issue-arming/gitea-arming-context/armed-issues-are-filtered-locally")
def test_arm_gitea_lists_armed_issues_client_side(context):
    fake, facet, cache = context
    state = fake.repositories["org/repo"]
    state["labels"].insert(0, {"id": 2, "name": "bug"})
    for current_id in (9, 19):
        state["labels"][-1] = current = {"id": current_id, "name": "spec:draft"}
        pages(fake, "issues", [issue(76, labels=[])],
              [issue(77, labels=[current], body=None)],
              [issue(78, labels=[{"id": current_id - 1, "name": "spec:draft"}])],
              [issue(79, labels=[state["labels"][0]])],
              [issue(80, pull_request={}), issue(81, state="closed")])
        cache = arm.IssueCache(forge=facet)
        found, notes = cache.armed("org/repo")
        assert [i.number for i in found] == [77] and found[0].body == "" and not notes
        before = len(fake.calls)
        assert cache.armed("org/repo")[0] is found
        assert cache.get("org/repo", 77) is found[0] and len(fake.calls) == before
    assert [c.query["page"] for c in fake.calls if c.path == ROOT + "labels"] == [["1"], ["2"], ["3"]] * 2
    assert [c.query["page"] for c in fake.calls if c.path == ROOT + "issues"] == [[str(n)] for n in range(1, 7)] * 2
    assert all(not {"label", "labels"} & c.query.keys() for c in fake.calls)


@pytest.mark.scenario("issue-arming/gitea-arming-context/incomplete-armed-listings-remain-findings")
@pytest.mark.parametrize("fault", ["missing", "duplicate", "body", "state", "labels", "number",
                                  "repeat", "conflict", "http", "termination", "bound"])
def test_strict_listing_never_exposes_a_prefix(context, fault):
    fake, _, cache = context
    state = fake.repositories["org/repo"]
    pages(fake, "issues", [issue(77)])
    if fault == "missing":
        state["labels"] = []
    elif fault == "duplicate":
        state["labels"].append({"id": 10, "name": "spec:draft"})
    elif fault in ("body", "state", "labels", "number"):
        pages(fake, "issues", [issue(77)], [{**issue(78), fault: 3.5}])
    elif fault == "bound":
        pages(fake, "issues", *[[issue(n)] for n in range(1, 1001)])
    else:
        tail = {"repeat": Reply([issue(77)]), "conflict": Reply([issue(77, body="changed")]),
                "http": Reply(status=403), "termination": Reply([], headers={"X-Next-Page": "3"})}[fault]
        pages(fake, "issues", [issue(77)], tail)
    target = cache.get("org/repo", 76)
    found, notes = cache.armed("org/repo")
    assert found == [] and len(notes) == 1
    assert set(cache._issues) == {("org/repo", 76)} and cache.get("org/repo", 76) is target
    before = len(fake.calls)
    assert cache.armed("org/repo") == (found, notes) and len(fake.calls) == before
    assert cache.open_prs("org/repo").evidence
    assert all(c.query.get("page") != ["1001"] for c in fake.calls)


@pytest.mark.scenario("issue-arming/gitea-arming-context/armed-issues-are-filtered-locally")
@pytest.mark.parametrize("failed", [False, True])
def test_gitea_snapshot_keeps_first_issue_read(context, failed):
    fake, _, cache = context
    fake.route("GET", ROOT + "issues/77", Reply(status=403) if failed else Reply(issue(77, body="first")))
    if failed:
        with pytest.raises(arm.IssueLookupError):
            cache.get("org/repo", 77)
    else:
        first = cache.get("org/repo", 77)
    found, notes = cache.armed("org/repo")
    assert (found, bool(notes)) == (([], True) if failed else ([first], False))


@pytest.mark.scenario("issue-arming/gitea-arming-context/pr-evidence-uses-real-helpers")
@pytest.mark.parametrize("endpoint", ["pulls", "pulls/901/files"])
@pytest.mark.parametrize("fault", ["duplicate", "repeat", "conflict", "http", "termination", "siblings"])
def test_real_pr_helper_retains_paths_and_notes(context, endpoint, fault):
    fake, _, cache = context
    first = [pr(901, "spec/org__repo-issue-77-work")] if endpoint == "pulls" else [
        {"filename": "docs/shared.md", "previous_filename": "docs/old.md"}]
    extra = pr(902, "ordinary") if endpoint == "pulls" else {"filename": "tests/new.py"}
    pages(fake, "pulls/902/files")
    tails = {"duplicate": [*first, extra], "repeat": first,
             "conflict": [{**first[0], **({"head": {"ref": "changed", "repo": None}}
                                         if endpoint == "pulls" else {"previous_filename": "untrusted.py"})}, extra],
             "http": Reply(status=403), "termination": Reply([], headers={"X-Next-Page": "3"}),
             "siblings": [None, extra] if endpoint == "pulls" else [
                 {"filename": "tests/new.py", "previous_filename": 3},
                 {"filename": None, "previous_filename": "tests/old.py"}]}
    pages(fake, endpoint, first, tails[fault])
    result = cache.open_prs("org/repo")
    paths = {e.path for e in result.evidence}
    assert {"docs/shared.md", "docs/old.md"} <= paths and "untrusted.py" not in paths
    assert bool(result.notes) == (fault != "duplicate")
    assert len(result.notes) == len(set(result.notes))
    assert all(e.issue == 77 and e.source == f"PR {label(901)}" for e in result.evidence)
    if endpoint.endswith("files") and fault in ("duplicate", "conflict", "siblings"):
        assert "tests/new.py" in paths
    if endpoint.endswith("files") and fault == "siblings":
        assert "tests/old.py" in paths
    before = len(fake.calls)
    assert cache.open_prs("org/repo") is result and len(fake.calls) == before
    assert len([c for c in fake.calls if c.path == ROOT + "pulls/901"]) == 1


@pytest.mark.scenario("issue-arming/gitea-arming-context/pr-evidence-uses-real-helpers")
@pytest.mark.scenario("issue-arming/concurrent-path-collection/partial-context-retains-evidence")
@pytest.mark.parametrize("metadata", [None, {"number": True}, {"number": 999}, {"head": None},
                                     {"head": {"ref": "spec/repo-issue-78-work", "repo": None}},
                                     {"head": {"ref": 3, "repo": {"full_name": "org/repo"}}}])
def test_missing_metadata_keeps_independently_attributed_pr(context, metadata):
    fake, _, cache = context
    second = pr(902, "spec/repo-issue-78-work", state="open")
    fake.repositories["org/repo"]["pulls"].append(second)
    pages(fake, "pulls/902/files", [{"filename": "docs/shared.md"}, {"filename": "docs/old.md"}])
    fake.route("GET", ROOT + "pulls/902", Reply(None if metadata is None else {**second, **metadata}))
    result = cache.open_prs("org/repo")
    assert {e.issue for e in result.evidence} == {77}
    assert {e.path for e in result.evidence} == {"docs/shared.md", "docs/old.md"}
    assert len(result.notes) == 2 and all("902" in note for note in result.notes)
    assert len([c for c in fake.calls if c.path == ROOT + "pulls/902"]) == 1


@pytest.mark.scenario("issue-arming/concurrent-path-collection/all-three-sources-contribute")
@pytest.mark.scenario("issue-arming/concurrent-path-collection/scope-and-path-rules-are-preserved")
@pytest.mark.scenario("issue-arming/concurrent-path-collection/pagination-identity-and-self-exclusion")
@pytest.mark.scenario("issue-arming/concurrent-path-collection/unattributed-run-overlap-is-visible")
def test_all_sources_and_source_issue_attribution(context, concurrent_checkout):
    fake, _, cache = context
    root, add_run = concurrent_checkout
    paths = {"README.md", "new/nested.py", "src/code.py", "src/*.py", "tests/shared.py", "docs/shared.md", "docs/old.md"}
    tokens = " ".join(f"`{p}`" for p in paths - {"src/code.py"}) + " `src/code.py::helper`"
    body = BODY.replace("`docs/shared.md`", tokens).replace("Edit.", "`outside/ignored.py`")
    fake.repositories["org/repo"]["issues"] = [issue(76, body=body), issue(77, body=body), issue(78)]
    add_run("org__repo-issue-77-running", tokens)
    add_run("repo-issue-78-paused", "`tests/shared.py`", status="paused")
    add_run("repo-issue-76-self", tokens)
    add_run("unattributed", "`README.md`", status="paused")
    (root / "src").mkdir()
    (root / "src/actual.py").touch()
    pulls = [pr(901, "spec/org__repo-issue-77-work", repo="ORG/REPO"),
             pr(902, "spec/repo-issue-78-legacy"), pr(903, "ordinary"),
             pr(904, "spec/repo-issue-79-fork", repo="fork/repo"), pr(905, "spec/repo-issue-76-self")]
    fake.repositories["org/repo"]["pulls"] = [dict(p, state="open") for p in pulls]
    for p in pulls[1:]:
        pages(fake, f"pulls/{p['number']}/files", [{"filename": "tests/shared.py"}])
    target = cache.get("org/repo", 76)
    result = arm.collect_concurrent("org/repo", root, [target], cache)
    overlaps = arm.concurrent_overlaps(target, root, result)
    assert not result.notes and arm.scope_paths(body, root) == paths
    assert {e.path for e in overlaps if e.source == "issue #77"} == paths
    assert {e.path for e in overlaps if e.source == "run org__repo-issue-77-running"} == paths
    assert {(e.path, e.issue) for e in overlaps if e.source.startswith("PR")} == {
        ("docs/shared.md", 77), ("docs/old.md", 77), ("tests/shared.py", 78)}
    assert arm.PathEvidence("tests/shared.py", 78, "run repo-issue-78-paused") in overlaps
    assert all(e.issue not in (76, 79, 901, 902) for e in overlaps)
    findings = arm.overlap_findings(target, root, result, "org/repo", cache)
    combined = next(f for f in findings if f.path == "docs/shared.md" and f.related_issue == 77)
    assert "run org__repo-issue-77-running" in combined.message and "PR #901" in combined.message
    unknown = next(f for f in findings if f.related_issue is None)
    assert "unattributed" in unknown.message and "Identify" in unknown.suggestion and "blocked-by" not in unknown.suggestion


@pytest.mark.scenario("issue-arming/gitea-arming-context/incomplete-armed-listings-remain-findings")
@pytest.mark.scenario("issue-arming/concurrent-path-collection/partial-context-retains-evidence")
@pytest.mark.parametrize("mode", [[], ["--force"], ["--check", "--force"]])
@pytest.mark.parametrize("fault", ["listing", "metadata", "files"])
def test_partial_context_preserves_evidence_and_mode_policy(context, concurrent_checkout, monkeypatch, capsys, mode, fault):
    fake, facet, cache = context
    root, add_run = concurrent_checkout
    add_run("repo-issue-77-running", "`docs/shared.md`")
    fake.repositories["org/repo"]["issues"].append(issue(78, labels=[]))
    if fault == "listing":
        pages(fake, "issues", [issue(77)], Reply(status=403))
    elif fault == "metadata":
        fake.route("GET", ROOT + "pulls/901", Reply(status=403))
    else:
        pages(fake, "pulls/901/files", [{"filename": "docs/shared.md", "previous_filename": 4}], Reply(status=403))
    monkeypatch.setattr(arm, "resolve_checkout", lambda: (root, "org/repo"))
    monkeypatch.setattr(arm, "IssueCache", lambda **kw: cache)
    writes = []
    monkeypatch.setattr(facet, "add_intake_label", lambda *args: writes.append(args))
    assert arm.main(["76", "78", *mode]) == (0 if mode == ["--force"] else 1)
    output = capsys.readouterr().out
    assert "incomplete-context" in output and "run repo-issue-77-running" in output
    assert "overlaps issue #78" in output and "overlaps issue #76" in output
    assert "PR #901" in output if fault != "metadata" else "metadata" in output
    assert writes == ([("org/repo", 76), ("org/repo", 78)] if mode == ["--force"] else [])
    assert ("org/repo", 76) in cache._issues
    if fault == "files":
        assert len(cache.open_prs("org/repo").notes) == 2


@pytest.mark.scenario("issue-arming/gitea-arming-context/context-deadline-remains-advisory")
@pytest.mark.parametrize("phase", ["files", "retry", "metadata"])
def test_deadline_stops_request_starts_and_resets(context, monkeypatch, phase):
    fake, facet, cache = context
    now = [0]
    monkeypatch.setattr(arm.time, "monotonic", lambda: now[0])
    original_guard = facet.forge._transport._guard
    cutoff = []
    fake.repositories["org/repo"]["pulls"].append(pr(902, "spec/repo-issue-78-work", state="open"))
    pages(fake, "pulls/902/files", [{"filename": "docs/shared.md"}])

    def slow(call):
        now[0] = 121
        cutoff.append(len(fake.calls))
        return Reply(status=503) if phase == "retry" else Reply(
            pr(901, "spec/repo-issue-77-work") if phase == "metadata" else [{"filename": "docs/shared.md"}])

    fake.route("GET", ROOT + ("pulls/901" if phase == "metadata" else "pulls/901/files"),
               slow, page=None if phase == "metadata" else 1)
    result = cache.open_prs("org/repo")
    assert cutoff == [len(fake.calls)]  # No request starts after the slow response or retry.
    assert any("120-second" in note for note in result.notes)
    assert bool(result.evidence) == (phase == "metadata")
    assert any("metadata unavailable" in note for note in result.notes) if phase != "retry" else True
    assert facet.forge._transport._guard is original_guard
    before = len(fake.calls)
    assert cache.get("org/repo", 76).number == 76 and len(fake.calls) == before + 1
    assert cache.armed("org/repo")[1] == []
