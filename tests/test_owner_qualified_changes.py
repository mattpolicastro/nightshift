"""New issue identities preserve repository spelling and fail closed when unsafe."""

import json

import pytest

from manifest_fixtures import manifest_text
from nightshift import daemon, queue, specloop_intake as intake
from nightshift.config import Config, Repo
from test_specloop_isolation import Pipeline
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
    lookup, = [c for c in pipeline.calls if c[:2] == ["pr", "list"] and "--head" in c]
    assert lookup[lookup.index("--head") + 1] == branch
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
