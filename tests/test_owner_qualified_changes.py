"""New issue identities preserve repository spelling and fail closed when unsafe."""

import json

import pytest

from manifest_fixtures import manifest_text
from nightshift import daemon, queue, specloop_intake as intake
from nightshift.config import Config, Repo
from test_specloop_isolation import Pipeline, HEAD, CHECKS
from test_specloop_approval import CHANGE as LEGACY
from test_specloop_build_revision import BuildCommands
from test_specloop_spec_revision import RevisionGh, Commands as RevisionCommands


@pytest.fixture
def template(tmp_path, monkeypatch):
    path = tmp_path / "template.toml"
    path.write_text(manifest_text(tmp_path / "source"))
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")
    return path


def draft(template, name, title="Add a gate"):
    pipeline = Pipeline(Repo(name, engine="specloop", manifest=template))
    pipeline.issue(7)
    pipeline.issues[7]["title"] = title
    receipt, = intake.propose_pending(
        pipeline.repo, queue.Labels(), daemon.STATE_DIR, runner=pipeline.gh,
        specloop_runner=pipeline.command, git_runner=pipeline.command,
    )
    return pipeline, receipt


def assert_identity(pipeline, receipt, change):
    branch = f"spec/{change}"
    assert receipt["change"] == change and receipt["result"]["branch"] == branch
    assert receipt["repo"] == pipeline.repo.name and receipt["delivered"]
    path = daemon.STATE_DIR / "proposals" / pipeline.repo.name.replace("/", "__") / "7.json"
    assert json.loads(path.read_text()) == receipt
    command, = [c for c in pipeline.calls if c[:2] == ["specloop", "propose"]]
    assert command[command.index("--change") + 1] == change
    assert command[command.index("--branch") + 1] == branch
    lookups = [c for c in pipeline.calls if c[:2] == ["pr", "list"] and "--head" in c]
    assert [c[c.index("--head") + 1] for c in lookups] == [
        branch, "spec/" + change.removeprefix(pipeline.repo.name.split("/", 1)[0] + "__"),
    ]
    assert all(c[c.index("--repo") + 1] == pipeline.repo.name for c in lookups)
    create, = [c for c in pipeline.calls if c[:2] == ["pr", "create"]]
    assert create[create.index("--head") + 1] == branch
    push, = [c for c in pipeline.calls if c[0] == "git"]
    assert push[-1].endswith(f":refs/heads/{branch}")
    assert intake._safe_change(change)


@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/different-owners-have-distinct-changes")
def test_change_names_are_owner_qualified(template):
    for owner in ("a", "b"):
        pipeline, receipt = draft(template, f"{owner}/x")
        assert_identity(pipeline, receipt, f"{owner}__x-issue-7-add-a-gate")
    assert len(list((daemon.STATE_DIR / "proposals").glob("*/7.json"))) == 2


@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/safe-punctuation-is-preserved")
@pytest.mark.parametrize("title,slug", [
    ("Add a gate", "add-a-gate"),
    ("!! Gate 0123456789 / CAFÉ !!", "gate-0123456789-caf"),
    ("🔥 λ --", "issue"),
    ("", "issue"),
    ("A" * 50, "a" * 40),
    ("A" * 39 + " B", "a" * 39),
])
@pytest.mark.parametrize("owner", ["owner", "Owner-X"])
def test_punctuation_and_bounded_slug(template, owner, title, slug):
    pipeline, receipt = draft(template, f"{owner}/widget.js_tools-x", title)
    assert_identity(pipeline, receipt, f"{owner}__widget.js_tools-x-issue-7-{slug}")


