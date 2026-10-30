"""Frozen GitHub command scripts reused across the named operation boundary."""

from pathlib import Path
import socket
import subprocess

import pytest

from nightshift import land
from test_land import (
    BASE, COMMENTS, CONFIRM_MERGED, FINISHED_HEAD, HEAD, PR, REPO,
    UPDATED_HEAD, URL, FakeClock, background_references, ci_check, config_path,
    expect_check_sources, expect_ci_entry, expect_ci_poll, expect_ready_to_merge,
    expect_round_end, expect_round_update, merge_command, operator_landing_script,
    scripted_runner,
)


@pytest.fixture(autouse=True)
def offline_github(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("GitHub landing must use only its injected runner, without credential loading")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(land.config, "parse_env_file", forbidden)


def github_script(runner, case):
    """Expected argv come from pre-boundary fixtures, never production builders."""
    if case in {"checklist", "attestation-recovery", "attestation-update"}:
        steps = operator_landing_script()
        if case != "checklist":
            index = next(i for i, (argv, _, _) in enumerate(steps) if "POST" in argv)
            argv, recorded, timeout = steps[index]
            patch = ["gh", "api", "--method", "PATCH",
                     f"repos/{REPO}/issues/comments/{recorded['id']}", "-f", argv[-1]]
            if case == "attestation-recovery":
                steps[index:index + 1] = [
                    (argv, OSError("lost response"), timeout),
                    (COMMENTS, [[], [recorded]], 30), (patch, {}, 30),
                ]
            else:
                steps[index - 1:index + 1] = [(COMMENTS, [[recorded]], 30), (patch, {}, 30)]
        runner.steps.extend(steps)
        return 0, FINISHED_HEAD, True
    if case == "checklist-refusal":
        expect_check_sources(runner, "- [ ] Review this head\r\n")
        return 1, HEAD, False
    if case in {"update", "ci-refusal"}:
        expect_ci_entry(runner, stale=case == "update")
        pr = expect_round_update(runner) if case == "update" else PR
        head = pr["headRefOid"]
        expect_ci_poll(runner, [ci_check("pending")], code=8, pr=pr)
        expect_ci_poll(runner, [ci_check("fail" if case == "ci-refusal" else "pass")],
                       code=1 if case == "ci-refusal" else 0, pr=pr)
        if case == "ci-refusal":
            return 1, head, False
        expect_round_end(runner, pr=pr, base=BASE)
    else:
        head = expect_ready_to_merge(runner, kind=case if case in {"finish", "done"} else "direct")
    runner.expect(merge_command(head), subprocess.CompletedProcess(
        [], 1, "", "protection requires approval") if case == "merge-refusal" else {})
    runner.expect(CONFIRM_MERGED, {"number": 78, "url": URL,
                                 "state": "OPEN" if case == "merge-refusal" else "MERGED"})
    return int(case == "merge-refusal"), head, False


@pytest.mark.real_trust
@pytest.mark.scenario("gitea-landing/offline-compatibility-evidence/github-transcripts-remain-exact")
@pytest.mark.scenario("reviewed-pr-landing/forge-specific-operations-preserve-gates/operation-boundary-retains-behavior")
@pytest.mark.scenario("reviewed-pr-landing/operator-entry-point/explicit-invocation")
@pytest.mark.parametrize("enrollment", ["legacy", "explicit-github"])
@pytest.mark.parametrize("case", [
    "direct", "finish", "done", "checklist", "attestation-recovery", "attestation-update",
    "update", "ci-refusal", "merge-refusal", "checklist-refusal",
])
def test_land_github_argv_unchanged(config_path, scripted_runner, capsys, enrollment, case):
    if enrollment == "explicit-github":
        config_path.write_text('[[forges]]\nname = "upstream"\nkind = "github"\n'
                               '[[repos]]\nname = "owner/project"\nforge = "upstream"\nverify = "true"\n')
    code, head, checks_done = github_script(scripted_runner, case)
    # Include legacy bare-number repository inference in the complete scripts.
    scripted_runner.steps.appendleft((
        ["gh", "repo", "view", "--json", "nameWithOwner"], {"nameWithOwner": REPO}, 30,
    ))
    expected = [(list(argv), {"capture_output": True, "text": True, "check": False,
                            "timeout": timeout}) for argv, _, timeout in scripted_runner.steps]
    clock = FakeClock()
    assert land.main(["78", "--config", str(config_path), "--timeout", "1800",
                      *(["--checks-done"] if checks_done else [])], runner=scripted_runner,
                     sleep=clock.sleep, monotonic=clock.monotonic) == code
    assert scripted_runner.calls == expected
    assert not any(argv == ["gh", "api", f"repos/{REPO}/pulls/78"]
                   or any("nightshift:land-ci:" in arg for arg in argv)
                   for argv, _ in scripted_runner.calls)
    assert not scripted_runner.steps
    assert clock.sleeps == ([10] if case in {"update", "ci-refusal"} else [])
    output = capsys.readouterr()
    if code == 0:
        assert output.out.endswith(f"Merged {URL}\n") and output.err == ""
        assert scripted_runner.calls[-2][0] == merge_command(head)
    else:
        assert "Merged" not in output.out
        assert {"ci-refusal": "CI check", "merge-refusal": "protection requires approval",
                "checklist-refusal": "unticked human checks"}[case] in output.err


@pytest.mark.scenario("reviewed-pr-landing/forge-specific-operations-preserve-gates/operation-boundary-retains-behavior")
def test_named_operations_receive_current_deadline(config_path, scripted_runner):
    class ObservedGitHub(land.GitHubLandingForge):
        def __init__(self):
            self.budgets = []

        def read_pr(self, identity, operations):
            self.budgets.append(operations.deadline)
            return super().read_pr(identity, operations)

        def merge(self, identity, head, operations):
            assert operations.deadline is None
            assert head == UPDATED_HEAD
            return super().merge(identity, head, operations)

    github_script(scripted_runner, "update")
    forge, clock = ObservedGitHub(), FakeClock()
    assert land.main([URL, "--config", str(config_path)], runner=scripted_runner,
                     sleep=clock.sleep, monotonic=clock.monotonic, forge=forge) == 0
    assert forge.budgets[:2] == [None, None]
    assert set(forge.budgets[2:]) == {1900.0}


@pytest.mark.scenario("reviewed-pr-landing/operator-entry-point/background-paths-excluded")
def test_operation_boundary_is_unreachable_from_background_helpers():
    root = Path(__file__).resolve().parents[1]
    visited, violations = background_references(root, [
        "nightshift.daemon", "specloop.serve", "nightshift.cli", "specloop.cli",
    ])
    assert {"nightshift.specloop_intake", "nightshift.specloop_job"} <= visited
    assert "nightshift.factory_land" not in visited
    assert not violations


@pytest.mark.scenario("reviewed-pr-landing/guarded-rest-branch-update/expected-head-supplied")
@pytest.mark.parametrize("status_code", [409, 422])
def test_github_update_keeps_expected_head_and_refuses_rejection(scripted_runner, status_code):
    from test_land import expect_update_request, update_operations

    clock = FakeClock()
    expect_update_request(scripted_runner, subprocess.CompletedProcess(
        [], 1, "", f"HTTP {status_code}: rejected"))
    with pytest.raises(land.Refusal, match="branch update"):
        land.update_branch(land.Identity(REPO, 78), HEAD, update_operations(scripted_runner, clock))
    assert scripted_runner.calls[-1][0] == [
        "gh", "api", "--method", "PUT", f"repos/{REPO}/pulls/78/update-branch",
        "-f", f"expected_head_sha={HEAD}",
    ]
    assert len(scripted_runner.calls) == 3
