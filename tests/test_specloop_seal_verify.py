import json
import os
import subprocess
import traceback
from dataclasses import replace
from pathlib import Path

import pytest

from nightshift import config
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


STEERING_PATHS = ("AGENTS.md", "CLAUDE.md", ".claude/settings.json", ".codex/config.toml", ".mcp.json")


@pytest.mark.scenario("agent-steering-seal/unconditional-steering-freeze/protected-paths-without-manifest-entries")
@pytest.mark.scenario("agent-steering-seal/frozen-edit-results/existing-steering-edits-are-reverted-and-reported")
@pytest.mark.scenario("agent-steering-seal/frozen-edit-results/only-protected-changes-produce-no-commit")
@pytest.mark.scenario("agent-steering-seal/frozen-edit-results/unchanged-steering-files-remain-quiet")
@pytest.mark.parametrize("name", STEERING_PATHS)
@pytest.mark.parametrize("prefix", ["", "src/nested/"])
@pytest.mark.parametrize("edit", ["add", "content", "delete", "mode", "unchanged"])
@pytest.mark.parametrize("companion", [False, True])
def test_agent_steering_files_frozen_without_manifest_entry(repo, name, prefix, edit, companion):
    bare, work, base = repo
    path = prefix + name
    m = manifest.parse({**M_DATA, "paths": {"allowed": [*STEERING_PATHS, "src/**"]}})
    assert m.frozen == () and manifest.matches(path, m.allowed)
    if edit != "add":
        base = seal.commit_files(bare, base, {path: "trusted instructions\n"}, "trusted base")
        sh(work, "git", "fetch", "-q", str(bare), base)
        sh(work, "git", "reset", "--hard", base)
    f = work / path
    if edit in ("add", "content"):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("attempted instructions\n")
    elif edit == "delete":
        f.unlink()
    elif edit == "mode":
        f.chmod(0o755)
    if companion:
        (work / "src/a.txt").write_text("admissible edit\n")

    s = seal.seal(bare, work, base, m, "msg")
    assert s.changed == (["src/a.txt"] if companion else [])
    assert s.violations == ([] if edit == "unchanged" else [f"{path}: frozen path (change reverted)"])
    assert s.secret_paths == []
    if companion:
        assert s.sha is not None
        # ls-tree compares both the protected blob and mode (or its absence).
        assert sh(bare, "git", "ls-tree", s.sha, path) == sh(bare, "git", "ls-tree", base, path)
        assert tree(bare, s.sha) == tree(bare, base)
        assert sh(bare, "git", "show", f"{s.sha}:src/a.txt") == "admissible edit"
        assert sh(bare, "git", "rev-parse", f"{s.sha}^") == base
    else:
        assert s.sha is None


@pytest.mark.scenario("agent-steering-seal/unconditional-steering-freeze/manifest-allowances-cannot-override-protection")
@pytest.mark.parametrize("frozen", [[], ["openspec/**"]], ids=["empty", "unrelated"])
@pytest.mark.parametrize("allowance", ["exact", "directory"])
def test_manifest_cannot_unfreeze_steering_files(repo, frozen, allowance):
    bare, work, base = repo
    paths = [prefix + name for prefix in ("", "src/nested/") for name in STEERING_PATHS]
    allowed = paths if allowance == "exact" else ["AGENTS.md", "CLAUDE.md", ".mcp.json",
                                                 ".claude/**", ".codex/**", "src/**"]
    m = manifest.parse({**M_DATA, "paths": {"allowed": [*allowed, "src/a.txt"], "frozen": frozen}})
    for path in paths:
        assert manifest.matches(path, m.allowed) and not manifest.matches(path, m.frozen)
        f = work / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("attempted instructions\n")
    (work / "src/a.txt").write_text("admissible edit\n")
    s = seal.seal(bare, work, base, m, "msg")
    assert s.sha is not None and s.changed == ["src/a.txt"]
    assert s.violations == [f"{path}: frozen path (change reverted)" for path in sorted(paths)]
    assert tree(bare, s.sha) == tree(bare, base)
    assert sh(bare, "git", "show", f"{s.sha}:src/a.txt") == "admissible edit"


