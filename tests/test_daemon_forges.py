"""Selected construction and the unconditional Gitea daemon boundary."""

from itertools import product
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fake_gitea import FakeGitea
from manifest_fixtures import manifest_text
from nightshift import daemon, queue, specloop_intake as intake, specloop_job as jobs
from nightshift.config import Config, ForgeConfig, Repo
from nightshift.forge import http
from test_specloop_isolation import Pipeline


TEA = ForgeConfig("tea", "gitea", "https://forge.example/gitea", "TEA_BOT", "TEA_LAND",
                  "Robot", ["Alice"], wip_prefix="Draft: ")
HUB = ForgeConfig("hub", "github")


def forbidden(*args, **kwargs):
    pytest.fail("held operation reached")


@pytest.mark.scenario("forge-interface/selected-forge-construction/github-retains-runner-construction")
@pytest.mark.parametrize("selection", [None, "hub"])
def test_github_factory_retains_runner(monkeypatch, selection):
    repo = Repo("owner/hub", forge=selection)
    cfg = Config([repo], forges=[HUB])
    for _ in range(2):
        runner = Mock(side_effect=forbidden)
        monkeypatch.setattr(queue, "_gh", runner)
        assert daemon.forge_for(repo, cfg=cfg)._runner is runner
        explicit = Mock(side_effect=forbidden)
        assert daemon.forge_for(repo, cfg=cfg, runner=explicit)._runner is explicit
        runner.assert_not_called()
        explicit.assert_not_called()


@pytest.mark.scenario("forge-interface/selected-forge-construction/gitea-uses-its-configured-adapter")
@pytest.mark.parametrize("token", [None, "", "bot-secret"])
def test_gitea_factory_uses_configured_adapter(monkeypatch, token):
    fake, guard = FakeGitea(), Mock(side_effect=forbidden)
    monkeypatch.setattr(http, "build_opener", lambda: fake)
    monkeypatch.setattr(queue, "_gh", forbidden)
    monkeypatch.setenv("GH_TOKEN", "not-the-gitea-token")
    monkeypatch.delenv("TEA_BOT", raising=False)
    repo = Repo("owner/tea", forge="tea")
    cfg = Config([repo], forges=[TEA])
    if token is not None:
        monkeypatch.setenv("TEA_BOT", token)
    if not token:
        with pytest.raises(intake.ConfigurationError, match="tea.*TEA_BOT") as caught:
            daemon.forge_for(repo, cfg=cfg, guard=guard)
        assert not caught.value.transient and "not-the-gitea-token" not in str(caught.value)
    else:
        forge = daemon.forge_for(repo, cfg=cfg, guard=guard)
        assert isinstance(forge, daemon.GiteaForge)
        assert (forge._transport._base, forge._transport._token) == (TEA.url, token)
        assert (forge._bot_login, forge._trusted_logins, forge._wip_prefix) == (
            "robot", frozenset({"alice"}), "Draft: ")
    assert not fake.calls
    guard.assert_not_called()


@pytest.mark.scenario("forge-interface/human-token-exclusion/landing-environment-refuses-startup")
@pytest.mark.parametrize("entry", ["service", "once", "startup", "poll"])
@pytest.mark.parametrize("value,paused", list(product(["", "human-secret"], [False, True])))
def test_daemon_refuses_land_token_in_env(monkeypatch, entry, value, paused):
    # TEA is deliberately unused: all configured landing keys are excluded.
    cfg = Config([Repo("owner/hub")], forges=[TEA])
    monkeypatch.setenv("TEA_LAND", value)
    if paused:
        daemon.PAUSE_FILE.touch()
    with monkeypatch.context() as patch:
        for owner, names in ((daemon, ["beat", "_note_unclean_exit", "_reconcile_engine_switch"]),
                             (daemon.notify, ["send"]), (jobs, ["reap"]),
                             (daemon.subprocess, ["run", "Popen"])):
            for name in names:
                patch.setattr(owner, name, forbidden)
        with pytest.raises(intake.ConfigurationError, match="tea.*TEA_LAND") as caught:
            if entry == "startup":
                daemon.startup(cfg, {})
            elif entry == "poll":
                daemon.poll_specloop(cfg, daemon.RepoHealth())
            else:
                daemon.loop(cfg, {}, once=entry == "once")
        assert "human-secret" not in str(caught.value)
    monkeypatch.delenv("TEA_LAND")
    daemon.PAUSE_FILE.unlink(missing_ok=True)
    recovered = Mock()
    monkeypatch.setattr(daemon, "_reconcile_engine_switch", recovered)
    daemon.startup(cfg, {})
    recovered.assert_called_once_with(cfg.repos[0], cfg.labels)


