import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from nightshift.cli import main

from specloop import agents, cli, critic, openspec, seal, state, tasks
from specloop.state import Run


CHANGE = "issue-7-y"
BRANCH = f"spec/{CHANGE}"
TASKS = "## 1. Core\n\n- [ ] 1.1 Implement `cap/req/example`\n"
CONTRADICTION = {"task": "1.1", "problem": "a frozen checker rejects it", "evidence": "tools/check.py:63"}
RISK = 'The behavior for "empty" input is vague.\nClarify it.'


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def template(tmp_path, monkeypatch):
    monkeypatch.setattr(critic, "open_pr_files", lambda github, branch: ({}, []))
    monkeypatch.setattr(state, "ROOT", tmp_path / "home")
    monkeypatch.setattr(cli, "load_env", lambda path: {})
    monkeypatch.setattr(seal, "credential_values", lambda secrets: [])
    monkeypatch.delenv("SPECLOOP_SLACK_WEBHOOK", raising=False)
    monkeypatch.setattr(openspec, "validate", lambda repo, change: [])
    monkeypatch.setattr(openspec, "scenarios", lambda repo, change: {"cap/req/example": ""})
    repo = tmp_path / "source"
    task_file = repo / "openspec" / "changes" / CHANGE / "tasks.md"
    task_file.parent.mkdir(parents=True)
    task_file.write_text(TASKS)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "spec")
    git(repo, "branch", BRANCH)
    path = tmp_path / "template.toml"
    path.write_text(f"""
[run]
repo = {json.dumps(str(repo))}
change = "x"
branch = "spec/x"
github = "example/repo"
[roles]
implement = "p"
review = ["p"]
[profiles.p]
adapter = "codex"
model = "test"
[paths]
allowed = ["out/**"]
frozen = ["openspec/**"]
[[verify]]
id = "tests"
run = ["python", "-c", "print('hello')"]
timeout = 42
[coverage.exempt]
"cap/req/manual" = "Human checks this"
[[drill]]
path = "out/file"
replace = {{ old = "x", new = "y" }}
""")
    return path, repo


def admission(path, *extra):
    return ["admit", str(path), "--change", CHANGE, "--branch", BRANCH, *extra]


def review(monkeypatch, contradictions=()):
    def agent(call):
        assert call.access == "read"
        assert f"openspec/changes/{CHANGE}/" in call.prompt
        print("feasibility progress")
        return agents.Result("ok", {"contradictions": list(contradictions), "risks": [RISK]})
    monkeypatch.setattr(agents, "run", agent)


def daemon_draft_evidence(change):
    from nightshift import specloop_job as jobs

    directory = state.state_root() / "proposals" / change / "7.jobs" / ("a" * 32)
    directory.mkdir(parents=True)
    idea = directory / "idea.txt"
    idea.write_text("Retained daemon idea\n")
    for name in ("lock", "control.lock", "log"):
        (directory / name).write_text(f"retained {name}\n")
    jobs.save(directory / "request.json", {
        "stage": "proposals", "args": ["propose", "template.toml", change, "--idea-file", str(idea)],
        "started_at": 100, "timeout_seconds": 3600, "deadline_at": 3700,
    })
    process = {
        "version": 1, "job_id": directory.name, "pid": 1234, "pgid": 1234,
        "start_identity": "retained-identity", "started_at": "2026-10-21T12:00:00Z",
    }
    jobs.save(directory / "process.json", process)
    jobs.save(directory / "stop.json", {
        "state": "stopped", "process": process,
        "requested_at": "2026-10-21T12:01:00Z", "stopped_at": "2026-10-21T12:01:01Z",
    })
    jobs.save(directory / "result.json", {"error": "stopped by operator"})
    jobs.save(directory.parent.with_suffix(".json"), {
        "job": directory.name, "repo": change.replace("__", "/", 1), "number": 7, "delivered": False,
    })
    return directory


@pytest.mark.parametrize("generated", [
    pytest.param(False, marks=pytest.mark.scenario(
        "state-migration/separate-draft-namespace/draft-replacement-preserves-daemon-evidence")),
    pytest.param(True, marks=pytest.mark.scenario(
        "state-migration/separate-draft-namespace/generated-names-cannot-collide-with-another-repository")),
])
@pytest.mark.scenario("state-migration/separate-draft-namespace/unsafe-draft-replacement-refuses")
def test_cli_draft_replacement_preserves_daemon_evidence(template, monkeypatch, capfd, generated):
    from nightshift import specloop_intake, specloop_job as jobs
    from test_specloop_propose import draft_evidence_snapshot

    path, repo = template
    change = specloop_intake._change_name("owner/repo", 7, "Add a gate") if generated else "owner__repo"
    assert change == ("owner__repo-issue-7-add-a-gate" if generated else "owner__repo")
    directory = daemon_draft_evidence(change)
    evidence = state.state_root() / "proposals"
    before = draft_evidence_snapshot(evidence)
    inventory = jobs.list_jobs(state.state_root(), include_delivery=True)
    rows, errors = inventory
    assert not errors and len(rows) == 1
    assert (rows[0].id, rows[0].repository, rows[0].number, rows[0].directory, rows[0].state) == (
        "a" * 32, "owner/repo-issue-7-add-a-gate" if generated else "owner/repo", 7, directory, "stopped")
    assert rows[0].identity_available and rows[0].process["start_identity"] == "retained-identity"
    out = state.proposals_dir() / change
    legacy = state.legacy_root() / "proposals" / change
    legacy_link = None

    def agent(call):
        if call.access == "read":
            return agents.Result("ok", {"contradictions": [], "risks": []})
        assert call.cwd == out / "work"
        task = call.cwd / "openspec" / "changes" / change / "tasks.md"
        task.parent.mkdir(parents=True)
        task.write_text(TASKS)
        return agents.Result("ok", {"status": "drafted", "summary": "", "questions": []})

    monkeypatch.setattr(agents, "run", agent)
    monkeypatch.setattr(cli, "notifier", lambda secrets: lambda message: None)
    for iteration, entry in enumerate((main, cli.main, main, cli.main)):
        # Exercise both names before and with an already completed legacy link.
        # Real migration and its marker are covered by tasks 2.6/2.10.
        if iteration == 2:
            legacy.parent.mkdir(parents=True)
            legacy.symlink_to(out, target_is_directory=True)
            legacy_link = draft_evidence_snapshot(legacy)
        assert entry(["propose", str(path), change, "--idea", "Add a gate", "--json"]) == 0
        output = capfd.readouterr()
        document = json.loads(output.out)
        assert document == {"version": 1, "outcome": "drafted", "branch": f"spec/{change}",
                            "commit": git(out / "work", "rev-parse", "HEAD"), "questions": [],
                            "contradictions": [], "risks": [], "problems": []}
        assert ("specloop has been renamed to nightshift" in output.err) == (entry is cli.main)
        assert git(repo, "rev-parse", f"spec/{change}") == document["commit"]
        assert not (out / "obsolete").exists()
        (out / "obsolete").write_text("must be removed by the next draft")
        assert draft_evidence_snapshot(evidence) == before
        assert jobs.list_jobs(state.state_root(), include_delivery=True) == inventory
        if legacy_link is not None:
            assert draft_evidence_snapshot(legacy) == legacy_link


