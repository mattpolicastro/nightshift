import subprocess
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
        assert cwd == tmp_path and timeout > 0 and text is True
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
