import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from specloop import manifest, seal, verify


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    sh(src, "git", "init", "-q", "-b", "main")
    (src / "src").mkdir()
    (src / "src" / "a.txt").write_text("a\n")
    (src / "openspec").mkdir()
    (src / "openspec" / "tasks.md").write_text("- [ ] 1.1 x\n")
    (src / "old.txt").write_text("delete me\n")
    (src / ".gitignore").write_text("build/\n")
    sh(src, "git", "add", "-A")
    sh(src, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    bare = tmp_path / "repo.git"
    sh(tmp_path, "git", "clone", "-q", "--bare", str(src), str(bare))
    base = sh(bare, "git", "rev-parse", "main")
    work = tmp_path / "work"
    sh(tmp_path, "git", "clone", "-q", str(bare), str(work))
    return bare, work, base


M_DATA = {
    "run": {"repo": "/x", "change": "c", "branch": "main"},
    "roles": {"implement": "p", "review": "p"},
    "profiles": {"p": {"adapter": "codex", "model": "m"}},
    "paths": {"allowed": ["src/**", "old.txt", "new.txt"], "frozen": ["openspec/**"]},
    "verify": [{"id": "t", "run": ["true"]}],
}
M = manifest.parse(M_DATA)


def tree(bare, sha):
    return set(sh(bare, "git", "ls-tree", "-r", "--name-only", sha).splitlines())


def test_allowed_changes_land_and_violations_are_reverted(repo):
    bare, work, base = repo
    (work / "src" / "a.txt").write_text("changed\n")
    (work / "src" / "b.txt").write_text("new\n")
    (work / "openspec" / "tasks.md").write_text("- [x] 1.1 x\n")  # frozen
    (work / "stray.txt").write_text("not allowed\n")
    (work / "build").mkdir()
    (work / "build" / "out.o").write_text("ignored\n")
    os.remove(work / "old.txt")
    s = seal.seal(bare, work, base, M, "msg")
    assert sorted(s.changed) == ["old.txt", "src/a.txt", "src/b.txt"]
    assert any("openspec/tasks.md" in v and "frozen" in v for v in s.violations)
    assert any("stray.txt" in v for v in s.violations)
    t = tree(bare, s.sha)
    assert "src/b.txt" in t and "old.txt" not in t and "stray.txt" not in t and "build/out.o" not in t
    assert sh(bare, "git", "show", f"{s.sha}:openspec/tasks.md") == "- [ ] 1.1 x"
    assert sh(bare, "git", "rev-parse", f"{s.sha}^") == base


def test_agent_commits_are_ignored_files_are_what_count(repo):
    bare, work, base = repo
    (work / "src" / "a.txt").write_text("committed by agent\n")
    sh(work, "git", "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qam", "agent commit")
    s = seal.seal(bare, work, base, M, "msg")
    assert s.changed == ["src/a.txt"] and sh(bare, "git", "rev-parse", f"{s.sha}^") == base


def test_symlinks_secrets_and_nothing_changed(repo, tmp_path):
    bare, work, base = repo
    assert seal.seal(bare, work, base, M, "msg").sha is None
    os.symlink("/etc/passwd", work / "src" / "link")
    (work / "src" / "leak.txt").write_text("token=sk-THIS-IS-A-SECRET-VALUE-123456\n")
    s = seal.seal(bare, work, base, M, "msg", secrets=["sk-THIS-IS-A-SECRET-VALUE-123456"])
    assert any("src/link" in v for v in s.violations)
    assert s.secret_paths == ["src/leak.txt"] and s.sha is None


def test_executable_bit_is_kept(repo):
    bare, work, base = repo
    (work / "src" / "run.sh").write_text("#!/bin/sh\n")
    os.chmod(work / "src" / "run.sh", 0o755)
    s = seal.seal(bare, work, base, M, "msg")
    assert sh(bare, "git", "ls-tree", s.sha, "src/run.sh").startswith("100755")


def test_size_cap_counts_what_the_agent_added_not_the_checkout(repo, monkeypatch):
    bare, work, base = repo
    monkeypatch.setattr(seal, "MAX_TOTAL", 20)
    (work / "src" / "a.txt").write_text("a\n" * 5)  # grows by 8 bytes over a 25-byte base tree
    assert seal.seal(bare, work, base, M, "msg").changed == ["src/a.txt"]
    (work / "src" / "b.txt").write_text("x" * 21)
    with pytest.raises(RuntimeError, match="agent added"):
        seal.seal(bare, work, base, M, "msg")


def test_a_large_file_the_base_already_has_is_not_a_violation(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "src").mkdir(parents=True)
    (src / "src" / "big.bin").write_bytes(b"x" * 64)
    sh(src, "git", "init", "-q", "-b", "main")
    sh(src, "git", "add", "-A")
    sh(src, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    bare, work = tmp_path / "repo.git", tmp_path / "work"
    sh(tmp_path, "git", "clone", "-q", "--bare", str(src), str(bare))
    sh(tmp_path, "git", "clone", "-q", str(bare), str(work))
    base = sh(bare, "git", "rev-parse", "main")
    monkeypatch.setattr(seal, "MAX_FILE", 32)
    assert seal.seal(bare, work, base, M, "msg").violations == []
    (work / "src" / "big.bin").write_bytes(b"y" * 64)
    assert any("big.bin" in v for v in seal.seal(bare, work, base, M, "msg").violations)


def test_a_base_submodule_is_not_a_violation(tmp_path):
    src = tmp_path / "src"
    (src / "src").mkdir(parents=True)
    (src / "src" / "a.txt").write_text("a\n")
    sh(src, "git", "init", "-q", "-b", "main")
    sh(src, "git", "add", "-A")
    sh(src, "git", "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},lib")
    sh(src, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    bare, work = tmp_path / "repo.git", tmp_path / "work"
    sh(tmp_path, "git", "clone", "-q", "--bare", str(src), str(bare))
    sh(tmp_path, "git", "clone", "-q", str(bare), str(work))
    base = sh(bare, "git", "rev-parse", "main")
    assert (work / "lib").is_dir()
    (work / "src" / "a.txt").write_text("b\n")
    s = seal.seal(bare, work, base, M, "msg")
    assert s.violations == [] and s.sha
    assert sh(bare, "git", "ls-tree", s.sha, "lib").startswith("160000 commit")


def steps(*specs):
    return tuple(manifest.Step(i, tuple(run), expect, timeout) for i, run, expect, timeout in specs)


def test_receipts_expected_exits_and_stop_at_first_failure(repo, tmp_path):
    bare, _, base = repo
    s = steps(("present", ["/bin/test", "-f", "{src}/src/a.txt"], 0, 10),
              ("negative", ["/bin/sh", "-c", "exit 1"], 1, 10),
              ("wrong", ["/bin/sh", "-c", "exit 3"], 0, 10),
              ("never", ["true"], 0, 10))
    rs = verify.run_steps(bare, base, s, M, tmp_path / "v")
    assert [r.passed for r in rs] == [True, True, False] and not verify.ok(rs, s)
    assert "wrong" in verify.summary(rs) and (tmp_path / "v" / "receipts.json").exists()


def test_signal_and_timeout_never_satisfy_a_nonzero_expectation(repo, tmp_path):
    bare, _, base = repo
    s = steps(("killed", ["/bin/sh", "-c", "kill -9 $$"], 1, 10), )
    assert not verify.run_steps(bare, base, s, M, tmp_path / "v")[0].passed
    s = steps(("slow", ["/bin/sleep", "30"], 1, 1))
    r = verify.run_steps(bare, base, s, M, tmp_path / "v2")[0]
    assert r.exit is None and not r.passed


@pytest.mark.scenario("verify-preparation/prepare-steps/dependencies-ready-for-sandboxed-tests")
def test_prepare_dependencies_and_receipts(repo, tmp_path, monkeypatch):
    bare, _, base = repo
    m = manifest.parse({
        **M_DATA,
        "prepare": [
            {"id": "dependencies", "run": ["/bin/sh", "-c", 'cp "{src}/src/a.txt" dependency']},
            {"id": "expected-status", "run": ["/bin/sh", "-c", "exit 2"], "expect": 2},
        ],
        "verify": [{"id": "consume", "run": ["/bin/sh", "-c", 'test "$(cat dependency)" = a']}],
    })
    profiles = []
    wrap = verify.wrap

    def record_profile(argv, sb):
        profiles.append(sb.name)
        return wrap(argv, sb)

    monkeypatch.setattr(verify, "wrap", record_profile)
    out = tmp_path / "verification"
    rs = verify.run_steps(bare, base, m.verify, m, out)
    assert verify.ok(rs, m.prepare + m.verify), verify.summary(rs)
    assert [r.id for r in rs] == ["dependencies", "expected-status", "consume"]
    assert profiles == ["prepare.sb", "prepare.sb", "verify.sb"]
    assert "(deny network*)" not in (out / "prepare.sb").read_text()
    assert "(deny network*)" in (out / "verify.sb").read_text()
    assert (out / "build" / "dependency").read_text() == "a\n"
    saved = json.loads((out / "receipts.json").read_text())
    assert [r["id"] for r in saved] == [r.id for r in rs]
    assert all(r["passed"] for r in saved)


@pytest.mark.scenario("verify-preparation/prepare-steps/failing-prepare-fails-verification")
@pytest.mark.parametrize("command,expect,timeout,exit_code", [
    ("exit 3", 0, 10, 3),
    ("kill -9 $$", 1, 10, -9),
    ("sleep 30", 1, 1, None),
])
def test_prepare_failure_stops_all_later_steps(repo, tmp_path, command, expect, timeout, exit_code):
    bare, _, base = repo
    m = manifest.parse({
        **M_DATA,
        "prepare": [
            {"id": "failed-prepare", "run": ["/bin/sh", "-c", command], "expect": expect, "timeout": timeout},
            {"id": "later-prepare", "run": ["/usr/bin/touch", "later-prepare"]},
        ],
        "verify": [{"id": "never-verify", "run": ["/usr/bin/touch", "verify-ran"]}],
    })
    out = tmp_path / "verification"
    rs = verify.run_steps(bare, base, m.verify, m, out)
    assert not verify.ok(rs, m.prepare + m.verify)
    assert [(r.id, r.exit, r.passed) for r in rs] == [("failed-prepare", exit_code, False)]
    assert not (out / "build" / "later-prepare").exists()
    assert not (out / "build" / "verify-ran").exists()
    assert not (out / "never-verify.log").exists()
    assert json.loads((out / "receipts.json").read_text())[0]["passed"] is False


@pytest.mark.scenario("verify-preparation/source-stays-read-only/write-to-source-refused")
def test_prepare_cannot_create_source_file(repo, tmp_path):
    # File permissions also hold inside the harness's enclosing sandbox.
    bare, _, base = repo
    m = replace(M, prepare=steps(("source-write", ["/usr/bin/touch", "{src}/new-file"], 1, 10)))
    out = tmp_path / "verification"
    rs = verify.run_steps(bare, base, m.verify, m, out)
    assert verify.ok(rs, m.prepare + m.verify), verify.summary(rs)
    assert not (out / "src" / "new-file").exists()
    assert (out / "src" / "src" / "a.txt").read_text() == "a\n"


def test_prepare_profile_allows_network_but_not_extra_writes(tmp_path):
    m = replace(M, writable=(str(tmp_path / "extra"),), deny_read=(str(tmp_path / "credentials"),))
    preparation = verify.profile(tmp_path / "build", m, prepare=True)
    offline = verify.profile(tmp_path / "build", m)
    assert "(allow default)" in preparation and "(deny network*)" not in preparation
    assert "(deny network*)" in offline
    assert "(deny file-write*)" in preparation
    assert f'(subpath "{tmp_path / "build"}")' in preparation
    assert str(tmp_path / "extra") not in preparation and str(tmp_path / "extra") in offline
    assert "(deny file-read*" in preparation
    assert f'(subpath "{tmp_path / "credentials"}")' in preparation
    assert str(Path.home() / ".ssh") in preparation
    assert str(Path.home() / "Library/Keychains") in preparation


@pytest.mark.scenario("verify-preparation/source-stays-read-only/write-to-source-refused")
@pytest.mark.skipif(verify.already_sandboxed(), reason="asserts the sandbox policy; an enclosing sandbox replaces it")
def test_prepare_sandbox_refuses_chmod_escape_and_credential_reads(repo, tmp_path):
    bare, _, base = repo
    fake_credentials = tmp_path / "fake-credentials"
    fake_credentials.mkdir()
    (fake_credentials / "token").write_text("fixture")
    outside = tmp_path / "outside"
    m = replace(M, deny_read=(str(fake_credentials),), writable=(str(outside),), prepare=steps(
        ("chmod-source", ["/bin/chmod", "u+w", "{src}"], 1, 10),
        ("write-source", ["/usr/bin/touch", "{src}/new-file"], 1, 10),
        ("write-outside", ["/usr/bin/touch", str(outside)], 1, 10),
        ("read-credential", ["/bin/cat", str(fake_credentials / "token")], 1, 10),
        ("write-build", ["/usr/bin/touch", "dependency"], 0, 10),
    ))
    out = tmp_path / "verification"
    rs = verify.run_steps(bare, base, m.verify, m, out)
    assert verify.ok(rs, m.prepare + m.verify), verify.summary(rs)
    assert not outside.exists() and not (out / "src" / "new-file").exists()


@pytest.mark.skipif(verify.already_sandboxed(), reason="asserts the sandbox policy; an enclosing sandbox replaces it")
def test_sandbox_confines_writes_and_reads(repo, tmp_path):
    bare, _, base = repo
    outside = tmp_path / "outside.txt"
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (secret_dir / "token").write_text("x")
    m = manifest.parse({**{"run": {"repo": "/x", "change": "c", "branch": "main"}},
                        "roles": {"implement": "p", "review": "p"},
                        "profiles": {"p": {"adapter": "codex", "model": "m"}},
                        "paths": {"allowed": ["src/**"]}, "verify": [{"id": "t", "run": ["true"]}],
                        "sandbox": {"deny_read": [str(secret_dir)]}})
    s = steps(("source-is-read-only", ["/usr/bin/touch", "{src}/x"], 1, 10),
              ("outside-write-denied", ["/usr/bin/touch", str(outside)], 1, 10),
              ("secret-read-denied", ["/bin/cat", str(secret_dir / "token")], 1, 10),
              ("build-is-writable", ["/usr/bin/touch", "{build}/ok"], 0, 10))
    rs = verify.run_steps(bare, base, s, m, tmp_path / "v")
    assert [r.passed for r in rs] == [True, True, True, True], verify.summary(rs)
    assert not outside.exists()
    verify.cleanup(tmp_path / "v")
    assert not (tmp_path / "v" / "src").exists() and (tmp_path / "v" / "receipts.json").exists()


@pytest.mark.skipif(verify.already_sandboxed(), reason="asserts the sandbox policy; an enclosing sandbox replaces it")
def test_prepare_reads_under_home_are_an_allowlist(tmp_path):
    home = tmp_path / "home"
    out, tool, secret = home / "run" / "verification", home / "toolchain", home / "secret"
    for d in (out / "build", tool, secret, home / ".ssh"):
        d.mkdir(parents=True)
    for d, text in ((out, "own"), (tool, "tool"), (secret, "secret"), (home / ".ssh", "key")):
        (d / "f").write_text(text)
    m = replace(M, vars={"tool": str(tool)}, deny_read=(str(home / ".ssh"),), prepare_read=())
    sb = tmp_path / "p.sb"
    sb.write_text(verify.profile(out / "build", m, prepare=True, home=home))

    def read(p):
        return subprocess.run(verify.wrap(["/bin/cat", str(p)], sb), capture_output=True, text=True).stdout

    assert read(out / "f") == "own" and read(tool / "f") == "tool"
    assert read(secret / "f") == "" and read(home / ".ssh" / "f") == ""
    sb.write_text(verify.profile(out / "build", replace(m, prepare_read=(str(secret),)), prepare=True, home=home))
    assert read(secret / "f") == "secret"


def test_a_step_leaves_no_background_processes(repo, tmp_path):
    bare, _, base = repo
    marker = "31.7183"
    m = replace(M, prepare=steps(("straggler", ["/bin/sh", "-c", f"/bin/sleep {marker} & exit 0"], 0, 10)))
    rs = verify.run_steps(bare, base, m.verify, m, tmp_path / "verification")
    assert verify.ok(rs, m.prepare + m.verify), verify.summary(rs)
    assert subprocess.run(["pgrep", "-af", f"sleep {marker}"], capture_output=True).returncode == 1


def test_profile_uses_real_paths(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    text = verify.profile(tmp_path / "link" / "build", M)
    assert str((tmp_path / "real" / "build").resolve()) in text and str(tmp_path / "link") not in text
