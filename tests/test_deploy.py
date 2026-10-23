import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import deploy

CHECK = "uv sync --check --frozen --offline"
STATUS = "git status --porcelain=v1 --untracked-files=all"
COUNTS = "git rev-list --left-right --count HEAD...@{upstream}"
STALE = (1, "", "Would install 1 package\n + mypy==2.4.0\nThe environment is outdated; run `uv sync`")
SERVICE_STATUS = "uv run --no-sync specloop status --json"
CURRENT_START = "Mon Jan  1 00:00:00 2024"


@pytest.fixture
def recording(tmp_path):
    (tmp_path / ".nightshift").mkdir()
    (tmp_path / ".nightshift/heartbeat.json").write_text('{"pid": 101}')
    (tmp_path / ".venv").mkdir()
    context = deploy.Context(tmp_path, {"VIRTUAL_ENV": "/wrong", "UV_PROJECT": "/wrong",
        "UV_PROJECT_ENVIRONMENT": "/wrong", "UV_WORKING_DIR": "/wrong", "UV_NO_DEV": "1"},
        daemon_state=tmp_path / ".nightshift", supervisor_state=tmp_path / ".specloop")
    replies = {"git rev-parse --show-toplevel": str(tmp_path), STATUS: "",
        "git symbolic-ref --quiet --short HEAD": "main", "git rev-parse --verify @{upstream}": "upstream",
        "git config --get branch.main.remote": "origin", "git fetch -- origin": "",
        COUNTS: "0 0", "git rev-parse HEAD": "before", CHECK: "", "uv sync --frozen": ""}
    calls = []
    replies.update({f"git log -1 --format=%ct HEAD -- {path}/": "1704067200"
                    for path in ("nightshift", "specloop")})
    replies.update({f"/bin/ps -p {pid} -o lstart=": CURRENT_START for pid in (101, 201)})
    replies[SERVICE_STATUS] = json.dumps({"version": 1, "supervisor": {
        "state": "running (pid 201, last poll 2s ago, driving nothing)"}, "runs": []})

    def runner(argv, *, cwd, env, timeout, text):
        key = " ".join(argv)
        calls.append(key)
        assert cwd == tmp_path and timeout > 0
        assert text is (not key.startswith(("git ls-tree", "git cat-file")))
        assert key in replies, f"unexpected command: {argv}"
        value = replies[key]
        value = value.pop(0) if isinstance(value, list) else value
        if isinstance(value, BaseException):
            raise value
        if argv[0] == "uv":
            assert env["UV_PROJECT_ENVIRONMENT"] == str(tmp_path / ".venv")
            assert not {"VIRTUAL_ENV", "UV_PROJECT", "UV_WORKING_DIR", "UV_NO_DEV"} & env.keys()
        if key == "git pull --ff-only":
            replies["git rev-parse HEAD"] = "after"
        if key == "uv sync --frozen":
            (tmp_path / ".venv").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(argv, *(value if isinstance(value, tuple) else (0, value, "")))

    return SimpleNamespace(context=context, replies=replies, calls=calls, runner=runner,
        run=lambda *args: deploy.main(list(args), runner=runner, context=context))


@pytest.mark.scenario("deployment-drift/checkout-drift-and-refusal/unsafe-checkout-refuses-application")
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("command,value", [
    (STATUS, " M tracked"), (STATUS, "M  staged"), (STATUS, "?? new"),
    (STATUS, ["", " M changed-before-pull"]), (COUNTS, "1 0"), (COUNTS, "1 2"),
    ("git symbolic-ref --quiet --short HEAD", "topic"),
    ("git symbolic-ref --quiet --short HEAD", (1, "", "detached")),
    ("git rev-parse --verify @{upstream}", (128, "", "no upstream")),
    ("git config --get branch.main.remote", (1, "", "no remote")),
    ("git fetch -- origin", (128, "", "fetch failed")),
    ("git fetch -- origin", subprocess.TimeoutExpired("git fetch", 120)),
])
def test_apply_refuses_dirty_or_diverged_checkout(recording, command, value, force):
    recording.replies[command] = value.copy() if isinstance(value, list) else value
    recording.replies["git config --get branch.topic.remote"] = "origin"
    assert recording.run("--apply", *(["--force"] if force else [])) == 1
    assert not any(c.startswith(("git pull", "uv")) for c in recording.calls)


