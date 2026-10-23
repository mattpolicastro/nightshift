import json
import shlex
import subprocess
import sys

import pytest

from nightshift.host_paths import host_path, tool_path
from specloop import agents, quota, verify
from specloop.manifest import Manifest, Profile, Step


SYSTEM = "/usr/bin:/bin:/usr/sbin:/sbin"
SETTINGS = {
    "NIGHTSHIFT_BIN_DIR": "/opt/homebrew/bin",
    "NIGHTSHIFT_NODE_BIN": "~/n/bin",
    "NIGHTSHIFT_CHECKOUT": "~/Projects/nightshift",
    "NIGHTSHIFT_UV": "/opt/homebrew/bin/uv",
}


@pytest.fixture(autouse=True)
def host(monkeypatch, tmp_path):
    home = tmp_path / "host"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/untrusted/inherited")
    monkeypatch.setenv("UNRELATED", "must not propagate")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-test-secret")
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    return home


def manifest(tmp_path):
    return Manifest(tmp_path, "paths", "main", "main", "", {}, (), (),
                    verify=(Step("verify", ("path-probe", "verify")),),
                    prepare=(Step("prepare", ("path-probe", "prepare")),),
                    deny_read=(str(tmp_path / "denied"),))


def executable(path, code):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable)
                    + " -c " + shlex.quote(code) + ' "$@"\n')
    path.chmod(0o755)


@pytest.mark.scenario("host-paths/trusted-host-path-settings/unconfigured-host-retains-defaults")
def test_defaults(host):
    for name, default in SETTINGS.items():
        expected = str(host) + default[1:] if default.startswith("~/") else default
        assert host_path(name, default) == expected
    assert tool_path() == "/opt/homebrew/bin:" + SYSTEM
    assert agents.base_env("CODEX_HOME", host)["PATH"] == tool_path()


@pytest.mark.scenario("host-paths/trusted-host-path-settings/explicit-settings-override-defaults")
def test_overrides_are_resolved_at_call_time(monkeypatch, host):
    for name, default in SETTINGS.items():
        monkeypatch.setenv(name, "~/alternate/" + name)
        assert host_path(name, default) == str(host / "alternate" / name)
    monkeypatch.delenv("NIGHTSHIFT_UV")
    derived = host_path("NIGHTSHIFT_BIN_DIR", SETTINGS["NIGHTSHIFT_BIN_DIR"]) + "/uv"
    assert host_path("NIGHTSHIFT_UV", derived) == derived
    monkeypatch.setenv("NIGHTSHIFT_UV", "/independent/uv")
    assert host_path("NIGHTSHIFT_UV", derived) == "/independent/uv"
    assert tool_path().startswith(str(host / "alternate/NIGHTSHIFT_BIN_DIR") + ":")
    monkeypatch.setenv("NIGHTSHIFT_BIN_DIR", "/second/tools")
    assert agents.base_env("CODEX_HOME", host)["PATH"] == "/second/tools:" + SYSTEM
    assert tool_path(env={}) == "/opt/homebrew/bin:" + SYSTEM


@pytest.mark.scenario("host-paths/literal-path-resolution/paths-remain-literal")
@pytest.mark.parametrize("name", SETTINGS)
def test_literal_nonexistent_paths(name, tmp_path):
    literal = "missing space/$HOME/$(touch marker);`whoami`/*/../last "
    for value in ("~/" + literal, str(tmp_path) + "/" + literal):
        expected = str(tmp_path) + "/" + literal
        assert host_path(name, "/default", {name: value}, tmp_path) == expected
    assert not list(tmp_path.iterdir())
    if name in ("NIGHTSHIFT_UV", "NIGHTSHIFT_CHECKOUT"):
        assert host_path(name, "/default", {name: "/valid:filename"}) == "/valid:filename"


@pytest.mark.scenario("host-paths/literal-path-resolution/invalid-settings-do-not-fall-back")
@pytest.mark.parametrize("value", ["", " \t", "relative", "~", "~other/bin", "~bin",
                                  "/bad\0path", "/bad\npath", "/bad\rpath", "/bad:path"])
def test_invalid_consumed_setting_fails_before_spawn(value, monkeypatch, tmp_path):
    for name in SETTINGS:
        if ":" in value and name in ("NIGHTSHIFT_UV", "NIGHTSHIFT_CHECKOUT"):
            continue
        with pytest.raises(ValueError) as error:
            host_path(name, "/default", {name: value})
        assert str(error.value) == f"invalid host path setting: {name}"
    if "\0" in value:  # OS environments cannot contain NUL; the pure resolver checks it above.
        return
    monkeypatch.setenv("NIGHTSHIFT_BIN_DIR", value)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("spawned invalid config"))
    m = manifest(tmp_path)
    out = tmp_path / "out"
    (out / "build").mkdir(parents=True)  # Even cleanup's chmod must not spawn.
    for consume in (lambda: agents.base_env("CODEX_HOME", tmp_path),
                    lambda: quota.codex_limits(tmp_path),
                    lambda: verify.run_extracted(tmp_path, "sha", m.verify, m, out)):
        with pytest.raises(ValueError, match="^invalid host path setting: NIGHTSHIFT_BIN_DIR$"):
            consume()


@pytest.fixture
def configured_tools(host, monkeypatch):
    tools = host / "tools with spaces;$literal"
    monkeypatch.setenv("NIGHTSHIFT_BIN_DIR", "~/" + tools.name)
    for name in SETTINGS.keys() - {"NIGHTSHIFT_BIN_DIR"}:
        monkeypatch.setenv(name, "invalid-unused-setting")
    return tools