@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/unsafe-spelling-holds-new-work")
@pytest.mark.parametrize("name", [
    "owner/widget..js", "owner/widget~js", "owner/widget js",
    "sample", "/sample", "owner/", "owner/sample/extra",
])
def test_unsafe_name_is_repository_health_hold(template, monkeypatch, name):
    pipeline = Pipeline(Repo(name, engine="specloop", manifest=template))
    pipeline.issue(7)
    pipeline.issues[7]["title"] = "Add a gate"
    pipeline.approved(8)
    # Even a safe existing PR cannot start a new admission while held.
    pipeline.prs[8]["headRefName"] = "spec/safe-recorded-change"
    monkeypatch.setattr(daemon.subprocess, "run", pipeline.command)
    messages = []
    monkeypatch.setattr(daemon.notify, "send", lambda hook, message: messages.append(message))
    health = daemon.RepoHealth()
    for _ in range(2):
        daemon.poll_specloop(Config(repos=[pipeline.repo]), health,
                             runner=pipeline.gh, specloop_runner=pipeline.command)
    assert health.failures == {name: 2} and not health.item_failures
    assert len(messages) == 1 and "unsupported repository spelling" in messages[0]
    assert name in messages[0] and "restart" in messages[0] and "token" not in messages[0]
    assert not any(c[0] == "git" or c[:2] in (["specloop", "propose"], ["specloop", "admit"])
                   for c in pipeline.calls)
    assert pipeline.issues[7]["labels"] == [{"name": "spec:draft"}]
    assert pipeline.prs[8]["labels"] == [{"name": "spec:approved"}]
    assert not pipeline.issues[7]["comments"] and not pipeline.prs[8]["comments"]
    assert not list(daemon.STATE_DIR.rglob("*.json"))
    for stage in ("propose", "admit"):
        with pytest.raises(intake.UnsupportedName, match="unsupported repository spelling"):
            getattr(intake, stage + "_pending")(
                pipeline.repo, queue.Labels(), daemon.STATE_DIR, runner=pipeline.gh,
                specloop_runner=pipeline.command, git_runner=pipeline.command,
            )


@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/unsafe-spelling-holds-new-work")
def test_malformed_repository_does_not_stop_healthy_repository(template, monkeypatch):
    malformed = Pipeline(Repo("sample", engine="specloop", manifest=template))
    healthy = Pipeline(Repo("owner/healthy", engine="specloop", manifest=template))
    for pipeline in (malformed, healthy):
        pipeline.issue(7)
        pipeline.issues[7]["title"] = "Add a gate"
    readiness = intake.manifest_readiness(malformed.repo)
    assert isinstance(readiness, intake.ConfigurationError)
    assert "expected owner/repository" in str(readiness)

    def github(args):
        if args[0] == "api":
            name = args[1].removeprefix("repos/").split("/issues/")[0]
        else:
            name = args[args.index("--repo") + 1]
        return {"sample": malformed, "owner/healthy": healthy}[name].gh(args)

    monkeypatch.setattr(daemon.subprocess, "run", healthy.command)
    messages = []
    monkeypatch.setattr(daemon.notify, "send", lambda hook, message: messages.append(message))
    health = daemon.RepoHealth()
    daemon.poll_specloop(Config(repos=[malformed.repo, healthy.repo]), health,
                         runner=github, specloop_runner=healthy.command)
    assert health.failures == {"sample": 1} and not health.item_failures
    assert len(messages) == 1 and "expected owner/repository" in messages[0]
    assert not (daemon.STATE_DIR / "proposals/sample").exists()
    assert malformed.issues[7]["labels"] == [{"name": "spec:draft"}]
    assert not malformed.issues[7]["comments"]
    receipt = json.loads((daemon.STATE_DIR / "proposals/owner__healthy/7.json").read_text())
    assert_identity(healthy, receipt, "owner__healthy-issue-7-add-a-gate")


@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/unsafe-spelling-holds-new-work")
def test_unsafe_name_holds_spec_redrafting(template):
    repo = Repo("owner/widget..js", engine="specloop", manifest=template)
    gh = RevisionGh()
    gh.pr_url = "https://github.com/owner/widget..js/pull/8"
    commands = RevisionCommands(gh)
    labels = gh.labels.copy()
    with pytest.raises(intake.UnsupportedName, match="spec/owner__widget..js-issue-7-add-a-gate"):
        intake.revise_pending(repo, queue.Labels(), daemon.STATE_DIR, runner=gh,
                              specloop_runner=commands, git_runner=commands)
    assert [c[1] for c in commands.calls] == ["status"]
    assert gh.labels == labels and not gh.comments
    assert not list(daemon.STATE_DIR.rglob("*.json"))