@pytest.mark.scenario("deployment-drift/environment-synchronization/missing-dependency-is-drift")
@pytest.mark.scenario("deployment-drift/environment-synchronization/conditional-frozen-sync")
@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("state", ["current", "stale", "missing", "error", "unknown", "still-stale", "sync-error"])
def test_check_reports_stale_venv(recording, state, apply, capsys):
    lock = recording.context.cwd / "uv.lock"
    lock.write_text("unchanged lockfile")
    if state == "missing":
        (recording.context.cwd / ".venv").rmdir()
    if state in {"stale", "still-stale", "sync-error"}:
        recording.replies[CHECK] = [STALE, STALE if state == "still-stale" else ""]
    if state in {"error", "unknown"}:
        recording.replies[CHECK] = (2 if state == "error" else 1, "", "lockfile unreadable")
    if state == "sync-error":
        recording.replies["uv sync --frozen"] = (1, "", "sync failed")
    expected = state != "current" and not (apply and state in {"stale", "missing"})
    assert recording.run(*(["--apply"] if apply else [])) == int(expected)
    assert recording.calls.count("uv sync --frozen") == int(apply and state in {"stale", "missing", "still-stale", "sync-error"})
    assert recording.calls.count(CHECK) == (2 if apply and state in {"stale", "missing", "still-stale"} else 1)
    assert lock.read_text() == "unchanged lockfile"
    if state in {"error", "unknown"}:
        assert "lockfile unreadable" in capsys.readouterr().out


@pytest.mark.scenario("deployment-drift/checkout-drift-and-refusal/fetch-reveals-behind-base")
@pytest.mark.parametrize("apply", [False, True])
def test_fetch_and_post_pull_head(recording, apply):
    recording.replies[COUNTS] = "0 2"
    recording.replies["git pull --ff-only"] = ""
    assert recording.run(*(["--apply"] if apply else [])) == (0 if apply else 1)
    assert recording.context.head == ("after" if apply else "before")
    assert recording.calls.index("git fetch -- origin") < recording.calls.index(COUNTS)
    assert recording.calls.count("git pull --ff-only") == int(apply)
    assert recording.calls.index("git rev-parse HEAD") < recording.calls.index(CHECK)
    if apply:
        assert recording.calls.index("git pull --ff-only") < recording.calls.index("git rev-parse HEAD")


@pytest.mark.scenario("deployment-drift/standalone-deployment-command/flags-and-plugin-location")
@pytest.mark.parametrize("env,flag,expected", [(None, None, None), ("env", None, "env"), ("env", "flag", "flag")])
def test_options(recording, env, flag, expected, capsys):
    if env is not None:
        recording.context.env["NIGHTSHIFT_PLUGIN_DIR"] = env
    args = deploy.parse_options([] if flag is None else ["--plugin-dir", flag], recording.context)
    assert args.plugin_dir == (recording.context.cwd / expected if expected else None)
    assert (args.base_branch, args.reload_cmd) == ("main", ["paseo", "plugin", "reload", "nightshift"])
    assert (args.daemon_label, args.supervisor_label) == ("com.mattpolicastro.nightshift", "com.mattpolicastro.specloop")
    custom = deploy.parse_options(["--apply", "--force", "--base-branch", "release", "--reload-cmd", "tool 'two words'"], recording.context)
    assert custom.force and custom.base_branch == "release" and custom.reload_cmd == ["tool", "two words"]
    assert deploy.parse_options(["--reload-cmd", ""], recording.context).reload_cmd == []
    if expected is None:
        assert recording.run() == 0
        assert "plugin check skipped: no plugin directory configured" in capsys.readouterr().out


@pytest.mark.scenario("deployment-drift/standalone-deployment-command/flags-and-plugin-location")
@pytest.mark.parametrize("argv", [["--force"], ["--plugin-dir", ""], ["--bogus"], ["--base-branch"], ["--reload-cmd", "'"]])
def test_invalid_options_have_no_effects(recording, argv):
    with pytest.raises(SystemExit) as exc:
        recording.run(*argv)
    assert exc.value.code == 2 and recording.calls == []


