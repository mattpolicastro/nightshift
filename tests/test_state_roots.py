import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from specloop import agents, state


def assert_descriptor_guard(root):
    fd = os.open(Path(root).parent, os.O_RDONLY)
    try:
        for name in (Path(root).name, os.fsencode(Path(root).name)):
            probe = os.path.join(name, b"probe" if isinstance(name, bytes) else "probe")
            for access in (lambda: os.stat(name, dir_fd=fd),
                           lambda: os.lstat(name, dir_fd=fd),
                           lambda: os.readlink(name, dir_fd=fd),
                           lambda: os.access(name, os.F_OK, dir_fd=fd),
                           lambda: os.open(name, os.O_RDONLY, dir_fd=fd),
                           lambda: os.open(probe, os.O_RDONLY, dir_fd=fd)):
                try:
                    result = access()
                except AssertionError as error:
                    assert "live state access" in str(error)
                else:
                    if isinstance(result, int) and not isinstance(result, bool):
                        os.close(result)
                    raise AssertionError("guard did not intercept descriptor-relative access")
    finally:
        os.close(fd)


def assert_isolated_descriptor_access(root):
    fd = os.open(root, os.O_RDONLY)
    try:
        assert os.stat("probe", dir_fd=fd).st_size == len(b"isolated")
        probe = os.open("probe", os.O_RDONLY, dir_fd=fd)
        try:
            assert os.read(probe, 100) == b"isolated"
        finally:
            os.close(probe)
    finally:
        os.close(fd)


@pytest.mark.scenario("state-migration/lazy-isolated-roots/defaults-and-overrides-are-lazy")
def test_lazy_roots(tmp_path, monkeypatch):
    assert state.ROOT is None
    for variable in ("NIGHTSHIFT_STATE_ROOT", "NIGHTSHIFT_LEGACY_STATE_ROOT"):
        monkeypatch.delenv(variable)
    for home in (tmp_path / "one", tmp_path / "two"):
        monkeypatch.setenv("HOME", str(home))
        assert state.state_root() == home / ".nightshift"
        assert state.legacy_root() == home / ".specloop"
        assert state.run_dir("new") == home / ".nightshift/runs/new"
        assert agents.homes_dir() == state.homes_dir() == home / ".nightshift/homes"
        assert state.proposals_dir() == home / ".nightshift/drafts"
        assert list(state.iter_run_dirs()) == []
        assert not home.exists()
    for variable, resolver in (("NIGHTSHIFT_STATE_ROOT", state.state_root),
                               ("NIGHTSHIFT_LEGACY_STATE_ROOT", state.legacy_root)):
        for invalid in ("", " ", "relative"):
            monkeypatch.setenv(variable, invalid)
            with pytest.raises(ValueError, match=variable):
                resolver()
        monkeypatch.setenv(variable, str(tmp_path / variable))
        assert resolver() == tmp_path / variable
    monkeypatch.setattr(state, "ROOT", tmp_path / "patched")
    assert state.state_root() == tmp_path / "patched"
    assert agents.homes_dir() == tmp_path / "patched/homes"
    assert not (tmp_path / "patched").exists()


@pytest.mark.scenario("state-migration/lazy-isolated-roots/tests-cannot-touch-live-state")
def test_live_state_guard_in_parent_and_child(tmp_path, monkeypatch):
    from conftest import LIVE_STATE_ROOTS

    # These calls must raise before the OS can observe existence or contents.
    for root in LIVE_STATE_ROOTS:
        path = Path(root)
        for access in (path.stat, path.lstat, path.read_bytes, lambda: list(path.iterdir()),
                       lambda: os.open(path, os.O_RDONLY), lambda: path.mkdir(exist_ok=True)):
            with pytest.raises(AssertionError, match="live state access"):
                access()
        assert_descriptor_guard(root)
    # Following a link through the real home's parent is blocked as well.
    link = tmp_path / "home-link"
    link.symlink_to(Path(LIVE_STATE_ROOTS[0]).parent, target_is_directory=True)
    with pytest.raises(AssertionError, match="live state access"):
        (link / ".nightshift" / "probe").read_bytes()
    with pytest.raises(AssertionError, match="live state access"):
        (link / ".specloop" / "probe").lstat()
    (tmp_path / "sitecustomize.py").write_text("import os\nos.environ['CUSTOM_BOOTSTRAP'] = 'loaded'\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    script = f"""
import os
from pathlib import Path
from specloop import state
{inspect.getsource(assert_descriptor_guard)}
{inspect.getsource(assert_isolated_descriptor_access)}
assert os.environ['CUSTOM_BOOTSTRAP'] == 'loaded'
for root in {LIVE_STATE_ROOTS!r}:
    assert_descriptor_guard(root)
    for access in (lambda: Path(root).stat(), lambda: Path(root).read_bytes(),
                   lambda: list(Path(root).iterdir())):
        try:
            access()
        except AssertionError as e:
            assert 'live state access' in str(e)
        else:
            raise AssertionError('guard did not intercept access')
for root in (state.state_root(), state.legacy_root()):
    assert root == Path(os.environ['HOME']) / root.name
    root.mkdir(exist_ok=True)
    (root / 'probe').write_text('isolated')
    assert_isolated_descriptor_access(root)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for root in (state.state_root(), state.legacy_root()):
        assert (root / "probe").read_text() == "isolated"
        assert_isolated_descriptor_access(root)


def tree_snapshot(*roots):
    return {str(p): (p.lstat().st_mtime_ns, os.readlink(p) if p.is_symlink()
                    else p.read_bytes() if p.is_file() else None)
            for root in roots for p in [root, *root.rglob("*")] if p.exists()}


def test_heartbeat_read_falls_back_without_writes():
    legacy = state.legacy_root() / "serve.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"pid": 123}))
    before = tree_snapshot(state.state_root(), state.legacy_root())
    assert state.heartbeat_path(existing=True) == legacy
    assert state.heartbeat_path() == state.state_root() / "serve.json"
    assert tree_snapshot(state.state_root(), state.legacy_root()) == before
