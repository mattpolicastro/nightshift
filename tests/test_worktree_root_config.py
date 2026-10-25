"""The historical worktree root is config-local, independent of enrollment health."""

import json
from pathlib import Path

import pytest

from manifest_fixtures import manifest_text
from nightshift import config, daemon, queue, specloop_intake
from test_specloop_isolation import Pipeline, poll


@pytest.fixture
def layout(tmp_path, monkeypatch):
    home = tmp_path / "host home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    config_dir = tmp_path / "config directory"
    config_dir.mkdir()
    elsewhere = tmp_path / "daemon cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return home, config_dir / "selected.toml"


def write_config(path, value=None):
    path.write_text("models = false\n[daemon]\nmax_tasks_per_night = 0\n"
                    + ("" if value is None else f"worktree_root = {json.dumps(value)}\n"))


def load_without_writes(path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(Path, "mkdir", lambda *a, **k: pytest.fail("config loading created a directory"))
        return config.load(path)


@pytest.mark.scenario("host-paths/configured-worker-worktree-root/worker-default-and-absolute-roots-remain-compatible")
@pytest.mark.parametrize("explicit", [False, True], ids=["default", "absolute"])
def test_default_and_absolute_roots_do_not_create_or_migrate(layout, monkeypatch, explicit):
    home, path = layout
    old_root = home / "Projects" / "nightshift-wt"
    old_worktree = old_root / "owner__repo-7" / "work.txt"
    old_worktree.parent.mkdir(parents=True)
    old_worktree.write_text("existing work")
    claims = home / ".nightshift" / "claims" / "claim.json"
    claims.parent.mkdir(parents=True)
    claims.write_text(json.dumps({"worktree": str(old_worktree.parent)}))
    run_state = home / ".specloop" / "runs" / "run.json"
    run_state.parent.mkdir(parents=True)
    run_state.write_text('{"status": "paused"}')
    expected = home / "alternate worker roots" if explicit else old_root
    write_config(path, str(expected) if explicit else None)
    before = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}

    cfg = load_without_writes(path, monkeypatch)

    assert cfg.worktree_root == expected
    assert old_worktree.read_text() == "existing work"
    assert json.loads(claims.read_text())["worktree"] == str(old_worktree.parent)
    assert {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
    if explicit:
        assert not expected.exists()


@pytest.mark.scenario("host-paths/configured-worker-worktree-root/worker-default-and-absolute-roots-remain-compatible")
def test_omitted_root_does_not_require_default_directory(layout, monkeypatch):
    home, path = layout
    write_config(path)
    monkeypatch.setattr(config, "DEFAULT_PATH", path)
    assert load_without_writes(None, monkeypatch).worktree_root == home / "Projects" / "nightshift-wt"
    assert list(home.iterdir()) == []


@pytest.mark.scenario("host-paths/configured-worker-worktree-root/worker-root-expansion-is-independent-of-cwd")
@pytest.mark.parametrize("value", ["~/worker roots", "worker roots", "./worker roots", "../worker roots",
                                       "./~/worker roots", "./~nonexistent_nightshift_user/worker roots",
                                       " worker roots ", "~/literal $HOME $(echo hi) * : roots"])
@pytest.mark.parametrize("relative_config", [False, True])
def test_expansion_uses_home_or_selected_config(layout, monkeypatch, value, relative_config):
    home, path = layout
    write_config(path, value)
    selected = Path("..") / path.parent.name / path.name if relative_config else path
    expected = home / value[2:] if value.startswith("~/") else path.parent / value

    cfg = load_without_writes(selected, monkeypatch)

    assert cfg.worktree_root.is_absolute()
    assert cfg.worktree_root.resolve() == expected.resolve()
    assert cfg.worktree_root.name == expected.name
    assert not expected.exists()
    # The returned path must keep its location even after the caller changes cwd.
    monkeypatch.chdir(home)
    assert cfg.worktree_root.resolve() == expected.resolve()


@pytest.mark.scenario("host-paths/configured-worker-worktree-root/worker-root-validation-preserves-enrollment-isolation")
@pytest.mark.parametrize("literal", [
    '42', '1.5', 'true', 'false', '[]', '["roots"]', '{}', '1979-05-27',
    *[json.dumps(value) for value in (
        "", " ", "\t", "~", "~somebody/roots", "~other", "~\\roots",
        "/roots\0bad", "roots\nbad", "~/roots\rbad",
    )],
])
def test_invalid_root_is_a_named_configuration_error(layout, monkeypatch, literal):
    _, path = layout
    path.write_text(f"models = false\n[daemon]\nconcurrency = 0\nworktree_root = {literal}\n")
    with pytest.raises(ValueError, match=r"daemon\.worktree_root"):
        load_without_writes(path, monkeypatch)
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.scenario("host-paths/configured-worker-worktree-root/worker-root-validation-preserves-enrollment-isolation")
@pytest.mark.parametrize("failure", ["absent", "missing", "directory", "invalid"])
@pytest.mark.parametrize("engine", ["", 'engine = "specloop"\n'])
def test_valid_root_preserves_repository_local_health(layout, tmp_path, monkeypatch, failure, engine):
    _, path = layout
    manifest = path.parent / "enrollment.toml"
    healthy_manifest = path.parent / "healthy.toml"
    healthy_manifest.write_text(manifest_text(tmp_path / "source"))
    if failure == "directory":
        manifest.mkdir()
    elif failure == "invalid":
        manifest.write_text("[broken")
    write_config(path, "worker roots")
    path.write_text(path.read_text() + '''
[[repos]]
name = "owner/healthy"
engine = "specloop"
manifest = "healthy.toml"
[[repos]]
name = "owner/bad"
''' + engine + ("" if failure == "absent" else 'manifest = "enrollment.toml"\n'))
    read_text = Path.read_text

    def no_manifest_read(self, *args, **kwargs):
        assert self not in (manifest, healthy_manifest), "config loading eagerly read enrollment"
        return read_text(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", no_manifest_read)
        cfg = load_without_writes(path, patch)
    assert cfg.worktree_root == path.parent / "worker roots"
    assert not cfg.worktree_root.exists()
    healthy, bad = cfg.repos
    assert healthy.engine == bad.engine == "specloop"
    assert healthy.manifest == healthy_manifest
    assert bad.manifest == (None if failure == "absent" else manifest)

    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")
    pipelines = [Pipeline(repo) for repo in cfg.repos]
    peer, pipeline = pipelines
    for candidate in pipelines:
        candidate.issue(7)
        candidate.approved(8)
    monkeypatch.setattr(specloop_intake, "run_git", peer.command)
    health = daemon.RepoHealth()
    poll(pipelines, health)
    assert health.failures == {bad.name: 1}
    assert not health.item_failures
    assert not any(c[0] == "git" or c[:2] in (["specloop", "propose"], ["specloop", "admit"])
                   for c in pipeline.calls)
    assert pipeline.issues[7]["labels"] == [{"name": "spec:draft"}]
    assert pipeline.prs[8]["labels"] == [{"name": "spec:approved"}]
    assert any(c[:2] == ["specloop", "propose"] for c in peer.calls)
    assert any(c[:2] == ["specloop", "admit"] for c in peer.calls)
    assert not peer.issues[7]["labels"] and not peer.prs[8]["labels"]
    change = peer.prs[8]["headRefName"].removeprefix("spec/")
    assert peer.runs[change]["pr"] == peer.prs[8]["url"]
    assert health.failures == {bad.name: 1}
    assert not cfg.worktree_root.exists()
