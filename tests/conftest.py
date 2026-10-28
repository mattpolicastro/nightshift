import os
from pathlib import Path
import subprocess

import pytest

from live_state_guard import install

# Capture names only, before HOME is replaced; never inspect live directories.
_REAL_HOME = Path.home()
LIVE_STATE_ROOTS = getattr(os, "_nightshift_test_guard_roots", tuple(
    str(home / name) for home in (_REAL_HOME, _REAL_HOME.resolve())
    for name in (".nightshift", ".specloop")))
install(LIVE_STATE_ROOTS)

from nightshift import config, daemon, mirror, outcomes, queue, specloop_intake


def pytest_configure(config):
    config.addinivalue_line("markers", "scenario(id): OpenSpec scenario exercised by this test")
    config.addinivalue_line("markers", "real_trust: use the production author-association filter unmodified")


@pytest.fixture(autouse=True)
def trust_fakes_without_associations(request, monkeypatch):
    """GitHub fakes written before the author filter carry no association field.

    Production treats a missing association as untrusted (queue.trusted). Here,
    an item with no association key at all is treated as a collaborator's, and
    the issue-author lookup is stubbed, so those fakes keep testing what they
    were written for. Items that do carry an association are still judged by
    the real rule, and tests marked `real_trust` get production behaviour.
    """
    if request.node.get_closest_marker("real_trust"):
        return
    real = queue.trusted

    def trusted(item, key="authorAssociation"):
        if "authorAssociation" not in item and "author_association" not in item:
            return True
        return real(item, key)

    monkeypatch.setattr(queue, "trusted", trusted)
    monkeypatch.setattr(specloop_intake, "_issue_author_trusted", lambda repo, number, gh: True)


@pytest.fixture(autouse=True)
def isolate_outcome_store(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("test-home")
    state = home / ".nightshift"
    state.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NIGHTSHIFT_STATE_ROOT", str(state))
    monkeypatch.setenv("NIGHTSHIFT_LEGACY_STATE_ROOT", str(home / ".specloop"))
    monkeypatch.setattr(config, "ENV_FILE", home / ".config/nightshift/env")
    monkeypatch.setattr(config, "DEFAULT_PATH", home / "config.toml")
    monkeypatch.delenv("NIGHTSHIFT_FORGE_CONFIG", raising=False)
    monkeypatch.setattr(mirror, "DEFAULT_CONFIG_ROOT", home / ".config/nightshift/claude")
    monkeypatch.setattr(mirror, "DEFAULT_PROJECTS", home / ".claude/projects")
    monkeypatch.setattr(daemon, "STATE_DIR", state)
    monkeypatch.setattr(queue, "CLAIM_DIR", state / "claims")
    monkeypatch.setattr(outcomes, "DB_PATH", state / "outcomes.sqlite3")
    # Loop tests must never touch the live service's heartbeat.
    monkeypatch.setattr(daemon, "HEARTBEAT_FILE", state / "heartbeat.json")
    # The daemon's kill switch must also be isolated from the live service.
    monkeypatch.setattr(daemon, "PAUSE_FILE", state / "pause")
    # Python children receive the same guard even when a fixture replaces
    # PYTHONPATH. Keep this generated bootstrap outside the candidate source.
    bootstrap = home / "test-bootstrap"
    bootstrap.mkdir()
    # Children cannot inherit DEFAULT_PATH monkeypatches; select an empty trusted source.
    child_config = bootstrap / "forge-config.toml"
    child_config.write_text("")
    guard = Path(__file__).with_name("live_state_guard.py").read_text()
    (bootstrap / "sitecustomize.py").write_text(guard + f"\ninstall({LIVE_STATE_ROOTS!r})\n" + """
# Preserve fixture-owned startup hooks, such as process-group inspection.
import importlib.machinery, importlib.util
seen = getattr(os, '_nightshift_test_bootstraps', set())
seen.add(os.path.dirname(__file__))
os._nightshift_test_bootstraps = seen
spec = importlib.machinery.PathFinder.find_spec(
    'sitecustomize', [p for p in sys.path if os.path.abspath(p) not in seen])
if spec and spec.loader:
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
""")
    original = subprocess.Popen.__init__

    def guarded_child(self, *args, **kwargs):
        env = dict(kwargs.get("env") if kwargs.get("env") is not None else os.environ)
        env.setdefault("NIGHTSHIFT_FORGE_CONFIG", str(child_config))
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(bootstrap), env.get("PYTHONPATH"))))
        kwargs["env"] = env
        original(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", guarded_child)
