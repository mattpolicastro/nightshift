"""Real land owns all gates and attestation, including across drained invocations."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import multiprocessing
import os
import subprocess
import sys

import pytest

from nightshift import factory_land as factory, land
from test_finish_expect_head import service, admitted, template, CHANGE
from test_land import (
    BASE, CONFIRM_MERGED, FINISH, FINISHED_HEAD, HEAD, MAIN, MAIN_REF, PR, RUN,
    STATUS, UPDATED_COMMIT, UPDATED_HEAD, URL, VIEW, config_path, scripted_runner,
    ci_check, expect_before_finish, expect_ci_poll, expect_initial_run,
    expect_round_end, merge_command, merge_commit, status_rows,
)


def adapter(service, *, runner=None, read_status=None):
    return factory.ConfirmedRunner(
        service.offer(), runner=runner or service, read_status=read_status or service.status,
        remote_head=service.remote_head, pause_file=service.pause_file)


@pytest.mark.scenario("factory-landing-jobs/confirmed-landing-handoff/head-changes-at-handoff")
@pytest.mark.parametrize("field", ["pr-head", "pr-url", "head", "change", "pr", "pause_id", "since"])
def test_actual_initial_responses_cannot_rebind_confirmation(service, field, capfd):
    offer = service.offer()

    def response(argv, **options):
        result = service(argv, **options)
        data = json.loads(result.stdout)
        if field.startswith("pr-") and argv[:3] == ["gh", "pr", "view"]:
            data["headRefOid" if field == "pr-head" else "url"] = (
                "f" * 40 if field == "pr-head" else "https://github.com/example/repo/pull/43")
        elif "status" in argv and not field.startswith("pr-"):
            row = data["runs"][0]
            if field == "pause_id":
                row["action"][field] += "new"
            elif field == "since":
                row["pause"][field] += 0.25
            else:
                row[field] = {"head": "f" * 40, "change": "another-change",
                              "pr": "https://github.com/example/repo/pull/43"}[field]
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    # Independent preflight still reports the confirmed identity. Only the
    # response actually consumed by land exposes this intervening change.
    factory.validate_offer(offer, read_status=service.status, remote_head=service.remote_head,
                           pause_file=service.pause_file)
    assert factory.invoke_confirmed(offer, runner=response, read_status=service.status,
                                    remote_head=service.remote_head, pause_file=service.pause_file,
                                    config_path=service.config) == 1
    assert "land:" in capfd.readouterr().err
    assert not any(service.commands(name) for name in ("PATCH", "POST", "finish", "merge"))
    assert service.archives == service.pushes == service.ready == []


@pytest.mark.scenario("factory-landing-jobs/confirmed-landing-handoff/head-changes-at-handoff")
@pytest.mark.parametrize("field", ["head", "change", "pr", "pause_id", "since"])
@pytest.mark.parametrize("boundary", ["PATCH", "POST", "finish"])
def test_fresh_five_field_identity_before_each_mutation(service, field, boundary, capfd):
    service.body = "- [ ] Inspect behavior\n" if boundary == "PATCH" else service.body
    frozen = service.status()
    changed = deepcopy(frozen)
    row = changed["runs"][0]
    if field == "pause_id":
        row["action"][field] += "new"
    elif field == "since":
        row["pause"][field] += 0.25
    else:
        row[field] = {"head": "f" * 40, "change": "another-change",
                      "pr": "https://github.com/example/repo/pull/43"}[field]
    confirmed = adapter(service, read_status=lambda _: changed)
    real_call = confirmed.__call__

    def at_boundary(argv, **options):
        # Keep the fresh check valid until precisely the chosen write.
        confirmed.read_status = lambda _: changed if boundary in argv else frozen
        return real_call(argv, **options)

    assert land.main([service.url, "--checks-done", "--config", str(service.config)],
                     runner=at_boundary) == 1
    assert ("expected one existing run" if field == "change" else "stale landing confirmation") in capfd.readouterr().err
    assert not service.commands(boundary)
    assert service.archives == service.pushes == service.ready == []
    assert not service.commands("merge")


@pytest.mark.scenario("factory-landing-jobs/confirmed-landing-handoff/landing-gates-and-head-pin-remain-authoritative")
@pytest.mark.parametrize("argv", [
    ["gh", "pr", "edit", "42", "--body", "text"],
    ["gh", "api", "--method", "DELETE", "repos/example/repo/issues/comments/1"],
    ["gh", "api", "--method", "POST", "repos/other/repo/issues/42/comments", "-f", "body=x"],
    ["gh", "api", "--method", "PATCH", "repos/example/repo/pulls/43", "-f", "body=x"],
    ["gh", "api", "--method", "PATCH", "repos/example/repo/pulls/42", "--input", "payload.json"],
    [sys.executable, "-m", "nightshift.cli", "finish", CHANGE],
    [sys.executable, "-m", "nightshift.cli", "finish", CHANGE, "--expect-head", "f" * 40],
    ["gh", "pr", "merge", "42", "--repo", "example/repo", "--admin"],
    ["gh", "api", "repos/example/repo/issues/42/comments", "-f", "body=implicit POST"],
])
def test_unknown_pre_finish_command_shapes_refuse(service, argv):
    confirmed = adapter(service)
    with pytest.raises(land.Refusal, match="unknown pre-finish"):
        confirmed(argv)
    assert service.calls == []


@pytest.mark.scenario("factory-landing-jobs/confirmed-landing-handoff/head-changes-at-handoff")
@pytest.mark.parametrize("upper", [False, True])
def test_known_finish_requires_actual_responses_and_forwards_them_unchanged(tmp_path, monkeypatch, upper):
    offer = factory.Offer(RUN["change"], URL, "human_checks:", HEAD, 0)
    document = status_rows({**RUN, "pause": {"reason": "human_checks", "since": 0},
                            "action": {"pause_id": offer.pause_id}})
    finish = [*FINISH[:-1], HEAD.upper() if upper else HEAD]
    results = {tuple(VIEW): subprocess.CompletedProcess(VIEW, 0, json.dumps(PR), "pr diagnostic"),
               tuple(STATUS): subprocess.CompletedProcess(STATUS, 0, json.dumps(document), "status diagnostic"),
               tuple(finish): subprocess.CompletedProcess(finish, 0, "finished", "finish diagnostic")}
    calls = []

    def runner(argv, **options):
        calls.append((argv, options))
        return results[tuple(argv)]

    monkeypatch.setattr(subprocess, "run", runner)
    confirmed = factory.ConfirmedRunner(offer, read_status=lambda _: document,
        remote_head=lambda _: HEAD, pause_file=tmp_path / "pause")
    with pytest.raises(land.Refusal, match="actual initial"):
        confirmed(finish, timeout=300)
    assert calls == []
    assert confirmed(VIEW, timeout=30) is results[tuple(VIEW)]
    with pytest.raises(land.Refusal, match="actual initial"):
        confirmed(finish, timeout=300)
    assert confirmed(STATUS, timeout=30) is results[tuple(STATUS)]
    assert confirmed(finish, timeout=300) is results[tuple(finish)]
    assert calls == [(VIEW, {"timeout": 30}), (STATUS, {"timeout": 30}), (finish, {"timeout": 300})]


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/wrong-state-refused")
@pytest.mark.parametrize("factory_invocation", [False, True])
@pytest.mark.parametrize("state,reason", [("running", None), ("aborted", None),
                                         ("paused", "ready_to_publish"), ("paused", "requested")])
def test_wrong_state_cannot_mutate(service, capfd, factory_invocation, state, reason):
    offer = service.offer()
    service.run.state.status = state
    if reason:
        service.run.pause(reason)
    else:
        service.run.state.pause = None
        service.run.save()
    assert service.land(factory=factory_invocation, offer=offer) == 1
    assert ("human_checks" if factory_invocation else reason or state) in capfd.readouterr().err
    assert not any(service.commands(name) for name in ("PATCH", "POST", "finish", "PUT", "merge"))


@pytest.mark.scenario("factory-landing-jobs/confirmed-landing-handoff/landing-gates-and-head-pin-remain-authoritative")
@pytest.mark.parametrize("gate", ["ci", "provenance", "protection", "success"])
def test_finish_and_base_successors_still_require_lands_gates(
    config_path, scripted_runner, tmp_path, capsys, gate,
):
    # Enrich actual status responses with Factory identity; use existing exact
    # command helpers so the adapter cannot synthesize a successor or merge.
    offer = factory.Offer(RUN["change"], URL, "human_checks:", HEAD, 0.25)
    initial = {**RUN, "pause": {"reason": "human_checks", "since": offer.since},
               "action": {"pause_id": offer.pause_id}}

    def runner(argv, **kwargs):
        result = scripted_runner(argv, **kwargs)
        if argv == STATUS:
            data = json.loads(result.stdout)
            if data["runs"] and data["runs"][0]["status"] == "paused":
                data["runs"][0] = initial
            return subprocess.CompletedProcess(argv, result.returncode, json.dumps(data), result.stderr)
        return result

    expect_initial_run(scripted_runner, PR, RUN)
    expect_before_finish(scripted_runner, PR)
    scripted_runner.expect(FINISH, {}, timeout=300)
    done = {**RUN, "status": "done", "pause": None, "head": FINISHED_HEAD}
    finished = {**PR, "headRefOid": FINISHED_HEAD}
    updated = {**PR, "headRefOid": UPDATED_HEAD}
    scripted_runner.expect(STATUS, status_rows(done)).expect(VIEW, finished)
    scripted_runner.expect(MAIN, MAIN_REF).expect(
        ["gh", "api", f"repos/owner/project/compare/{BASE}...{FINISHED_HEAD}"],
        {"status": "behind", "merge_base_commit": {"sha": "e" * 40}})
    scripted_runner.expect(VIEW, finished).expect(MAIN, MAIN_REF).expect(
        ["gh", "api", "--method", "PUT", "repos/owner/project/pulls/78/update-branch",
         "-f", f"expected_head_sha={FINISHED_HEAD}"], {})
    scripted_runner.expect(VIEW, updated).expect(UPDATED_COMMIT,
        merge_commit(FINISHED_HEAD, BASE) if gate != "provenance" else merge_commit(HEAD, BASE))
    if gate != "provenance":
        expect_ci_poll(scripted_runner, [ci_check("fail" if gate == "ci" else "pass")],
                       code=1 if gate == "ci" else 0, pr=updated)
        if gate != "ci":
            expect_round_end(scripted_runner, pr=updated, row=done)
            scripted_runner.expect(merge_command(UPDATED_HEAD),
                subprocess.CompletedProcess([], 1, "", "protection requires approval")
                if gate == "protection" else {})
            scripted_runner.expect(CONFIRM_MERGED, {"number": 78, "url": URL,
                                                   "state": "OPEN" if gate == "protection" else "MERGED"})
    assert factory.invoke_confirmed(offer, runner=runner,
        read_status=lambda _: status_rows(initial), remote_head=lambda _: HEAD,
        pause_file=tmp_path / "pause", config_path=config_path) == (0 if gate == "success" else 1)
    calls = [argv for argv, _ in scripted_runner.calls]
    assert calls.count(FINISH) == 1
    merges = [argv for argv in calls if "merge" in argv]
    assert merges == ([merge_command(UPDATED_HEAD)] if gate in ("protection", "success") else [])
    if gate != "success":
        assert {"ci": "CI check", "provenance": "provenance refused",
                "protection": "protection requires approval"}[gate] in capsys.readouterr().err


def _invocation_child(connection, fake, offer):
    """Run the actual entry point in a distinct, reapable local group."""
    os.setsid()
    output, errors = StringIO(), StringIO()

    def remote(argv, **options):
        connection.send(("command", argv, options))
        response = connection.recv()
        if isinstance(response, Exception):
            raise response
        return response

    with redirect_stdout(output), redirect_stderr(errors):
        code = factory.invoke_confirmed(offer, runner=remote, read_status=fake.status,
            remote_head=fake.remote_head, pause_file=fake.pause_file, config_path=fake.config)
    connection.send(("done", code, output.getvalue(), errors.getvalue()))
    connection.close()


def drained_invocation(fake, offer):
    ctx = multiprocessing.get_context("fork")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_invocation_child, args=(child, fake, offer))
    process.start()
    child.close()
    try:
        while True:
            assert parent.poll(20), "invocation failed to report a result"
            message = parent.recv()
            if message[0] == "done":
                break
            _, argv, options = message
            try:
                response = fake(argv, **options)
            except (OSError, subprocess.SubprocessError) as exc:
                response = exc
            parent.send(response)
        process.join(10)
        assert process.exitcode == 0
        with pytest.raises(ProcessLookupError):
            os.killpg(process.pid, 0)  # Only ESRCH establishes local drainage.
        return message[1], message[3]
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
        parent.close()


@pytest.mark.real_trust
@pytest.mark.scenario("factory-landing-jobs/terminal-attestation-behavior/late-attestation-duplicate-recovery")
@pytest.mark.scenario("factory-landing-jobs/terminal-attestation-behavior/no-cross-attempt-create-restriction")
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/late-create-crosses-invocation-boundaries")
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/uncertain-attestation-refused")
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/repeated-attestation-updates")
def test_late_post_crosses_drained_invocations_then_duplicate_deletion_recovers(service, tmp_path, capfd):
    offer = service.offer()
    service.post_mode = "delayed"
    code, reason = drained_invocation(service, offer)
    assert code == 1 and "could not be reconciled" in reason
    assert len(service.commands("POST")) == 1
    assert service.comments == [] and len(service.pending) == 1
    assert not service.commands("finish") and not service.commands("merge")

    # A has drained, but the accepted request remains in the remote service.
    # B may POST on its own empty lookup; its *valid* acknowledgement needs no
    # new lookup. Fail the later status read to retain the human_checks pause.
    service.post_mode, service.after_post, service.fail_after_post = "ack", False, True
    before_reads = service.comment_reads
    code, reason = drained_invocation(service, offer)
    assert code == 1 and "late status read failed" in reason
    assert len(service.commands("POST")) == 2
    assert service.comment_reads - before_reads == 2  # publication + pre-create lookup only
    assert len(service.comments) == len(service.pending) == 1
    assert not service.commands("finish") and not service.commands("merge")
    assert service.offer() == offer

    service.comments.extend(service.pending)
    service.pending.clear()  # A commits remotely only after both groups drained.
    assert len(service.comments) == 2
    assert service.comments[0]["body"] == service.comments[1]["body"]
    service.after_post = service.fail_after_post = False
    accepted = factory.start_attempt(offer, root=tmp_path / "records", launch=lambda _: None,
        read_status=service.status, remote_head=service.remote_head, pause_file=service.pause_file)
    directory = factory.attempt_directory(tmp_path / "records", offer, accepted["attempt_id"])
    assert service.land(factory=True, offer=offer) == 1
    reason = capfd.readouterr().err
    assert "duplicate marker matches; resolve duplicates before retrying" in reason
    assert len(service.commands("POST")) == 2
    assert service.archives == service.pushes == service.ready == []
    assert not service.commands("finish") and not service.commands("merge")

    # The lifecycle retains land's actual refusal, without attestation metadata.
    factory.record_result(directory, "refused", reason, exit_code=1)
    row = factory.landing_status(root=tmp_path / "records")["attempts"][0]
    assert row["reason"] == reason and row["outcome"] == "refused"
    assert {p.name for p in directory.iterdir()} == {"request.json", "progress.json", "result.json"}

    # Explicit operator deletion and retry updates the sole trusted marker.
    del service.comments[0]
    survivor = service.comments[0]["id"]
    assert service.land(factory=True, offer=offer) == 0
    assert len(service.commands("POST")) == 2
    patches = service.commands("PATCH")
    assert patches[-1][4] == f"repos/example/repo/issues/comments/{survivor}"
    assert "@operator recorded completion" in patches[-1][-1]
    assert len(service.commands("finish")) == 1
    assert service.commands("checks") and service.commands("merge")[-1][-1] == service.remote_head()
    assert service.merged and len(service.comments) == 1


@pytest.mark.real_trust
@pytest.mark.scenario("factory-landing-jobs/terminal-attestation-behavior/no-cross-attempt-create-restriction")
@pytest.mark.parametrize("failure", ["unsent", "delayed"])
def test_no_marker_after_failed_create_has_no_historical_restriction(service, capfd, failure):
    offer = service.offer()
    service.post_mode = failure
    assert service.land(factory=True, offer=offer) == 1
    assert "could not be reconciled" in capfd.readouterr().err
    assert service.comments == [] and len(service.commands("POST")) == 1
    service.post_mode, service.after_post = "ack", False
    assert service.land(factory=True, offer=offer) == 0
    assert len(service.commands("POST")) == 2 and len(service.comments) == 1
    assert service.commands("finish")[0][-1] == offer.head
    assert service.commands("checks") and service.commands("merge")[-1][-1] == service.remote_head()


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/lost-comment-response")
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/repeated-attestation-updates")
def test_lost_response_reconciles_later_page_within_invocation(service):
    service.post_mode = "lost"
    assert service.land(factory=True) == 0
    assert len(service.commands("POST")) == 1
    assert len(service.comments) == 1 and service.commands("PATCH")
    assert service.commands("PATCH")[-1][4].endswith(f"/{service.comments[0]['id']}")
    assert service.merged