@pytest.mark.scenario("agent-steering-seal/unconditional-steering-freeze/component-matching-is-precise")
@pytest.mark.parametrize("prefix", ["", "src/nested/"])
@pytest.mark.parametrize("name,protected", [
    ("agents.MD", True), ("cLaUdE.mD", True), (".ClAuDe/settings.json", True),
    (".CoDeX/config.toml", True), (".MCP.JSON", True),
    ("AGENTS.md/child.txt", True), ("CLAUDE.md/child.txt", True),
    (".mcp.json/child.txt", True), (".claude", True), (".codex", True),
    ("AGENTS.md.example", False), ("CLAUDE.md.example", False),
    (".claude-backup/settings.json", False), (".codex-backup/config.toml", False),
    (".mcp.json.example", False), ("prefixAGENTS.md", False),
    ("AGENTS.md.example/child.txt", False),
])
def test_steering_component_boundaries(repo, prefix, name, protected):
    bare, work, base = repo
    path = prefix + name
    m = manifest.parse({**M_DATA, "paths": {"allowed": [path, "src/a.txt"]}})
    assert manifest.matches(path, m.allowed)
    f = work / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("new file\n")
    (work / "src/a.txt").write_text("admissible edit\n")
    s = seal.seal(bare, work, base, m, "msg")
    assert s.sha is not None
    assert s.changed == sorted(["src/a.txt"] + ([] if protected else [path]))
    assert s.violations == ([f"{path}: frozen path (change reverted)"] if protected else [])
    assert tree(bare, s.sha) == tree(bare, base) | (set() if protected else {path})
    if not protected:
        assert sh(bare, "git", "show", f"{s.sha}:{path}") == "new file"


@pytest.mark.parametrize("path", ["AGENTS.md", "src/nested/.codex/config.toml"])
def test_steering_symlink_is_still_rejected_without_reading(repo, path):
    bare, work, base = repo
    base = seal.commit_files(bare, base, {path: "trusted instructions\n"}, "trusted base")
    sh(work, "git", "fetch", "-q", str(bare), base)
    sh(work, "git", "reset", "--hard", base)
    f = work / path
    f.unlink()
    f.symlink_to(work / "missing-target")
    (work / "src/a.txt").write_text("admissible edit\n")
    m = manifest.parse({**M_DATA, "paths": {"allowed": [path, "src/**"]}})
    s = seal.seal(bare, work, base, m, "msg")
    assert s.sha is not None and s.changed == ["src/a.txt"]
    assert s.violations == [f"{path}: not a regular file"]
    assert sh(bare, "git", "ls-tree", s.sha, path) == sh(bare, "git", "ls-tree", base, path)


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


FORGE_TABLES = '''
[[forges]]
name = "used"
kind = "github"
token_env = "SEAL_BOT"
land_token_env = "SEAL_LAND"
[[forges]]
name = "unused"
kind = "gitea"
url = "https://forge.example"
token_env = "SEAL_UNUSED_BOT"
land_token_env = "SEAL_UNUSED_LAND"
bot_login = "bot"
# Enrollment validation must not run during credential discovery.
[[repos]]
name = 42
forge = "used"
'''
FORGE_KEYS = ("SEAL_BOT", "SEAL_LAND", "SEAL_UNUSED_BOT", "SEAL_UNUSED_LAND")


@pytest.fixture
def forge_sources(monkeypatch):
    for key in (*FORGE_KEYS, "CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
                "SPECLOOP_SLACK_WEBHOOK"):
        monkeypatch.delenv(key, raising=False)
    config.ENV_FILE.parent.mkdir(parents=True)
    config.DEFAULT_PATH.write_text(FORGE_TABLES)


