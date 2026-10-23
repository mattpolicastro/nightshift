import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import deploy

CHECK = "uv sync --check --frozen --offline"
STATUS = "git status --porcelain=v1 --untracked-files=all"
COUNTS = "git rev-list --left-right --count HEAD...@{upstream}"
STALE = (1, "", "Would install 1 package\n + mypy==2.4.0\nThe environment is outdated; run `uv sync`")


@pytest.fixture
def recording(tmp_path):
    (tmp_path / ".venv").mkdir()
    context = deploy.Context(tmp_path, {"VIRTUAL_ENV": "/wrong", "UV_PROJECT": "/wrong",
        "UV_PROJECT_ENVIRONMENT": "/wrong", "UV_WORKING_DIR": "/wrong", "UV_NO_DEV": "1"})
    replies = {"git rev-parse --show-toplevel": str(tmp_path), STATUS: "",
        "git symbolic-ref --quiet --short HEAD": "main", "git rev-parse --verify @{upstream}": "upstream",
        "git config --get branch.main.remote": "origin", "git fetch -- origin": "",
        COUNTS: "0 0", "git rev-parse HEAD": "before", CHECK: "", "uv sync --frozen": ""}
    calls = []

    def runner(argv, *, cwd, env, timeout, text):
        key = " ".join(argv)
        calls.append(key)
        assert cwd == tmp_path and timeout > 0
        assert text is (not key.startswith(("git ls-tree", "git cat-file")))
        assert key in replies, f"unexpected command: {argv}"
        value = replies[key]
        value = value.pop(0) if isinstance(value, list) else value
        if isinstance(value, Exception):
            raise value
        if argv[0] == "uv":
            assert env["UV_PROJECT_ENVIRONMENT"] == str(tmp_path / ".venv")
            assert not {"VIRTUAL_ENV", "UV_PROJECT", "UV_WORKING_DIR", "UV_NO_DEV"} & env.keys()
        if key == "git pull --ff-only":
            replies["git rev-parse HEAD"] = "after"
        if key == "uv sync --frozen":
            (tmp_path / ".venv").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(argv, *(value if isinstance(value, tuple) else (0, value, "")))

    return SimpleNamespace(context=context, replies=replies, calls=calls,
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
