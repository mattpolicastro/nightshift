"""Native producer identities retain the existing arming and history boundaries."""

from datetime import date
from pathlib import Path

import pytest

from fake_gitea import FakeGitea
from nightshift import arm
from nightshift.touchpoints import collect_history, collect_report
from specloop.seal import IDENTITY, git
from test_arm import armed_reader, concurrent_checkout, context_reader, pr_record
from test_arm_gitea_context import pages
from test_specloop_critic_gitea import pr
from test_touchpoints import assert_offline_output_pair, put, snapshot_local_tree


@pytest.mark.scenario("issue-arming/native-source-issue-attribution/native-pr-paths-retain-source-issue")
@pytest.mark.parametrize("forge", ["github", "gitea"])
@pytest.mark.parametrize("change", ["007-add-a-gate", "org__repo-007-add-a-gate"])
def test_native_pr_paths(tmp_path, forge, change):
    branches = [(f"spec/{change}", "org/repo"), (f"spec/{change}", "fork/repo"),
                (change, "org/repo"), ("spec/other__repo-007-add-a-gate", "org/repo"),
                ("spec/007-../add-a-gate", "org/repo"), ("spec/007-two--words", "org/repo")]
    files = [{"filename": "src/new.py", "previous_filename": "src/old.py", "status": "renamed"}]
    if forge == "github":
        pulls = [pr_record(n, 7, branch=branch, repo=repo)
                 for n, (branch, repo) in enumerate(branches, 901)]
        responses = {(kind, "pulls?state=open&per_page=100&page=1"): pulls
                     for kind in ("helper", "metadata")}
        responses.update({("helper", f"pulls/{p['number']}/files?per_page=100&page=1"): files
                          for p in pulls})
        cache, _ = context_reader(responses)
    else:
        fake = FakeGitea()
        pulls = [pr(n, branch, repo=repo, state="open")
                 for n, (branch, repo) in enumerate(branches, 901)]
        fake.repository("org/repo", labels=[], pulls=pulls)
        for p in pulls:
            pages(fake, f"pulls/{p['number']}/files", files)
        cache = arm.IssueCache(forge=arm._GiteaArmingForge(arm.GiteaForge(
            fake.base_url, "synthetic", bot_login="Robot", trusted_logins=[], opener=fake)))
    context = cache.open_prs("org/repo")
    assert not context.notes
    assert {(e.path, e.issue) for e in context.evidence} == {("src/new.py", 7), ("src/old.py", 7)}
    assert all(e.source.startswith("PR #901 ") for e in context.evidence)
    for number in (7, 8):
        target = arm.Issue(number, "**Scope:** `src/new.py` `src/old.py`", "OPEN", None, frozenset())
        assert arm.concurrent_overlaps(target, tmp_path, context) == ([] if number == 7 else context.evidence)