@pytest.mark.scenario("forge-seal/every-forge-token-is-scanned/all-declared-values-are-protected")
def test_seal_scans_every_forge_token(repo, forge_sources, monkeypatch, capsys, caplog):
    bare, work, base = repo
    extra = {key: f"extra-value-{key}" for key in FORGE_KEYS}
    ambient = {key: f"process-value-{key}" for key in FORGE_KEYS}
    file_values = {key: f"file-value-{key}" for key in FORGE_KEYS}
    file_values["SEAL_UNUSED_LAND"] = "xy"  # Short declared tokens are secrets too.
    extra["SEAL_BOT"] = ambient["SEAL_BOT"]  # Duplicate values need only one scan.
    monkeypatch.setenv("GH_TOKEN", extra["SEAL_BOT"])  # Also found by the legacy scan.
    for key, value in ambient.items():
        monkeypatch.setenv(key, value)
    config.ENV_FILE.write_text("".join(f"export {k}='{v}'\n" for k, v in file_values.items()))
    values = seal.credential_values(extra)
    expected = set(extra.values()) | set(ambient.values()) | set(file_values.values())
    assert set(values) == expected and len(values) == len(expected)
    for i, value in enumerate(sorted(expected)):
        (work / "src" / f"leak-{i:02}.txt").write_text(value)
    (work / "new.txt").write_text("otherwise admissible")
    real_git = seal.git

    def no_commit(repo, *args, **kwargs):
        assert "commit-tree" not in args
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(seal, "git", no_commit)
    result = seal.seal(bare, work, base, M, "msg", secrets=values)
    assert result.sha is None and result.changed == ["new.txt"]
    assert result.secret_paths == [f"src/leak-{i:02}.txt" for i in range(len(expected))]
    captured = capsys.readouterr()
    assert all(v not in repr(result) + captured.out + captured.err + caplog.text for v in expected)


@pytest.mark.scenario("forge-seal/trusted-forge-discovery/configuration-selection-is-deterministic")
@pytest.mark.parametrize("selection", ["explicit", "default", "absent"])
def test_seal_trusted_config_selection(tmp_path, forge_sources, monkeypatch, selection):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "config.toml").write_text("malformed candidate config [")
    monkeypatch.chdir(candidate)
    explicit = tmp_path / "trusted.toml"
    explicit.write_text(FORGE_TABLES)
    if selection == "explicit":
        monkeypatch.setenv("NIGHTSHIFT_FORGE_CONFIG", str(explicit))
        config.DEFAULT_PATH.write_text("malformed ignored default [")
    elif selection == "absent":
        config.DEFAULT_PATH.unlink()
    monkeypatch.setenv("SEAL_BOT", "trusted-bot-value")
    # An untrusted manifest env source cannot override process selection.
    extra = {"NIGHTSHIFT_FORGE_CONFIG": str(candidate / "config.toml")}
    expected = [] if selection == "absent" else ["trusted-bot-value"]
    assert seal.credential_values(extra) == expected
    assert seal.credential_values() == expected


@pytest.mark.scenario("forge-seal/trusted-forge-discovery/invalid-selected-source-blocks-sealing")
@pytest.mark.parametrize("case", ["blank", "empty", "relative", "missing", "explicit-unreadable",
                                  "default-unreadable", "explicit-malformed", "default-malformed",
                                  "explicit-invalid-forge", "default-invalid-forge", "env-unreadable",
                                  "default-dangling", "env-dangling"])
