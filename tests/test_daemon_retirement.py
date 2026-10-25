"""The front door owns polling and liveness, never an execution pool."""

import os
import plistlib
import shlex
import signal
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest

from manifest_fixtures import manifest_text
from nightshift import cli, config, daemon, queue, specloop_job
from nightshift.config import Config
from test_engine_routing import DraftGh
from test_specloop_intake import Commands


@pytest.fixture
def lifecycle(monkeypatch):
    events, handlers = [], {}
    real_beat = daemon.beat

    def beat(started):
        events.append("beat")
        real_beat(started)

    def startup(*args):
        events.append("startup")

    def forbidden(*args, **kwargs):
        pytest.fail("polling must not claim, wait for, or stop detached work")

    class DetachedChild:
        def poll(self):
            events.append("reap")
            return None

        wait = terminate = kill = forbidden

    child = DetachedChild()
    monkeypatch.setattr(specloop_job, "_children", {"detached": child})
    monkeypatch.setattr(daemon, "beat", beat)
    monkeypatch.setattr(daemon, "startup", startup)
    monkeypatch.setattr(daemon, "_note_unclean_exit", lambda *a: events.append("check-crash"))
    monkeypatch.setattr(daemon, "poll_specloop", lambda *a: events.append("poll"))
    monkeypatch.setattr(queue, "claim", forbidden)
    monkeypatch.setattr(daemon.signal, "signal", lambda sig, handler: handlers.update({sig: handler}))
    return events, handlers, child


@pytest.mark.scenario("worker-retirement/sole-execution-engine/pause-and-one-shot-retain-control-boundaries")
@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("existing_heartbeat", [False, True])
def test_once_does_one_pass_without_waiting(lifecycle, monkeypatch, paused, existing_heartbeat):
    events, _, child = lifecycle
    if existing_heartbeat:
        daemon.HEARTBEAT_FILE.write_text("service heartbeat")
    if paused:
        daemon.PAUSE_FILE.touch()
    monkeypatch.setattr(daemon.time, "sleep", lambda _: pytest.fail("one-shot slept"))

    assert daemon.loop(Config(repos=[]), {}, once=True) is None

    assert events == ["startup", "reap"] + ([] if paused else ["poll"])
    assert specloop_job._children == {"detached": child}
    assert daemon.HEARTBEAT_FILE.exists() == existing_heartbeat
    if existing_heartbeat:
        assert daemon.HEARTBEAT_FILE.read_text() == "service heartbeat"


@pytest.mark.scenario("worker-retirement/sole-execution-engine/pause-and-one-shot-retain-control-boundaries")
@pytest.mark.scenario("worker-retirement/sole-execution-engine/polling-remains-responsive")
@pytest.mark.parametrize("paused", [False, True])
def test_service_keeps_beating_and_reaping_until_sigterm(lifecycle, monkeypatch, paused):
    events, handlers, child = lifecycle
    if paused:
        daemon.PAUSE_FILE.touch()
    sleeps = []

    def sleep(seconds):
        assert daemon.heartbeat().alive
        sleeps.append(seconds)
        events.append("sleep")
        if len(sleeps) == 2:
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        daemon.PAUSE_FILE.unlink(missing_ok=True)

    monkeypatch.setattr(daemon.time, "sleep", sleep)
    with pytest.raises(SystemExit) as stop:
        daemon.loop(Config(repos=[], poll_seconds=7), {})

    assert stop.value.code == 0 and sleeps == [7, 7]
    assert events == (["check-crash", "beat", "startup", "beat", "reap"]
                      + ([] if paused else ["poll"])
                      + ["sleep", "beat", "reap", "poll", "sleep"])
    assert not daemon.HEARTBEAT_FILE.exists()
    assert specloop_job._children == {"detached": child}


