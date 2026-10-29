"""Real Gitea transport with deterministic request-start and response-read clocks."""

import pytest

from fake_gitea import Reply
from nightshift.forge import http
from specloop import critic
from test_specloop_critic_context import review
from test_specloop_critic_gitea import ROOT, gitea, label, pr


BUDGET = "spec-critic/gitea-context-request-limits/budget-prevents-later-dispatch"
SLOW = "spec-critic/gitea-context-request-limits/slowly-arriving-response-overruns"
PARTIAL = "spec-critic/concurrent-file-context/partial-lookup-remains-visible"
NOTE = "Open PR context incomplete: 120-second lookup budget exhausted."


@pytest.fixture
def now(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(critic.time, "monotonic", lambda: clock[0])
    return clock


def assert_rendered(review, files, notes):
    for echoes in ([], notes + [f"Critic: {note}" for note in notes]):
        prompt, result = review(files, notes + notes, github="forge.example/org/repo", risks=echoes)
        assert all(path in prompt and all(label in prompt for label in labels)
                   for path, labels in files.items())
        assert "unavailable or partial" in prompt
        assert result == {"contradictions": [], "risks": [f"Critic: {note}" for note in notes]}


@pytest.mark.scenario(BUDGET)
@pytest.mark.scenario(PARTIAL)
@pytest.mark.parametrize("retry_time", [119.95, 120, 121])
def test_retry_after_backoff_checks_budget(gitea, now, monkeypatch, review, retry_time):
    def first(call):
        now[0] = 1119.9
        return Reply([{"filename": "kept.py"}])

    def backoff(delay):
        assert delay == 0.1
        now[0] = 1000 + retry_time

    def retried(call):
        assert now[0] == 1000 + retry_time
        now[0] = 1120
        return Reply([{"filename": "retry.py"}])

    monkeypatch.setattr(http, "sleep", backoff)
    gitea.route("GET", ROOT + "pulls", Reply([pr(1), pr(2)]), page=1)
    gitea.route("GET", ROOT + "pulls/1/files", first, page=1)
    gitea.route("GET", ROOT + "pulls/1/files", OSError("private transport error"), retried, page=2)
    files, notes = critic.open_pr_files("forge.example/org/repo", "proposal")
    assert files == {path: [label(1)] for path in
                     (["kept.py", "retry.py"] if retry_time < 120 else ["kept.py"])}
    assert notes == [NOTE]
    assert [(c.path.removeprefix(ROOT), c.query["page"]) for c in gitea.calls] == [
        ("pulls", ["1"]), ("pulls/1/files", ["1"]), ("pulls/1/files", ["2"]),
        *([("pulls/1/files", ["2"])] if retry_time < 120 else [])]
    assert all(c.timeout == 30 for c in gitea.calls)
    assert all(response.closed for response in gitea.responses)
    assert_rendered(review, files, notes)


@pytest.mark.scenario(SLOW)
@pytest.mark.scenario(BUDGET)
@pytest.mark.parametrize("elapsed", [31, 120, 150])
@pytest.mark.parametrize("endpoint,page", [("pulls", 1), ("pulls/1/files", 1), ("pulls", 2)])
def test_slow_body_read_retains_overrun_evidence(gitea, now, monkeypatch, review, endpoint, page, elapsed):
    gitea.route("GET", ROOT + "pulls", Reply([pr(1), pr(2)]), page=1)
    gitea.route("GET", ROOT + "pulls", Reply([]), page=2)
    gitea.route("GET", ROOT + "pulls/1/files", Reply([
        {"filename": "src/new.py", "previous_filename": "old.py"}]), page=1)
    gitea.route("GET", ROOT + "pulls/1/files", Reply([]), page=2)
    gitea.route("GET", ROOT + "pulls/2/files", Reply([]), page=1)
    original = gitea.open

    def opener(request, *, timeout):
        response = original(request, timeout=timeout)
        if gitea.calls[-1].path == ROOT + endpoint and gitea.calls[-1].query["page"] == [str(page)]:
            read = response.read

            def slow_read():
                now[0] += elapsed
                return read()

            response.read = slow_read
        return response

    monkeypatch.setattr(gitea, "open", opener)
    files, notes = critic.open_pr_files("forge.example/org/repo", "proposal")
    assert files == ({} if (endpoint, page) == ("pulls", 1) and elapsed >= 120 else
                     {"old.py": [label(1)], "src/new.py": [label(1)]})
    assert notes == ([NOTE] if elapsed >= 120 else [])
    expected_calls = {("pulls", 1): 1, ("pulls/1/files", 1): 2, ("pulls", 2): 5}[
        endpoint, page] if elapsed >= 120 else 5
    assert len(gitea.calls) == expected_calls
    assert all(c.timeout == 30 for c in gitea.calls)
    assert all(response.closed for response in gitea.responses)
    if notes:
        assert_rendered(review, files, notes)


@pytest.mark.scenario("spec-critic/concurrent-file-context/empty-and-unconfigured-context")
@pytest.mark.parametrize("github", ["", "forge.example/org/repo"])
def test_gitea_empty_and_unconfigured_context(gitea, review, github):
    gitea.route("GET", ROOT + "pulls", Reply([]), page=1)
    files, notes = critic.open_pr_files(github, "proposal")
    assert (files, notes) == ({}, [])
    assert len(gitea.calls) == bool(github)
    prompt, result = review(files, notes, github=github)
    assert ("Successful lookup: no open PR changed files." if github else
            "PR context is not configured.") in prompt
    assert result == {"contradictions": [], "risks": []}