def test_seal_invalid_credential_source(repo, forge_sources, monkeypatch, case):
    bare, work, base = repo
    sentinel = "synthetic-secret-must-not-appear"
    if case in ("blank", "empty", "relative", "missing"):
        override = {"blank": " ", "empty": "", "relative": sentinel, "missing": str(work / sentinel)}[case]
        monkeypatch.setenv("NIGHTSHIFT_FORGE_CONFIG", override)
    if case.startswith("explicit-"):
        monkeypatch.setenv("NIGHTSHIFT_FORGE_CONFIG", str(config.DEFAULT_PATH))
    if case.endswith("malformed"):
        config.DEFAULT_PATH.write_text(f'[{sentinel}\n')
    if case.endswith("invalid-forge"):
        config.DEFAULT_PATH.write_text(f'forges = "{sentinel}"\n')
    if case.endswith("dangling"):
        target = config.ENV_FILE if case == "env-dangling" else config.DEFAULT_PATH
        target.unlink(missing_ok=True)
        target.symlink_to(work / sentinel)
    if case.endswith("unreadable"):
        target = config.ENV_FILE if case == "env-unreadable" else config.DEFAULT_PATH
        target.touch()
        read_text = Path.read_text

        def unreadable(path, *args, **kwargs):
            if path == target:
                raise PermissionError(sentinel)
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", unreadable)
    (work / "new.txt").write_text("otherwise admissible")
    monkeypatch.setattr(seal, "git", lambda *a, **kw: pytest.fail("sealing reached git"))
    with pytest.raises(ValueError) as caught:
        seal.seal(bare, work, base, M, "msg", secrets=seal.credential_values())
    assert sentinel not in "".join(traceback.format_exception(caught.value))
    assert "NIGHTSHIFT_FORGE_CONFIG" in str(caught.value) or "invalid trusted config or env file" in str(caught.value)


@pytest.mark.scenario("forge-seal/every-forge-token-is-scanned/missing-values-do-not-invent-secrets")
def test_seal_missing_forge_values_preserves_existing_sources(forge_sources, monkeypatch):
    monkeypatch.setenv("SEAL_BOT", "")
    config.ENV_FILE.write_text("export SEAL_LAND=''\nexport UNDECLARED='not-a-known-secret'\n")
    auth = Path.home() / ".codex-api" / "auth.json"
    auth.parent.mkdir()
    profile_token = "profile-secret-" + "x" * 24
    auth.write_text(json.dumps({"tokens": {"access": profile_token, "short": "ignore"}}))
    monkeypatch.setenv("GH_TOKEN", "shadowed-fixed-value")
    monkeypatch.setenv("GITHUB_TOKEN", "existing-github-token")
    monkeypatch.setenv("SPECLOOP_SLACK_WEBHOOK", "short")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("credential retrieval"))
    monkeypatch.setattr(config, "load_env_file", lambda *a, **kw: pytest.fail("credential injection"))
    values = seal.credential_values({"GH_TOKEN": "existing-extra-token", "SEAL_UNUSED_BOT": ""})
    assert set(values) == {profile_token, "existing-extra-token", "existing-github-token"}
    assert os.environ["SEAL_BOT"] == "" and "SEAL_LAND" not in os.environ
    assert "SEAL_UNUSED_LAND" not in os.environ  # No attempt to retrieve a human keychain token.


@pytest.mark.scenario("forge-seal/every-forge-token-is-scanned/all-declared-values-are-protected")
def test_seal_env_file_values_are_literal(forge_sources, monkeypatch, tmp_path):
    marker = tmp_path / "must-not-exist"
    literal = f"$(touch {marker})"
    config.ENV_FILE.write_text(f"export SEAL_BOT='{literal}'\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("shell execution"))
    assert seal.credential_values() == [literal]
    assert not marker.exists() and "SEAL_BOT" not in os.environ


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


@pytest.mark.skipif(verify.already_sandboxed(), reason="asserts the sandbox policy; an enclosing sandbox replaces it")
def test_prepare_can_read_a_candidate_source_outside_its_own_directory(tmp_path):
    # Mutants run in out/mutant-N/build while the candidate stays in out/src.
    home = tmp_path / "home"
    src, build = home / "run" / "v" / "src", home / "run" / "v" / "mutant-1" / "build"
    src.mkdir(parents=True); build.mkdir(parents=True)
    (src / "f").write_text("candidate")
    sb = tmp_path / "p.sb"
    sb.write_text(verify.profile(build, M, prepare=True, home=home, src=src))
    out = subprocess.run(verify.wrap(["/bin/cat", str(src / "f")], sb), capture_output=True, text=True).stdout
    assert out == "candidate"
    sb.write_text(verify.profile(build, M, prepare=True, home=home))
    assert subprocess.run(verify.wrap(["/bin/cat", str(src / "f")], sb), capture_output=True, text=True).stdout == ""
