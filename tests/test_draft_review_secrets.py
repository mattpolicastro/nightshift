"""Drafter, feasibility and critic sessions get the manifest's env file, like build sessions."""

import subprocess

import pytest

from specloop import agents, critic, feasibility, manifest, propose

TOKEN = "sk-ant-oat-test-manifest-token"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "repo"
    src.mkdir()
    git(src, "init", "-q", "-b", "main")
    (src / "calc.py").write_text("def sign(x): return 0\n")
    git(src, "add", "-A")
    git(src, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
    return src


@pytest.fixture
def m(tmp_path, repo):
    env = tmp_path / "manifest.env"
    env.write_text(f'export CLAUDE_CODE_OAUTH_TOKEN="{TOKEN}"\n')
    return manifest.parse({
        "run": {"repo": str(repo), "change": "c", "branch": "spec/c", "env_file": str(env)},
        "roles": {"implement": "p", "review": "p"},
        "profiles": {"p": {"adapter": "claude", "model": "sonnet"}},
        "paths": {"allowed": ["calc.py"]},
        "verify": [{"id": "tests", "run": ["true"]}],
    })


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, call):
        self.calls.append(call)
        return agents.Result("infra", detail="fake agent")


def assert_carries_token(rec):
    assert rec.calls, "the agent was never called"
    for call in rec.calls:
        assert call.secrets.get("CLAUDE_CODE_OAUTH_TOKEN") == TOKEN
        assert TOKEN not in call.prompt


@pytest.mark.scenario("provider-fallback/manifest-credentials-for-every-session/drafting-session-carries-the-manifest-credentials")
def test_drafter_call_carries_manifest_secrets(tmp_path, repo, m, monkeypatch):
    monkeypatch.setattr(propose, "in_flight", lambda source, change: {})
    rec = Recorder()
    d = propose.draft(repo, "main", "c", "idea", [], m, tmp_path / "draft", agent=rec)
    assert d.problems and "drafting session failed" in d.problems[0]
    assert_carries_token(rec)


@pytest.mark.scenario("provider-fallback/manifest-credentials-for-every-session/feasibility-session-carries-the-manifest-credentials")
def test_feasibility_call_carries_manifest_secrets(tmp_path, repo, m):
    rec = Recorder()
    result = feasibility.check(repo, git(repo, "rev-parse", "HEAD"), m, tmp_path / "feasibility", agent=rec)
    assert result.get("error", "").startswith("infra")
    assert_carries_token(rec)


@pytest.mark.scenario("provider-fallback/manifest-credentials-for-every-session/critic-session-carries-the-manifest-credentials")
def test_critic_call_carries_manifest_secrets(tmp_path, repo, m):
    rec = Recorder()
    result = critic.check(repo, git(repo, "rev-parse", "HEAD"), m, tmp_path / "critic", change="c",
                          planned={}, pr_files={}, context_notes=[], agent=rec)
    assert result.get("error", "").startswith("infra")
    assert_carries_token(rec)