@pytest.mark.scenario("deployment-drift/standalone-deployment-command/flags-and-plugin-location")
def test_path_expansion_and_empty_environment(recording, monkeypatch):
    monkeypatch.setenv("HOME", str(recording.context.cwd))
    args = deploy.parse_options(["--plugin-dir", "~/plugin", "--daemon-label", "daemon",
                                "--supervisor-label", "supervisor"], recording.context)
    assert args.plugin_dir == recording.context.cwd / "plugin"
    assert (args.daemon_label, args.supervisor_label) == ("daemon", "supervisor")
    recording.context.env["NIGHTSHIFT_PLUGIN_DIR"] = ""
    with pytest.raises(SystemExit) as exc:
        recording.run()
    assert exc.value.code == 2 and recording.calls == []


def test_check_keeps_observing_after_fetch_failure(recording, capsys):
    recording.replies["git fetch -- origin"] = (128, "", "fetch unavailable")
    recording.replies[CHECK] = FileNotFoundError("uv unavailable")
    assert recording.run() == 1 and CHECK in recording.calls
    output = capsys.readouterr().out
    assert "fetch unavailable" in output and "uv unavailable" in output


@pytest.fixture(params=[b"main.ts", b"./main.ts"])
def plugin_recording(recording, request):
    r = recording
    r.destination = r.context.cwd.with_name(r.context.cwd.name + "-plugin")
    r.state = r.destination.parent / f".{r.destination.name}.nightshift-deploy"
    r.marker = r.state / "pending-reload"
    r.files = {"main.ts": ("100644", b"committed"), "bin/tool": ("100755", b"tool"),
               "link": ("120000", request.param), "generated/tracked": ("100644", b"tracked")}
    r.tree = "git ls-tree -rz HEAD -- plugins/nightshift"
    r.replies[r.tree] = b"".join(f"{mode} blob {i}\tplugins/nightshift/{name}\0".encode()
                               for i, (name, (mode, _)) in enumerate(r.files.items()))
    for i, (name, (mode, data)) in enumerate(r.files.items()):
        r.replies[f"git cat-file blob {i}"] = data
        path = r.destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            path.symlink_to(data.decode())
        else:
            path.write_bytes(data)
            path.chmod(int(mode, 8) & 0o777)
    return r


@pytest.mark.scenario("deployment-drift/committed-plugin-drift/plugin-differs-from-committed-tree")
@pytest.mark.parametrize("change", ["current", "missing-directory", "missing", "changed", "obsolete",
                                    "ignored", "mode", "link", "link-spelling", "type", "tracked-ignored", "parent-link"])
def test_check_reports_plugin_drift(plugin_recording, change, capsys):
    r = plugin_recording
    path = r.destination / "main.ts"
    if change == "missing-directory":
        deploy.shutil.rmtree(r.destination)
    elif change == "missing":
        path.unlink()
    elif change == "changed":
        path.write_text("uncommitted")
    elif change in {"obsolete", "ignored"}:
        (r.destination / change).write_text("extra")
        r.replies[f"git check-ignore --quiet --no-index -- plugins/nightshift/{change}"] = (0 if change == "ignored" else 1, "", "")
    elif change == "mode":
        (r.destination / "bin/tool").chmod(0o644)
    elif change in {"link", "link-spelling", "type"}:
        (r.destination / "link").unlink()
        if change == "link":
            (r.destination / "link").symlink_to("bin/tool")
        elif change == "link-spelling":
            (r.destination / "link").symlink_to("main.ts" if r.files["link"][1] == b"./main.ts" else "./main.ts")
        else:
            (r.destination / "link").write_text("main.ts")
    elif change == "tracked-ignored":
        (r.destination / "generated/tracked").write_text("changed")
    elif change == "parent-link":
        (r.destination / "bin").rename(r.destination / "elsewhere")
        (r.destination / "bin").symlink_to("elsewhere")
    assert r.run("--plugin-dir", str(r.destination)) == int(change not in {"current", "ignored"})
    assert ("differs from committed HEAD" in capsys.readouterr().out) == (change not in {"current", "ignored"})
    if change in {"current", "ignored"}:
        assert r.run("--apply", "--plugin-dir", str(r.destination)) == 0
    assert not r.state.exists() and "uv sync --frozen" not in r.calls


@pytest.mark.scenario("deployment-drift/staged-plugin-replacement/swap-retains-rollback-copies")
@pytest.mark.scenario("deployment-drift/staged-plugin-replacement/unsafe-destination-or-failed-swap")
@pytest.mark.parametrize("failure", [None, "first-install", "parent-alias", "destination-link", "inside", "ancestor", "file",
    "escape", "absolute", "link-escape", "link-chain", "link-cycle", "submodule", "parent-entry",
    "staging", "verification", "marker", "rename-1", "rename-2"])
