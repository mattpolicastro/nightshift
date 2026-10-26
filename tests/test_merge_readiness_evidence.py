import json
import subprocess
from pathlib import Path

import pytest

from merge_readiness_fixtures import local_run
from nightshift.specloop_intake import _readiness_evidence
from specloop import state, state_locks


@pytest.mark.scenario("merge-readiness/local-evidence-assessment/measured-sections-pass")
@pytest.mark.parametrize("score", [0, 0.5, 1])
def test_measured_sections_pass(local_run, score):
    env = local_run
    # Append order, not timestamps or the worst historical result, selects each gate.
    passing = [dict(event, score=score, at=1) for event in env.events]
    env.events = [dict(event, passed=False, timeouts=3, at=999999) for event in env.events]
    env.events.extend(reversed(passing))
    env.events.append({"event": "future-event", "unknown": True})
    env.write_events()
    raw = json.loads((env.run.dir / "state.json").read_text())
    (env.run.dir / "state.json").write_text(json.dumps({**raw, "future_field": {}}))
    assert _readiness_evidence(env.row) == []


@pytest.mark.parametrize("field,value,reason", [
    ("unmeasured", True, "unmeasured"), ("passed", False, "passed"),
    ("uncovered", ["cap/req/scenario"], "uncovered"), ("score", None, "unmeasured"),
])
@pytest.mark.scenario("merge-readiness/local-evidence-assessment/unmeasured-evidence-blocks")
def test_unmeasured_evidence_blocks_readiness(local_run, field, value, reason):
    env = local_run
    # Both section diffs are test-only; a recorded failure must prevent that fallback.
    env.events.append(dict(env.events[0], **{field: value}))
    env.write_events()
    assert any("section 1:" in r and reason in r for r in _readiness_evidence(env.row))


@pytest.mark.scenario("merge-readiness/local-evidence-assessment/any-timeout-blocks")
@pytest.mark.parametrize("section,count", [(1, 1), (2, 2), (2, 100)])
def test_any_timeout_blocks(local_run, section, count):
    local_run.events.append(dict(local_run.events[section - 1], timeouts=count))
    local_run.write_events()
    assert _readiness_evidence(local_run.row) == [f"evidence section {section}: {count} timeouts"]


@pytest.mark.parametrize("summary,passes", [
    ("coverage: passed\nmutation: skipped (no mutable lines added under mutation_paths)\n", True),
    ("mutation: skipped (no mutation steps)", False),
    ("mutation: skipped (no section base)", False),
    ("prefix mutation: skipped (no mutable lines added under mutation_paths)", False),
    ("mutation: skipped (no mutable lines added under mutation_paths) suffix", False),
    (" mutation: skipped (no mutable lines added under mutation_paths)", False),
])
@pytest.mark.scenario("merge-readiness/local-evidence-assessment/no-mutable-lines-need-no-measurement")
def test_no_mutable_lines_need_no_measurement(local_run, monkeypatch, summary, passes):
    env = local_run
    env.events[0]["score"] = None
    env.write_events()
    env.run.state.evidence_summary["1"] = summary
    env.run.state.section_base.clear()  # This exemption needs no diff or baseline.
    env.run.save()
    # Allow only the documented inputs, even if an implementation tries to probe
    # absent receipts or scan an attempt directory before deciding on the skip.
    allowed = {env.run.dir / name for name in ("state.json", "manifest.toml", "events.jsonl")}
    # Lazy selection checks directory identity in both roots, without reading
    # additional evidence or scanning attempts.
    allowed_probes = allowed | {env.run.dir, state.legacy_root() / "runs" / env.run.dir.name,
                                state_locks.journal_path()}
    original_open, original_stat = Path.open, Path.stat

    def guarded_open(path, *args, **kwargs):
        assert path in allowed, f"unexpected evidence read: {path}"
        return original_open(path, *args, **kwargs)

    def guarded_stat(path, *args, **kwargs):
        assert path in allowed_probes, f"unexpected evidence probe: {path}"
        return original_stat(path, *args, **kwargs)

    def forbidden_scan(*args, **kwargs):
        pytest.fail("readiness must not scan attempt directories")

    def git(args, **kwargs):
        assert args == ["git", "--git-dir", str(env.run.dir / "repo.git"), "show",
                        f"{env.row['head']}^{{commit}}:{env.tasks_path}"]
        return subprocess.run(args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded_open)
        patch.setattr(Path, "stat", guarded_stat)
        for method in ("iterdir", "glob", "rglob"):
            patch.setattr(Path, method, forbidden_scan)
        reasons = _readiness_evidence(env.row, git_runner=git)
    assert reasons == ([] if passes else [
        "evidence section 1: score missing, invalid or unmeasured without no-mutable-lines summary"])


@pytest.mark.parametrize("case", ["valid", "tasks-only", "missing", "unknown", "reversed",
    "beyond-head", "production", "integrated", "rename", "rename-out", "empty", "next-missing",
    "other-tasks", "unsupported-tests", "first-production"])
