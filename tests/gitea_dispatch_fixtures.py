"""Real Gitea transport with the existing local pipeline command fixture."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from fake_gitea import FakeGitea
from manifest_fixtures import manifest_text
from nightshift import daemon, queue, specloop_intake as intake
from nightshift.config import Config, ForgeConfig, Repo
from nightshift.forge import http
from test_forge_gitea import candidate, label_event, pull
from test_specloop_isolation import Pipeline


TEA = ForgeConfig("tea", "gitea", "https://forge.example/gitea", "TEA_BOT", "TEA_LAND",
                  "Robot", ["Alice"], wip_prefix="Draft: ")


def forbidden(*args, **kwargs):
    pytest.fail("Gitea must not use GitHub or worker recovery")


def install_gitea(monkeypatch, repo, *, labels=(), issues=(), pulls=()):
    fake = FakeGitea()
    definitions = [{"id": i, "name": name} for i, name in enumerate(dict.fromkeys([
        *vars(queue.Labels()).values(), "merge:ready", "merge:blocked", "unrelated", *labels,
    ]), 1)]

    def prepare(row, *, pr=False):
        number = row["number"]
        names = {label["name"] for label in row.get("labels", [])}
        item = (pull(number, row.get("headRefName", "spec/change")) if pr else
                candidate(number, []))
        item.update(title=row.get("title", "Gate"), body=row.get("body", "Please draft it."),
                    user={"login": "Alice"}, comments=row.get("comments", []),
                    labels=[d for d in definitions if d["name"] in names],
                    timeline=[label_event(i, label={"name": name})
                              for i, name in enumerate(sorted(names), 1)])
        if pr:
            item["head"]["sha"] = row.get("headRefOid", "a" * 40)
            item["html_url"] = f"{TEA.url}/{repo.name}/pulls/{number}"
        return item

    fake.repository(repo.name, labels=definitions,
                    issues=[prepare(row) for row in issues],
                    pulls=[prepare(row, pr=True) for row in pulls], page_size=50)
    monkeypatch.setattr(http, "build_opener", lambda: fake)
    monkeypatch.setattr(http, "sleep", lambda _: None)
    monkeypatch.setenv("TEA_BOT", "test-bot-token")
    cfg = Config([replace(repo, forge="tea")], forges=[TEA])
    return SimpleNamespace(fake=fake, cfg=cfg, repo=cfg.repos[0],
                           remote=fake.repositories[repo.name],
                           root=f"/gitea/api/v1/repos/{repo.name}")


@pytest.fixture
def gitea_pipeline(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(manifest_text(tmp_path / "source"))
    pipeline = Pipeline(Repo("owner/tea", manifest=manifest))
    pipeline.issue(1)
    e = install_gitea(monkeypatch, pipeline.repo, issues=pipeline.issues.values())
    e.pipeline = pipeline
    e.health = daemon.RepoHealth()
    e.messages = []
    monkeypatch.setattr(intake, "run_git", pipeline.command)
    monkeypatch.setattr(queue, "_gh", forbidden)
    monkeypatch.setattr(daemon, "_reconcile_engine_switch", forbidden)
    monkeypatch.setattr(daemon.notify, "send", lambda hook, message: e.messages.append(message))
    e.poll = lambda: daemon.poll_specloop(e.cfg, e.health, runner=forbidden,
                                         specloop_runner=pipeline.command)
    e.receipt_path = daemon.STATE_DIR / "proposals/owner__tea/1.json"
    return e