@pytest.mark.scenario("state-migration/separate-draft-namespace/unsafe-draft-replacement-refuses")
@pytest.mark.parametrize("entry", [main, cli.main], ids=["nightshift", "specloop"])
@pytest.mark.parametrize("unsafe", ["legacy", "daemon-link"])
def test_cli_unsafe_draft_refuses_without_touching_evidence(template, monkeypatch, capfd, entry, unsafe):
    from specloop import propose, state_locks
    from test_specloop_propose import draft_evidence_snapshot

    path, _ = template
    change = "owner__repo"
    directory = daemon_draft_evidence(change)
    out = state.proposals_dir() / change
    out.parent.mkdir(parents=True)
    legacy = state.legacy_root() / "proposals" / change
    if unsafe == "legacy":
        legacy.mkdir(parents=True)
        (legacy / "retained").write_text("legacy workspace")
        out.mkdir()
        (out / "retained").write_text("canonical workspace")
    else:
        out.symlink_to(directory.parent.parent)
    # Initialize the stable coordination inode before snapshotting; even a
    # refused writer acquires it, while all draft/receipt evidence must survive.
    with state_locks.writer():
        pass
    roots = (state.state_root(), state.legacy_root())
    before = [draft_evidence_snapshot(root) for root in roots]
    monkeypatch.setattr(propose, "start_commit", lambda *a: pytest.fail("Git ran before refusal"))
    assert entry(["propose", str(path), change, "--idea", "Add a gate", "--json"]) == 1
    document = json.loads(capfd.readouterr().out)
    assert document["outcome"] == "rejected" and document["commit"] == ""
    assert ("nightshift migrate" if unsafe == "legacy" else "unsafe draft path") in document["problems"][0]
    assert [draft_evidence_snapshot(root) for root in roots] == before


@pytest.mark.scenario("specloop-interface/command-json/refused-admission-as-json")
def test_refused_admission_as_json(template, monkeypatch, capfd):
    path, repo = template
    review(monkeypatch, [CONTRADICTION])
    assert main(admission(path, "--json")) == 1
    document = json.loads(capfd.readouterr().out)
    assert document["version"] == 1
    assert document["outcome"] == "refused"
    assert document["head"] == git(repo, "rev-parse", BRANCH)
    assert document["risks"] == [RISK]
    for value in CONTRADICTION.values():
        assert value in "\n".join(document["reasons"])
    assert list(Run.for_change(CHANGE).dir.iterdir()) == [Run.for_change(CHANGE).dir / "lock"]


@pytest.mark.scenario("specloop-interface/manifest-templates/template-admission")
def test_template_admission(template, monkeypatch, capfd):
    path, repo = template
    original = path.read_text()
    review(monkeypatch)
    assert main(admission(path, "--json")) == 0
    head = git(repo, "rev-parse", BRANCH)
    assert json.loads(capfd.readouterr().out) == {
        "version": 1, "outcome": "admitted", "head": head, "reasons": [], "risks": [RISK],
    }
    run = Run.for_change(CHANGE)
    assert run.state.change == CHANGE
    assert run.state.status == "running"
    stored = tomllib.loads(run.manifest_path.read_text())
    expected = tomllib.loads(original)
    expected["run"].update(change=CHANGE, branch=BRANCH)
    assert stored == expected
    assert cli._manifest(run).change == CHANGE
    assert cli._manifest(run).branch == BRANCH
    assert git(run.dir / "repo.git", "rev-parse", f"refs/heads/{BRANCH}") == head
    assert path.read_text() == original
    assert not Run.for_change("x").dir.exists()
    # Refusing a duplicate must preserve the already admitted run.
    before = run.manifest_path.read_text()
    assert main(admission(path, "--json")) == 1
    assert "already exists" in json.loads(capfd.readouterr().out)["reasons"][0]
    assert run.manifest_path.read_text() == before
    assert (run.dir / "state.json").exists()


