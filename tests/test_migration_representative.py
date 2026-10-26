"""Generated evidence only: no host run, repository, or credential is copied."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from nightshift import specloop_job as jobs
from nightshift.cli import main
from specloop import cli, migrate, seal, state, state_locks, tasks
from test_migration_preflight import held_lock, snapshot
from test_specloop_command_json import TASKS, daemon_draft_evidence, git, template
from test_specloop_propose import draft_evidence_snapshot


@pytest.fixture
def repositories(template, tmp_path, monkeypatch, capfd):
    template_path, seed = template
    source, destination = state.legacy_root(), state.state_root()
    # Children and parent resolve the same isolated roots, without ROOT patches.
    monkeypatch.setenv("NIGHTSHIFT_STATE_ROOT", str(destination))
    monkeypatch.setattr(state, "ROOT", None)
    monkeypatch.setattr(cli, "notifier", lambda secrets: lambda message: None)
    cases = []
    # Sanitized identities for Phreak, Compact Mixer, and Nightshift.
    for name in ("phreak", "compact-mixer", "nightshift"):
        repository = f"example/{name}"
        change = f"example__{name}-issue-7-add-a-gate"
        branch = f"spec/{change}"
        repo = tmp_path / name
        git(seed, "clone", "-q", str(seed), str(repo))
        task = repo / "openspec/changes" / change / "tasks.md"
        task.parent.mkdir(parents=True)
        task.write_text(TASKS)
        git(repo, "add", ".")
        git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", name)
        git(repo, "branch", branch)
        manifest = tmp_path / f"{name}.toml"
        manifest.write_text(template_path.read_text().replace(str(seed), str(repo))
                            .replace('github = "example/repo"', f'github = "{repository}"'))
        # Admission produces actual manifest/state/Git evidence, then seed the
        # legacy layout as a same-filesystem rename of this generated run.
        assert main(["admit", str(manifest), "--change", change, "--branch", branch,
                     "--skip-feasibility", "--json"]) == 0
        admitted = json.loads(capfd.readouterr().out)
        assert admitted["outcome"] == "admitted"
        old = source / "runs" / change
        old.parent.mkdir(parents=True, exist_ok=True)
        state.run_dir(change).rename(old)
        run = state.Run.for_change(change, require_existing=True)
        assert run.dir == old
        feedback = tmp_path / f"{name}-feedback.json"
        feedback.write_text(json.dumps([f"Retain {name} history", "Handle empty input"]))
        argv = ["revise", change, "--feedback", str(feedback), "--feedback-format", "json",
                "--round-id", f"{name}-review-1", "--json"]
        assert main(argv) == 0
        result = json.loads(capfd.readouterr().out)
        assert result["outcome"] == "revised" and result["replayed"] is False
        run.reload()
        candidate = seal.commit_files(old / "repo.git", result["commit"],
                                      {"out/gate.txt": name}, "generated candidate")
        ref = f"refs/specloop/candidates/{change}/1"
        git(old / "repo.git", "update-ref", ref, candidate)
        key = tasks.parse(TASKS)[0].key
        attempt = run.attempt_dir(key, 1)
        git(repo, "clone", "-q", "--shared", str(old / "repo.git"), str(attempt / "work"))
        git(attempt / "work", "checkout", "-q", "--detach", candidate)
        for filename, contents in {
            "seal.json": {"sha": candidate, "changed": ["out/gate.txt"], "violations": []},
            "review.json": {"verdict": "pass", "repository": repository},
            "verify.json": {"id": "tests", "exit_code": 0},
        }.items():
            (attempt / filename).write_text(json.dumps(contents) + "\n")
        (attempt / "implement.jsonl").write_text(json.dumps({"status": "ready", "summary": name}) + "\n")
        (attempt / "implement.log").write_text(f"generated {repository} attempt at {attempt}\n")
        (attempt / "implement.log").chmod(0o640)
        (old / "latest-attempt").symlink_to(f"{key}/a1")
        task_state = run.state.task(key)
        task_state.candidate, task_state.candidate_base = candidate, result["commit"]
        task_state.attempts = task_state.sessions = 1
        run.save()
        run.event("sealed", task=key, sha=candidate, changed=1, violations=[])
        run.pause("requested", detail=f"Retain {repository} operator hold")
        run.request_pause()
        # A local Git workspace plus proposal artifacts, with a deliberately
        # same-named daemon receipt for a different repository identity.
        draft = source / "proposals" / change
        draft.mkdir(parents=True)
        git(repo, "clone", "-q", str(repo), str(draft / "work"))
        git(draft / "work", "update-ref", f"refs/specloop/proposals/{change}", admitted["head"])
        (draft / "proposal.json").write_text(json.dumps({
            "version": 1, "outcome": "drafted", "branch": branch, "commit": admitted["head"],
        }))
        (draft / "idea.txt").write_text(f"Sanitized {name} proposal\n")
        daemon_draft_evidence(change)
        cases.append(SimpleNamespace(change=change, repository=repository, branch=branch,
                                     candidate=candidate, ref=ref, argv=argv, result=result))
    return source, destination, cases


@pytest.mark.scenario(
    "state-migration/representative-isolated-migration/other-repositories-and-history-survive"
)
def test_generated_repositories_retain_evidence_and_replay(
    repositories, tmp_path, monkeypatch, capfd,
):
    source, destination, cases = repositories
    home = source / "homes/codex-fixture"
    home.mkdir(parents=True)
    (home / "config.toml").write_text('model = "fixture"\n')
    (home / "internal").symlink_to("config.toml")
    external = tmp_path / "synthetic-auth.json"
    external.write_text("synthetic credential sentinel; never read by migration")
    (home / "auth.json").symlink_to(os.path.relpath(external, home))
    (home / "dangling").symlink_to(os.path.relpath(tmp_path / "absent-auth.json", home))
    # Assert guard inheritance without probing any live path.
    assert getattr(os, "_nightshift_test_guard_roots", ())
    probe = subprocess.run([sys.executable, "-c", """
