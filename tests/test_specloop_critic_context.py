"""Offline collector/reviewer contracts; proposal retry wiring is tested separately."""

import json
import subprocess
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

from specloop import agents, critic, manifest


BOTH = "spec-critic/concurrent-file-context/both-sources-reach-the-critic"
PAGES = "spec-critic/concurrent-file-context/pagination-renames-and-own-branch"
EMPTY = "spec-critic/concurrent-file-context/empty-and-unconfigured-context"
PARTIAL = "spec-critic/concurrent-file-context/partial-lookup-remains-visible"
M = manifest.parse({
    "run": {"repo": "/unused", "change": "candidate", "branch": "spec/candidate", "github": "org/repo"},
    "roles": {"implement": "review", "review": "review"},
    "profiles": {"review": {"adapter": "codex", "model": "test"}},
    "paths": {"allowed": ["src/**", "tests/**"], "frozen": []},
    "verify": [{"id": "tests", "run": ["true"]}],
})


def pr(number, branch="feature", repository="org/repo", **extra):
    return {"number": number, "html_url": f"https://example.test/org/repo/pull/{number}",
            "head": {"ref": branch, "repo": {"full_name": repository}}, **extra}


def label(number):
    return f"#{number} https://example.test/org/repo/pull/{number}"


class FakeRunner:
    def __init__(self, pages, host=None):
        self.pages = pages
        self.host = host
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        assert argv[:2] == ["gh", "api"]
        assert argv[3:] == (["--hostname", self.host] if self.host else []) + ["--method", "GET"]
        assert kwargs == {"capture_output": True, "text": True, "check": True,
                          "timeout": kwargs["timeout"]}
        assert 0 < kwargs["timeout"] <= 30
        url = urlsplit(argv[2])
        query = parse_qs(url.query)
        assert query["per_page"] == ["100"]
        assert query == ({"state": ["open"]} if url.path.endswith("/pulls") else {}) | {
            "per_page": ["100"], "page": query["page"]}
        key = (url.path.removeprefix("repos/org/repo/"), int(query["page"][0]))
        assert key in self.pages, f"Unexpected request: {key}"
        data = self.pages[key]
        if isinstance(data, Exception):
            raise data
        return subprocess.CompletedProcess(argv, 0, data if isinstance(data, str) else json.dumps(data))