@pytest.mark.parametrize("tip", ["unchanged", "advanced", "rebased"])
@pytest.mark.parametrize("refused", [
    pytest.param(False, marks=pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")),
    pytest.param(True, marks=pytest.mark.scenario("issue-intake/approval-admits/refused-admission-explained")),
])
def test_approval_from_checked_out_source(template, tmp_path, monkeypatch, capfd, tip, refused):
    """Real Git and admission preserve a human's checkout while using the PR tip."""
    from nightshift import queue, specloop_intake
    from nightshift.config import Repo
    from test_specloop_approval import ApprovalGh

    path, source = template
    base = git(source, "rev-parse", BRANCH)

    def commit(repo, parent, message):
        return git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                   "commit-tree", f"{base}^{{tree}}", "-p", parent, "-m", message)

    drafted = commit(source, base, "original draft")
    git(source, "update-ref", f"refs/heads/{BRANCH}", drafted)
    git(source, "switch", BRANCH)
    remote = tmp_path / "remote.git"
    git(source, "clone", "-q", "--bare", str(source), str(remote))
    reviewed = drafted if tip == "unchanged" else commit(
        remote, drafted if tip == "advanced" else base, "reviewed spec",
    )
    git(remote, "update-ref", f"refs/heads/{BRANCH}", reviewed)
    git(source, "remote", "add", "origin", str(remote))

    # Preserve staged, unstaged and untracked work, even across a remote rebase.
    task_file = source / "openspec" / "changes" / CHANGE / "tasks.md"
    task_file.write_text(TASKS + "\nStaged human notes.\n")
    git(source, "add", ".")
    task_file.write_text(task_file.read_text() + "Unstaged human notes.\n")
    (source / "notes.txt").write_text("Untracked human notes.\n")
    before = [git(source, *args) for args in (
        ("status", "--porcelain"), ("diff",), ("diff", "--cached"),
    )]
    checked = []

    def feasibility(repo, head, manifest, output):
        checked.append(head)
        assert head == reviewed
        return {"contradictions": [CONTRADICTION] if refused else [], "risks": []}

    monkeypatch.setattr(cli.feasibility, "check", feasibility)
    gh = ApprovalGh()
    gh.branch, gh.head = BRANCH, reviewed
    commands = []

    def run_cli(args, **kwargs):
        commands.append(args)
        assert args[0] == "specloop"
        code = cli.main(args[1:])
        output = capfd.readouterr()
        return subprocess.CompletedProcess(args, code, output.out, output.err)

    repo = Repo("owner/sample", engine="specloop", manifest=path)
    for _ in range(2):
        specloop_intake.admit_pending(repo, queue.Labels(), tmp_path / "intake",
                                     runner=gh, specloop_runner=run_cli)
    assert checked == [reviewed]
    assert sum(args[1] == "admit" for args in commands) == 1
    assert len(gh.comments) == 1
    run = Run.for_change(CHANGE)
    if refused:
        assert list(run.dir.iterdir()) == [run.dir / "lock"]
        assert all(detail in gh.comments[0]["body"] for detail in CONTRADICTION.values())
        assert gh.labels == {"spec:review", "needs-human"}
    else:
        assert run.state.pr == gh.pr_url
        assert git(run.dir / "repo.git", "rev-parse", BRANCH) == reviewed
        assert "build has started" in gh.comments[0]["body"]
        assert gh.labels == set()
    assert git(source, "symbolic-ref", "HEAD") == f"refs/heads/{BRANCH}"
    assert git(source, "rev-parse", "HEAD") == drafted
    assert before == [git(source, *args) for args in (
        ("status", "--porcelain"), ("diff",), ("diff", "--cached"),
    )]
    assert (source / "notes.txt").read_text() == "Untracked human notes.\n"


@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
@pytest.mark.parametrize("interruption", ["manifest", "feasibility", "state"])
def test_approval_recovers_interrupted_admission(template, tmp_path, monkeypatch, capfd, interruption):
    from nightshift import queue, specloop_intake
    from nightshift.config import Repo
    from test_specloop_approval import ApprovalGh

    path, source = template
    remote = tmp_path / "remote.git"
    git(source, "clone", "-q", "--bare", str(source), str(remote))
    git(source, "remote", "add", "origin", str(remote))
    gh = ApprovalGh()
    gh.branch, gh.head = BRANCH, git(source, "rev-parse", BRANCH)
    repo = Repo("owner/sample", engine="specloop", manifest=path)
    run = Run.for_change(CHANGE)
    checked = []
    commands = []

    def feasibility(repo, head, manifest, output):
        checked.append(head)
        return {"contradictions": [], "risks": []}

    monkeypatch.setattr(cli.feasibility, "check", feasibility)

    def run_cli(args, **kwargs):
        commands.append(args)
        code = cli.main(args[1:])
        output = capfd.readouterr()
        return subprocess.CompletedProcess(args, code, output.out, output.err)

    def poll():
        return specloop_intake.admit_pending(
            repo, queue.Labels(), tmp_path / "intake", runner=gh, specloop_runner=run_cli,
        )

    def interrupted(*args, **kwargs):
        # Bypass normal exception cleanup, as a killed process would.
        assert run.dir.exists()
        (run.dir / "interrupted-artifact").touch()
        raise SystemExit("admission interrupted")

    with monkeypatch.context() as crash:
        if interruption == "manifest":
            write_text = Path.write_text

            def write_manifest(file, *args, **kwargs):
                if file == run.manifest_path:
                    interrupted()
                return write_text(file, *args, **kwargs)

            crash.setattr(Path, "write_text", write_manifest)
        elif interruption == "feasibility":
            crash.setattr(cli.feasibility, "check", interrupted)
        else:
            crash.setattr(Run, "save", interrupted)
        with pytest.raises(SystemExit, match="admission interrupted"):
            poll()
    capfd.readouterr()
    assert run.dir.exists()
    assert not (run.dir / "state.json").exists()
    assert main(["status", "--json"]) == 0
    assert json.loads(capfd.readouterr().out)["runs"] == []
    assert not gh.comments
    assert gh.labels == {"spec:approved", "spec:review"}

    poll()
    poll()
    assert sum(args[1] == "admit" for args in commands) == 2
    assert checked == [gh.head] * (2 if interruption == "state" else 1)
    assert not (run.dir / "interrupted-artifact").exists()
    assert Run.for_change(CHANGE).state.pr == gh.pr_url
    assert main(["status", "--json"]) == 0
    entry, = json.loads(capfd.readouterr().out)["runs"]
    assert (entry["change"], entry["status"], entry["head"], entry["pr"]) == (
        CHANGE, "running", gh.head, gh.pr_url,
    )
    assert len(gh.comments) == 1
    assert "build has started" in gh.comments[0]["body"]
    assert gh.labels == set()


@pytest.mark.parametrize("head", ["not-a-commit", "0" * 40])
def test_admit_unavailable_head_refused(template, capfd, head):
    path, source = template
    before = git(source, "rev-parse", BRANCH)
    assert main(admission(path, "--head", head, "--skip-feasibility", "--json")) == 1
    result = json.loads(capfd.readouterr().out)
    assert result["outcome"] == "refused" and result["reasons"]
    assert list(Run.for_change(CHANGE).dir.iterdir()) == [Run.for_change(CHANGE).dir / "lock"]
    assert git(source, "rev-parse", BRANCH) == before


