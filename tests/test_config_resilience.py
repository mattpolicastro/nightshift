"""Repository-local enrollment health, with real template parsing and intake."""

import builtins
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import daemon, queue, specloop_intake as intake, specloop_job as jobs
from nightshift.config import Config, Repo, load
from specloop import manifest
from manifest_fixtures import manifest_text
from test_specloop_isolation import Pipeline, poll
from test_engine_routing import DraftGh
from test_specloop_intake import Commands as DraftCommands
from test_specloop_approval import ApprovalGh, Commands as AdmissionCommands, CHANGE, HEAD
from test_specloop_spec_revision import RevisionGh, Commands as RevisionCommands, REVISED, seed_consumed
from test_specloop_build_revision import BuildCommands
from test_specloop_publication import PublishCommands, DETAIL, CHECKS
from merge_readiness_fixtures import PublicationOnlyGh


@pytest.fixture
def enrollment(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")
    messages = []
    monkeypatch.setattr(daemon.notify, "send", lambda hook, message: messages.append(message))
    path = tmp_path / "template.toml"
    path.write_text(manifest_text(tmp_path / "source"))
    return path, messages


@pytest.mark.scenario("config-resilience/repository-manifest-health/bad-manifest-isolates-the-repository")
@pytest.mark.parametrize("failure", ["absent", "missing", "directory", "unreadable", "toml", "schema",
                                      "encoding", "overflow", "source_home"])
def test_bad_manifest_marks_repo_unhealthy_not_config(enrollment, tmp_path, monkeypatch, failure):
    path, messages = enrollment
    good = tmp_path / "good.toml"
    good.write_bytes(path.read_bytes())
    if failure == "missing":
        path.unlink()
    elif failure == "directory":
        path.unlink()
        path.mkdir()
    elif failure == "toml":
        path.write_text("[run")
    elif failure == "schema":
        path.write_text('run = 42')
    elif failure == "encoding":
        path.write_bytes(b"\xff")
    elif failure == "overflow":
        path.write_text(path.read_text() + "\n[limits]\nmax_attempts = inf\n")
    elif failure == "source_home":
        path.write_text(manifest_text("~missing/source"))
        expanduser = Path.expanduser

        def missing_home(self):
            if str(self) == "~missing/source":
                raise RuntimeError("Could not determine home directory.")
            return expanduser(self)

        monkeypatch.setattr(Path, "expanduser", missing_home)
    if failure == "unreadable":
        read = Path.read_text

        def unreadable(self, *args, **kwargs):
            if self == path:
                raise PermissionError("denied")
            return read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", unreadable)
    config = tmp_path / "config.toml"
    config.write_text('''[[repos]]
name = "owner/peer"
engine = "specloop"
manifest = "good.toml"
[[repos]]
name = "owner/good"
engine = "specloop"
manifest = "good.toml"
[[repos]]
name = "owner/bad"
engine = "specloop"
''' + ('' if failure == "absent" else 'manifest = "template.toml"\n'))
    cfg = load(config)
    pipelines = [Pipeline(repo) for repo in cfg.repos]
    for pipeline in pipelines:
        pipeline.issue(7)
        pipeline.approved(8)
    monkeypatch.setattr(intake, "run_git", pipelines[0].command)
    health = daemon.RepoHealth()
    for attempt in range(1, 4):
        for pipeline in pipelines[:-1]:
            pipeline.issue(10 + attempt)
            pipeline.approved(20 + attempt)
        poll(pipelines, health)
        assert health.failures == {"owner/bad": attempt}
        for pipeline in pipelines[:-1]:
            assert not pipeline.issues[10 + attempt]["labels"]
            change = pipeline.prs[20 + attempt]["headRefName"].removeprefix("spec/")
            assert pipeline.runs[change]["status"] == "running"
    # The temporary path may contain "token" when derived from the change name.
    # Exclude that path when checking that the diagnostic does not leak a token.
    assert len(messages) == 1 and "token" not in messages[0].replace(str(tmp_path), "")
    assert "owner/bad" in messages[0] and "manifest" in messages[0]
    assert not health.item_failures
    for pipeline in pipelines[:-1]:
        good_commands = [c[:2] for c in pipeline.calls]
        assert ["specloop", "propose"] in good_commands
        assert ["specloop", "admit"] in good_commands
        assert not pipeline.issues[7]["labels"]
        assert not pipeline.prs[8]["labels"]
    bad = pipelines[-1]
    assert not any(c[0] == "git" or c[:2] in (["specloop", "propose"], ["specloop", "admit"])
                   for c in bad.calls)
    assert bad.issues[7]["labels"] == [{"name": "spec:draft"}]
    assert bad.prs[8]["labels"] == [{"name": "spec:approved"}]


@pytest.mark.scenario("config-resilience/repository-manifest-health/template-omits-per-change-fields")
@pytest.mark.parametrize("omitted", [("change",), ("branch",), ("change", "branch"), ("repo",)])
def test_manifest_template_accepts_omitted_change_fields(enrollment, monkeypatch, omitted):
    path, _ = enrollment
    text = path.read_text().replace('[run]\n', '[run]\nchange = "original"\nbranch = "spec/original"\n')
    text = "\n".join(line for line in text.splitlines() if not any(
        line.startswith(field + " =") for field in omitted))
    path.write_text(text)
    before = path.read_bytes()
    monkeypatch.setattr(manifest, "load", lambda *a, **k: pytest.fail("readiness used load"))
    parsed = []
    template = manifest.template

    def probe(path, **kwargs):
        parsed.append(kwargs)
        return template(path, **kwargs)

    monkeypatch.setattr(manifest, "template", probe)
    pipeline = Pipeline(Repo("owner/sample", engine="specloop", manifest=path))
    health = daemon.RepoHealth()
    poll([pipeline], health)
    assert bool(health.failures) == (omitted == ("repo",))
    assert not health.item_failures
    assert parsed == [{"change": "readiness-probe", "branch": "spec/readiness-probe"}]
    assert path.read_bytes() == before


@pytest.mark.scenario("config-resilience/repository-manifest-health/nonregular-manifest-is-never-read")
@pytest.mark.parametrize("kind", ["directory", "fifo", "device"])
@pytest.mark.parametrize("replaced", [False, True], ids=["initial", "between-polls"])
def test_nonregular_manifest_is_never_read(enrollment, tmp_path, monkeypatch, kind, replaced):
    path, _ = enrollment
    repo = Repo("owner/bad", engine="specloop", manifest=path)
    good = tmp_path / "good.toml"
    good.write_bytes(path.read_bytes())
    pipelines = [Pipeline(repo), Pipeline(Repo("owner/good", engine="specloop", manifest=good))]
    health = daemon.RepoHealth()
    if replaced:
        poll(pipelines, health)
        assert not health.failures
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.symlink_to("/dev/null")
    # Guard both APIs: a regression must fail immediately, never block on FIFO.
    for owner, name in ((Path, "open"), (builtins, "open")):
        original = getattr(owner, name)

        def guarded(file, *args, _original=original, **kwargs):
            assert Path(file) not in (path, Path("/dev/null")), "opened nonregular manifest"
            return _original(file, *args, **kwargs)

        monkeypatch.setattr(owner, name, guarded)
    bad, healthy = pipelines
    for pipeline in pipelines:
        pipeline.issue(7)
        pipeline.approved(8)
    monkeypatch.setattr(intake, "run_git", healthy.command)
    poll(pipelines, health)
    assert health.failures == {repo.name: 1}
    assert not health.item_failures
    assert bad.issues[7]["labels"] == [{"name": "spec:draft"}]
    assert bad.prs[8]["labels"] == [{"name": "spec:approved"}]
    assert not any(c[:2] in (["specloop", "propose"], ["specloop", "admit"])
                   for c in bad.calls)
    assert not healthy.issues[7]["labels"] and not healthy.prs[8]["labels"]
    assert any(c[:2] == ["specloop", "propose"] for c in healthy.calls)
    assert any(c[:2] == ["specloop", "admit"] for c in healthy.calls)
    # Direct stage callers use the same regular-file guard.
    with pytest.raises(intake.ConfigurationError):
        intake._require_manifest(repo)


@pytest.mark.scenario("config-resilience/repository-manifest-health/repair-clears-the-hold")
def test_repair_clears_hold_and_rearms_notification(enrollment, monkeypatch, caplog):
    path, messages = enrollment
    valid = path.read_bytes()
    path.write_text("[broken")
    pipeline = Pipeline(Repo("owner/sample", engine="specloop", manifest=path))
    pipeline.issue(7)
    pipeline.approved(8)
    monkeypatch.setattr(intake, "run_git", pipeline.command)
    health = daemon.RepoHealth()
    for _ in range(3):
        poll([pipeline], health)
    assert len(messages) == 1 and health.failures == {pipeline.repo.name: 3}
    assert pipeline.issues[7]["labels"] == [{"name": "spec:draft"}]
    assert pipeline.prs[8]["labels"] == [{"name": "spec:approved"}]
    path.write_bytes(valid)
    with caplog.at_level(logging.INFO):
        poll([pipeline], health)
    assert not health.failures and not health.announced
    assert "healthy again" in caplog.text
    assert pipeline.issues[7]["labels"] == []
    assert pipeline.prs[8]["labels"] == []
    assert sum(c[:2] == ["specloop", "propose"] for c in pipeline.calls) == 1
    assert sum(c[:2] == ["specloop", "admit"] for c in pipeline.calls) == 1
    path.unlink()
    poll([pipeline], health)
    assert len(messages) == 2


@pytest.mark.scenario("config-resilience/repository-manifest-health/configuration-shape-remains-strict")
@pytest.mark.parametrize("settings", ['engine = "typo"', 'manifest = ""', 'manifest = 42',
                                      'manifest = []', 'manifest = false', '[broken'])
def test_configuration_shape_remains_strict(tmp_path, settings):
    path = tmp_path / "config.toml"
    engine = '' if settings.startswith('engine =') else 'engine = "specloop"\n'
    path.write_text('[[repos]]\nname = "o/r"\n' + engine + settings)
    with pytest.raises(ValueError):
        load(path)


@pytest.mark.scenario("config-resilience/repository-manifest-health/configuration-shape-remains-strict")
def test_manifest_path_semantics(enrollment, tmp_path, monkeypatch):
    path, _ = enrollment
    monkeypatch.setenv("HOME", str(tmp_path))
    link = tmp_path / "link.toml"
    link.symlink_to(path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    config = tmp_path / "config.toml"
    for value in ("link.toml", "~/link.toml"):
        config.write_text('[[repos]]\nname = "o/r"\nengine = "specloop"\nmanifest = ' + json.dumps(value))
        repo = load(config).repos[0]
        assert repo.manifest == path
        assert isinstance(intake.manifest_readiness(repo), manifest.Manifest)
    assert Repo("o/r", engine="specloop", manifest=Path("local.toml")).manifest == elsewhere / "local.toml"


@pytest.mark.scenario("config-resilience/repository-manifest-health/repair-clears-the-hold")
def test_resolution_failure_is_deferred_and_retried(enrollment, tmp_path, monkeypatch):
    path, _ = enrollment
    config = tmp_path / "config.toml"
    config.write_text('[[repos]]\nname = "o/r"\nengine = "specloop"\nmanifest = "template.toml"')
    resolve = Path.resolve

    def unavailable(self, *args, **kwargs):
        if self == path:
            raise PermissionError("filesystem unavailable")
        return resolve(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", unavailable)
        repo = load(config).repos[0]
        assert repo.manifest == path
        assert isinstance(intake.manifest_readiness(repo), intake.ConfigurationError)
    assert isinstance(intake.manifest_readiness(repo), manifest.Manifest)


@pytest.mark.scenario("config-resilience/repository-manifest-health/repair-clears-the-hold")
@pytest.mark.parametrize("stage", ["propose", "admit", "revise"])
@pytest.mark.parametrize("direct", [False, True], ids=["daemon", "direct-stage"])
def test_home_resolution_recovery_reaches_cli_template(enrollment, tmp_path, monkeypatch, stage, direct):
    path, _ = enrollment
    before = path.read_bytes()
    configured = Path("~builduser/template.toml")
    recovered = False
    expanduser = Path.expanduser

    def home(self):
        if self == configured:
            if not recovered:
                raise RuntimeError("Could not determine home directory.")
            return path
        return expanduser(self)

    monkeypatch.setattr(Path, "expanduser", home)
    config = tmp_path / "config.toml"
    config.write_text('[[repos]]\nname = "owner/sample"\nengine = "specloop"\n'
                      'manifest = "~builduser/template.toml"')
    repo = load(config).repos[0]
    assert repo.manifest == configured
    if stage == "propose":
        gh, commands = DraftGh(("spec:draft",)), DraftCommands()
    elif stage == "admit":
        gh, commands = ApprovalGh(), AdmissionCommands()
    else:
        gh = RevisionGh()
        commands = RevisionCommands(gh)
    monkeypatch.setattr(intake, "run_git", commands)
    parsed, errors = [], []
    health = daemon.RepoHealth()

    def cli(args, **kwargs):
        if args[1] in ("propose", "admit"):
            # Match the CLI: it reads this path without expanding the home.
            effective, _ = manifest.template(
                Path(args[2]), change=args[args.index("--change") + 1],
                branch=args[args.index("--branch") + 1],
            )
            assert Path(args[2]) == path
            expected = "owner__" + CHANGE if stage == "propose" else CHANGE
            assert effective.change == expected
            assert effective.branch == f"spec/{expected}"
            parsed.append(effective)
        return commands(args, **kwargs)

    def advance():
        if direct:
            getattr(intake, stage + "_pending")(
                repo, queue.Labels(), daemon.STATE_DIR, runner=gh,
                specloop_runner=cli, git_runner=commands,
                on_error=lambda number, exc: errors.append(exc),
            )
        else:
            daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=cli)

    labels = gh.labels.copy()
    advance()
    assert not parsed and gh.labels == labels
    if direct:
        assert len(errors) == 1 and isinstance(errors.pop(), intake.ConfigurationError)
    else:
        assert health.failures == {repo.name: 1}
    recovered = True
    advance()
    assert not errors and not health.failures
    assert len(parsed) == 1
    assert repo.manifest == path
    assert path.read_bytes() == before
    assert ("agent:revise" if stage == "revise" else
            "spec:approved" if stage == "admit" else "spec:draft") not in gh.labels


def test_readiness_does_not_disguise_programming_errors(enrollment, monkeypatch):
    path, _ = enrollment

    def broken(*args, **kwargs):
        raise RuntimeError("parser bug")

    monkeypatch.setattr(manifest, "template", broken)
    with pytest.raises(RuntimeError, match="parser bug"):
        intake.manifest_readiness(Repo("o/r", engine="specloop", manifest=path))


def stage_fakes(stage):
    if stage == "propose":
        return DraftGh(("spec:draft",)), DraftCommands()
    if stage == "admit":
        return ApprovalGh(), AdmissionCommands()
    gh = RevisionGh()
    return gh, RevisionCommands(gh)


@pytest.mark.scenario("config-resilience/repository-manifest-health/dispatch-uses-actual-change-identity")
@pytest.mark.parametrize("stage", ["propose", "admit", "revise"])
@pytest.mark.parametrize("omitted", [(), ("change",), ("branch",), ("change", "branch")])
def test_template_readiness_preserves_dispatch_identity(enrollment, monkeypatch, stage, omitted):
    path, _ = enrollment
    defaults = {"change": "unrelated-default", "branch": "spec/unrelated-default"}
    path.write_text(path.read_text().replace("[run]\n", "[run]\n" + "".join(
        f'{key} = "{value}"\n' for key, value in defaults.items() if key not in omitted)))
    before = path.read_bytes()
    repo = Repo("owner/sample", engine="specloop", manifest=path)
    gh, commands = stage_fakes(stage)
    parsed = []

    def cli(args, **kwargs):
        if args[1] in ("propose", "admit"):
            effective, _ = manifest.template(
                Path(args[2]), change=args[args.index("--change") + 1],
                branch=args[args.index("--branch") + 1])
            expected = "owner__" + CHANGE if stage == "propose" else CHANGE
            assert (effective.change, effective.branch) == (expected, f"spec/{expected}")
            parsed.append(effective)
        return commands(args, **kwargs)

    monkeypatch.setattr(intake, "run_git", commands)
    health = daemon.RepoHealth()
    daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=cli)
    assert not health.failures and len(parsed) == 1
    assert path.read_bytes() == before
    evidence = json.dumps([gh.calls, commands.calls]) + "".join(
        receipt.read_text() for receipt in daemon.STATE_DIR.rglob("*.json"))
    expected = "owner__" + CHANGE if stage == "propose" else CHANGE
    assert expected in evidence and f"spec/{expected}" in evidence
    assert "readiness-probe" not in evidence and "unrelated-default" not in evidence
    assert not health.item_failures
    assert ("agent:revise" if stage == "revise" else
            "spec:approved" if stage == "admit" else "spec:draft") not in gh.labels


@pytest.mark.scenario("issue-intake/approval-admits/unhealthy-enrollment-retains-approval")
@pytest.mark.scenario("pr-revision/revision-health-isolation/spec-drafting-waits-for-repair")
@pytest.mark.scenario("pr-revision/spec-stage-revision/spec-revised-from-review")
@pytest.mark.parametrize("stage", ["admit", "revise"])
@pytest.mark.parametrize("saved", [False, True])
def test_new_work_waits_without_recording_attempt(enrollment, monkeypatch, stage, saved):
    path, _ = enrollment
    valid = path.read_bytes()
    path.unlink()
    repo = Repo("owner/sample", engine="specloop", manifest=path)
    gh, commands = stage_fakes(stage)
    if saved:
        if stage == "revise":
            seed_consumed(daemon.STATE_DIR)
        else:
            jobs.save(daemon.STATE_DIR / "approvals/owner__sample/8.json", {"event": 0, "head": HEAD})
            jobs.save(daemon.STATE_DIR / "admissions/owner__sample/8.json", {
                "change": CHANGE, "head": HEAD, "approval_event": 0,
                "result": {"outcome": "refused"}, "delivered": True})
    before = {p: p.read_bytes() for p in daemon.STATE_DIR.rglob("*.json")}
    labels = gh.labels.copy()
    monkeypatch.setattr(intake, "run_git", commands)
    health = daemon.RepoHealth()
    for _ in range(3):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert health.failures == {repo.name: 3} and not health.item_failures
    assert gh.labels == labels and not gh.comments
    assert all(c[:2] == ["specloop", "status"] for c in commands.calls)
    assert {p: p.read_bytes() for p in daemon.STATE_DIR.rglob("*.json")} == before
    path.write_bytes(valid)
    for _ in range(2):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert not health.failures and len(gh.comments) == 1
    assert len([c for c in commands.calls if c[1] in ("propose", "admit")]) == 1
    if stage == "revise":
        receipt = json.loads((daemon.STATE_DIR / "revisions/owner__sample/8.json").read_text())
        assert "conversation:102" in receipt["consumed_feedback_ids"]
        assert gh.head == REVISED and gh.labels == {"spec:review"}
    else:
        assert "build has started" in gh.comments[0]["body"] and not gh.labels


@pytest.mark.scenario("issue-intake/approval-admits/unhealthy-enrollment-retains-approval")
@pytest.mark.parametrize("other_pr", ["https://github.com/other/sample/pull/8",
                                       "https://github.com/owner/sample/pull/9", None])
@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("failure", ["absent", "invalid"])
def test_colliding_run_does_not_bypass_admission_hold(enrollment, other_pr, saved, failure):
    path, _ = enrollment
    valid = path.read_bytes()
    path.write_text("[broken")
    repo = Repo("owner/sample", engine="specloop", manifest=None if failure == "absent" else path)
    gh, commands = ApprovalGh(), AdmissionCommands()
    commands.runs = [{"change": CHANGE, "pr": other_pr, "status": "running"}]
    if saved:
        # Even a retained result must not authorize a new collision refusal.
        event = intake._approval_event(repo.name, 8, "spec:approved", runner=gh)
        jobs.save(daemon.STATE_DIR / "approvals/owner__sample/8.json", {"event": event, "head": HEAD})
        jobs.save(daemon.STATE_DIR / "admissions/owner__sample/8.json", {
            "change": CHANGE, "head": HEAD, "approval_event": event,
            "result": {"outcome": "admitted"}})
    before = {p: p.read_bytes() for p in daemon.STATE_DIR.rglob("*.json")}
    labels = gh.labels.copy()
    health = daemon.RepoHealth()
    for _ in range(2):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert health.failures == {repo.name: 2} and not health.item_failures
    assert gh.labels == labels and not gh.comments
    assert commands.calls and all(c == ["specloop", "status", "--json"] for c in commands.calls)
    assert {p: p.read_bytes() for p in daemon.STATE_DIR.rglob("*.json")} == before
    path.write_bytes(valid)
    repaired = Repo(repo.name, engine="specloop", manifest=path)
    daemon.poll_specloop(Config(repos=[repaired]), health, runner=gh, specloop_runner=commands)
    assert not health.failures and not commands.admissions
    assert gh.labels == {"spec:review", "needs-human"}
    assert len(gh.comments) == 1 and "already belongs to a run" in gh.comments[0]["body"]


@pytest.mark.scenario("config-resilience/health-gates-new-work-only/unhealthy-repository-still-publishes")
@pytest.mark.scenario("config-resilience/health-gates-new-work-only/pause-and-stop-still-prevail")
@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/unsafe-spelling-holds-new-work")
@pytest.mark.parametrize("failure,name", [
    (failure, name) for failure in ("missing", "invalid", "absent")
    for name in ("owner/sample", "owner/widget..js")
] + [("unsupported-name", "owner/widget..js")])
def test_unhealthy_repo_still_publishes_admitted_run(enrollment, failure, name, monkeypatch):
    monkeypatch.setattr(daemon, "forge_for", lambda *a, **k: pytest.fail("GitHub must retain its runner path"))
    path, _ = enrollment
    if failure == "invalid":
        path.write_text("[broken")
    elif failure != "unsupported-name":
        path.unlink()
    repo = Repo(name, engine="specloop", manifest=None if failure == "absent" else path)
    gh, commands = PublicationOnlyGh(labels=()), PublishCommands()
    gh.pr_url = f"https://github.com/{name}/pull/8"
    commands.runs = [{"change": CHANGE, "pr": gh.pr_url, "status": "paused", "head": HEAD,
                      "pause": {"reason": "ready_to_publish", "detail": DETAIL}}]
    directory = name.replace("/", "__")
    jobs.save(daemon.STATE_DIR / "admissions" / directory / "8.json",
              {"change": CHANGE, "result": {"outcome": "admitted"}})
    health = daemon.RepoHealth()
    daemon.PAUSE_FILE.touch()
    daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert not commands.calls and not gh.calls
    daemon.PAUSE_FILE.unlink()
    for _ in range(2):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert health.failures == {name: 2} and not health.item_failures
    assert commands.publications == [["specloop", "publish", CHANGE, "--json"]]
    assert not commands.admissions
    assert len(gh.comments) == 1 and CHECKS in gh.comments[0]["body"]
    assert gh.pr_url in gh.comments[0]["body"] and not gh.labels
    receipt = json.loads((daemon.STATE_DIR / "publications" / directory / "8.json").read_text())
    assert receipt["delivered"] and receipt["change"] == CHANGE


@pytest.mark.scenario("config-resilience/health-gates-new-work-only/unhealthy-repository-still-takes-build-feedback")
@pytest.mark.scenario("config-resilience/health-gates-new-work-only/pause-and-stop-still-prevail")
@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/unsafe-spelling-holds-new-work")
@pytest.mark.parametrize("name", ["owner/sample", "owner/widget..js"])
def test_build_feedback_does_not_read_enrollment(enrollment, monkeypatch, name):
    monkeypatch.setattr(daemon, "forge_for", lambda *a, **k: pytest.fail("GitHub must retain its runner path"))
    path, _ = enrollment
    if name == "owner/sample":
        path.unlink()
    repo = Repo(name, engine="specloop", manifest=path)
    gh = RevisionGh()
    gh.pr_url = f"https://github.com/{name}/pull/8"
    commands = BuildCommands(gh)
    seed_consumed(daemon.STATE_DIR)
    directory = name.replace("/", "__")
    if directory != "owner__sample":
        (daemon.STATE_DIR / "revisions/owner__sample").rename(daemon.STATE_DIR / "revisions" / directory)
    monkeypatch.setattr(manifest, "template", lambda *a, **k: pytest.fail("enrollment read"))
    health = daemon.RepoHealth()
    daemon.PAUSE_FILE.touch()
    daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert not commands.calls and not gh.calls
    daemon.PAUSE_FILE.unlink()
    for _ in range(2):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert health.failures == {repo.name: 2} and not health.item_failures
    assert [c[1] for c in commands.calls] == ["status", "revise"]
    assert json.loads(commands.feedback[0]) == ["**reviewer:**\n\nHandle empty input"]
    receipt = json.loads((daemon.STATE_DIR / "revisions" / directory / "8.json").read_text())
    assert receipt["consumed_feedback_ids"] == ["conversation:101", "conversation:102"]
    assert receipt["delivered"] and gh.head == HEAD
    assert gh.labels == {"spec:review"} and len(gh.comments) == 1
    assert REVISED in gh.comments[0]["body"]


@pytest.mark.scenario("config-resilience/health-gates-new-work-only/saved-work-survives-a-hold")
@pytest.mark.scenario("config-resilience/health-gates-new-work-only/pause-and-stop-still-prevail")
@pytest.mark.parametrize("stage", ["propose", "admit", "revise"])
@pytest.mark.parametrize("stopped", [False, True])
def test_finished_jobs_survive_hold(enrollment, monkeypatch, stage, stopped):
    monkeypatch.setattr(daemon, "forge_for", lambda *a, **k: pytest.fail("GitHub must retain its runner path"))
    path, _ = enrollment
    valid = path.read_bytes()
    repo = Repo("owner/sample", engine="specloop", manifest=path)
    gh, commands = stage_fakes(stage)
    launched = []

    def launch(args, **kwargs):
        launched.append(Path(args[3]))
        return SimpleNamespace(poll=lambda: 0)

    kwargs = {"status_runner": commands} if stage != "propose" else {}
    getattr(intake, stage + "_pending")(
        repo, queue.Labels(), daemon.STATE_DIR, runner=gh, git_runner=commands,
        launcher=launch, **kwargs)
    job, = launched
    if stopped:
        jobs.save(job / "stop.json", {"state": "stopping"})
    jobs.finalize(job, {"result": commands.result})
    path.unlink()
    if stage == "admit":
        commands.runs = [{"change": CHANGE, "pr": gh.pr_url, "status": "running"}]
    commands.calls.clear()
    gh.calls.clear()
    monkeypatch.setattr(intake, "run_git", commands)
    health = daemon.RepoHealth()
    for _ in range(3):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert health.failures == {repo.name: 3} and not health.item_failures
    assert not any(c[0] == "git" or c[1] in ("propose", "admit") for c in commands.calls)
    folder = {"propose": "proposals", "admit": "admissions", "revise": "revisions"}[stage]
    receipt_path = daemon.STATE_DIR / folder / "owner__sample" / ("7.json" if stage == "propose" else "8.json")
    receipt = json.loads(receipt_path.read_text())
    assert bool(receipt.get("delivered")) == (stage == "admit" and not stopped)
    if stage != "admit" or stopped:
        assert not any(c[1] in ("comment", "edit", "create") for c in gh.calls)
    if stage == "revise":
        assert receipt["consumed_feedback_ids"] == []
    assert (job / "result.json").is_file() and len(launched) == 1
    if not stopped:
        assert receipt["result"]["outcome"] == commands.result["outcome"]
    else:
        assert "result" not in receipt and not (job / "collected.json").exists()
    path.write_bytes(valid)
    daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert not health.failures
    assert bool(json.loads(receipt_path.read_text()).get("delivered")) == (not stopped)
    assert not any(c[:2] in (["specloop", "propose"], ["specloop", "admit"]) for c in commands.calls)