@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/unsafe-spelling-holds-new-work")
@pytest.mark.parametrize("shared_readiness", [False, True])
def test_unsupported_name_allows_saved_safe_draft_delivery(template, shared_readiness):
    pipeline = Pipeline(Repo("owner/widget..js", engine="specloop", manifest=template))
    pipeline.issue(7)
    change = "legacy-safe-issue-7-gate"
    path = daemon.STATE_DIR / "proposals/owner__widget..js/7.json"
    intake._save(path, {"change": change, "result": {
        "outcome": "drafted", "branch": f"spec/{change}", "commit": "abc123",
    }})
    readiness = intake.manifest_readiness(pipeline.repo) if shared_readiness else None
    receipt, = intake.propose_pending(
        pipeline.repo, queue.Labels(), daemon.STATE_DIR, runner=pipeline.gh,
        specloop_runner=pipeline.command, git_runner=pipeline.command, readiness=readiness,
    )
    assert receipt["delivered"] and receipt["change"] == change
    assert not any(c[0] == "specloop" for c in pipeline.calls)
    assert next(c for c in pipeline.calls if c[0] == "git")[-1] == f"abc123:refs/heads/spec/{change}"


@pytest.fixture
def legacy_case(template):
    pipeline = Pipeline(Repo("owner/sample", engine="specloop", manifest=template))
    pipeline.issue(7)
    pipeline.issues[7]["title"] = "Add a gate"
    path = daemon.STATE_DIR / "proposals/owner__sample/7.json"
    saved = {"repo": pipeline.repo.name, "issue": 7, "change": LEGACY,
             "title": "Add a gate", "idea": "Original idea", "job": "saved-job",
             "result": {"outcome": "drafted", "branch": f"spec/{LEGACY}", "commit": HEAD}}
    return pipeline, path, saved


def propose(pipeline, **kwargs):
    return intake.propose_pending(
        pipeline.repo, queue.Labels(), daemon.STATE_DIR, runner=pipeline.gh,
        specloop_runner=pipeline.command, git_runner=pipeline.command, **kwargs,
    )


def lookup_heads(pipeline):
    return [c[c.index("--head") + 1] for c in pipeline.calls
            if c[:2] == ["pr", "list"] and "--head" in c]


@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/legacy-receipt-survives-title-edits")
@pytest.mark.parametrize("pending_job", [False, True])
def test_legacy_receipt_survives_title_edits(legacy_case, monkeypatch, pending_job):
    pipeline, path, saved = legacy_case
    result = saved["result"]
    if pending_job:
        saved = {k: v for k, v in saved.items() if k != "result"}
        results = iter([None, result])

        def collect(receipt_path, receipt):
            assert receipt_path == path and receipt == saved
            return next(results)

        monkeypatch.setattr(intake.specloop_job, "collect", collect)
    intake._save(path, saved)
    pipeline.issues[7]["title"] = "An edited title"
    if pending_job:
        assert propose(pipeline) == []
        assert json.loads(path.read_text()) == saved
    receipt, = propose(pipeline)
    assert receipt == {**saved, "result": result, "delivered": True}
    assert json.loads(path.read_text()) == receipt
    assert lookup_heads(pipeline) == [f"spec/{LEGACY}"]
    assert not any(c[0] == "specloop" for c in pipeline.calls)
    push, = [c for c in pipeline.calls if c[0] == "git"]
    assert push[-1] == f"{HEAD}:refs/heads/spec/{LEGACY}"
    assert next(iter(pipeline.prs.values()))["headRefName"] == f"spec/{LEGACY}"
    assert propose(pipeline) == []