@pytest.mark.scenario("specloop-interface/command-json/questions-as-json")
@pytest.mark.scenario("specloop-interface/proposal-source-ref-json/unsuccessful-proposal-retains-its-outcome")
def test_questions_as_json(template, monkeypatch, capfd):
    path, repo = template
    questions = ['Should "empty" mean zero?\nOr an error?']

    def agent(call):
        assert call.access == "write"
        assert "openspec/changes/new-change/" in call.prompt
        assert git(call.cwd, "branch", "--show-current") == "draft/new-change"
        print("draft progress")
        return agents.Result("ok", {"status": "needs_input", "summary": "", "questions": questions})

    monkeypatch.setattr(agents, "run", agent)
    assert main(["propose", str(path), "--change", "new-change", "--branch", "draft/new-change",
                     "--idea", "Implement empty input", "--json"]) == 2
    assert json.loads(capfd.readouterr().out) == {
        "version": 1, "outcome": "needs_input", "branch": "draft/new-change", "commit": "",
        "questions": questions, "contradictions": [], "risks": [], "problems": [],
    }
    assert not git(repo, "for-each-ref", "refs/specloop/proposals/", "refs/heads/draft/new-change")


@pytest.mark.scenario("specloop-interface/proposal-source-ref-json/ordinary-branch-retains-its-spelling")
@pytest.mark.parametrize("arguments,change,branch,json_mode", [
    ([], "x", "spec/x", True),
    (["--change", "new-change"], "new-change", "spec/x", True),
    (["--branch", "draft/custom"], "x", "draft/custom", True),
    (["legacy"], "legacy", "spec/legacy", False),
    (["--change", "new-change", "--branch", "draft/custom"], "new-change", "draft/custom", True),
])
@pytest.mark.scenario("state-migration/lazy-isolated-roots/defaults-and-overrides-are-lazy")
def test_propose_drafts_to_effective_branch(template, monkeypatch, capfd, arguments, change, branch, json_mode):
    path, repo = template

    def agent(call):
        assert call.home == state.homes_dir() / call.profile.name
        if call.access == "read":
            assert f"openspec/changes/{change}/" in call.prompt
            if call.prompt.startswith("# Spec critic of an OpenSpec change"):
                return agents.Result("ok", {"contradictions": [], "risks": []})
            return agents.Result("ok", {"contradictions": [], "risks": [RISK]})
        assert call.cwd == state.proposals_dir() / change / "work"
        assert call.cwd.parent.parent == state.state_root() / "drafts"
        task_file = call.cwd / "openspec" / "changes" / change / "tasks.md"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text(TASKS)
        return agents.Result("ok", {"status": "drafted", "summary": "", "questions": []})

    monkeypatch.setattr(agents, "run", agent)
    assert main(["propose", str(path), *arguments, "--idea", "Add a feature",
                     *(["--json"] if json_mode else [])]) == 0
    output = capfd.readouterr().out
    head = git(repo, "rev-parse", branch)
    assert git(repo, "show", f"{branch}:openspec/changes/{change}/tasks.md") == TASKS.strip()
    if json_mode:
        assert json.loads(output) == {
            "version": 1, "outcome": "drafted", "branch": branch, "commit": head,
            "questions": [], "contradictions": [], "risks": [RISK], "problems": [],
        }
    else:
        assert f"draft ready on spec/legacy in {repo} ({head[:12]})" in output
        assert f"risk: {RISK}" in output


@pytest.mark.parametrize("checkout_kind,branch", [
    pytest.param("main", BRANCH, marks=pytest.mark.scenario(
        "specloop-interface/proposal-source-ref-json/checked-out-branch-reports-private-ref")),
    pytest.param("linked", BRANCH, marks=pytest.mark.scenario(
        "specloop-interface/proposal-source-ref-json/checked-out-branch-reports-private-ref")),
    pytest.param("ordinary", BRANCH, marks=pytest.mark.scenario(
        "specloop-interface/proposal-source-ref-json/ordinary-branch-retains-its-spelling")),
    pytest.param("ordinary", "draft/custom", marks=pytest.mark.scenario(
        "specloop-interface/proposal-source-ref-json/ordinary-branch-retains-its-spelling")),
])
def test_propose_json_reports_real_ref(template, tmp_path, monkeypatch, capfd, checkout_kind, branch):
    path, repo = template
    old = git(repo, "rev-parse", "HEAD")
    checkout = repo
    fallback = checkout_kind != "ordinary"
    if checkout_kind == "linked":
        checkout = tmp_path / "review"
        git(repo, "worktree", "add", str(checkout), branch)
    elif checkout_kind == "main":
        git(repo, "checkout", branch)

    task_file = f"openspec/changes/{CHANGE}/tasks.md"
    (checkout / task_file).write_text(TASKS + "\nStaged notes\n")
    git(checkout, "add", task_file)
    (checkout / task_file).write_text(TASKS + "\nUnstaged notes\n")
    (checkout / "notes.txt").write_text("Untracked review notes\n")

    def snapshot(work):
        index = Path(git(work, "rev-parse", "--path-format=absolute", "--git-path", "index"))
        return (
            git(work, "symbolic-ref", "HEAD"), git(work, "rev-parse", "HEAD"),
            git(work, "diff"), git(work, "diff", "--cached"), index.read_bytes(),
            (work / task_file).read_bytes(),
            {name: (work / name).read_bytes() for name in
             git(work, "ls-files", "--others", "--exclude-standard").splitlines()},
        )

    worktrees = [repo, checkout] if checkout != repo else [repo]
    before = [snapshot(work) for work in worktrees]

    def agent(call):
        print("proposal progress")
        if call.access == "read":
            return agents.Result("ok", {"contradictions": [], "risks": []})
        assert (call.cwd / task_file).read_text() == TASKS
        (call.cwd / task_file).write_text(TASKS + "\nHandle empty input.\n")
        return agents.Result("ok", {"status": "drafted", "summary": "New spec", "questions": []})

    monkeypatch.setattr(agents, "run", agent)
    assert main(["propose", str(path), "--change", CHANGE, "--branch", branch,
                     "--from-branch", "main", "--idea", "Handle empty input", "--json"]) == 0
    output = capfd.readouterr()
    result = json.loads(output.out)  # Reject extra stdout documents or progress.
    ref = f"refs/specloop/proposals/{CHANGE}" if fallback else branch
    commit = git(repo, "rev-parse", ref)
    assert result == {
        "version": 1, "outcome": "drafted", "branch": ref, "commit": commit,
        "questions": [], "contradictions": [], "risks": [], "problems": [],
    }
    assert commit != old
    assert "proposal progress" in output.err
    assert [snapshot(work) for work in worktrees] == before
    if fallback:
        assert git(repo, "rev-parse", branch) == old

    # A consumer must be able to fetch the reported source ref verbatim.
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    git(consumer, "init", "-q")
    git(consumer, "fetch", str(repo), result["branch"])
    assert git(consumer, "rev-parse", "FETCH_HEAD") == result["commit"]


