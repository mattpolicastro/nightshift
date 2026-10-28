import json
import shutil
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest

from specloop import evidence, loop, manifest, state, verify
from test_snapshot_consumption import command, stored
from test_snapshot_warming import warming
from test_specloop_seal_verify import sh


@pytest.fixture
def candidate(stored, monkeypatch):
    repo, base, m, context, events, entry = stored
    for name in ("a.py", "b.py"):
        (repo / name).write_text("x = 1\n")
    sh(repo, "git", "add", ".")
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "candidate")
    head = sh(repo, "git", "rev-parse", "HEAD")
    # Portable clone boundary; these fixtures do not establish APFS COW behavior.
    monkeypatch.setattr(verify, "_clone_api", lambda: lambda src, dst, flags: (shutil.copyfile(src, dst), 0)[1])
    return repo, base, head, m, context, entry


@pytest.mark.scenario("parallel-evidence-mutants/snapshot-isolation-across-evidence-phases/baselines-mutants-and-confirmations-stay-independent")
@pytest.mark.scenario("parallel-evidence-mutants/snapshot-isolation-across-evidence-phases/mutated-lockfile-falls-back-without-changing-classifications")
@pytest.mark.parametrize("mode", ["hit", "lockfile", "clone_failed"])
def test_evidence_snapshot_phases(candidate, tmp_path, monkeypatch, mode):
    repo, base, head, m, context, entry = candidate
    selected = [evidence.Mutant("a.py", 1, "int", "x = 1", "x = 2"),
                evidence.Mutant("b.py", 1, "int", "x = 1", "x = 3")]
    if mode == "lockfile":
        selected[0] = evidence.Mutant("lock", 1, "test", "base", "changed")
    prepare = command("prepare", 'printf "%s" "$1" > "$1/prepared"', "{snapshot:deps}")
    check = command("check", 'test "$(cat "$1/prepared")" = "$1"; '
                    'test "$(cat "$1/dependency")" = "$2"', "{snapshot:deps}", "{mutant_path}")
    other = replace(check, id="other")
    m = replace(m, prepare=(prepare,), verify=(check, other), mutation_steps=("check",),
                mutation_paths=("**",), mutation_parallel=2, mutation_min=0, coverage_step="",
                mutation_routes=(manifest.MutationRoute(("b.py",), ("other",)),))
    ctx = loop.Ctx(state.Run(tmp_path / "run"), m, {})
    ctx.repo, ctx.snapshots.root = repo, context.root
    ctx.run.state.section_base["1"] = base
    monkeypatch.setattr(loop, "_scenarios_at", lambda *a: {})
    monkeypatch.setattr(evidence, "generate", lambda *a, **kw: selected)
    original_event = ctx.run.event
    forwarded = []

    def event(kind, **data):
        if kind == "snapshot_fallback":
            assert ctx.snapshots._lock.locked()
            forwarded.append(dict(event=kind, **data))
        original_event(kind, **data)

    monkeypatch.setattr(ctx.run, "event", event)
    if mode == "clone_failed":
        def fail_clone(payload, destination):
            destination.mkdir()
            (destination / "partial").write_text("discard me")
            raise OSError("clone unavailable")
        monkeypatch.setattr(verify, "_clone_payload", fail_clone)
    original = {p.relative_to(entry): (p.stat().st_mode, p.read_bytes() if p.is_file() else None)
                for p in verify._payload_paths(entry)}
    out = ctx.run.dir / "gates/section-1-0"
    baselines, executions, paths, expected_events = [], [], [], []
    pair = Barrier(2)
    run_commands = verify._run_commands

    def observe(scheduled, values, build, run_out, path):
        baseline = run_out in (out, out / "baseline-2")
        clone = Path(values["snapshot:deps"])
        assert clone == build / "snapshots/deps" and clone not in paths
        paths.append(clone)
        assert values["sha"] == head
        fallback = mode == "clone_failed" or (mode == "lockfile" and not baseline
                                              and "mutant-1" in run_out.parts)
        if fallback:
            assert list(clone.iterdir()) == []
            expected_events.append(dict(event="snapshot_fallback", id="deps",
                key=verify.snapshot_key(Path(values["src"]), m.snapshots[0]), sha=head,
                reason="stale" if mode == "lockfile" else "clone_failed", out=str(run_out)))
        else:
            assert (clone / "dependency").read_text() == "base"
            assert not (clone / "prepared").exists()
        if not baseline:
            assert baselines == [out, out / "baseline-2"]
            mut = selected[0 if "mutant-1" in run_out.parts else 1]
            assert (Path(values["src"]) / mut.path).read_text().rstrip("\n") == mut.after
            pair.wait(timeout=10)  # Both worker slots overlap in each phase.
        (clone / "dependency").write_text(values["mutant_path"])
        if not baseline:
            pair.wait(timeout=10)  # Check after the sibling also writes its clone.
        receipts = run_commands(scheduled, values, build, run_out, path)
        assert verify.ok(receipts, tuple(s for s, _ in scheduled)), verify.summary(receipts)
        assert [r.id for r in receipts] == ["prepare", "other" if run_out == out / "baseline-2"
                                               or "mutant-2" in run_out.parts else "check"]
        assert all(set(r) == {"id", "argv", "expect", "exit", "passed", "seconds", "tail"}
                   for r in json.loads((run_out / "receipts.json").read_text()))
        executions.append(run_out)
        if baseline:
            baselines.append(run_out)
        return receipts

    monkeypatch.setattr(verify, "_run_commands", observe)
    assert loop.section_evidence(ctx, 1, head, [])
    assert [mut.result for mut in selected] == ["survived", "survived"]
    assert len(paths) == len(set(paths)) == 6
    result = json.loads((out / "evidence.json").read_text())
    assert set(result) == {"section", "covered", "uncovered", "mutants", "skipped", "baseline_failed", "score"}
    assert result["score"] == 0 and not result["baseline_failed"]
    index = json.loads((out / "mutation-runs.json").read_text())
    assert [b["steps"] for b in index["baselines"]] == [["check"], ["other"]]
    for number, result in enumerate(index["mutants"], 1):
        assert result == dict(number=number, path=selected[number - 1].path, line=1,
            op=selected[number - 1].op, route=None if number == 1 else 1,
            steps=["check" if number == 1 else "other"], baseline=number, result="survived",
            decided_by="confirmation", runs=[
                dict(phase="narrowed", receipts=f"mutant-{number}/receipts.json"),
                dict(phase="confirmation", receipts=f"mutant-{number}/confirmation/receipts.json")])
    records = [json.loads(line) for execution in executions
               for line in (execution / "snapshot-events.jsonl").read_text().splitlines()]
    assert len(records) == {"hit": 0, "lockfile": 2, "clone_failed": 6}[mode]
    assert sorted(records, key=lambda e: e["out"]) == sorted(expected_events, key=lambda e: e["out"])
    assert sorted(records, key=lambda e: e["out"]) == sorted(forwarded, key=lambda e: e["out"])
    run_events = [json.loads(line) for line in (ctx.run.dir / "events.jsonl").read_text().splitlines()]
    assert [e["out"] for e in run_events if e["event"] == "snapshot_fallback"] == [e["out"] for e in forwarded]
    for record in records:
        assert record["sha"] == head and record["id"] == "deps"
        assert record["reason"] == ("stale" if mode == "lockfile" else "clone_failed")
        assert (record["key"] == verify.snapshot_key(repo, m.snapshots[0])) == (mode != "lockfile")
    assert original == {p.relative_to(entry): (p.stat().st_mode, p.read_bytes() if p.is_file() else None)
                        for p in verify._payload_paths(entry)}
    assert all(not (execution / "build").exists() for execution in executions)
    assert all(not (execution / "src").exists() for execution in executions)