import os
from pathlib import Path
from specloop import state
assert os._nightshift_test_guard_roots
assert state.ROOT is None
assert state.state_root() == Path(os.environ['NIGHTSHIFT_STATE_ROOT'])
assert state.legacy_root() == Path(os.environ['NIGHTSHIFT_LEGACY_STATE_ROOT'])
"""], capture_output=True, text=True, timeout=10)
    assert probe.returncode == 0, probe.stderr

    open_file = io.open

    def no_credential_reads(path, *args, **kwargs):
        if not isinstance(path, int) and Path(path).resolve() == external:
            pytest.fail("migration read linked credential contents")
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(io, "open", no_credential_reads)
    before = snapshot((source, destination))
    entries = {path.relative_to(source): draft_evidence_snapshot(path)
               for namespace in migrate.MAPPING for path in (source / namespace).iterdir()}
    daemon = draft_evidence_snapshot(destination / "proposals")
    inventory = jobs.list_jobs(destination, include_delivery=True)
    rows, errors = inventory
    assert not errors and len(rows) == len(cases)
    assert {(row.repository, row.number, row.id, row.state) for row in rows} == {
        (case.change.replace("__", "/", 1), 7, "a" * 32, "stopped") for case in cases}
    assert all(row.identity_available and row.process["start_identity"] == "retained-identity"
               for row in rows)
    locked = cases[1].change
    with held_lock(source / "runs" / locked / "lock"):
        assert main(["migrate"]) == 1
        refusal = capfd.readouterr()
        assert locked in refusal.err and "locked" in refusal.err and "retry" in refusal.err
        assert snapshot((source, destination)) == before
        assert not (source / migrate.MARKER).exists()
        assert not state_locks.journal_path().exists()

    assert main(["migrate"]) == 0
    capfd.readouterr()
    assert json.loads((source / migrate.MARKER).read_text())["completed"] is True
    assert not state_locks.journal_path().exists()
    for relative, evidence in entries.items():
        moved = destination / migrate.MAPPING[relative.parts[0]] / relative.name
        assert (source / relative).readlink() == moved
        actual = draft_evidence_snapshot(moved)
        if relative.parts[0] != "homes":
            assert actual == evidence  # Bytes, membership, modes, mtimes, inodes.
        else:
            assert actual.keys() == evidence.keys()
            for name in ("config.toml", "internal"):
                assert actual[Path(name)] == evidence[Path(name)]
            # Rewriting links changes only their inode/mtime and parent mtime.
            assert actual[Path(".")][1] == evidence[Path(".")][1]  # Mode.
            assert actual[Path(".")][3] == evidence[Path(".")][3]  # Inode.
            for name, target in (("auth.json", external), ("dangling", tmp_path / "absent-auth.json")):
                assert (moved / name).readlink() == target
                assert (home / name).resolve() == target
            assert (moved / "internal").resolve() == moved / "config.toml"
            assert not (moved / "dangling").exists()
    assert draft_evidence_snapshot(destination / "proposals") == daemon
    assert jobs.list_jobs(destination, include_delivery=True) == inventory
    assert list(state.iter_run_dirs()) == sorted(destination / "runs" / c.change for c in cases)
    for case in cases:
        run = state.Run.for_change(case.change, require_existing=True)
        assert run.dir == destination / "runs" / case.change
        assert cli._manifest(run).github == case.repository
        assert git(run.dir / "repo.git", "rev-parse", case.ref) == case.candidate
        assert git(run.dir / "latest-attempt/work", "rev-parse", "HEAD") == case.candidate
        assert git(run.dir / "repo.git", "show", f"{case.candidate}:out/gate.txt") == case.repository.split("/")[1]
        assert git(run.dir / "repo.git", "rev-parse", case.branch) == case.result["commit"]
        assert git(destination / "drafts" / case.change / "work", "rev-parse",
                   f"refs/specloop/proposals/{case.change}") == git(run.dir / "repo.git", "rev-parse",
                                                                 f"{case.result['commit']}^")
        retained = draft_evidence_snapshot(run.dir)
        assert main(case.argv) == 0
        replay = json.loads(capfd.readouterr().out)
        assert replay == {**case.result, "replayed": True, "status": "paused",
                          "pause": json.loads((run.dir / "state.json").read_text())["pause"]}
        replayed = draft_evidence_snapshot(run.dir)
        # Run.lock opens with 'w': replay refreshes its mtime, but keeps bytes,
        # mode and inode. All historical evidence must remain exactly unchanged.
        previous_lock = retained.pop(Path("lock"))
        current_lock = replayed.pop(Path("lock"))
        assert current_lock[:2] == previous_lock[:2] and current_lock[3] == previous_lock[3]
        assert replayed == retained
        events = [json.loads(line) for line in (run.dir / "events.jsonl").read_text().splitlines()]
        assert sum(e["event"] == "revised" for e in events) == 1
        record, = (run.dir / "revision-rounds").glob("*.json")
        assert json.loads(record.read_text())["commit"] == case.result["commit"]
    completed = [draft_evidence_snapshot(root) for root in (source, destination)]
    assert main(["migrate"]) == 0
    capfd.readouterr()
    assert [draft_evidence_snapshot(root) for root in (source, destination)] == completed
    assert jobs.list_jobs(destination, include_delivery=True) == inventory