@pytest.mark.scenario("pr-revision/spec-stage-revision/spec-revised-from-review")
@pytest.mark.parametrize("private_ref", [False, True])
def test_propose_from_branch_continues_existing_spec(template, monkeypatch, capfd, private_ref):
    path, repo = template
    base = git(repo, "rev-parse", "main")
    design = f"openspec/changes/{CHANGE}/design.md"
    old = seal.commit_files(repo, base, {design: "Keep the existing design.\n"}, "reviewed spec")
    git(repo, "update-ref", f"refs/heads/{BRANCH}", old)
    start = f"refs/nightshift/revise/{CHANGE}" if private_ref else BRANCH
    if private_ref:
        git(repo, "update-ref", start, old)

    def agent(call):
        if call.access == "read":
            return agents.Result("ok", {"contradictions": [], "risks": []})
        assert git(call.cwd, "rev-parse", "HEAD") == old
        assert (call.cwd / design).read_text() == "Keep the existing design.\n"
        assert "Handle empty input" in call.prompt
        (call.cwd / design).write_text("Keep the existing design.\nHandle empty input.\n")
        return agents.Result("ok", {"status": "drafted", "summary": "Updated empty input", "questions": []})

    monkeypatch.setattr(agents, "run", agent)
    assert main(["propose", str(path), "--change", CHANGE, "--branch", BRANCH,
                     "--from-branch", start, "--idea", "Handle empty input", "--json"]) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["outcome"] == "drafted" and result["branch"] == BRANCH
    assert git(repo, "rev-parse", BRANCH) == result["commit"]
    assert git(repo, "rev-parse", f"{BRANCH}^") == old
    assert git(repo, "rev-parse", "main") == base
    assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", BRANCH) == design


@pytest.mark.scenario("pr-revision/spec-stage-revision/spec-revised-from-review")
@pytest.mark.scenario("specloop-interface/proposal-source-ref-json/checked-out-branch-reports-private-ref")
@pytest.mark.parametrize("linked", [False, True])
def test_propose_from_checked_out_spec_preserves_review_checkout(template, tmp_path, monkeypatch, capfd, linked):
    path, repo = template
    old = git(repo, "rev-parse", BRANCH)
    checkout = tmp_path / "review" if linked else repo
    if linked:
        git(repo, "worktree", "add", str(checkout), BRANCH)
    else:
        git(repo, "checkout", BRANCH)
    # Keep staged, unstaged and untracked reviewer work intact, even when it
    # overlaps the spec the drafter will revise.
    task_file = f"openspec/changes/{CHANGE}/tasks.md"
    (checkout / task_file).write_text(TASKS + "\nStaged notes\n")
    git(checkout, "add", task_file)
    (checkout / task_file).write_text(TASKS + "\nUnstaged notes\n")
    (checkout / "notes.txt").write_text("Untracked review notes\n")
    before = [git(checkout, *args) for args in (
        ("status", "--porcelain"), ("diff",), ("diff", "--cached"), ("rev-parse", "HEAD"),
    )]
    remote = tmp_path / "remote.git"
    git(repo, "clone", "--bare", str(repo), str(remote))
    start = f"refs/nightshift/revise/{CHANGE}"
    git(repo, "update-ref", start, old)

    def agent(call):
        if call.access == "read":
            return agents.Result("ok", {"contradictions": [], "risks": []})
        assert git(call.cwd, "rev-parse", "HEAD") == old
        assert (call.cwd / task_file).read_text() == TASKS
        (call.cwd / task_file).write_text(TASKS + "\nHandle empty input.\n")
        return agents.Result("ok", {"status": "drafted", "summary": "Revised spec", "questions": []})

    monkeypatch.setattr(agents, "run", agent)
    assert main(["propose", str(path), "--change", CHANGE, "--branch", BRANCH,
                     "--from-branch", start, "--idea", "Handle empty input", "--json"]) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["outcome"] == "drafted" and result["branch"] == f"refs/specloop/proposals/{CHANGE}"
    commit = result["commit"]
    assert git(repo, "rev-parse", f"{commit}^") == old
    assert git(repo, "rev-parse", f"refs/specloop/proposals/{CHANGE}") == commit
    assert git(repo, "rev-parse", BRANCH) == old
    assert [git(checkout, *args) for args in (
        ("status", "--porcelain"), ("diff",), ("diff", "--cached"), ("rev-parse", "HEAD"),
    )] == before
    assert (checkout / "notes.txt").read_text() == "Untracked review notes\n"
    # The same commit-addressed, non-forced push used by intake must work.
    git(repo, "push", str(remote), f"{commit}:refs/heads/{BRANCH}")
    assert git(remote, "rev-parse", BRANCH) == commit


@pytest.mark.scenario("specloop-interface/proposal-source-ref-json/unsuccessful-proposal-retains-its-outcome")
@pytest.mark.parametrize("contradictions,problems", [([CONTRADICTION], []), ([], ["invalid scenario"])])
def test_rejected_draft_reports_details(template, monkeypatch, capfd, contradictions, problems):
    from specloop import propose

    path, repo = template
    monkeypatch.setattr(propose, "draft", lambda *a, **kw: propose.Draft(
        "x", repo, problems=problems, contradictions=contradictions, risks=[RISK], commit="candidate"))
    assert main(["propose", str(path), "--idea", "feature", "--json"]) == 1
    document = json.loads(capfd.readouterr().out)
    assert document["outcome"] == "rejected"
    assert document["version"] == 1 and document["branch"] == "spec/x"
    assert document["contradictions"] == contradictions
    assert document["problems"] == problems
    assert document["risks"] == [RISK]
    assert document["commit"] == "candidate"
    assert "spec/x" not in git(repo, "branch", "--list")
    assert not git(repo, "for-each-ref", "refs/specloop/proposals/")