@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/legacy-pr-is-recovered")
@pytest.mark.parametrize("authorized", [False, True])
@pytest.mark.parametrize("qualified_exists", [False, True])
def test_lost_receipt_uses_exact_pr_and_existing_authorization(
    legacy_case, monkeypatch, authorized, qualified_exists,
):
    pipeline, path, _ = legacy_case
    qualified = "owner__" + LEGACY
    pipeline.approved(8)
    pipeline.prs[8]["headRefName"] = f"spec/{LEGACY}"
    if qualified_exists:
        pipeline.approved(9)
        pipeline.prs[9]["headRefName"] = f"spec/{qualified}"
    # Recovery bypasses blockers, but the current label still needs trust.
    pipeline.issues[7]["body"] = "blocked-by: #99"
    original = pipeline.gh

    def github(args):
        response = original(args)
        if not authorized and args[0] == "api" and args[1].endswith("/events"):
            pages = json.loads(response)
            for event in pages[0]:
                event["actor"] = {"login": "ghost"}
            return json.dumps(pages)
        return response

    monkeypatch.setattr(pipeline, "gh", github)
    assert propose(pipeline) == []
    assert lookup_heads(pipeline) == ([f"spec/{qualified}"] if qualified_exists else
                                       [f"spec/{qualified}", f"spec/{LEGACY}"])
    assert not path.exists()
    assert not any(c[0] in ("git", "specloop") or c[:2] == ["pr", "create"] for c in pipeline.calls)
    issue = pipeline.issues[7]
    if authorized:
        number = 9 if qualified_exists else 8
        assert not issue["labels"]
        assert pipeline.prs[number]["url"] in issue["comments"][-1]["body"]
        assert {"name": "spec:review"} in pipeline.prs[number]["labels"]
    else:
        assert issue["labels"] == [{"name": "spec:draft"}]
        assert "Ignoring" in issue["comments"][-1]["body"]
        assert all({"name": "spec:review"} not in pr["labels"] for pr in pipeline.prs.values())


@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/lookup-failure-does-not-create-duplicate-work")
@pytest.mark.parametrize("failed_head", ["owner__" + LEGACY, LEGACY])
@pytest.mark.parametrize("old_receipt", [False, True])
def test_exact_lookup_failure_preserves_state(legacy_case, monkeypatch, failed_head, old_receipt):
    pipeline, path, saved = legacy_case
    if old_receipt:
        # A completed non-draft round is eligible for replacement, but only
        # once both candidate lookups establish there is no existing PR.
        saved.update(result={"outcome": "needs_input"}, delivered=True)
        intake._save(path, saved)
    before = path.read_bytes() if path.exists() else None
    original = pipeline.gh
    failure = queue.GhError("exact lookup unavailable", transient=True)

    def github(args):
        if args[:2] == ["pr", "list"] and f"spec/{failed_head}" in args:
            pipeline.calls.append(args)
            raise failure
        return original(args)

    monkeypatch.setattr(pipeline, "gh", github)
    errors = []
    assert propose(pipeline, on_error=lambda number, exc: errors.append((number, exc))) == []
    assert errors == [(7, failure)]
    assert (path.read_bytes() if path.exists() else None) == before
    assert lookup_heads(pipeline) == ([f"spec/owner__{LEGACY}"] if failed_head.startswith("owner__") else
                                       [f"spec/owner__{LEGACY}", f"spec/{LEGACY}"])
    assert pipeline.issues[7]["labels"] == [{"name": "spec:draft"}]
    assert not pipeline.issues[7]["comments"] and not pipeline.prs
    assert not any(c[0] in ("git", "specloop") for c in pipeline.calls)