@pytest.mark.scenario("worker-retirement/sole-execution-engine/pause-and-one-shot-retain-control-boundaries")
@pytest.mark.parametrize("once", [False, True])
@pytest.mark.parametrize("failure", ["exception", "exit", "sigterm"])
def test_startup_failure_remains_visible_but_sigterm_is_orderly(lifecycle, monkeypatch, once, failure):
    events, handlers, child = lifecycle
    if once:
        daemon.HEARTBEAT_FILE.write_text("service heartbeat")

    def startup(*args):
        assert daemon.HEARTBEAT_FILE.exists()
        if failure == "sigterm":
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        if failure == "exit":
            raise SystemExit(1)
        raise RuntimeError("startup failed")

    monkeypatch.setattr(daemon, "startup", startup)
    with pytest.raises(RuntimeError if failure == "exception" else SystemExit):
        daemon.loop(Config(repos=[]), {}, once=once)

    assert events == ([] if once else ["check-crash", "beat"])
    assert daemon.HEARTBEAT_FILE.exists() == (once or failure != "sigterm")
    if once:
        assert daemon.HEARTBEAT_FILE.read_text() == "service heartbeat"
    assert specloop_job._children == {"detached": child}


@pytest.mark.scenario("worker-retirement/sole-execution-engine/worker-execution-is-absent")
def test_daemon_has_no_worker_executor_or_dispatch_entry_points():
    retired = """Claimed Tally _WindowStart _FALLBACK_WINDOW_SECONDS
        endpoints_ready claim_next run_claimed _run_claimed _recover _merge
        _running _harvest _wrap_up _record_quota _branch_to_delete _escalation_text
        task worker recovery trace vcs preflight ThreadPoolExecutor Future
        futures_wait FIRST_COMPLETED""".split()
    assert not (set(retired) & vars(daemon).keys())


@pytest.mark.scenario("worker-retirement/sole-execution-engine/worker-execution-is-absent")
def test_retired_modules_are_absent_and_surviving_imports_load():
    # A fresh interpreter catches dependencies hidden by pytest's import cache.
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib
import importlib.abc
import importlib.util
import sys
from pathlib import Path
import nightshift

package = Path(nightshift.__file__).parent
retired = ("task", "worker", "prompts", "recovery")
for name in retired:
    assert not (package / f"{name}.py").exists(), name
    assert importlib.util.find_spec(f"nightshift.{name}") is None, name