@pytest.mark.scenario("specloop-interface/proposal-source-ref-json/unsuccessful-proposal-retains-its-outcome")
def test_propose_publication_failure_retains_rejected_outcome(template, tmp_path, monkeypatch, capfd):
    from specloop import propose

    path, repo = template
    git(repo, "checkout", BRANCH)
    old = git(repo, "rev-parse", "HEAD")
    missing = tmp_path / "missing-draft"
    monkeypatch.setattr(propose, "draft", lambda *a, **kw: propose.Draft(
        CHANGE, missing, branch=BRANCH, risks=[RISK], commit="candidate"))
    # Real publication selects the private ref, then fails to fetch the draft.
    assert main(["propose", str(path), "--change", CHANGE, "--branch", BRANCH,
                     "--idea", "feature", "--json"]) == 1
    result = json.loads(capfd.readouterr().out)
    assert result == {
        "version": 1, "outcome": "rejected", "branch": BRANCH, "commit": "candidate",
        "questions": [], "contradictions": [], "risks": [RISK], "problems": result["problems"],
    }
    assert len(result["problems"]) == 1 and str(missing) in result["problems"][0]
    assert not git(repo, "for-each-ref", "refs/specloop/proposals/")
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", BRANCH) == old


@pytest.mark.parametrize("json_mode", [True, False])
@pytest.mark.parametrize("hook_exit", [0, 1])
def test_publish_json(template, monkeypatch, capfd, tmp_path, json_mode, hook_exit):
    path, _ = template
    assert main(admission(path, "--skip-feasibility", "--json")) == 0
    capfd.readouterr()
    run = Run.for_change(CHANGE)
    run.pause("ready_to_publish")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    # The publication gate now fetches main before reaching the push hook.
    git(run.dir / "repo.git", "push", "-q", str(remote), "main:main")
    hooks = run.dir / "repo.git" / "hooks"
    git(run.dir / "repo.git", "config", "core.hooksPath", str(hooks))
    hook = hooks / "pre-push"
    hook.write_text(f"#!/bin/sh\necho 'pre-push progress'\nexit {hook_exit}\n")
    hook.chmod(0o755)
    monkeypatch.setattr(cli, "_remote", lambda ctx: str(remote))
    calls = []

    def gh(*args):
        calls.append(args)
        if args[:2] == ("api", "repos/example/repo/pulls"):
            return "[]"
        return "https://github.com/example/repo/pull/7"

    monkeypatch.setattr(cli, "_gh", gh)
    command = ["publish", CHANGE, *(["--json"] if json_mode else [])]
    if hook_exit and not json_mode:
        with pytest.raises(subprocess.CalledProcessError):
            main(command)
    else:
        assert main(command) == hook_exit
    output = capfd.readouterr()
    assert "pre-push progress" in (output.err if json_mode else output.out)
    if json_mode:
        document = json.loads(output.out)
        if hook_exit:
            assert document["version"] == 1
            assert document["error"]
            assert "outcome" not in document
        else:
            assert document == {
                "version": 1, "outcome": "published", "pr": "https://github.com/example/repo/pull/7",
            }
    # Both Python's stream and inherited child stdout must be restored.
    print("after publish")
    subprocess.run(["sh", "-c", "echo 'child after publish'"], check=True)
    restored = capfd.readouterr()
    assert restored.out == "after publish\nchild after publish\n"
    assert restored.err == ""
    if hook_exit:
        assert calls == []
        saved = Run.for_change(CHANGE).state
        assert saved.pause.reason == "ready_to_publish" and not saved.pr
        return
    assert [call[:2] for call in calls] == [("api", "repos/example/repo/pulls"), ("pr", "create")]
    assert git(remote, "rev-parse", BRANCH) == git(run.dir / "repo.git", "rev-parse", BRANCH)
    saved = Run.for_change(CHANGE).state
    assert saved.pr.endswith("/pull/7") and saved.pause.reason == "human_checks"


@pytest.mark.parametrize("command,detail", [
    (["admit", "missing.toml"], "missing.toml"),
    (["propose", "missing.toml", "--idea", "feature"], "missing.toml"),
    (["publish", "unknown"], "not ready to publish"),
])
def test_command_errors_are_json(template, capfd, command, detail):
    assert main([*command, "--json"]) == 1
    document = json.loads(capfd.readouterr().out)
    assert document["version"] == 1
    assert document["outcome"] in ("refused", "rejected")
    assert detail in str(document.get("problems", document.get("reasons")))


@pytest.mark.scenario("specloop-interface/pr-adoption/publish-reuses-the-pr")
@pytest.mark.parametrize("json_mode", [True, False])
def test_publish_reuses_admitted_pr(template, monkeypatch, capfd, tmp_path, json_mode):
    path, repo = template
    url = "https://github.com/example/repo/pull/42"
    output_flags = ["--json"] if json_mode else []
    assert main(admission(path, "--skip-feasibility", "--pr", url, *output_flags)) == 0
    capfd.readouterr()
    run = Run.for_change(CHANGE)
    assert run.state.pr == url

    # Model the remote branch already backing the adopted PR.
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    bare = run.dir / "repo.git"
    old_head = git(bare, "rev-parse", BRANCH)
    head = git(bare, "-c", "user.name=Test", "-c", "user.email=test@example.com",
               "commit-tree", f"{old_head}^{{tree}}", "-p", old_head, "-m", "completed work")
    git(bare, "update-ref", f"refs/heads/{BRANCH}", head)
    run.pause("ready_to_publish")

    def gh(*args):
        pytest.fail(f"Publishing an adopted PR must not call gh: {args}")

    monkeypatch.setattr(cli, "_gh", gh)
    assert main(["publish", CHANGE, *output_flags]) == 0
    output = capfd.readouterr().out
    if json_mode:
        assert json.loads(output) == {"version": 1, "outcome": "published", "pr": url}
    else:
        assert url in output
    assert git(remote, "rev-parse", BRANCH) == head
    saved = Run.for_change(CHANGE).state
    assert saved.pr == url
    assert saved.pause.reason == "human_checks"
    assert url in saved.pause.detail


@pytest.mark.parametrize("pr", ["", "https://github.com/example/repo/pull/42"])
def test_publish_failure_is_json_and_remains_ready(template, monkeypatch, capfd, pr):
    path, _ = template
    assert main(admission(path, "--skip-feasibility", "--json", *(["--pr", pr] if pr else []))) == 0
    capfd.readouterr()
    Run.for_change(CHANGE).pause("ready_to_publish")
    monkeypatch.setattr(cli, "_remote", lambda ctx: "unused")
    # This transport fake models an unchanged base before the failing push.
    monkeypatch.setattr(cli.loop, "_fetch_publish_base", lambda ctx, remote, **kw: ctx.head())

    def fail(*args):
        raise RuntimeError("push failed")

    monkeypatch.setattr(cli, "_push", fail)
    assert main(["publish", CHANGE, "--json"]) == 1
    assert json.loads(capfd.readouterr().out) == {
        "version": 1, "error": "push failed",
    }
    saved = Run.for_change(CHANGE).state
    assert saved.pause.reason == "ready_to_publish" and saved.pr == pr