@pytest.mark.scenario("merge-readiness/local-evidence-assessment/test-only-missing-event-has-positive-proof")
def test_test_only_missing_event_has_positive_proof(local_run, case):
    env = local_run
    env.events = []
    env.write_events()
    env.run.state.evidenced = []
    if case == "valid":
        env.commit({env.tasks_path: env.document + "\n"})
    if case == "tasks-only":
        env.run.state.section_base["2"] = env.row["head"]
        env.commit({env.tasks_path: env.document + "\n"})
    elif case in ("missing", "unknown", "reversed"):
        env.run.state.section_base["1"] = {"missing": "", "unknown": "f" * 40,
                                          "reversed": env.row["head"]}[case]
    elif case == "beyond-head":
        env.row["head"] = env.middle
        env.run.state.section_base["2"] = env.git("rev-parse", "HEAD")
    elif case == "production":
        env.commit({"source.py": "changed\n"})
    elif case == "integrated":
        head = env.row["head"]
        env.git("checkout", "-qb", "upstream", env.base)
        env.commit({"integrated.py": "production\n"})
        env.git("checkout", "--detach", head)
        env.git("merge", "--no-ff", "-qm", "integrate", "upstream")
        env.row["head"] = env.git("rev-parse", "HEAD")
    elif case == "rename":
        env.commit({"source.py": None, "tests/source.py": "original\n"})
    elif case == "rename-out":
        env.commit({"tests/one.py": None, "moved.py": "first\n"})
    elif case == "empty":
        env.run.state.section_base["2"] = env.row["head"]
    elif case == "next-missing":
        del env.run.state.section_base["2"]
    elif case == "other-tasks":
        env.commit({"openspec/changes/other/tasks.md": env.document})
    elif case == "unsupported-tests":
        env.commit({"test/test_other.py": "test\n"})
    elif case == "first-production":
        env.commit({"source.py": "production\n"})
        env.run.state.section_base["2"] = env.row["head"]
        env.commit({"tests/three.py": "third\n"})
    env.run.save()
    blocked = ([] if case == "valid" else [1, 2] if case in ("next-missing", "beyond-head") else [1]
               if case in ("missing", "unknown", "reversed", "first-production") else [2])
    assert _readiness_evidence(env.row) == [
        f"evidence section {n}: missing event; no bounded test-only diff proof" for n in blocked]


@pytest.mark.scenario("merge-readiness/local-evidence-assessment/test-only-missing-event-has-positive-proof")
@pytest.mark.parametrize("missing_section", [1, 2])
def test_test_only_diff_ends_at_next_of_three_sections(local_run, missing_section):
    env = local_run
    files = {env.tasks_path: env.document + "\n## 3. Final\n- [x] 3.1 Finish\n"}
    if missing_section == 1:
        # Section 2's measured production edit must not leak into section 1's diff.
        files["source.py"] = "section two\n"
    third_base = env.commit(files)
    env.run.state.section_base["3"] = third_base
    env.run.state.evidenced = [n for n in (1, 2, 3) if n != missing_section]
    env.run.save()
    env.commit({"source.py": "section three\n"})
    env.events.append(dict(env.events[0], section=3))
    env.events = [event for event in env.events if event["section"] != missing_section]
    env.write_events()

    # The first and middle sections use their immediate successor's start;
    # neither a previous section nor the final head bounds their test-only work.
    assert _readiness_evidence(env.row) == []


@pytest.mark.scenario("merge-readiness/local-evidence-assessment/unknown-evidence-blocks")
def test_missing_saved_state_reports_missing_file(local_run):
    path = local_run.run.dir / "state.json"
    path.unlink()

    reasons = _readiness_evidence(local_run.row)

    # Missing persisted state must be diagnosed directly, rather than loading
    # admission defaults and misleadingly reporting a saved PR identity mismatch.
    assert len(reasons) == 1
    assert "check run identity/state.json (FileNotFoundError:" in reasons[0]
    assert str(path) in reasons[0]
    assert not path.exists()


@pytest.mark.parametrize("field,value", [
    ("score", True), ("score", "0.9"), ("score", -0.1), ("score", 1.1),
    ("score", float("nan")), ("score", float("inf")), ("passed", 1),
    ("timeouts", None), ("timeouts", True), ("timeouts", -1), ("timeouts", 0.0),
    ("uncovered", None), ("unmeasured", "false"), ("section", True),
])
@pytest.mark.scenario("merge-readiness/local-evidence-assessment/unknown-evidence-blocks")
def test_strict_evidence_fields(local_run, field, value):
    local_run.events[1][field] = value
    local_run.write_events()
    reasons = _readiness_evidence(local_run.row)
    assert any(field in reason and ("events.jsonl" if field == "section" else "section 2:")
               in reason for reason in reasons)


@pytest.mark.parametrize("case", ["state.json", "manifest.toml", "events.jsonl", "repo.git",
    "bad-json", "partial", "bad-state", "bad-manifest", "identity", "manifest-identity",
    "missing-timeouts", "missing-score", "missing-revision", "completion", "invalid-completion", "tasks",
    "evidence_summary", "section_base", "pr-type"])
