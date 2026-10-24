"""Advisory assessment composition; delivery and polling have separate contracts."""

import itertools
import json
import subprocess

import pytest

from merge_readiness_fixtures import local_run
from nightshift import queue, specloop_intake as intake
from nightshift.config import Repo


REPO = Repo(name="owner/repo")
HEAD = "a" * 40
OTHER = "b" * 40
BODY = "## Checks a human must make\n(none)"
COMMAND = ["pr", "checks", "1", "--repo", "owner/repo", "--json", "name,bucket,state"]
NOT_GREEN = "CI: checks are not green; require at least one pass and only pass or skipping buckets"
UNAVAILABLE = "CI: checks unavailable; green CI could not be established"


def checks(*buckets):
    return json.dumps([dict(name=f"check-{n}", bucket=bucket, state="COMPLETED")
                       for n, bucket in enumerate(buckets)])


class StringRunner:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        assert args == COMMAND
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


BUCKETS = ("pass", "skipping", "pending", "fail", "cancel", "unknown")
COMBINATIONS = [combo for size in range(len(BUCKETS) + 1)
                for combo in itertools.combinations(BUCKETS, size)]


@pytest.mark.scenario("merge-readiness/advisory-ci-snapshot/green-checks-qualify")
@pytest.mark.scenario("merge-readiness/advisory-ci-snapshot/ci-not-established-blocks")
@pytest.mark.parametrize("buckets", COMBINATIONS + [("pass", "pass"), ("skipping", "pass", "skipping")])
def test_all_bucket_combinations(buckets):
    expected = [] if "pass" in buckets and set(buckets) <= {"pass", "skipping"} else [NOT_GREEN]
    for ordered in (buckets, tuple(reversed(buckets))):
        runner = StringRunner(checks(*ordered))
        assert intake._readiness_ci(REPO, 1, runner=runner) == expected
        assert runner.calls == [COMMAND]


@pytest.mark.scenario("merge-readiness/advisory-ci-snapshot/ci-not-established-blocks")
@pytest.mark.parametrize("payload", ["", "{", "null", "true", "1", '"pass"', "{}", "[[]]",
    '[null]', '[{"name":"build","bucket":"pass"}]',
    '[{"name":"build","bucket":"pass","state":"COMPLETED"},null]'])
def test_malformed_results_block(payload):
    runner = StringRunner(payload)
    assert intake._readiness_ci(REPO, 1, runner=runner) == ["CI: malformed check results"]
    assert runner.calls == [COMMAND]


@pytest.mark.parametrize("field", ["name", "bucket", "state"])
@pytest.mark.parametrize("value", [None, True, 1, [], {}, "", " \t\n"])
def test_check_fields_are_nonempty_strings(field, value):
    record = dict(name="build", bucket="pass", state="COMPLETED")
    record[field] = value
    runner = StringRunner(json.dumps([record]))
    assert intake._readiness_ci(REPO, 1, runner=runner) == ["CI: malformed check results"]
    assert runner.calls == [COMMAND]


@pytest.mark.parametrize("error", [queue.GhError("pending", transient=False),
    queue.GhError("service unavailable", transient=True), RuntimeError("runner failed"),
    OSError("gh unavailable"), subprocess.TimeoutExpired(COMMAND, 30),
    subprocess.CalledProcessError(8, COMMAND, output=checks("pass"))])
@pytest.mark.scenario("merge-readiness/advisory-ci-snapshot/ci-not-established-blocks")
def test_string_runner_errors_never_become_green(error):
    runner = StringRunner(error)
    assert intake._readiness_ci(REPO, 1, runner=runner) == [UNAVAILABLE]
    assert runner.calls == [COMMAND]


@pytest.mark.parametrize("code", [1, 8])
def test_default_runner_rejects_nonzero_even_with_green_stdout(monkeypatch, code):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs == dict(capture_output=True, text=True, check=False)
        return subprocess.CompletedProcess(args, code, checks("pass"), "checks not established")

    monkeypatch.setattr(queue.subprocess, "run", run)
    assert intake._readiness_ci(REPO, 1) == [UNAVAILABLE]
    assert calls == [["gh", *COMMAND]]


@pytest.mark.parametrize("head", [None, "", "  ", 123, OTHER, HEAD])
def test_all_conditions_are_composed_even_with_unknown_head(tmp_path, monkeypatch, head):
    observed = []
    for name in ("evidence", "revision", "human_checks"):
        def condition(*args, name=name, **kwargs):
            observed.append(name)
            return [f"{name}: blocked"]
        monkeypatch.setattr(intake, f"_readiness_{name}", condition)
    runner = StringRunner(checks("pending"))
    result = intake._readiness_assessment(REPO, queue.Labels(), tmp_path, 1,
        {"head": HEAD}, {"headRefOid": head, "labels": [], "body": BODY}, runner=runner)
    head_reasons = ([] if head == HEAD else
        [f"head: PR head {OTHER} does not match run head {HEAD}"] if head == OTHER else
        ["head: PR head is missing or unknown"])
    assert result["reasons"] == head_reasons + [NOT_GREEN, "evidence: blocked",
                                               "revision: blocked", "human_checks: blocked"]
    assert result["label"] == "merge:blocked"
    assert observed == ["evidence", "revision", "human_checks"]
    assert runner.calls == [COMMAND]