def test_apply_swaps_plugin_keeping_rollback(plugin_recording, failure, monkeypatch, capsys):
    r = plugin_recording
    destination = r.destination
    (destination / "main.ts").write_text("old")
    unrelated = destination.with_name(destination.name + "-backup")
    unrelated.mkdir()
    r.state.mkdir()
    (r.state / "rollback-unrelated").mkdir()
    if failure == "first-install":
        deploy.shutil.rmtree(destination)
    elif failure == "parent-alias":
        alias = r.context.cwd / "alias"
        alias.symlink_to(destination.parent)
        destination = alias / destination.name
    elif failure == "destination-link":
        destination.rename(unrelated / "target")
        destination.symlink_to(unrelated / "target")
    elif failure in {"inside", "ancestor", "file"}:
        destination = {"inside": r.context.cwd / "plugin", "ancestor": r.context.cwd.parent,
                       "file": r.destination / "main.ts"}[failure]
    elif failure in {"escape", "absolute", "submodule", "parent-entry"}:
        entry = {"escape": "100644 blob 0\tplugins/nightshift/../escape",
                 "absolute": "100644 blob 0\tplugins/nightshift//escape",
                 "submodule": "160000 commit 0\tplugins/nightshift/module",
                 "parent-entry": "100644 blob 0\tplugins/nightshift/bin"}[failure]
        r.replies[r.tree] += entry.encode() + b"\0"
    elif failure in {"link-escape", "link-chain", "link-cycle"}:
        r.replies["git cat-file blob 2"] = {"link-escape": b"../../escape", "link-chain": b"other", "link-cycle": b"link"}[failure]
        if failure == "link-chain":
            r.replies[r.tree] += b"120000 blob 4\tplugins/nightshift/other\0"
            r.replies["git cat-file blob 4"] = b"/outside"
    original_open, original_rename = Path.open, Path.rename
    renames = []

    def open_path(path, *args, **kwargs):
        if (failure == "marker" and path == r.marker) or (failure == "staging" and ".stage-" in str(path)):
            raise OSError(f"{failure} failed")
        return original_open(path, *args, **kwargs)

    def rename(path, target):
        assert r.marker.read_bytes() == b""  # Written before either rename.
        renames.append((path, target))
        if failure == f"rename-{len(renames)}":
            raise OSError("rename failed")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "open", open_path)
    monkeypatch.setattr(Path, "rename", rename)
    if failure == "verification":
        monkeypatch.setattr(deploy, "plugin_differs", lambda *args: True)
    assert r.run("--apply", "--plugin-dir", str(destination), "--reload-cmd", "") == 1
    backups = sorted(r.state.glob("rollback-0*"))
    if failure in {None, "first-install", "parent-alias"}:
        for name, (mode, data) in r.files.items():
            path = destination / name
            assert (os.fsencode(os.readlink(path)) if mode == "120000" else path.read_bytes()) == data
            if mode != "120000":
                assert path.stat().st_mode & 0o777 == int(mode, 8) & 0o777
        assert len(backups) == int(failure != "first-install")
        if backups:
            assert (backups[0] / "main.ts").read_text() == "old"
        for i in range(5):
            (destination / "main.ts").write_text(str(i))
            assert r.run("--apply", "--plugin-dir", str(destination), "--reload-cmd", "") == 1
        backups = sorted(r.state.glob("rollback-0*"))
        assert [(p / "main.ts").read_text() for p in backups] == ["2", "3", "4"]
        extra = r.state / ("rollback-" + "0" * 20 + "-" + "a" * 32)
        extra.mkdir()
        before = list(renames)
        assert r.run("--apply", "--plugin-dir", str(destination), "--reload-cmd", "") == 1
        assert renames == before and extra.exists()  # No pruning on a no-op.
    else:
        assert "plugin failed:" in capsys.readouterr().out
        assert len(renames) == ({"rename-1": 1, "rename-2": 2}.get(failure, 0))
        assert r.marker.exists() == failure.startswith("rename-")
        assert len(backups) == int(failure == "rename-2")
        if failure == "rename-2":
            assert not destination.exists() and (backups[0] / "main.ts").read_text() == "old"
        elif failure not in {"inside", "ancestor", "file"}:
            assert (destination / "main.ts").read_text() == "old"
    assert unrelated.is_dir() and (r.state / "rollback-unrelated").is_dir()
    assert not list(r.destination.parent.glob(f".{r.destination.name}.stage-*"))


