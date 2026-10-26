"""Credential policy text and real sandbox evidence share a synthetic layout."""

import errno
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from specloop import manifest, verify


SCENARIOS = [
    "verify-preparation/agent-homes-denied-in-both-modes/legacy-exclusions-cannot-expose-new-homes",
    "verify-preparation/credential-directories-denied-in-both-modes/profile-login-directories-are-denied",
    "verify-preparation/auth-targets-denied-by-real-path/external-targets-cannot-escape-denial",
    "verify-preparation/auth-targets-denied-by-real-path/sandbox-evidence-is-explicit",
]
pytestmark = [pytest.mark.scenario(s) for s in SCENARIOS]


@pytest.fixture(params=[False, True], ids=["verify", "prepare"])
def credentials(request, tmp_path, monkeypatch):
    actual = tmp_path / "actual-home"
    actual.mkdir()
    home = tmp_path / "home"
    home.symlink_to(actual, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))
    external = tmp_path / "external"
    external.mkdir()
    (external / "actual-state").mkdir()
    (external / "state").symlink_to(external / "actual-state", target_is_directory=True)
    monkeypatch.setenv("NIGHTSHIFT_STATE_ROOT", str(external / "state"))
    roots = [home / ".specloop/homes", home / ".nightshift/homes", verify.homes_dir()]
    directories = {home / p for p in verify.CREDENTIAL_DIRS} | set(roots)
    # Directory symlinks exercise real-path matching, including the login suffix.
    login = home / ".codex-test-login"
    login.symlink_to(external / "login", target_is_directory=True)
    (external / "login").mkdir()
    directories.add(login)
    sentinels = []
    for directory in sorted(directories):
        directory.mkdir(parents=True, exist_ok=True)
        sentinel = directory / "sentinel"
        sentinel.write_text("synthetic credential")
        sentinels.append(sentinel)

    def auth_link(path, name):
        target = external / name
        target.write_text("synthetic auth " + name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
        sentinels.extend([path, target])
        return path

    auths = [auth_link(home / ".codex/auth.json", "default"),
             auth_link(login / "auth.json", "login-auth")]
    for i, root in enumerate(roots):
        if i == 2:
            (external / "agent-home").mkdir()
            (root / "agent").symlink_to(external / "agent-home", target_is_directory=True)
        auths.append(auth_link(root / "agent/auth.json", f"agent-{i}"))
    active = auth_link(external / "active.json", "active-target")
    fallback = auth_link(home / "fallback.json", "fallback-target")
    unused = auth_link(external / "unused.json", "unused-target")
    auths.extend([active, fallback, unused])
    control = external / "control"
    control.write_text("allowed control")
    private = home / "private"
    private.write_text("not allowlisted")
    src, build = tmp_path / "source", tmp_path / "run/build"
    src.mkdir()
    (src / "file").write_text("source")
    build.mkdir(parents=True)
    return dict(home=home, directories=directories, sentinels=sentinels, auths=auths,
                control=control, private=private, src=src, build=build, prepare=request.param,
                active=active, fallback=fallback, unused=unused)


def make_profile(c, old_only):
    m = manifest.parse({
        "run": {"repo": "/unused", "change": "c", "branch": "main"},
        "roles": {"implement": ["active", "fallback"], "review": "active"},
        "profiles": {name: {"adapter": "codex", "model": "m", "auth": auth}
                     for name, auth in (("active", str(c["active"])),
                                        ("fallback", "~/fallback.json"),
                                        ("unused", str(c["unused"])))},
        "paths": {"allowed": ["src/**"]},
        "verify": [{"id": "t", "run": ["true"]}],
        "sandbox": {"deny_read": ["~/.specloop/homes"] if old_only else [],
                    # Allow parents so only unconditional denials protect secrets.
                    "prepare_read": [str(c["home"]), str(c["home"].resolve()),
                                     str(c["control"].parent)]},
    })
    assert set(m.auth_targets) == {str(c["active"]), "~/fallback.json", str(c["unused"])}
    return verify.profile(c["build"], m, prepare=c["prepare"], src=c["src"])


@pytest.mark.parametrize("old_only", [False, True], ids=["empty-denials", "legacy-only"])
def test_credential_profile_text(credentials, old_only):
    c = credentials
    text = make_profile(c, old_only)
    denial = text.splitlines()[-1]
    assert denial.startswith("(deny file-read*")
    for path in [*c["directories"], *c["auths"]]:
        for spelling in (path, path.resolve()):
            assert f'(subpath "{spelling}")' in denial
    assert ("(deny network*)" in text) is (not c["prepare"])
    assert "(deny file-write*)" in text
    assert f'(subpath "{c["build"].resolve()}")' in text
    if c["prepare"]:
        assert text.index(f'(subpath "{c["home"]}")') < text.index(denial)


@pytest.mark.parametrize("old_only", [False, True], ids=["empty-denials", "legacy-only"])
def test_credential_sandbox_probes(credentials, old_only, tmp_path):
    if sys.platform != "darwin":
        pytest.skip("sandbox-exec probes require macOS; profile text is tested separately")
    if verify.already_sandboxed():
        pytest.skip("nested macOS sandbox cannot apply a new profile; profile text is tested separately")
    c = credentials
    sb = tmp_path / "credentials.sb"
    sb.write_text(make_profile(c, old_only))

    def run(*argv):
        return subprocess.run(["/usr/bin/sandbox-exec", "-f", str(sb), *map(str, argv)],
                              capture_output=True, text=True)

    control = run("/bin/cat", c["control"])
    assert control.returncode == 0 and control.stdout == "allowed control", control.stderr
    for path in c["sentinels"]:
        result = run("/bin/cat", path)
        assert result.returncode != 0 and result.stdout == "", (path, result)
    assert run("/bin/cat", c["src"] / "file").stdout == "source"
    assert run("/usr/bin/touch", c["src"] / "new-file").returncode != 0
    assert run("/bin/chmod", "u+w", c["src"]).returncode != 0
    assert run("/usr/bin/touch", c["build"] / "output").returncode == 0
    if c["prepare"]:
        # Rebuild with a narrow allowlist to check preparation's home restriction.
        m = manifest.Manifest(Path("/unused"), "c", "main", "main", "", {}, (), (), (),
                              prepare_read=(str(c["control"]),))
        sb.write_text(verify.profile(c["build"], m, prepare=True, home=c["home"].resolve(), src=c["src"]))
        assert run("/bin/cat", c["private"]).returncode != 0
        assert run("/bin/cat", c["control"]).stdout == "allowed control"


@pytest.mark.parametrize("failure", ["loop", "permission", "readlink-permission"])
@pytest.mark.parametrize("target", ["default", "agent", "unused", "login-directory"])
def test_target_resolution_errors_refuse_profile(credentials, monkeypatch, failure, target):
    c = credentials
    path = {"default": c["auths"][0], "agent": c["auths"][2], "unused": c["unused"],
            "login-directory": c["home"] / ".codex-test-login"}[target]
    if failure == "loop":
        path.unlink()
        path.symlink_to(path)
    else:
        operation = "readlink" if failure == "readlink-permission" else "lstat"
        original = getattr(verify.os, operation)
        inaccessible = path.parent.resolve() / path.name

        def denied(p, *args, **kwargs):
            if Path(p) == inaccessible:
                raise PermissionError(errno.EACCES, "synthetic metadata denial", str(p))
            return original(p, *args, **kwargs)

        monkeypatch.setattr(verify.os, operation, denied)
    with pytest.raises((ValueError, OSError), match="loop|Too many|synthetic metadata denial"):
        make_profile(c, False)


def test_missing_auth_target_keeps_lexical_and_real_denials(credentials):
    c = credentials
    # Dangling relative links still identify a credential destination. No auth
    # contents need to exist for protection to apply.
    c["unused"].unlink()
    c["unused"].symlink_to("../home/../external/not-created/auth.json")
    text = make_profile(c, False)
    assert f'(subpath "{c["unused"]}")' in text.splitlines()[-1]
    target = c["unused"].parent / "not-created/auth.json"
    assert f'(subpath "{target}")' in text.splitlines()[-1]


def test_direct_manifest_role_and_fallback_auth_are_denied(credentials):
    c = credentials
    active = manifest.Profile("active", "codex", "m", auth=str(c["active"]))
    fallback = replace(active, name="fallback", auth="~/fallback.json")
    m = manifest.Manifest(Path("/unused"), "c", "main", "main", "",
                          {"implement": active}, (), (), (),
                          chains={"implement": (active, fallback)})
    denial = verify.profile(c["build"], m, prepare=c["prepare"]).splitlines()[-1]
    for path in (c["active"], c["fallback"]):
        for spelling in (path, path.resolve()):
            assert f'(subpath "{spelling}")' in denial


@pytest.mark.parametrize("root", ["home", "agent-homes"])
def test_discovery_permission_error_refuses_profile(credentials, monkeypatch, root):
    c = credentials
    inaccessible = c["home"] if root == "home" else verify.homes_dir()
    original = Path.iterdir

    def denied(path):
        if path == inaccessible:
            raise PermissionError(errno.EACCES, "synthetic directory denial", str(path))
        return original(path)

    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(PermissionError, match="synthetic directory denial"):
        make_profile(c, False)


def test_each_profile_rediscovers_logins_without_reading_credentials(credentials, monkeypatch):
    c = credentials
    make_profile(c, False)
    login = c["home"] / ".codex-new-login"
    login.mkdir()
    target = c["control"].parent / "new-auth"
    target.write_text("synthetic new auth")
    (login / "auth.json").symlink_to(target)

    def no_contents(*args, **kwargs):
        pytest.fail("profile construction must use metadata, never credential contents")

    # Cover both pathlib and ordinary Python file reads; directory discovery and
    # readlink remain available.
    monkeypatch.setattr("builtins.open", no_contents)
    monkeypatch.setattr("io.open", no_contents)
    denial = make_profile(c, False).splitlines()[-1]
    for path in (login, login.resolve(), target):
        assert f'(subpath "{path}")' in denial