@pytest.mark.scenario("host-paths/engine-executable-search-paths/verify-path-is-configurable")
@pytest.mark.scenario("host-paths/literal-path-resolution/invalid-settings-do-not-fall-back")
def test_verify_path_is_configurable(configured_tools, monkeypatch, tmp_path, host):
    executable(configured_tools / "path-probe",
               "import json, os, sys; print(json.dumps([sys.argv[1], dict(os.environ)]))")
    real_popen, launches = subprocess.Popen, []

    def launch(argv, **kwargs):
        assert argv[:2] == ["sandbox-exec", "-f"]
        launches.append((argv, kwargs.copy()))
        # Inspect production wrapping, then run the stub without nesting a sandbox.
        return real_popen(argv[3:], **kwargs)

    monkeypatch.setattr(verify, "already_sandboxed", lambda: False)
    monkeypatch.setattr(subprocess, "Popen", launch)
    m, out = manifest(tmp_path), tmp_path / "out"
    receipts = verify.run_extracted(tmp_path, "sha", m.verify, m, out)
    assert [r.id for r in receipts] == ["prepare", "verify"]
    assert all(r.passed for r in receipts)
    expected = {"PATH": str(configured_tools) + ":" + SYSTEM,
                "HOME": str(out / "build/home"), "TMPDIR": str(out / "build/tmp"),
                "LANG": "en_US.UTF-8", "COPYFILE_DISABLE": "1"}
    for (argv, kwargs), receipt in zip(launches, receipts, strict=True):
        assert kwargs["env"] == expected
        assert kwargs["cwd"] == out / "build" and kwargs["start_new_session"] is True
        assert argv[2] == str(out / (receipt.id + ".sb"))
        phase, child_env = json.loads(receipt.tail)
        assert phase == receipt.id
        assert all(child_env[k] == v for k, v in expected.items())
        assert "UNRELATED" not in child_env and "ANTHROPIC_API_KEY" not in child_env
    prep, check = (out / "prepare.sb").read_text(), (out / "verify.sb").read_text()
    assert "(deny network*)" not in prep and "(deny network*)" in check
    assert "(deny file-write*)" in prep and "(deny file-write*)" in check
    assert str(configured_tools) not in prep  # PATH grants no extra sandbox reads.
    for credential in verify.CREDENTIAL_DIRS:
        assert f'(subpath "{host / credential}")' in prep
    assert str(tmp_path / "denied") in prep and str(tmp_path / "denied") in check
    assert [r["id"] for r in json.loads((out / "receipts.json").read_text())] == ["prepare", "verify"]


@pytest.mark.scenario("host-paths/engine-executable-search-paths/agents-and-quota-use-configured-tools")
@pytest.mark.scenario("host-paths/literal-path-resolution/invalid-settings-do-not-fall-back")
def test_agents_and_quota_process_environments(configured_tools, monkeypatch, tmp_path, host):
    executable(configured_tools / "path-probe", "import json, os; print(json.dumps(dict(os.environ)))")
    executable(configured_tools / "codex", '''
import json, os, sys
requests = [json.loads(sys.stdin.readline())]
print(json.dumps({"id": 1, "result": {}}), flush=True)
requests += [json.loads(sys.stdin.readline()), json.loads(sys.stdin.readline())]
print(json.dumps({"id": 2, "result": {"env": dict(os.environ), "requests": requests,
                                    "argv": sys.argv[1:]}}), flush=True)
''')
    real_popen, launches = subprocess.Popen, []

    def launch(argv, **kwargs):
        launches.append((argv, kwargs.copy()))
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    private = tmp_path / "private"
    call = agents.Call(Profile("test", "codex", "test"), "read", "", tmp_path, {},
                       tmp_path / "agent.json", 10, 10, private)
    env = agents.base_env("CODEX_HOME", private, EXPLICIT="kept", EMPTY="")
    assert agents.spawn(["path-probe"], env, call) == (0, "")
    result = quota.codex_limits(private, timeout=5)
    assert result is not None
    expected = {"HOME": str(host), "CODEX_HOME": str(private)}
    for index, child_env in enumerate((json.loads(call.transcript.read_text()), result["env"])):
        suffix = SYSTEM if index == 0 else "/usr/bin:/bin"
        boundary = {**expected, "PATH": str(configured_tools) + ":" + suffix}
        if index == 0:
            boundary.update(LANG="en_US.UTF-8", EXPLICIT="kept")
        assert launches[index][1]["env"] == boundary
        assert all(child_env[k] == v for k, v in boundary.items())
        assert "UNRELATED" not in child_env and "ANTHROPIC_API_KEY" not in child_env
    assert launches[1][0] == ["codex", "app-server"] and result["argv"] == ["app-server"]
    assert result["requests"] == [
        {"method": "initialize", "id": 1,
         "params": {"clientInfo": {"name": "specloop", "title": "specloop", "version": "1"}}},
        {"method": "initialized", "params": {}}, {"method": "account/rateLimits/read", "id": 2}]


@pytest.mark.scenario("host-paths/engine-executable-search-paths/agents-and-quota-use-configured-tools")
@pytest.mark.parametrize("mode", ["missing", "timeout"])
def test_quota_fallback_is_unchanged(configured_tools, monkeypatch, tmp_path, mode):
    if mode == "timeout":
        executable(configured_tools / "codex", "import time; time.sleep(30)")
    else:
        def missing(argv, **kwargs):
            assert kwargs["env"]["PATH"] == str(configured_tools) + ":/usr/bin:/bin"
            raise FileNotFoundError("test missing executable")
        monkeypatch.setattr(subprocess, "Popen", missing)
    assert quota.codex_limits(tmp_path, timeout=0.05) is None
    assert quota.resume_at(None, now=100) == 100 + quota.FALLBACK_SECONDS
