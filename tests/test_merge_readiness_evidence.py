import json
import subprocess

import pytest

from merge_readiness_fixtures import local_run
from nightshift.specloop_intake import _readiness_evidence


def test_measured_sections_pass(local_run):
    """merge-readiness/local-evidence-assessment/measured-sections-pass"""
    env = local_run
    env.events.insert(0, dict(env.events[0], passed=False, timeouts=3, at=999999))
    env.events.append({"event": "future-event", "unknown": True})
    env.write_events()
    raw = json.loads((env.run.dir / "state.json").read_text())
    (env.run.dir / "state.json").write_text(json.dumps({**raw, "future_field": {}}))
    assert _readiness_evidence(env.row) == []


@pytest.mark.parametrize("field,value,reason", [
    ("unmeasured", True, "unmeasured"), ("passed", False, "passed"),
    ("uncovered", ["cap/req/scenario"], "uncovered"), ("score", None, "unmeasured"),
])
def test_unmeasured_evidence_blocks_readiness(local_run, field, value, reason):
    """merge-readiness/local-evidence-assessment/unmeasured-evidence-blocks"""
    env = local_run
    env.events.append(dict(env.events[0], **{field: value}))
    env.write_events()
    assert any("section 1:" in r and reason in r for r in _readiness_evidence(env.row))


def test_any_timeout_blocks(local_run):
    """merge-readiness/local-evidence-assessment/any-timeout-blocks"""
    local_run.events[1]["timeouts"] = 2
    local_run.write_events()
    assert _readiness_evidence(local_run.row) == ["evidence section 2: 2 timeouts"]


@pytest.mark.parametrize("summary,passes", [
    ("coverage: passed\nmutation: skipped (no mutable lines added under mutation_paths)\n", True),
    ("mutation: skipped (no mutation steps)", False),
    ("mutation: skipped (no section base)", False),
    ("prefix mutation: skipped (no mutable lines added under mutation_paths)", False),
])
def test_no_mutable_lines_need_no_measurement(local_run, summary, passes):
    """merge-readiness/local-evidence-assessment/no-mutable-lines-need-no-measurement"""
    env = local_run
    env.events[0]["score"] = None
    env.write_events()
    env.run.state.evidence_summary["1"] = summary
    env.run.save()
    assert (not _readiness_evidence(env.row)) is passes  # No attempt directories or receipts exist.


@pytest.mark.parametrize("case", ["valid", "tasks-only", "missing", "unknown", "reversed",
                                     "beyond-head", "production", "integrated", "rename"])
def test_test_only_missing_event_has_positive_proof(local_run, case):
    """merge-readiness/local-evidence-assessment/test-only-missing-event-has-positive-proof"""
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
    env.run.save()
    assert (not _readiness_evidence(env.row)) is (case == "valid")


@pytest.mark.parametrize("field,value", [
    ("score", True), ("score", "0.9"), ("score", -0.1), ("score", 1.1),
    ("score", float("nan")), ("score", float("inf")), ("passed", 1),
    ("timeouts", None), ("timeouts", True), ("timeouts", -1), ("timeouts", 0.0),
    ("uncovered", None), ("unmeasured", "false"), ("section", True),
])
def test_strict_evidence_fields(local_run, field, value):
    local_run.events[1][field] = value
    local_run.write_events()
    assert _readiness_evidence(local_run.row)


@pytest.mark.parametrize("case", ["state.json", "manifest.toml", "events.jsonl", "repo.git",
    "bad-json", "partial", "bad-state", "bad-manifest", "identity", "manifest-identity",
    "missing-timeouts", "missing-score", "missing-revision", "completion", "invalid-completion", "tasks",
    "evidence_summary", "section_base", "pr-type"])
def test_unknown_evidence_blocks(local_run, case):
    """merge-readiness/local-evidence-assessment/unknown-evidence-blocks"""
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
    assert _readiness_evidence(env.row)


def test_git_timeout_is_unknown(local_run):
    def timeout(args, **kwargs):
        assert kwargs["timeout"] == 30
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    assert "TimeoutExpired" in _readiness_evidence(local_run.row, git_runner=timeout)[0]