@pytest.mark.scenario("merge-readiness/local-evidence-assessment/unknown-evidence-blocks")
def test_unknown_evidence_blocks(local_run, case):
    env = local_run
    if case in ("state.json", "manifest.toml", "events.jsonl", "repo.git"):
        (env.run.dir / case).rename(env.run.dir / (case + ".hidden"))
    elif case in ("bad-json", "partial"):
        with (env.run.dir / "events.jsonl").open("a") as stream:
            stream.write("not-json\n" if case == "bad-json" else '{"event":')
    elif case in ("bad-state", "bad-manifest"):
        (env.run.dir / ("state.json" if case == "bad-state" else "manifest.toml")).write_text("[]")
    elif case == "manifest-identity":
        path = env.run.manifest_path
        path.write_text(path.read_text().replace('change = "c"', 'change = "other"'))
    elif case.startswith("missing-"):
        if case == "missing-revision":
            env.events.pop()
            env.run.state.section_base.clear()  # Missing revision cannot use the diff exception.
            env.run.save()
        else:
            del env.events[1][case.removeprefix("missing-")]
        env.write_events()
    elif case == "tasks":
        env.commit({env.tasks_path: "unparseable tasks\n"})
    else:
        if case in ("evidence_summary", "section_base"):
            setattr(env.run.state, case, {"1": []})
        elif case == "identity":
            env.run.state.pr = "https://github.com/owner/repo/pull/2"
        elif case == "pr-type":
            env.run.state.pr = env.row["pr"] = 1
        else:
            env.run.state.evidenced = [True] if case == "invalid-completion" else [1]
        env.run.save()
    if case in ("state.json", "bad-state", "identity", "invalid-completion",
                "evidence_summary", "section_base", "pr-type"):
        source = "state.json"
    elif case in ("manifest.toml", "bad-manifest", "manifest-identity"):
        source = "manifest.toml"
    elif case in ("repo.git", "tasks"):
        source = "committed tasks.md/repo.git"
    elif case in ("events.jsonl", "bad-json", "partial"):
        source = "events.jsonl"
    else:
        source = "section 2: " + {
            "missing-revision": "missing event", "missing-timeouts": "timeouts missing",
            "missing-score": "score missing", "completion": "saved evidence completion missing",
        }[case]
    assert any(source in reason for reason in _readiness_evidence(env.row))


@pytest.mark.scenario("merge-readiness/local-evidence-assessment/no-mutable-lines-need-no-measurement")
@pytest.mark.scenario("merge-readiness/local-evidence-assessment/unmeasured-evidence-blocks")
@pytest.mark.parametrize("condition,reason", [
    ("passed", "passed must be true"), ("unmeasured", "unmeasured"),
    ("uncovered", "uncovered"), ("timeouts", "1 timeouts"),
    ("completion", "saved evidence completion missing"),
])
def test_no_mutable_lines_summary_does_not_excuse_failed_gate(local_run, condition, reason):
    env = local_run
    env.events[0]["score"] = None
    env.run.state.evidence_summary["1"] = (
        "mutation: skipped (no mutable lines added under mutation_paths)")
    if condition == "completion":
        env.run.state.evidenced.remove(1)
    else:
        env.events[0][condition] = {
            "passed": False, "unmeasured": True, "uncovered": ["cap/req/missing"], "timeouts": 1,
        }[condition]
    env.write_events()
    env.run.save()
    assert any("section 1:" in r and reason in r for r in _readiness_evidence(env.row))


@pytest.mark.scenario("merge-readiness/local-evidence-assessment/unknown-evidence-blocks")
@pytest.mark.parametrize("record", [None, [], {}, {"event": ""}, {"event": 1},
    {"event": "evidence"}, {"event": "evidence", "section": "2"}])
def test_malformed_event_records_block(local_run, record):
    local_run.events.append(record)
    local_run.write_events()
    reasons = _readiness_evidence(local_run.row)
    assert len(reasons) == 1 and "check events.jsonl (ValueError:" in reasons[0]


@pytest.mark.scenario("merge-readiness/local-evidence-assessment/unknown-evidence-blocks")
@pytest.mark.parametrize("field", ["head", "change", "pr"])
@pytest.mark.parametrize("value", [None, "", "invalid", 1])
def test_unknown_run_identity_blocks(local_run, field, value):
    local_run.row[field] = value
    reasons = _readiness_evidence(local_run.row)
    assert len(reasons) == 1 and "check run identity/state.json" in reasons[0]


@pytest.mark.scenario("merge-readiness/local-evidence-assessment/unknown-evidence-blocks")
def test_git_timeout_is_unknown(local_run):
    def timeout(args, **kwargs):
        assert kwargs["timeout"] == 30
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    assert "TimeoutExpired" in _readiness_evidence(local_run.row, git_runner=timeout)[0]