@pytest.fixture
def revision_run(template, capfd, monkeypatch):
    path, source = template
    assert main(admission(path, "--skip-feasibility", "--pr", "https://github.com/example/repo/pull/42")) == 0
    capfd.readouterr()
    run = Run.for_change(CHANGE)
    run.state.gates_passed = [1]
    run.state.task(tasks.parse(TASKS)[0].key).note = "Keep this existing task state"
    run.pause("human_checks", detail="Review the PR")

    def unexpected(*args):
        pytest.fail("Revision must not start execution or load credentials")

    monkeypatch.setattr(cli, "_ctx", unexpected)
    monkeypatch.setattr(cli.loop, "run", unexpected)
    return run, source


@pytest.mark.scenario("specloop-interface/revision-round/feedback-becomes-tasks")
@pytest.mark.parametrize("json_mode", [False, True])
def test_revision_feedback_becomes_tasks(revision_run, tmp_path, capfd, json_mode):
    run, source = revision_run
    repo = run.dir / "repo.git"
    original_head = git(repo, "rev-parse", BRANCH)
    original_state = json.loads((run.dir / "state.json").read_text())
    feedback = tmp_path / "feedback.md"
    blocks = [
        '**reviewer:** `src/widget.py:42`\n\nKeep the "café" label.\n- [ ] Add a regression test.',
        "**reviewer:** `src/widget.py:98`\n\n## 99. Not a task section\nHandle empty input.",
    ]
    feedback.write_text("\n---\n" + "\n---\n".join(blocks) + "\n---\n")
    flags = ["--json"] if json_mode else []
    for number in (1, 2):
        parent = git(repo, "rev-parse", BRANCH)
        previous = git(repo, "show", f"{BRANCH}:openspec/changes/{CHANGE}/tasks.md")
        assert main(["revise", CHANGE, "--feedback", str(feedback), *flags]) == 0
        output = capfd.readouterr().out
        head = git(repo, "rev-parse", BRANCH)
        if json_mode:
            assert json.loads(output) == {"version": 1, "outcome": "revised", "commit": head}
        else:
            assert head[:12] in output and "running" in output
        assert git(repo, "rev-parse", f"{head}^") == parent
        assert git(repo, "show", "-s", "--format=%an <%ae> / %cn <%ce>", head) == (
            "specloop <specloop@localhost> / specloop <specloop@localhost>"
        )
        assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", head) == (
            f"openspec/changes/{CHANGE}/tasks.md"
        )
        text = git(repo, "show", f"{head}:openspec/changes/{CHANGE}/tasks.md")
        assert text.startswith(previous + "\n")
        assert f"## {number + 1}. Review round {number}\n" in text
        parsed = tasks.parse(text)
        assert len(parsed) == 1 + 2 * number
        added = parsed[-2:]
        assert [t.label for t in added] == [f"{number + 1}.1", f"{number + 1}.2"]
        assert all(not t.done and t.section_title == f"Review round {number}" for t in added)
        assert [json.loads(t.text.split(": ", 1)[1]) for t in added] == blocks
        assert len({t.key for t in parsed}) == len(parsed)
        saved = json.loads((run.dir / "state.json").read_text())
        assert saved == {**original_state, "status": "running", "pause": None}
        event = json.loads((run.dir / "events.jsonl").read_text().splitlines()[-1])
        assert event["event"] == "revised" and event["commit"] == head and event["tasks"] == 2
    assert git(source, "rev-parse", BRANCH) == original_head
    assert (source / "openspec" / "changes" / CHANGE / "tasks.md").read_text() == TASKS


@pytest.mark.scenario("pr-revision/build-stage-revision/review-comments-become-tasks")
@pytest.mark.scenario("pr-revision/consumption-based-eligibility/relabel-does-not-requeue-consumed-feedback")
@pytest.mark.real_trust
def test_pr_comments_preserved_through_real_revision_cli(revision_run, tmp_path, capfd, monkeypatch):
    from nightshift import daemon
    from nightshift.config import Config, Repo
    from test_specloop_spec_revision import RevisionGh, TIP_DATE, seed_consumed

    run, _ = revision_run
    gh = RevisionGh()
    gh.label_event("spec:review", "unlabeled")
    gh.branch = BRANCH
    gh.head = git(run.dir / "repo.git", "rev-parse", BRANCH)
    run.state.pr = gh.pr_url
    run.save()
    gh.feedback = []
    bodies = [
        'Preserve this YAML example:\n\n```yaml\nlabel: "café"\n---\nlabel: next\n```',
        "Handle empty input.\n\n- [ ] Add coverage.\n## 99. This is still a comment.",
    ]
    gh.inline = [
        {"id": 1, "author_association": "COLLABORATOR", "user": {"login": "reviewer"}, "path": "src/gate.py", "line": 12,
         "body": bodies[0], "created_at": "2026-10-20T13:00:00Z"},
        {"id": 2, "author_association": "COLLABORATOR", "user": {"login": "reviewer"}, "path": "tests/test_gate.py", "line": 42,
         "body": bodies[1], "created_at": "2026-10-20T14:00:00Z"},
        {"id": 3, "author_association": "COLLABORATOR", "user": {"login": "reviewer"}, "path": "old.py", "line": 1,
         "body": "Already addressed.", "created_at": TIP_DATE},
    ]
    calls = []

    def run_cli(args, **kwargs):
        calls.append(args)
        assert "agent:revise" in gh.labels
        code = cli.main(args[1:])
        output = capfd.readouterr()
        return subprocess.CompletedProcess(args, code, output.out, output.err)

    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path / "intake")
    seed_consumed(tmp_path / "intake", ("inline:3",))
    repo = Repo("owner/sample", engine="specloop", manifest=run.manifest_path)
    health = daemon.RepoHealth()
    for _ in range(2):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=run_cli)
    assert not health.failures
    assert [args[1] for args in calls] == ["status", "revise"]
    text = git(run.dir / "repo.git", "show", f"{BRANCH}:openspec/changes/{CHANGE}/tasks.md")
    parsed = tasks.parse(text)
    assert len(parsed) == 3
    assert all(not task.done and task.section_title == "Review round 1" for task in parsed[1:])
    assert [json.loads(task.text.split(": ", 1)[1]) for task in parsed[1:]] == [
        f"**reviewer on `src/gate.py:12`:**\n\n{bodies[0]}",
        f"**reviewer on `tests/test_gate.py:42`:**\n\n{bodies[1]}",
    ]
    head = git(run.dir / "repo.git", "rev-parse", BRANCH)
    assert git(run.dir / "repo.git", "rev-parse", f"{head}^") == gh.head
    assert not gh.labels and len(gh.comments) == 1 and head in gh.comments[0]["body"]
    assert Run.for_change(CHANGE).state.status == "running"
    gh.label_event("agent:revise", "labeled")
    daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=run_cli)
    assert not health.failures and not gh.labels
    assert [args[1] for args in calls] == ["status", "revise"]
    assert git(run.dir / "repo.git", "rev-parse", BRANCH) == head != gh.head
    assert "no unconsumed review feedback" in gh.comments[-1]["body"]