class RejectRetired(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        assert fullname not in {f"nightshift.{name}" for name in retired}, fullname

# Even a swallowed optional import of the retired engine is a regression.
sys.meta_path.insert(0, RejectRetired())
for name in ("nightshift.cli", "specloop.cli"):
    assert callable(importlib.import_module(name).main), name
for name in ("nightshift.preflight", "nightshift.trace", "nightshift.mirror",
             "nightshift.queue", "nightshift.vcs", "specloop.prompts"):
    importlib.import_module(name)

# Import every application module, including provider adapters, in a fresh process.
import pkgutil
import specloop
for package in (nightshift, specloop):
    for module in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        importlib.import_module(module.name)
assert not {f"nightshift.{name}" for name in retired} & sys.modules.keys()
for module, names in {
    "nightshift.cli": ("_elapsed", "_progress_of"),
    "nightshift.preflight": ("_endpoint_checks", "_one_endpoint", "_default_prober",
                            "_LIST_TIMEOUT_S", "_BANNED_VARS", "worker"),
}.items():
    assert not set(names) & vars(importlib.import_module(module)).keys(), module
"""],
        cwd=Path(daemon.__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.scenario("worker-retirement/service-prerequisites-match-the-surviving-engine/codex-enrollment-needs-no-worker-login")
@pytest.mark.real_trust
def test_python_preflight_then_one_shot_dispatch(tmp_path, monkeypatch, capsys):
    # Failure permutations live in test_preflight (task 1.8); here a successful
    # preflight is followed by real CLI/daemon dispatch using the same enrollment.
    manifest = tmp_path / "repository.toml"
    manifest.write_text(manifest_text(tmp_path / "source"))
    before = manifest.read_bytes()
    selected = tmp_path / "config.toml"
    selected.write_text('''[daemon]
forbidden_probe = "owner/private"
[[repos]]
name = "owner/sample"
manifest = "repository.toml"
''')
    env_file = tmp_path / "env"
    env_file.write_text("export GH_TOKEN='github_pat_test'\n"
                        "export NIGHTSHIFT_SLACK_WEBHOOK='https://hooks.invalid/test'\n")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.setattr(os, "environ", {"HOME": str(tmp_path), "PATH": ""})
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")
    monkeypatch.setattr(specloop_job, "_children", {})
    monkeypatch.setattr(daemon.signal, "signal", lambda *a: None)
    gh, commands, probes = DraftGh(), Commands(), []

    def external(args, **kwargs):
        assert os.environ["GH_TOKEN"] == "github_pat_test"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ
        assert os.environ["PATH"] == ""
        if args[:3] == ["gh", "repo", "view"]:
            probes.append(args)
            assert args[4:] == ["--json", "name"]
            return subprocess.CompletedProcess(args, int(args[3] == "owner/private"), "{}", "not found")
        return commands(args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("unexpected worker probe, claim, live process or alert")

    monkeypatch.setattr(subprocess, "run", external)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(queue, "claim", forbidden)
    monkeypatch.setattr(queue, "_gh", gh)
    monkeypatch.setattr(daemon.notify, "send", forbidden)
    monkeypatch.setattr(daemon.specloop_intake, "run_git", external)
    monkeypatch.setattr(daemon, "poll_specloop", partial(daemon.poll_specloop, specloop_runner=external))
    assert cli.main(["preflight", "--deep", "--config", str(selected)]) == 0
    output = capsys.readouterr()
    assert "worker probes are retired" in output.out and output.out.endswith("\nready\n")
    assert [args[3] for args in probes] == ["owner/sample", "owner/private"]
    assert cli.main(["run", "--once", "--config", str(selected)]) == 0
    assert capsys.readouterr().out == "poll complete\n"
    assert [args[:2] for args in commands.calls] == [["specloop", "propose"], ["git", "-C"]]
    assert gh.labels == set() and len(gh.prs) == 1
    assert not queue.CLAIM_DIR.exists() and not daemon.HEARTBEAT_FILE.exists()
    assert manifest.read_bytes() == before


@pytest.mark.scenario("worker-retirement/service-prerequisites-match-the-surviving-engine/services-retain-independent-roles")
def test_bundled_service_roles_read_only():
    # These are repository templates, not installed jobs. Never execute either
    # wrapper: the daemon's unchanged shell guards still require legacy tools.
    from specloop import state

    assert daemon.STATE_DIR == Path.home() / ".nightshift"
    assert state.ROOT == Path.home() / ".specloop"
    root = Path(__file__).resolve().parents[1]
    home = "/Users/YOUR_USERNAME"
    for service, wrapper, target, keepalive, process_type in (
        ("nightshift", "nightshift-daemon", ["nightshift", "run"], {"SuccessfulExit": False}, "Background"),
        ("specloop", "specloop-serve", ["--no-sync", "specloop", "serve"], True, "Standard"),
    ):
        with (root / "launchd" / f"com.mattpolicastro.{service}.plist").open("rb") as stream:
            job = plistlib.load(stream)
        assert job["Label"] == f"com.mattpolicastro.{service}"
        assert job["ProgramArguments"] == [f"{home}/Projects/nightshift/bin/{wrapper}"]
        assert job["WorkingDirectory"] == f"{home}/Projects/nightshift"
        assert job["StandardOutPath"] == job["StandardErrorPath"] == f"{home}/.logs/{service}.log"
        assert job["RunAtLoad"] is True and job["ThrottleInterval"] == 300
        assert job["KeepAlive"] == keepalive and job["ProcessType"] == process_type
        assert job.get("Nice") == (5 if service == "nightshift" else None)
        lines = [shlex.split(line, comments=True) for line in (root / "bin" / wrapper).read_text().splitlines()]
        assert [line for line in lines if line[:1] == ["exec"]] == [["exec", "/opt/homebrew/bin/uv", "run", *target]]
        assert ["cd", "$HOME/Projects/nightshift"] in lines
        if service == "nightshift":
            assert ['ENV_FILE=$HOME/.config/nightshift/env'] in lines
            assert ["if", "[", "!", "-f", "$ENV_FILE", "];", "then"] in lines
            assert [".", "$ENV_FILE"] in lines
            assert ["unset", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"] in lines
            assert ["for", "bin", "in", "gh", "claude", "pnpm", "node", "git", "swift;", "do"] in lines
            for token in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN"):
                assert ["if", "[", "-z", "${" + token + ":-}", "];", "then"] in lines