@pytest.fixture(params=list(product([False, True], ["refused", "admitted"],
                                   [([], "owner"), (["Alice"], "owner"), (["Alice"], "Alice")])))
def held(request, tmp_path, monkeypatch):
    broken, outcome, (logins, actor) = request.param
    template = tmp_path / "template.toml"
    template.write_text(manifest_text(tmp_path / "source"))
    hub = Pipeline(Repo("owner/hub", manifest=template, forge="hub" if broken else None))
    hub.approved(8)
    tea_template = tmp_path / "tea.toml"
    tea_template.write_text("[broken" if broken else template.read_text())
    tea = Repo("owner/tea", forge="tea", manifest=tea_template)
    cfg = Config([tea, hub.repo], forges=[HUB, ForgeConfig(**{**vars(TEA), "trusted_logins": logins})])
    fake = FakeGitea()
    labels = [{"id": 1, "name": "spec:approved"}, {"id": 2, "name": "agent:revise"}]
    fake.repository(tea.name, labels=labels, pulls=[dict(number=8, labels=list(labels),
                    user={"login": actor}, timeline=[{"actor": {"login": actor}}])])
    def lost_delete(call):
        fake._state_reply(call, None)
        return TimeoutError("accepted DELETE; response lost")
    fake.route("DELETE", "/gitea/api/v1/repos/owner/tea/issues/8/labels/1", lost_delete)
    monkeypatch.setattr(http, "build_opener", lambda: fake)
    monkeypatch.setenv("TEA_BOT", "configured-bot")
    for folder, success in (("approvals", "approved"), ("admissions", "admitted"),
                            ("revisions", "revised"), ("publications", "published")):
        jobs.save(daemon.STATE_DIR / folder / "owner__tea/8.json", {
            "change": "tea-issue-8-gate", "head": "a" * 40, "approval_event": 1,
            "result": {"outcome": "refused" if outcome == "refused" else success},
            "delivered": False, "consumed_feedback_ids": ["old"]})
    jobs.save(daemon.STATE_DIR / "admissions/owner__tea/8.jobs" / ("a" * 32) / "result.json",
              {"result": {"outcome": outcome}})
    run = daemon.STATE_DIR / "runs/tea-issue-8-gate"
    jobs.save(run / "state.json", dict(change=run.name, status="paused",
              pr=TEA.url + "/owner/tea/pulls/8", pause={"reason": "ready_to_publish", "detail": "Check it"}))
    (run / "manifest.toml").write_text(template.read_text().replace("[run]\n",
        '[run]\nchange = "tea-issue-8-gate"\nbranch = "spec/tea-issue-8-gate"\n'))
    (tmp_path / "work").mkdir()
    (tmp_path / "work/diff.txt").write_text("surviving work")
    queue.Claim(tea.name, 8, "claude/8", str(tmp_path / "work"), "yesterday").write()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    before.update({p: p.read_bytes() for p in daemon.STATE_DIR.rglob("*") if p.is_file()})
    for owner, name in [(daemon, "_reconcile_engine_switch"), (intake, "manifest_readiness"),
                        *[(intake, n) for n in ("propose_pending", "admit_pending", "revise_pending",
                                               "publish_pending", "report_failure")]]:
        original = getattr(owner, name)
        def github_only(repo, *args, _original=original, **kwargs):
            if repo.name != hub.repo.name:
                forbidden()
            assert "forge" not in kwargs
            return _original(repo, *args, **kwargs)
        monkeypatch.setattr(owner, name, github_only)
    for owner, name in ((socket, "socket"), (daemon.subprocess, "run"),
                        (daemon.subprocess, "Popen"), (daemon, "forge_for")):
        monkeypatch.setattr(owner, name, forbidden)
    def github(args):
        if tea.name in " ".join(args):
            forbidden()
        return hub.gh(args)
    monkeypatch.setattr(queue, "_gh", github)
    monkeypatch.setattr(intake, "run_git", hub.command)
    messages = []
    monkeypatch.setattr(daemon.notify, "send", lambda hook, message: messages.append(message))
    return SimpleNamespace(cfg=cfg, hub=hub, fake=fake, before=before, messages=messages,
                           remote=json.dumps(fake.repositories, sort_keys=True))