def fresh_plugin_run(r, *args):
    return deploy.main(["--plugin-dir", str(r.destination), *args], runner=r.runner,
                       context=deploy.Context(r.context.cwd, dict(r.context.env),
                           daemon_state=r.context.daemon_state, supervisor_state=r.context.supervisor_state))


@pytest.mark.scenario("deployment-drift/pending-plugin-reload/interrupted-reload-succeeds-on-rerun")
@pytest.mark.scenario("deployment-drift/pending-plugin-reload/failed-reload-succeeds-on-rerun")
@pytest.mark.parametrize("failure", ["interrupted", "failed", "first-install", "timeout", "unavailable"])
def test_pending_reload_rerun(plugin_recording, failure, monkeypatch, capsys):
    r = plugin_recording
    # Spaces and apostrophes must survive the printed shell commands.
    destination = r.destination.with_name(r.destination.name + " plugin's copy")
    r.destination.rename(destination)
    r.destination = destination
    r.state = destination.parent / f".{destination.name}.nightshift-deploy"
    r.marker = r.state / "pending-reload"
    (destination / "main.ts").write_text("old")
    if failure == "first-install":
        deploy.shutil.rmtree(destination)
    else:
        previous = r.state / ("rollback-" + "0" * 20 + "-" + "a" * 32)
        previous.mkdir(parents=True)
        (previous / "main.ts").write_text("older")
    command = "paseo plugin reload nightshift"
    r.replies[command] = {
        "interrupted": KeyboardInterrupt(), "failed": (1, "", "reload failed"),
        "first-install": (1, "", "reload failed"),
        "timeout": subprocess.TimeoutExpired(command, 120),
        "unavailable": FileNotFoundError("paseo unavailable"),
    }[failure]
    if failure == "interrupted":
        with pytest.raises(KeyboardInterrupt):
            fresh_plugin_run(r, "--apply")
    else:
        assert fresh_plugin_run(r, "--apply") == 1
    output = capsys.readouterr().out
    assert r.calls[-1] == command
    assert (destination / "main.ts").read_bytes() == b"committed"
    assert r.marker.read_bytes() == b""
    backups = sorted(r.state.glob("rollback-*"))
    assert len(backups) == 2 * int(failure != "first-install")
    if backups:
        assert (backups[-1] / "main.ts").read_text() == "old"
    if failure == "first-install":
        assert "No rollback available" in output and command in output
    elif failure != "interrupted":
        advice = next(line for line in output.splitlines() if line.startswith("mv "))
        move, restore, reload = [shlex.split(part) for part in advice.split(" && ")]
        assert move[:3] == ["mv", "--", str(destination)]
        aside = Path(move[3])
        assert aside.is_absolute() and aside.parent == destination.parent and not aside.exists()
        assert restore == ["mv", "--", str(backups[-1]), str(destination)]
        assert reload == shlex.split(command)

    def no_rename(*args):
        pytest.fail("matching plugin must not be swapped again")

    original_open = Path.open

    def no_marker_write(path, mode="r", *args, **kwargs):
        assert path != r.marker or mode in {"r", "rb"}
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "rename", no_rename)
    monkeypatch.setattr(Path, "open", no_marker_write)
    calls = list(r.calls)
    assert fresh_plugin_run(r) == 1
    assert "plugin installed but not reloaded" in capsys.readouterr().out
    assert command not in r.calls[len(calls):] and r.marker.exists()
    r.replies[command] = ""
    assert fresh_plugin_run(r, "--apply") == 0
    assert not r.marker.exists() and sorted(r.state.iterdir()) == backups
    assert r.calls.count(command) == 2
    assert fresh_plugin_run(r, "--apply") == 0
    assert r.calls.count(command) == 2 and sorted(r.state.iterdir()) == backups
    assert "uv sync --frozen" not in r.calls and "git pull --ff-only" not in r.calls