@pytest.fixture
def review(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    (repo / "candidate.txt").write_text("candidate\n")
    git("add", ".")
    git("-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "candidate")
    rev = git("rev-parse", "HEAD")
    prompts = []

    def run(files, notes, *, planned=None, github="org/repo", risks=()):
        out = tmp_path / f"critic-{len(prompts)}"

        def agent(call):
            assert call.access == "read"
            prompts.append(call.prompt)
            return agents.Result("ok", {"contradictions": [], "risks": list(risks)})

        result = critic.check(repo, rev, replace(M, github=github), out, change="candidate",
                              planned=planned or {}, pr_files=files, context_notes=notes, agent=agent)
        assert "error" not in result
        assert result == json.loads((out / "critic.json").read_text())
        return prompts[-1], result

    return run


def context_map(prompt, heading, following):
    return json.loads(prompt.split(heading, 1)[1].split(following, 1)[0].strip())


@pytest.mark.scenario(BOTH)
def test_critic_receives_in_flight_and_open_pr_files(review):
    runner = FakeRunner({("pulls", 1): [pr(7)], ("pulls/7/files", 1): [
        {"filename": "src/remote.py", "patch": "DO NOT INCLUDE PATCH"},
        {"filename": "tests/test_remote.py"}, {"filename": "README.md"},
    ]})
    snapshot = critic.open_pr_files("org/repo", "spec/candidate", runner=runner)
    planned = {"tests/test_local.py": ["z-change", "a-change", "z-change"],
               "src/local.py": ["local-change"]}
    original = json.dumps(planned)
    first, result = review(*snapshot, planned=planned)
    # A reviewer recheck consumes the already-collected snapshot, without I/O.
    second, _ = review(*snapshot, planned=dict(reversed(list(planned.items()))))
    assert first == second
    assert len(runner.calls) == 2
    assert json.dumps(planned) == original
    assert context_map(first, "In-flight planned files (path to change names):", "Open PR changed files") == {
        "src/local.py": ["local-change"], "tests/test_local.py": ["a-change", "z-change"]}
    for path in ("src/remote.py", "tests/test_remote.py", "README.md"):
        assert f'"{path}"' in first
    assert label(7) in first
    assert "DO NOT INCLUDE PATCH" not in first
    assert result == {"contradictions": [], "risks": []}


@pytest.mark.scenario(PAGES)
def test_pagination_renames_own_branch_and_explicit_host(review):
    prs = [pr(1, "spec/effective", "ORG/REPO"),
           pr(2, "spec/effective", "fork/repo", draft=True, base={"ref": "release"}),
           pr(3, "spec/manifest-template")]
    prs += [pr(n) for n in range(4, 101)]
    pages = {("pulls", 1): prs, ("pulls", 2): [pr(101)],
             ("pulls/2/files", 1): [{"filename": "README.md"}],
             ("pulls/3/files", 1): [{"filename": "z.py"}] * 100,
             ("pulls/3/files", 2): [{"filename": "src/new.py", "previous_filename": "old.py"},
                                      {"filename": "README.md"}],
             ("pulls/101/files", 1): [{"filename": "README.md"}]}
    pages.update({(f"pulls/{n}/files", 1): [] for n in range(4, 101)})
    runner = FakeRunner(pages, host="github.com")
    files, notes = critic.open_pr_files("github.com/org/repo/", "spec/effective", runner=runner)
    assert notes == []
    assert list(files) == ["README.md", "old.py", "src/new.py", "z.py"]
    assert files == {"README.md": sorted([label(2), label(3), label(101)]),
                     "old.py": [label(3)], "src/new.py": [label(3)], "z.py": [label(3)]}
    prompt, _ = review(files, notes)
    reordered = {path: list(reversed(labels)) + labels for path, labels in reversed(list(files.items()))}
    second, _ = review(reordered, notes)
    assert prompt == second
    assert label(1) not in prompt
    assert "old.py" in prompt and "src/new.py" in prompt
    assert "Successfully collected PR context." in prompt


@pytest.mark.scenario(PAGES)
@pytest.mark.scenario(PARTIAL)
@pytest.mark.parametrize("head", [{"ref": "spec/candidate", "repo": None},
                                  {"ref": "spec/candidate"},
                                  {"ref": "spec/candidate", "repo": {"full_name": 1}}])
def test_unknown_head_repository_does_not_exclude_pr(review, head):
    runner = FakeRunner({("pulls", 1): [pr(1, head=head)],
                         ("pulls/1/files", 1): [{"filename": "root.py"}]})
    files, notes = critic.open_pr_files("org/repo", "spec/candidate", runner=runner)
    assert files == {"root.py": [label(1)]}
    assert bool(notes) == (head != {"ref": "spec/candidate", "repo": None})
    prompt, result = review(files, notes)
    assert "root.py" in prompt
    assert result["risks"] == [f"Critic: {note}" for note in notes]


@pytest.mark.scenario(EMPTY)
@pytest.mark.parametrize("github", ["", "org/repo"])
def test_empty_and_unconfigured_have_distinct_prompt_states(review, github, monkeypatch):
    runner = FakeRunner({("pulls", 1): []})
    # Also verify that the production default resolves the runner at call time.
    with monkeypatch.context() as patch:
        patch.setattr(critic.subprocess, "run", runner)
        files, notes = critic.open_pr_files(github, "spec/candidate")
    assert (files, notes) == ({}, [])
    assert len(runner.calls) == bool(github)
    prompt, result = review(files, notes, github=github)
    expected = "Successful lookup: no open PR changed files." if github else "PR context is not configured."
    assert expected in prompt
    assert result["risks"] == []


@pytest.mark.scenario(PARTIAL)
@pytest.mark.parametrize("failure", [
    subprocess.CalledProcessError(1, ["gh"], stderr="PRIVATE ERROR"),
    FileNotFoundError("gh"), subprocess.TimeoutExpired(["gh"], 30),
    "not json PRIVATE ERROR", {"message": "PRIVATE ERROR"}, [None, {"filename": None}],
])
def test_partial_files_survive_failures_and_limitations_reach_verdict(review, failure):
    runner = FakeRunner({("pulls", 1): [pr(1), pr(2)],
                         ("pulls/1/files", 1): [{"filename": "kept.py"}] * 100,
                         ("pulls/1/files", 2): failure,
                         ("pulls/2/files", 1): [{"filename": "tests/test_later.py"}]})
    files, notes = critic.open_pr_files("org/repo", "spec/candidate", runner=runner)
    assert files == {"kept.py": [label(1)], "tests/test_later.py": [label(2)]}
    assert len(notes) == 1 and "#1" in notes[0]
    for echoes in ([], [notes[0], f"Critic: {notes[0]}"]):
        prompt, result = review(files, notes + notes, risks=echoes)
        assert "unavailable or partial" in prompt
        assert "kept.py" in prompt and "tests/test_later.py" in prompt
        assert "PRIVATE ERROR" not in prompt
        assert result == {"contradictions": [], "risks": [f"Critic: {notes[0]}"]}


@pytest.mark.scenario(PARTIAL)
def test_pr_list_failure_retains_prior_pages_and_malformed_records_keep_valid_files(review):
    runner = FakeRunner({("pulls", 1): [pr(1)] * 100,
                         ("pulls", 2): "invalid json",
                         ("pulls/1/files", 1): [None, {"filename": "root.py", "previous_filename": 1},
                                                   {"filename": "tests/valid.py"}]})
    files, notes = critic.open_pr_files("org/repo", "spec/candidate", runner=runner)
    assert files == {"root.py": [label(1)], "tests/valid.py": [label(1)]}
    assert len(notes) == 2
    prompt, result = review(files, notes)
    assert "root.py" in prompt
    assert result["risks"] == [f"Critic: {note}" for note in notes]


@pytest.mark.scenario(PARTIAL)
@pytest.mark.parametrize("prs", [[{}, pr(1)], {"message": "bad"}])
def test_malformed_pr_list_is_visible(review, prs):
    runner = FakeRunner({("pulls", 1): prs, ("pulls/1/files", 1): [{"filename": "valid.py"}]})
    files, notes = critic.open_pr_files("org/repo", "spec/candidate", runner=runner)
    assert bool(files) == isinstance(prs, list)
    assert notes
    prompt, result = review(files, notes)
    assert "unavailable or partial" in prompt
    assert "Successful lookup" not in prompt
    assert result["risks"] == [f"Critic: {note}" for note in notes]


@pytest.mark.scenario(PARTIAL)
@pytest.mark.parametrize("timeout", [False, True])
def test_overall_budget_bounds_every_request_and_preserves_collected_paths(review, monkeypatch, timeout):
    now = [0.0]
    monkeypatch.setattr(critic.time, "monotonic", lambda: now[0])
    calls = []

    def runner(argv, **kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) == 1:
            data = [pr(1), pr(2)]
        else:
            data = [{"filename": f"page-{len(calls)}.py"}] * 100
        now[0] += [30, 30, 30, 29, 1][len(calls) - 1]
        if timeout and len(calls) == 5:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, json.dumps(data))

    files, notes = critic.open_pr_files("org/repo", "spec/candidate", runner=runner)
    assert calls == [30, 30, 30, 30, 1]
    assert set(files) == {f"page-{n}.py" for n in range(2, 5 if timeout else 6)}
    assert any("120-second" in note for note in notes)
    assert len(notes) == (2 if timeout else 1)
    prompt, result = review(files, notes)
    assert "page-2.py" in prompt and "unavailable or partial" in prompt
    assert result["risks"] == [f"Critic: {note}" for note in notes]


@pytest.mark.scenario(PARTIAL)
def test_api_file_ceiling_is_reported_without_requesting_page_31(review):
    pages = {("pulls", 1): [pr(1), pr(2)], ("pulls/2/files", 1): [{"filename": "later.py"}]}
    pages.update({("pulls/1/files", page): [
        {"filename": f"src/file-{(page - 1) * 100 + n}.py"} for n in range(100)
    ] for page in range(1, 31)})
    runner = FakeRunner(pages)
    files, notes = critic.open_pr_files("org/repo", "spec/candidate", runner=runner)
    assert len(files) == 3001
    assert len(runner.calls) == 32
    assert len(notes) == 1 and "3000-file-per-PR ceiling" in notes[0] and "#1" in notes[0]
    prompt, result = review(files, notes)
    assert "src/file-2999.py" in prompt and "later.py" in prompt
    assert result["risks"] == [f"Critic: {notes[0]}"]