def check_hold(held):
    daemon.PAUSE_FILE.touch()
    daemon.startup(held.cfg, {})
    daemon.poll_specloop(held.cfg, daemon.RepoHealth(), runner=forbidden, specloop_runner=forbidden)
    assert not held.messages
    daemon.PAUSE_FILE.unlink()
    for _ in range(2):  # Restart loses in-memory health, never durable evidence.
        health = daemon.RepoHealth()
        daemon.startup(held.cfg, {}, health)
        for _ in range(2):
            daemon.poll_specloop(held.cfg, health, runner=queue._gh, specloop_runner=held.hub.command)
        assert health.failures == {"owner/tea": 3} and not health.item_failures
    assert not held.fake.calls and json.dumps(held.fake.repositories, sort_keys=True) == held.remote
    assert {p: p.read_bytes() for p in held.before} == held.before
    assert not held.hub.prs[8]["labels"] and len(held.hub.runs) == 1
    assert len(held.messages) == 2


@pytest.mark.scenario("forge-interface/gitea-dispatch-disabled/allowlist-cannot-enable-unsafe-authorization")
@pytest.mark.scenario("forge-interface/gitea-dispatch-disabled/pending-transitions-cannot-lose-their-trigger")
@pytest.mark.scenario("forge-interface/selected-forge-construction/github-retains-runner-construction")
def test_gitea_daemon_stages_are_disabled(held):
    check_hold(held)
    daemon.startup(held.cfg, {})  # Direct reconciliation must also escalate.
    assert len(held.messages) == 3
    assert all("authorization and durable label transitions are not implemented" in m for m in held.messages)


@pytest.mark.scenario("config-resilience/foreign-recovery-journal-hold/gitea-journal-holds-the-repository")
@pytest.mark.scenario("config-resilience/foreign-recovery-journal-hold/journal-location-is-uncertain-or-clear")
@pytest.mark.scenario("config-resilience/health-gates-new-work-only/foreign-journal-overrides-admitted-delivery")
@pytest.mark.parametrize("journal", ['{"done": false}', '{"done": true}', 'broken', 'unreadable', 'nested'])
def test_gitea_repo_with_engine_switch_journal_is_held(held, monkeypatch, journal):
    root = daemon.STATE_DIR / "engine-switch/owner__tea"
    root.mkdir(parents=True)
    path = root / ("completed/8.json" if journal == "nested" else "8.json")
    jobs.save(path, {})
    path.write_text(journal)
    held.before[path] = path.read_bytes()
    with monkeypatch.context() as patch:
        if journal == "unreadable":
            original = Path.iterdir
            def unreadable(self):
                if self == root:
                    raise PermissionError("unreadable")
                return original(self)
            patch.setattr(Path, "iterdir", unreadable)
        check_hold(held)
    assert all((str(root) in m) == (journal != "nested") for m in held.messages)
    path.unlink()  # Operator removal only removes the more specific diagnostic.
    del held.before[path]
    template = held.cfg.repos[0].manifest
    held.before[template] = held.hub.repo.manifest.read_bytes()
    template.write_bytes(held.before[template])
    held.messages.clear()
    check_hold(held)
    assert all("Gitea daemon stages disabled" in m for m in held.messages)