@pytest.mark.scenario("deployment-drift/staged-plugin-replacement/missing-destination-after-interruption")
def test_rename_gap_stages_current_head_afresh(plugin_recording, monkeypatch, capsys):
    r = plugin_recording
    (r.destination / "main.ts").write_text("old")
    original_rename = Path.rename
    renames = []

    def rename(path, target):
        renames.append((path, target))
        if len(renames) == 2:
            raise KeyboardInterrupt()
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(KeyboardInterrupt):
        fresh_plugin_run(r, "--apply")
    backups = sorted(r.state.glob("rollback-*"))
    assert len(backups) == 1 and not r.destination.exists() and r.marker.read_bytes() == b""
    assert fresh_plugin_run(r) == 1
    output = capsys.readouterr().out
    assert "differs from committed HEAD" in output and "plugin installed but not reloaded" in output
    r.replies["git cat-file blob 0"] = b"new HEAD"
    r.replies["paseo plugin reload nightshift"] = ""
    assert fresh_plugin_run(r, "--apply") == 0
    assert len(renames) == 3 and renames[1][0] != renames[2][0]
    assert (r.destination / "main.ts").read_bytes() == b"new HEAD"
    assert sorted(r.state.iterdir()) == backups and (backups[0] / "main.ts").read_text() == "old"
    assert r.calls[-6:] == ["paseo plugin reload nightshift",
        "/bin/ps -p 101 -o lstart=", "git log -1 --format=%ct HEAD -- nightshift/",
        SERVICE_STATUS, "/bin/ps -p 201 -o lstart=", "git log -1 --format=%ct HEAD -- specloop/"]


@pytest.mark.scenario("deployment-drift/pending-plugin-reload/reload-override-and-explicit-skip")
def test_reload_override_and_skip(plugin_recording, capsys):
    r = plugin_recording
    (r.destination / "main.ts").write_text("old")
    assert fresh_plugin_run(r, "--apply", "--reload-cmd", "") == 1
    assert "reload skipped" in capsys.readouterr().out and r.marker.read_bytes() == b""
    before = {p: p.stat() for p in r.state.iterdir()}
    assert fresh_plugin_run(r, "--apply", "--reload-cmd", "") == 1
    assert {p: p.stat() for p in r.state.iterdir()} == before
    command = ["custom reload", "two words", "$(touch surprise);", "plugin's name"]
    runner = r.runner
    results = [1, 0]

    def custom_runner(argv, **kwargs):
        if argv == command:
            assert r.marker.exists()
            return subprocess.CompletedProcess(argv, results.pop(0), "", "custom reload result")
        return runner(argv, **kwargs)

    r.runner = custom_runner
    assert fresh_plugin_run(r, "--apply", "--reload-cmd", shlex.join(command)) == 1
    assert shlex.join(command) in capsys.readouterr().out and r.marker.exists()
    assert fresh_plugin_run(r, "--apply", "--reload-cmd", shlex.join(command)) == 0
    assert not r.marker.exists() and not results
    assert sorted(r.state.iterdir()) == sorted(p for p in before if p != r.marker)
    assert fresh_plugin_run(r, "--apply", "--reload-cmd", "") == 0


@pytest.mark.scenario("deployment-drift/process-age-drift/process-predates-code")
@pytest.mark.scenario("deployment-drift/process-age-drift/process-identity-is-unavailable")
@pytest.mark.parametrize("name,pid,path", [("daemon", 101, "nightshift"), ("supervisor", 201, "specloop")])
@pytest.mark.parametrize("case", ["older", "equal", "newer", "missing", "malformed", "dead", "unsupported", "history", "start"])
def test_check_reports_process_older_than_code(recording, name, pid, path, case, capsys):
    r = recording
    heartbeat = r.context.daemon_state / "heartbeat.json"
    if case in {"missing", "malformed", "unsupported"}:
        if name == "daemon":
            heartbeat.unlink() if case == "missing" else heartbeat.write_text("{" if case == "malformed" else '{"pid": true}')
        else:
            r.replies[SERVICE_STATUS] = {"missing": (1, "", "missing"), "malformed": "{",
                "unsupported": '{"version": 2, "supervisor": {"state": "not running"}}'}[case]
    elif case == "history":
        r.replies[f"git log -1 --format=%ct HEAD -- {path}/"] = ""
    else:
        r.replies[f"/bin/ps -p {pid} -o lstart="] = {"older": "Sun Dec 31 23:59:59 2023",
            "newer": "Mon Jan  1 00:00:01 2024", "dead": (1, "", "dead"), "start": "unreadable"}.get(case, CURRENT_START)
    before = {p: (p.read_bytes() if p.is_file() else None, p.stat()) for p in r.context.cwd.rglob("*")}
    assert r.run() == int(case not in {"equal", "newer"})
    output = capsys.readouterr().out
    assert (f"{name}: process older than code" in output) == (case == "older")
    if case not in {"older", "equal", "newer"}:
        assert r.run("--apply", "--force") == 1
        assert f"{name} unavailable:" in output
        assert f"Manual {name} start: launchctl kickstart gui/{r.context.uid}/com.mattpolicastro.{path}" in output
    assert before == {p: (p.read_bytes() if p.is_file() else None, p.stat()) for p in r.context.cwd.rglob("*")}
    assert not any(c.startswith("launchctl") for c in r.calls)