@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/closed-legacy-pr-starts-a-qualified-round")
@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
@pytest.mark.parametrize("recorded", [False, True])
def test_legacy_pr_lifecycle_and_qualified_replacement(legacy_case, monkeypatch, state, recorded):
    pipeline, path, saved = legacy_case
    pipeline.approved(8)
    pipeline.prs[8].update(headRefName=f"spec/{LEGACY}", state=state)
    if recorded:
        intake._save(path, {**saved, "delivered": True})
        # Only the receipt can recover the old title's identity.
        pipeline.issues[7]["title"] = "A new title"
    pipeline.issues[7]["labels"] = []
    assert propose(pipeline) == []  # Closing alone does not rearm intake.
    pipeline.issues[7]["labels"] = [{"name": "spec:draft"}]
    if state == "MERGED":
        assert propose(pipeline) == []
        assert not any(c[0] in ("git", "specloop") for c in pipeline.calls)
        assert not pipeline.issues[7]["comments"]
        assert (json.loads(path.read_text()) if recorded else None) == (
            {**saved, "delivered": True} if recorded else None)
        return

    original = pipeline.command

    def fail_push(args, **kwargs):
        if args[0] == "git" and "push" in args:
            raise RuntimeError("push response lost")
        return original(args, **kwargs)

    monkeypatch.setattr(pipeline, "command", fail_push)
    with pytest.raises(RuntimeError, match="push response lost"):
        propose(pipeline)
    replacement = json.loads(path.read_text())
    expected = "owner__sample-issue-7-" + ("a-new-title" if recorded else "add-a-gate")
    assert replacement["change"] == expected
    assert replacement["replaces_pr"] == {"number": 8, "head": HEAD}
    monkeypatch.setattr(pipeline, "command", original)
    pipeline.issues[7]["title"] = "Edited again before delivery"
    receipt, = propose(pipeline)
    assert receipt == {**replacement, "delivered": True}
    assert sum(c[:2] == ["specloop", "propose"] for c in pipeline.calls) == 1
    assert pipeline.prs[8]["headRefName"] == f"spec/{LEGACY}"
    assert [pr["headRefName"] for pr in pipeline.prs.values() if pr["state"] == "OPEN"] == [f"spec/{expected}"]


@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/legacy-receipt-survives-title-edits")
def test_ownerless_admission_and_publication_keep_recorded_identity(legacy_case):
    pipeline, _, _ = legacy_case
    pipeline.approved(8)
    pipeline.prs[8]["headRefName"] = f"spec/{LEGACY}"
    intake.admit_pending(pipeline.repo, queue.Labels(), daemon.STATE_DIR,
                         runner=pipeline.gh, specloop_runner=pipeline.command, git_runner=pipeline.command)
    pipeline.runs[LEGACY].update(status="paused", pause={"reason": "ready_to_publish", "detail": CHECKS})
    intake.publish_pending(pipeline.repo, queue.Labels(), daemon.STATE_DIR,
                           runner=pipeline.gh, specloop_runner=pipeline.command)
    admit, publish = [c for c in pipeline.calls if c[:2] in (["specloop", "admit"], ["specloop", "publish"])]
    assert admit[admit.index("--change") + 1] == LEGACY
    assert admit[admit.index("--branch") + 1] == f"spec/{LEGACY}"
    assert publish[2] == LEGACY
    assert list(pipeline.runs) == [LEGACY]
    assert all(LEGACY in c["body"] for c in pipeline.prs[8]["comments"])


@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/legacy-receipt-survives-title-edits")
@pytest.mark.parametrize("admitted", [False, True])
def test_ownerless_revision_keeps_pr_identity(template, admitted):
    repo = Repo("owner/sample", engine="specloop", manifest=template)
    gh = RevisionGh()
    commands = BuildCommands(gh) if admitted else RevisionCommands(gh)
    intake.revise_pending(repo, queue.Labels(), daemon.STATE_DIR, runner=gh,
                          specloop_runner=commands, git_runner=commands)
    command, = [c for c in commands.calls if c[:2] in (["specloop", "propose"], ["specloop", "revise"])]
    if admitted:
        assert command[2] == LEGACY
    else:
        assert command[command.index("--change") + 1] == LEGACY
        assert command[command.index("--branch") + 1] == f"spec/{LEGACY}"
    receipt = json.loads((daemon.STATE_DIR / "revisions/owner__sample/8.json").read_text())
    assert receipt["change"] == LEGACY and receipt["delivered"]
    assert gh.branch == f"spec/{LEGACY}" and "agent:revise" not in gh.labels