@pytest.mark.parametrize("feedback_text", [
    "[]", '["", " \\n\\t"]', '{}', '"a single string"', '[null]', '[42]', '["Fix it", {}]', '[',
])
def test_invalid_or_empty_json_feedback_preserves_run(revision_run, tmp_path, capfd, feedback_text):
    run, _ = revision_run
    repo = run.dir / "repo.git"
    before = git(repo, "rev-parse", BRANCH)
    saved = (run.dir / "state.json").read_bytes()
    events = (run.dir / "events.jsonl").read_bytes()
    feedback = tmp_path / "feedback.json"
    feedback.write_text(feedback_text)
    assert main(["revise", CHANGE, "--feedback", str(feedback),
                     "--feedback-format", "json", "--json"]) == 1
    result = json.loads(capfd.readouterr().out)
    assert result["outcome"] == "refused" and result["reasons"]
    assert git(repo, "rev-parse", BRANCH) == before
    assert (run.dir / "state.json").read_bytes() == saved
    assert (run.dir / "events.jsonl").read_bytes() == events


@pytest.mark.scenario("specloop-interface/revision-round/empty-feedback-refused")
@pytest.mark.parametrize("feedback_text", ["", " \n\t\n", "---\n\n---\n", "\r\n---\r\n \t\r\n---\r\n"])
@pytest.mark.parametrize("json_mode", [False, True])
def test_revision_empty_feedback_refused(revision_run, tmp_path, capfd, feedback_text, json_mode):
    run, _ = revision_run
    repo = run.dir / "repo.git"
    before = git(repo, "rev-parse", BRANCH)
    objects = git(repo, "count-objects", "-v")
    saved = (run.dir / "state.json").read_bytes()
    events = (run.dir / "events.jsonl").read_bytes()
    feedback = tmp_path / "feedback.md"
    feedback.write_text(feedback_text)
    flags = ["--json"] if json_mode else []
    assert main(["revise", CHANGE, "--feedback", str(feedback), *flags]) == 1
    output = capfd.readouterr()
    if json_mode:
        document = json.loads(output.out)
        assert document["version"] == 1 and document["outcome"] == "refused"
        assert "feedback is empty" in document["reasons"][0]
    else:
        assert not output.out and "feedback is empty" in output.err
    assert git(repo, "rev-parse", BRANCH) == before
    assert git(repo, "count-objects", "-v") == objects
    assert (run.dir / "state.json").read_bytes() == saved
    assert (run.dir / "events.jsonl").read_bytes() == events


def test_revision_numbering_and_duplicate_comments(revision_run, tmp_path, capfd):
    run, _ = revision_run
    repo, ref = run.dir / "repo.git", f"refs/heads/{BRANCH}"
    path = f"openspec/changes/{CHANGE}/tasks.md"
    text = TASKS + "\n## 7. Review round 3\n\n- [x] 7.1 Earlier feedback\n\n## 12. Empty section\n"
    head = git(repo, "rev-parse", ref)
    commit = seal.commit_files(repo, head, {path: text}, "prior round")
    git(repo, "update-ref", ref, commit, head)
    feedback = tmp_path / "feedback.md"
    feedback.write_text("Fix this --- inline separator.\n---\nFix this --- inline separator.")
    assert main(["revise", CHANGE, "--feedback", str(feedback), "--json"]) == 0
    capfd.readouterr()
    text = git(repo, "show", f"{ref}:{path}")
    assert "## 13. Review round 4\n" in text
    added = tasks.parse(text)[-2:]
    assert len({t.key for t in added}) == 2
    assert all(json.loads(t.text.split(": ", 1)[1]) == "Fix this --- inline separator." for t in added)


@pytest.mark.parametrize("failure", ["missing_file", "unknown_change", "busy", "update_ref"])
def test_revision_refusal_preserves_run(revision_run, tmp_path, capfd, monkeypatch, failure):
    run, _ = revision_run
    repo = run.dir / "repo.git"
    head = git(repo, "rev-parse", BRANCH)
    saved = (run.dir / "state.json").read_bytes()
    events = (run.dir / "events.jsonl").read_bytes()
    feedback = tmp_path / "feedback.md"
    if failure != "missing_file":
        feedback.write_text("Fix the empty state")
    if failure == "update_ref":
        original = seal.git

        def fail_update(repo, *args, **kwargs):
            if args[0] == "update-ref":
                raise RuntimeError("branch moved")
            return original(repo, *args, **kwargs)

        monkeypatch.setattr(seal, "git", fail_update)
    command = ["revise", "unknown" if failure == "unknown_change" else CHANGE,
               "--feedback", str(feedback), "--json"]
    if failure == "busy":
        with run.lock() as mine:
            assert mine
            result = main(command)
    else:
        result = main(command)
    assert result == 1
    document = json.loads(capfd.readouterr().out)
    assert document["version"] == 1 and document["outcome"] == "refused"
    assert document["reasons"] and "commit" not in document
    assert git(repo, "rev-parse", BRANCH) == head
    assert (run.dir / "state.json").read_bytes() == saved
    assert (run.dir / "events.jsonl").read_bytes() == events
    assert not Run.for_change("unknown").dir.exists()