@pytest.fixture
def restarting(recording, monkeypatch):
    r = recording
    for filename in ("worker-tasks.json", "pause.json", "receipt.json", "run.json"):
        (r.context.daemon_state / filename).write_text('{"untouched": true}')
    r.now, r.replacement, r.mode, r.fresh, r.error = 0, 0, "idle", None, None
    r.kicks, r.observations = [], {"daemon": 0, "supervisor": 0}
    r.context.uid = 502
    r.context.monotonic = lambda: r.now
    r.context.sleep = lambda seconds: setattr(r, "now", r.now + seconds)
    original_read, original_runner = Path.read_text, r.runner

    def identity(name):
        r.observations[name] += 1
        mode = (r.fresh if r.observations[name] > 1 else None) or r.mode
        replaced = r.kicks and r.now >= r.replacement and r.fresh != "same-pid"
        return (102 if name == "daemon" else 202) if replaced else (101 if name == "daemon" else 201), mode

    def read(path, *args, **kwargs):
        assert path.name in {"heartbeat.json", "serve.json"}, "unexpected state/worker-task inspection"
        return json.dumps({"pid": identity("daemon")[0]}) if path.name == "heartbeat.json" else original_read(path, *args, **kwargs)

    def runner(argv, **kwargs):
        key = " ".join(argv)
        if argv[:2] == ["launchctl", "kickstart"]:
            assert argv in [["launchctl", "kickstart", *flag, r.target] for flag in (["-k"], [])]
            r.kicks.append(key)
            if r.error == "timeout":
                raise subprocess.TimeoutExpired(key, kwargs["timeout"])
            if r.error == "missing":
                raise FileNotFoundError("launchctl missing")
            return subprocess.CompletedProcess(argv, int(r.error == "exit"), "", "command error")
        if key == SERVICE_STATUS:
            pid, mode = identity("supervisor")
            state = f"running (pid {pid}, last poll 2s ago" + ({"busy": ", driving run)", "unknown": ")",
                "wedged": ")"}.get(mode, ", driving nothing)"))
            if mode == "wedged":
                state = f"WEDGED (pid {pid} alive, but no poll for 301s)"
            r.replies[key] = json.dumps({"version": 1, "supervisor": {"state": state}, "runs":
                {"running": [{"status": "running"}], "error": [{"status": "paused", "error": "bad run"}],
                 "missing-runs": None, "bad-row": [{"status": []}]}.get(mode, []), "future": "ignored"})
        if argv[0] == "/bin/ps":
            assert kwargs["env"]["LC_ALL"] == "C" and kwargs["env"]["TZ"] == "UTC"
            assert 0 < kwargs["timeout"] <= 30 - (r.now % 30)
            if r.fresh == "probe-timeout" and r.kicks:
                r.now += kwargs["timeout"]
                raise subprocess.TimeoutExpired(key, kwargs["timeout"])
            current = (argv[2] in {"102", "202"} and r.fresh != "replacement-stale"
                or r.fresh == "current" and max(r.observations.values()) > 1 or r.fresh == "same-pid" and r.kicks)
            r.replies[key] = CURRENT_START if current else "Sun Dec 31 23:59:59 2023"
            if r.fresh == "unavailable" and max(r.observations.values()) > 1:
                r.replies[key] = (1, "", "process disappeared")
        return original_runner(argv, **kwargs)

    def run(*args):
        before = {p: (p.read_bytes() if p.is_file() else None, p.stat()) for p in r.context.cwd.rglob("*")}
        result = deploy.main(["--apply", *args], runner=runner, context=r.context)
        assert before == {p: (p.read_bytes() if p.is_file() else None, p.stat()) for p in r.context.cwd.rglob("*")}
        return result

    monkeypatch.setattr(Path, "read_text", read)
    r.run = run
    return r


