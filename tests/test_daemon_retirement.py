"""The front door owns polling and liveness, never an execution pool."""

import json
import os
import plistlib
import shlex
import signal
import socket
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest

from manifest_fixtures import manifest_text
from nightshift import cli, config, daemon, outcomes, queue, specloop_job
from nightshift.config import Config
from test_engine_routing import DraftGh
from test_engine_switch_recovery import RecoveryPipeline, labels, working
from test_specloop_attention import run_record, status_result
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
        task worker recovery trace preflight ThreadPoolExecutor Future
        futures_wait FIRST_COMPLETED""".split()
    assert not (set(retired) & vars(daemon).keys())


@pytest.mark.scenario("worker-retirement/sole-execution-engine/worker-execution-is-absent")
@pytest.mark.scenario("worker-retirement/client-command-boundaries-remain-stable/existing-clients-use-surviving-commands")
def test_retired_modules_are_absent_and_surviving_imports_load():
    # A fresh interpreter catches dependencies hidden by pytest's import cache.
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib
import importlib.abc
import importlib.util
import sys
import ast
import tomllib
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
             "nightshift.queue", "specloop.prompts"):
    importlib.import_module(name)

# Import every application module, including provider adapters, in a fresh process.
import pkgutil
import specloop
for package in (nightshift, specloop):
    for module in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        importlib.import_module(module.name)
    # Include imports behind unexecuted branches, including relative imports.
    for path in Path(package.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = package.__name__ + "." + ".".join(
                        path.parent.relative_to(Path(package.__file__).parent).parts)
                    base = importlib.util.resolve_name("." * node.level + base, parent.rstrip("."))
                names = [base, *(base + "." + alias.name for alias in node.names)]
            assert not any(name == "nightshift." + old or name.startswith("nightshift." + old + ".")
                           for name in names for old in retired), (path, names)
assert not {f"nightshift.{name}" for name in retired} & sys.modules.keys()
project = tomllib.loads(Path("pyproject.toml").read_text())
assert project["project"]["scripts"] == {"nightshift": "nightshift.cli:main", "specloop": "specloop.cli:main"}
assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["nightshift", "specloop"]
from nightshift import daemon, outcomes, queue
from specloop import state
assert daemon.STATE_DIR == Path.home() / ".nightshift"
assert outcomes.DB_PATH == daemon.STATE_DIR / "outcomes.sqlite3"
assert queue.CLAIM_DIR == daemon.STATE_DIR / "claims"
assert state.ROOT is None
assert state.state_root() == Path.home() / ".nightshift"
assert state.legacy_root() == Path.home() / ".specloop"
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
@pytest.mark.scenario("worker-retirement/retired-configuration-is-inert/old-knobs-cannot-control-builds")
@pytest.mark.scenario("nightshift-cli/explicit-daemon-controls/daemon-controls-retain-boundaries")
@pytest.mark.scenario("worker-retirement/sole-execution-engine/pause-and-one-shot-retain-control-boundaries")
@pytest.mark.real_trust
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("run_command", [["run"], ["daemon", "run"]])
def test_python_preflight_then_one_shot_dispatch(tmp_path, monkeypatch, capsys, caplog, legacy, run_command):
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
    if legacy:
        selected.write_text('endpoints = {url = "https://worker.invalid/secret"}\nmodels = "secret-model"\n'
                            + selected.read_text().replace('[daemon]', '[daemon]\nmax_tasks_per_night = 0')
                            + 'verify = "secret-command"\nconcurrency = 0\n')
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
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(queue, "_gh", gh)
    monkeypatch.setattr(daemon.notify, "send", forbidden)
    monkeypatch.setattr(daemon.specloop_intake, "run_git", external)
    monkeypatch.setattr(daemon, "poll_specloop", partial(daemon.poll_specloop, specloop_runner=external))
    assert cli.main(["preflight", "--deep", "--config", str(selected)]) == 0
    output = capsys.readouterr()
    assert "worker probes are retired" in output.out and output.out.endswith("\nready\n")
    assert [args[3] for args in probes] == ["owner/sample", "owner/private"]
    assert cli.main([*run_command, "--once", "--config", str(selected)]) == 0
    assert capsys.readouterr().out == "poll complete\n"
    assert [args[:2] for args in commands.calls] == [["specloop", "propose"], ["git", "-C"]]
    assert gh.labels == set() and len(gh.prs) == 1
    assert not queue.CLAIM_DIR.exists() and not daemon.HEARTBEAT_FILE.exists()
    assert manifest.read_bytes() == before
    assert sorted(tmp_path.glob("*.toml")) == sorted([selected, manifest])
    assert str(manifest) in commands.calls[0]
    assert "secret" not in caplog.text + repr(commands.calls)
    if legacy:
        assert {message.split(" is ignored;")[0] for message in caplog.messages if " is ignored;" in message} == {
            "endpoints", "models", "daemon.max_tasks_per_night", "repos.verify", "repos.concurrency",
        }


@pytest.mark.scenario("worker-retirement/retired-configuration-is-inert/worker-rejection-takes-precedence")
@pytest.mark.parametrize("command", [["daemon", "run", "--once"], ["preflight", "--deep"], ["attention", "--json"]])
@pytest.mark.parametrize("manifest", ["", 'manifest = 42\n'])
def test_worker_cli_refuses_before_any_child(tmp_path, monkeypatch, capsys, caplog, command, manifest):
    selected = tmp_path / "config.toml"
    selected.write_text('endpoints = false\nmodels = "private-model"\n[daemon]\nmax_tasks_per_night = 0\n'
                        '[[repos]]\nname = "owner/retired"\nengine = "worker"\n' + manifest)
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "absent-env")

    def forbidden(*args, **kwargs):
        pytest.fail("retired enrollment reached a child or dispatch")

    for target, name in ((subprocess, "run"), (subprocess, "Popen"), (daemon, "loop"),
                         (socket, "create_connection")):
        monkeypatch.setattr(target, name, forbidden)
    assert cli.main([*command, "--config", str(selected)]) == 2
    output = capsys.readouterr()
    assert output.out == "" and not caplog.messages
    assert output.err == ('configuration error: owner/retired: worker engine is retired; '
                          'use engine = "specloop" (or omit engine) and configure a trusted manifest\n')
    assert not daemon.HEARTBEAT_FILE.exists()


@pytest.mark.scenario("worker-retirement/historical-evidence-remains-available/recovery-preserves-interrupted-work")
@pytest.mark.scenario("worker-retirement/historical-evidence-remains-available/historical-attention-remains-readable")
@pytest.mark.parametrize("evidence", ["surviving", "missing", "unreadable"])
def test_one_shot_recovery_then_public_attention(tmp_path, monkeypatch, capsys, evidence):
    selected = tmp_path / "config.toml"
    selected.write_text('[[repos]]\nname = "owner/switched"\n')  # Recovery needs no manifest.
    monkeypatch.setattr(os, "environ", {"HOME": str(tmp_path), "PATH": ""})
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "absent-env")
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")
    monkeypatch.setattr(specloop_job, "_children", {})
    monkeypatch.setattr(daemon.signal, "signal", lambda *a: None)
    pipeline = RecoveryPipeline(config.load(selected).repos[0])
    claim = working(pipeline, 1, tmp_path, survives=evidence == "surviving")
    if evidence == "unreadable":
        claim.path.write_text("{broken historical evidence")
    original = claim.path.read_bytes()
    monkeypatch.setattr(queue, "_gh", pipeline.gh)

    def forbidden(*args, **kwargs):
        pytest.fail("historical recovery/read attempted execution or destructive cleanup")

    for target, name in ((subprocess, "run"), (subprocess, "Popen")):
        monkeypatch.setattr(target, name, forbidden)
    assert cli.main(["daemon", "run", "--once", "--config", str(selected)]) == 0
    assert capsys.readouterr().out == "poll complete\n"
    assert labels(pipeline, 1) == {"keep-me", {
        "surviving": "needs-human", "missing": "agent:ready", "unreadable": "agent:working",
    }[evidence]}
    assert len(outcomes.tasks()) == int(evidence != "unreadable")
    if evidence == "unreadable":
        assert claim.path.read_bytes() == original
    else:
        assert not claim.path.exists()
        journal = daemon.STATE_DIR / "engine-switch/owner__switched/1.json"
        assert json.loads(journal.read_text())["done"]
    if evidence == "surviving":
        assert (Path(claim.worktree) / "diff.txt").read_text() == "unfinished work"
    outcomes.record("owner/switched", 9, state="needs_decision", reason="Historical review")
    pipeline.issue(9)
    preserved = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(outcomes, "_read_github", lambda args, deadline: pipeline.gh(args))

    def status(args, **kwargs):
        assert args == ["specloop", "status", "--json"]
        assert kwargs["timeout"] == outcomes.REQUEST_SECONDS
        return status_result([run_record()])

    monkeypatch.setattr(subprocess, "run", status)
    for offline in (False, True):
        if offline:
            monkeypatch.setattr(subprocess, "run", forbidden)
            monkeypatch.setattr(outcomes, "_read_github", forbidden)
        assert cli.main(["attention", "--json", "--config", str(selected)] + (["--offline"] if offline else [])) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {"version", "offline", "tasks", "specloop", "specloop_error", "stale"}
        assert payload["version"] == 1 and payload["offline"] is offline
        assert {item["issue"] for item in payload["tasks"]} == ({9} if evidence == "unreadable" else {1, 9})
        assert any(item["issue"] == 9 and item["reason"] == "Historical review" for item in payload["tasks"])
        assert payload["specloop"][0]["change"] == "add-gate"
        assert payload["specloop"][0]["stale"] is False and payload["stale"] == []

    def unavailable(*args, **kwargs):
        raise OSError("transport unavailable")

    monkeypatch.setattr(subprocess, "run", unavailable)
    monkeypatch.setattr(outcomes, "_read_github", unavailable)
    for offline in (False, True):
        if offline:
            monkeypatch.setattr(subprocess, "run", forbidden)
            monkeypatch.setattr(outcomes, "_read_github", forbidden)
        assert cli.main(["attention", "--json", "--config", str(selected)] + (["--offline"] if offline else [])) == 1
        stale = json.loads(capsys.readouterr().out)
        assert stale["specloop_error"] and stale["specloop"][0]["stale"] is True
        assert stale["specloop"][0]["checked_at"] == payload["specloop"][0]["checked_at"]
        assert any(item["issue"] == 9 and item["error"] for item in stale["stale"])
        assert any(item["issue"] == 9 for item in stale["tasks"])
    monkeypatch.setattr(subprocess, "run", status)
    monkeypatch.setattr(outcomes, "_read_github", lambda *a: '{"state":"CLOSED","labels":[]}')
    assert cli.main(["attention", "--json", "--config", str(selected)]) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == []
    assert all(item["state"] == "resolved" for item in outcomes.tasks())
    assert {p: p.read_bytes() for p in preserved} == preserved


@pytest.mark.scenario("worker-retirement/service-prerequisites-match-the-surviving-engine/services-retain-independent-roles")
def test_bundled_service_roles_read_only():
    # These are repository templates, not installed jobs. Never execute either
    # wrapper: the daemon's unchanged shell guards still require legacy tools.
    from specloop import state

    assert daemon.STATE_DIR == Path.home() / ".nightshift"
    assert state.ROOT is None
    assert state.state_root() == Path.home() / ".nightshift"
    assert state.legacy_root() == Path.home() / ".specloop"
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
        # Parse the frozen argv without executing a wrapper or touching launchd.
        argv = target[1:] if service == "nightshift" else target[2:]
        parsed = cli.build_parser().parse_args(argv)
        if service == "nightshift":
            explicit = cli.build_parser().parse_args(["daemon", "run"])
            assert parsed.handler is explicit.handler is cli._daemon_command
            assert parsed.command == explicit.command == "run"
        else:
            assert parsed.handler is cli.build_cli.dispatch and parsed.cmd == "serve"
        if service == "nightshift":
            assert ['ENV_FILE=$HOME/.config/nightshift/env'] in lines
            assert ["if", "[", "!", "-f", "$ENV_FILE", "];", "then"] in lines
            assert [".", "$ENV_FILE"] in lines
            assert ["unset", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"] in lines
            assert ["for", "bin", "in", "gh", "claude", "pnpm", "node", "git", "swift;", "do"] in lines
            for token in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN"):
                assert ["if", "[", "-z", "${" + token + ":-}", "];", "then"] in lines


@pytest.mark.scenario("worker-retirement/service-prerequisites-match-the-surviving-engine/services-retain-independent-roles")
def test_alias_supervisor_uses_canonical_state_and_legacy_fallback(monkeypatch, capsys):
    from specloop import cli as build, serve, state

    canonical = state.Run.for_change("canonical")
    canonical.save()
    legacy = state.Run(state.legacy_root() / "runs" / "legacy")
    legacy.save()
    expected = [canonical.dir, legacy.dir]

    def supervisor(poll, notify, *, once):
        assert poll == 60 and once is True
        assert serve.heartbeat_path() == state.state_root() / "serve.json"
        assert [run.dir for run in serve.runs()] == expected
        return 0

    def forbidden(*a, **kw):
        pytest.fail("alias routing must not install or restart a service")

    monkeypatch.setattr(serve, "serve", supervisor)
    monkeypatch.setattr(build, "load_env", lambda *a: {})
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert build.main(["serve", "--once"]) == 0
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "specloop has been renamed to nightshift; use nightshift instead.\n"