@pytest.mark.scenario("parallel-evidence-mutants/snapshot-isolation-across-evidence-phases/mutated-lockfile-falls-back-without-changing-classifications")
@pytest.mark.parametrize("outcome", ["baseline", "invalid", "timeout", "killed", "survived"])
def test_snapshot_fallback_preserves_outcomes(candidate, tmp_path, monkeypatch, outcome):
    repo, base, head, m, context, entry = candidate
    context.root = tmp_path / "missing-store"
    prepare_script = 'test -z "$(ls -A "$1")"; cp "$2/lock" "$1/fetched"; '
    prepare_script += 'exit 7' if outcome == "baseline" else (
        'test "$(cat "$2/lock")" = base' if outcome == "invalid" else 'true')
    prepare = command("prepare", prepare_script, "{snapshot:deps}", "{src}")
    action = {"timeout": "exec sleep 5", "killed": "exit 7"}.get(outcome, "true")
    check = replace(command("check", 'cmp "$1/fetched" "$2/lock"; '
        'if [ "$(cat "$2/lock")" != base ]; then ' + action + '; fi', "{snapshot:deps}", "{src}"),
        timeout=0.1 if outcome == "timeout" else 10)
    m = replace(m, prepare=(prepare,), verify=(check,), mutation_steps=("check",))
    mutant = evidence.Mutant("lock", 1, "test", "base", "changed")
    out = tmp_path / "out"
    assert evidence.mutate(repo, head, [mutant], m, out, snapshots=context) == (outcome != "baseline")
    assert mutant.result == ("invalid" if outcome == "baseline" else outcome)
    executions = [out] if outcome == "baseline" else [out, out / "mutant-1"]
    for execution in executions:
        receipts = json.loads((execution / "receipts.json").read_text())
        expected_ids = ["prepare"] if outcome == "baseline" or (
            outcome == "invalid" and execution != out) else ["prepare", "check"]
        assert [r["id"] for r in receipts] == expected_ids
        event = json.loads((execution / "snapshot-events.jsonl").read_text())
        assert event["reason"] == "missing" and event["sha"] == head
        assert (event["key"] == verify.snapshot_key(repo, m.snapshots[0])) == (execution == out)
    if outcome == "baseline":
        assert not (out / "mutant-1").exists()
    elif outcome == "timeout":
        assert receipts[-1]["exit"] is None and not receipts[-1]["passed"]