@pytest.mark.scenario("deployment-drift/guarded-service-restart/background-jobs-defer-daemon-restart")
@pytest.mark.parametrize("state", ["starting", "running", "stopping", "unknown", "diagnostic", "finished", "failed", "stopped"])
def test_daemon_restart_waits_for_background_jobs(restarting, state, capsys):
    r = restarting
    r.target = "gui/502/com.mattpolicastro.specloop" if state not in {"finished", "failed", "stopped"} else "gui/502/com.mattpolicastro.nightshift"
    def inventory(root):
        assert root == r.context.daemon_state
        return [SimpleNamespace(state=state, delivery="pending")], ["bad inventory"] if state == "diagnostic" else []
    r.context.inventory = inventory
    blocked = state not in {"finished", "failed", "stopped"}
    assert r.run() == int(blocked)
    assert ("daemon restart pending" in capsys.readouterr().out) == blocked
    assert r.kicks == [f"launchctl kickstart -k {r.target}"]  # Independent supervisor still repairs.


@pytest.mark.scenario("deployment-drift/guarded-service-restart/driving-supervisor-defers-restart")
@pytest.mark.scenario("deployment-drift/guarded-service-restart/fresh-idle-or-forced-restart")
@pytest.mark.scenario("deployment-drift/bounded-restart-confirmation/first-wait-confirms-replacement")
@pytest.mark.scenario("deployment-drift/bounded-restart-confirmation/second-wait-or-final-failure")
@pytest.mark.parametrize("name", ["daemon", "supervisor"])
@pytest.mark.parametrize("mode,force,delay,error", [("idle", False, 2, None), ("idle", False, 32, None),
    ("idle", False, 99, None), ("busy", False, 0, None), ("busy", True, 0, None),
    ("unknown", False, 0, None), ("unknown", True, 0, None), ("running", False, 0, None),
    ("error", False, 0, None), ("contradictory", False, 0, None), ("wedged", False, 0, None),
    ("mismatch", False, 0, None), ("bad-serve", False, 0, None), ("missing-runs", False, 0, None), ("bad-row", False, 0, None),
    ("replacement-stale", False, 0, None), ("same-pid", False, 0, None), ("probe-timeout", False, 0, None), ("unavailable", True, 0, None),
    ("current", False, 0, None), ("idle", False, 0, "exit"), ("idle", False, 32, "timeout"), ("idle", False, 99, "missing")])
def test_supervisor_restart_deferred_while_driving(restarting, name, mode, force, delay, error, monkeypatch, capsys):
    r = restarting
    r.fresh, r.replacement, r.error, r.target = mode, delay, error, "gui/502/override label"
    original_probe = deploy.process_probe
    def probe(context, runner, service, **kw):
        assert not (error and r.kicks and service != name), "command errors must stop later repairs"
        return original_probe(context, runner, service, **kw) if service == name else (301, False, True)
    monkeypatch.setattr(deploy, "process_probe", probe)
    idle = mode in {"idle", "current", "wedged", "replacement-stale", "same-pid", "probe-timeout"}
    r.context.inventory = lambda root: ([], []) if idle else ([], ["unknown activity"])
    if mode in {"contradictory", "wedged", "mismatch", "bad-serve"}:
        r.context.supervisor_state.mkdir()
        (r.context.supervisor_state / "serve.json").write_text("{" if mode == "bad-serve" else json.dumps(
            {"pid": 999 if mode == "mismatch" else 201, "driving": ["run"] if mode == "contradictory" else []}))
    blocked = not idle and not force
    timeout = delay == 99 or mode in {"replacement-stale", "same-pid", "probe-timeout"}
    assert r.run(f"--{name}-label", "override label", *(["--force"] if force else [])) == int(blocked or timeout or error is not None or mode == "unavailable")
    output = capsys.readouterr().out
    assert (f"{name} restart pending" in output) == blocked
    expected = [] if blocked or mode in {"current", "unavailable"} else [f"launchctl kickstart -k {r.target}"] + ([f"launchctl kickstart {r.target}"] if delay >= 30 or timeout else [])
    assert r.kicks == expected and r.now == (0 if not expected else 60 if timeout else delay)
    if timeout or error or mode == "unavailable":
        assert "launchctl kickstart 'gui/502/override label'" in output