@pytest.mark.scenario("issue-arming/native-source-issue-attribution/native-runs-permit-normal-exemptions")
@pytest.mark.parametrize("status", ["running", "paused"])
@pytest.mark.parametrize("change", ["007-add-a-gate", "org__repo-007-add-a-gate"])
@pytest.mark.parametrize("relation", ["self", "forward", "reverse", "none", "unknown"])
def test_native_run_exemptions(concurrent_checkout, monkeypatch, status, change, relation):
    root, add_run = concurrent_checkout
    if relation == "unknown":
        change = "other__repo-007-add-a-gate"
    run = add_run(change, "`src/legacy.py`", status=status)
    tasks = root / "specs" / change / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("## Phase 1: Setup\n- [ ] T001 Edit `src/shared.py`\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "native tasks", env=IDENTITY)
    git(root, "push", str(run.dir / "repo.git"), "main")
    manifest = run.dir / "manifest.toml"
    manifest.write_text(manifest.read_text().replace("[run]", '[run]\nformat = "speckit"'))
    bodies = {7: "", 8: "", 9: ""}
    if relation in ("forward", "unknown"):
        bodies.update({8: "blocked-by: #9", 9: "blocked-by: #7"})
    elif relation == "reverse":
        bodies.update({7: "blocked-by: #9", 9: "blocked-by: #8"})
    issues = {n: arm.Issue(n, "**Scope:** `src/shared.py`\n" + body, "OPEN", None, frozenset())
              for n, body in bodies.items()}
    cache, _ = armed_reader([[]])
    monkeypatch.setattr(cache, "get", lambda repo, number: issues[number])
    target = issues[7 if relation == "self" else 8]
    context = arm.collect_concurrent("org/repo", root, [target], cache)
    run, = [e for e in context.evidence if e.source.startswith("run ")]
    assert run.issue == (None if relation == "unknown" else 7)
    findings = arm.overlap_findings(target, root, context, "org/repo", cache)
    assert [(f.code, f.related_issue) for f in findings] == (
        [("overlap", 7)] if relation == "none" else [("overlap", None)] if relation == "unknown" else [])
    if relation == "unknown":
        assert "source issue unknown" in findings[0].message and "blocked-by" not in findings[0].suggestion


@pytest.mark.scenario("issue-arming/native-source-issue-attribution/native-roots-remain-reserved")
def test_native_scope_roots_remain_reserved(tmp_path):
    assert arm.scope_paths("**Scope:** `specs/007-add-a-gate/spec.md` "
                           "`openspec/changes/example/tasks.md` `src/gate.py`", tmp_path) == {"src/gate.py"}


@pytest.mark.scenario("touchpoint-report/producer-naming-compatibility/qualified-fallback-recovers-without-a-proposal")
@pytest.mark.scenario("touchpoint-report/producer-naming-compatibility/native-admission-recovers-without-proposal")
@pytest.mark.parametrize("change", ["007-add-a-gate", "owner__sample-007-add-a-gate",
                                   "owner__sample-issue-7-add-a-gate", "sample-issue-7-add-a-gate"])
@pytest.mark.parametrize("storage", ["receipt", "job", "pending_job"])
@pytest.mark.parametrize("kind,command,outcome,counts", [
    ("admissions", "admit", "refused", {"admission_refusals": 1}),
    ("revisions", "propose", "needs_input", {"spec_revisions": 1, "drafter_questions": 1}),
    ("revisions", "revise", "refused", {"build_revisions": 1}),
])
def test_proposal_free_history(tmp_path, change, storage, kind, command, outcome, counts):
    if storage == "receipt":
        path = put(tmp_path, f"{kind}/owner__sample/28.json",
                   dict(change=change, command=command, result=dict(outcome=outcome)))
    else:
        args = ["revise", change] if command == "revise" else [command, "manifest", "--change", change]
        path = put(tmp_path, f"{kind}/owner__sample/28.jobs/j/request.json", dict(args=args))
        if storage == "job":
            put(path.parent, "result.json", dict(result=dict(outcome=outcome)))
    events = put(tmp_path, f"runs/{change}/events.jsonl", dict(event="human_accept", at=1))
    before = snapshot_local_tree(tmp_path)
    history = collect_history(tmp_path, tmp_path / "runs")
    assert not history["errors"] and not (tmp_path / "proposals").exists()
    row, = history["changes"]
    assert (row["repo"], row["issue"], row["change"], row["events_path"]) == ("owner/sample", 7, change, events)
    record, = row["records"]
    assert (record["number"], record["path"], record["retained"]) == (28, path, storage != "receipt")
    report = collect_report(tmp_path, tmp_path / "runs")
    assert not report["errors"]
    row, = report["changes"]
    assert row["drafted_at"] is row["drafted_at_source"] is None
    assert any("Missing proposal" in warning for warning in row["warnings"])
    assert any("Draft date unknown" in warning for warning in row["warnings"])
    for field, count in counts.items():
        assert row["counts"][field] == (0 if storage == "pending_job" else count)
    assert row["counts"]["human_accepts"] == 1
    assert row["counts"]["total"] == 1 + (0 if storage == "pending_job" else sum(counts.values()))
    filtered = collect_report(tmp_path, tmp_path / "runs", since=date(2026, 1, 1))
    assert filtered["changes"] == [] and filtered["totals"]["total"] == 0
    assert filtered["errors"] == [dict(path=str(path), line=None, message=(
        f"ValueError: Cannot apply --since 2026-01-01 to {change}: draft date unknown; change excluded"))]
    assert snapshot_local_tree(tmp_path) == before


@pytest.mark.scenario("touchpoint-report/producer-naming-compatibility/both-conventions-retain-isolated-attribution")
@pytest.mark.scenario("touchpoint-report/producer-naming-compatibility/native-repositories-remain-separate")
@pytest.mark.parametrize("shared_change,issue", [("007-add-a-gate", 7), ("x-issue-8-add-a-gate", 8)])
def test_repository_isolation_offline(tmp_path, shared_change, issue):
    state, runs = tmp_path / ".nightshift", tmp_path / ".specloop/runs"
    for owner in ("a", "b"):
        for number, change in enumerate((shared_change, f"{owner}__x-issue-9-add-a-gate"), 28):
            put(state, f"admissions/{owner}__x/{number}.json", dict(change=change, result=dict(outcome="refused")))
            put(runs, f"{change}/events.jsonl", dict(event="human_accept", at=1))
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("invalid = [")
    missing = tmp_path / "missing.toml"
    config = tmp_path / "config.toml"
    config.write_text("".join(f'[[repos]]\nname = "{owner}/x"\nengine = "specloop"\nmanifest = "{manifest}"\n'
                              for owner, manifest in (("a", invalid), ("b", missing))))
    shared = runs / shared_change / "events.jsonl"
    history = collect_history(state, runs)
    assert [(r["repo"], r["issue"]) for r in history["changes"]] == [
        ("a/x", issue), ("a/x", 9), ("b/x", issue), ("b/x", 9)]
    assert all(r["events_path"] is None for r in history["changes"] if r["issue"] == issue)
    report = assert_offline_output_pair(tmp_path, exit_code=1, denied=(shared, config, invalid, missing))
    assert report["errors"] == [dict(path=str(shared), line=None, message=(
        "ValueError: ambiguous run name belongs to multiple rows; events not read"))]
    assert report["totals"]["admission_refusals"] == 4
    assert report["totals"]["human_accepts"] == 2 and report["totals"]["total"] == 6


@pytest.mark.scenario("touchpoint-report/producer-naming-compatibility/fallback-requires-exact-repository-and-suffix")
@pytest.mark.scenario("touchpoint-report/producer-naming-compatibility/native-repositories-remain-separate")
@pytest.mark.parametrize("prefix", ["", "owner__widget.js_tools-x-"])
def test_exact_native_identity(tmp_path, monkeypatch, prefix):
    repo = "owner/widget.js_tools-x"
    valid = {prefix + "007-add-a-gate": 7, prefix + "0007-title": 7, prefix + "1234-" + "x" * 40: 1234}
    invalid = [prefix + suffix for suffix in (
        "000-title", "07-title", "7-title", "007-", "007-Title", "007-two--words", "007-title-",
        "007-under_score", "007-" + "x" * 41, "００７-title", "007-title.lock", "007-title\n")]
    invalid += ["other__widget.js_tools-x-007-title", "owner__widgetXjs_tools-x-007-title",
                "widget.js_tools-x-007-title", "../007-title", "spec/007-title"]
    for number, change in enumerate([*valid, *invalid], 28):
        put(tmp_path, f"admissions/owner__widget.js_tools-x/{number}.json", dict(repo=repo, change=change))
        assert arm.front_door_issue(repo, change) == valid.get(change)
        assert arm.front_door_issue("github.example/" + repo, change) == valid.get(change)
    for change in valid:
        put(tmp_path, f"runs/{change}/events.jsonl", dict(event="human_accept", at=1))
    conflict = put(tmp_path, "admissions/owner__widget.js_tools-x/99.json", dict(change=next(iter(valid)), issue=9))
    ambiguous = put(tmp_path, "admissions/owner__widget__extra/80.json", dict(change="007-title"))
    original = Path.open
    opened = []

    def guarded(path, *args, **kwargs):
        if path.name == "events.jsonl":
            assert path.parent.name in valid
            opened.append(path.parent.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    report = collect_report(tmp_path, tmp_path / "runs")
    assert {(r["repo"], r["issue"], r["change"]) for r in report["changes"]} == {
        (repo, issue, change) for change, issue in valid.items()}
    assert len(report["errors"]) == len(invalid) + 2
    assert sorted(opened) == sorted(valid)
    assert report["totals"]["human_accepts"] == len(valid)
    errors = {e["path"]: e["message"] for e in report["errors"]}
    assert "issue conflicts" in errors[str(conflict)] and "ambiguous repository" in errors[str(ambiguous)]


@pytest.mark.scenario("touchpoint-report/producer-naming-compatibility/native-repositories-remain-separate")
@pytest.mark.parametrize("repo,change", [("owner/sample", "007-add-a-gate"),
                                       ("owner/007", "007-issue-8-test")])
@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_native_proposal_identity_is_authoritative(tmp_path, repo, change, retained, ambiguous):
    directory = repo.replace("/", "__")
    for issue in ([9, 10] if ambiguous else [9]):
        if retained:
            put(tmp_path, f"proposals/{directory}/{issue}.jobs/j/request.json",
                dict(args=["propose", "manifest", "--change", change]))
        else:
            put(tmp_path, f"proposals/{directory}/{issue}.json", dict(change=change))
    receipt = put(tmp_path, f"admissions/{directory}/28.json", dict(change=change, issue=9))
    before = snapshot_local_tree(tmp_path)
    history = collect_history(tmp_path, tmp_path / "runs")
    assert [r["issue"] for r in history["changes"]] == ([9, 10] if ambiguous else [9])
    if ambiguous:
        assert len(history["errors"]) == 2
        assert any(e["path"] == str(receipt) and "ambiguous proposal identity" in e["message"]
                   for e in history["errors"])
        assert all(r["events_path"] is None for r in history["changes"])
    else:
        assert history["errors"] == [] and history["changes"][0]["warnings"] == []
        assert len(history["changes"][0]["records"]) == 2
    assert snapshot_local_tree(tmp_path) == before


@pytest.mark.scenario("issue-arming/native-source-issue-attribution/native-runs-permit-normal-exemptions")
@pytest.mark.scenario("touchpoint-report/producer-naming-compatibility/fallback-requires-exact-repository-and-suffix")
@pytest.mark.parametrize("change,expected", [("007-issue-8-test", None), ("007-issue-7-test", 7),
    ("owner__007-issue-8-test", 8), ("owner__007-007-issue-8-test", 7)])
def test_numeric_repository_collision(tmp_path, monkeypatch, change, expected):
    source = arm.front_door_issue("owner/007", change)
    assert source == expected
    put(tmp_path, "admissions/owner__007/28.json", dict(change=change))
    events = put(tmp_path, f"runs/{change}/events.jsonl", dict(event="human_accept", at=1))
    if expected is None:
        for issue in (7, 8):
            put(tmp_path, f"admissions/owner__007/{issue}.json", dict(change=change, issue=issue))
            target = arm.Issue(issue, "**Scope:** `src/gate.py`\nblocked-by: #7 #8",
                               "OPEN", None, frozenset())
            context = arm.ConcurrentContext([arm.PathEvidence("src/gate.py", source, f"run {change}")], [])
            finding, = arm.overlap_findings(target, tmp_path, context, "owner/007", arm.IssueCache())
            assert finding.related_issue is None and "source issue unknown" in finding.message
        original = Path.open

        def guarded(path, *args, **kwargs):
            assert path != events, "ambiguous producer identity must not read events"
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", guarded)
    report = collect_report(tmp_path, tmp_path / "runs")
    if expected is None:
        assert report["changes"] == [] and len(report["errors"]) == 3
        assert all("ambiguous" in e["message"] for e in report["errors"])
    else:
        row, = report["changes"]
        assert row["issue"] == expected and row["counts"]["human_accepts"] == 1
        assert not report["errors"]