@pytest.mark.parametrize("blocked", list(itertools.product((False, True), repeat=5)))
def test_every_condition_combination(tmp_path, monkeypatch, blocked):
    head, ci, evidence, revision, human = blocked
    expected = [f"head: PR head {OTHER} does not match run head {HEAD}"] if head else []
    if ci:
        expected.append(NOT_GREEN)
    for name, fails in (("evidence", evidence), ("revision", revision), ("human_checks", human)):
        reasons = [f"{name}: failed"] if fails else []
        expected.extend(reasons)
        monkeypatch.setattr(intake, f"_readiness_{name}", lambda *a, reasons=reasons, **kw: reasons)
    runner = StringRunner(checks("fail" if ci else "pass"))
    result = intake._readiness_assessment(REPO, queue.Labels(), tmp_path, 1,
        {"head": HEAD}, {"headRefOid": OTHER if head else HEAD, "labels": [], "body": BODY}, runner=runner)
    assert result["reasons"] == expected
    assert result["label"] == ("merge:blocked" if any(blocked) else "merge:ready")
    assert result["body"].splitlines()[0] == f"Merge readiness: `{result['label']}`."
    assert [line[2:] for line in result["body"].splitlines() if line.startswith("- ")] == expected
    assert f"Advisory snapshot for {OTHER if head else HEAD}. Recheck CI and current main when landing; " \
           "this label does not authorize merge." in result["body"]
    assert runner.calls == [COMMAND]


@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/blocked-comment-lists-conditions")
def test_full_ordered_blockers_and_comment(local_run, tmp_path):
    env = local_run
    env.events[0].update(passed=False, timeouts=2)
    env.events[1].update(uncovered=["cap/req/missing"])
    env.write_events()
    labels = queue.Labels(revise="custom:revise")
    pr = dict(headRefOid=OTHER, labels=[{"name": labels.revise}], body="unknown checklist")
    before = {p.name: p.read_bytes() for p in env.run.dir.iterdir() if p.is_file()}
    runner = StringRunner(queue.GhError("unavailable", transient=True))
    result = intake._readiness_assessment(REPO, labels, tmp_path, 1, env.row, pr, runner=runner)
    expected = [f"head: PR head {OTHER} does not match run head {env.row['head']}", UNAVAILABLE,
        "evidence section 1: passed must be true", "evidence section 1: 2 timeouts",
        "evidence section 2: uncovered scenarios missing, invalid or nonempty",
        "revision: pending trigger `custom:revise`", "human checks: unknown checklist or scenario annotation"]
    assert result == dict(head=OTHER, run_head=env.row["head"], label="merge:blocked", reasons=expected,
        body="Merge readiness: `merge:blocked`.\n\n"
             f"Advisory snapshot for {OTHER}. Recheck CI and current main when landing; "
             "this label does not authorize merge.\n\n" + "\n".join(f"- {reason}" for reason in expected))
    assert runner.calls == [COMMAND]
    assert before == {p.name: p.read_bytes() for p in env.run.dir.iterdir() if p.is_file()}
    assert not (tmp_path / "merge-readiness").exists()  # Composition has no delivery effects.


@pytest.mark.scenario("merge-readiness/advisory-ci-snapshot/base-provenance-is-delegated-to-landing")
def test_readiness_does_not_query_current_base(local_run, tmp_path):
    env = local_run
    row = dict(env.row)
    env.git("branch", "main", env.base)
    moved = env.commit({"source.py": "main moved after CI\n"})
    env.git("branch", "-f", "main", moved)
    git_calls = []

    def git(args, **kwargs):
        git_calls.append(args)
        assert args == ["git", "--git-dir", str(env.run.dir / "repo.git"),
                        "show", f"{row['head']}^{{commit}}:{env.tasks_path}"]
        return subprocess.run(args, **kwargs)

    runner = StringRunner(checks("pass", "skipping"))
    result = intake._readiness_assessment(REPO, queue.Labels(), tmp_path, 1, row,
        dict(headRefOid=row["head"], labels=[], body=BODY), runner=runner, git_runner=git)
    assert result["label"] == "merge:ready"
    assert result["reasons"] == []
    assert result["head"] == result["run_head"] == row["head"]
    assert result["body"] == ("Merge readiness: `merge:ready`.\n\n"
        f"Advisory snapshot for {row['head']}. Recheck CI and current main when landing; "
        "this label does not authorize merge.\n\nPR head matches the run; CI checks are green; "
        "section evidence passed with zero timeouts (or an allowed measurement exemption); "
        "no build revision is pending; human checks are empty or explicitly post-deploy.")
    assert runner.calls == [COMMAND]
    assert len(git_calls) == 1


def test_pause_propagates_without_assessing_more_conditions(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("no further assessment after pause")
    monkeypatch.setattr(intake, "_readiness_evidence", forbidden)
    monkeypatch.setattr(intake, "_readiness_revision", forbidden)
    monkeypatch.setattr(intake, "_readiness_human_checks", forbidden)
    runner = StringRunner(intake.Paused())
    with pytest.raises(intake.Paused):
        intake._readiness_assessment(REPO, queue.Labels(), tmp_path, 1, {"head": HEAD},
            dict(headRefOid=HEAD, labels=[], body=BODY), runner=runner)
    assert runner.calls == [COMMAND]
