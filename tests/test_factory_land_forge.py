"""Factory delegates command definitions while retaining confirmation authority."""

from copy import deepcopy
import json
import subprocess

import pytest

from nightshift import factory_land as factory, land
from test_forge_boundary import BACKEND, DEFERRED, ROOT, argv_violations, source
from test_land import (
    FINISH, HEAD, PR, RUN, STATUS, URL, VIEW, config_path,
    operator_landing_script, scripted_runner, status_rows,
)


PRE_FINISH_WRITES = [
    pytest.param(["gh", "api", "--method", "PATCH", "repos/owner/project/pulls/78",
                  "-f", "body=- [x] Reviewed"], id="checklist"),
    pytest.param(["gh", "api", "--method", "POST", "repos/owner/project/issues/78/comments",
                  "-f", "body=attestation"], id="attestation"),
    pytest.param(FINISH, id="finish"),
]


def confirmation():
    offer = factory.Offer(RUN["change"], URL, "human_checks:reviewed", HEAD, 0.25)
    row = {**RUN, "pause": {"reason": "human_checks", "since": offer.since},
           "action": {"pause_id": offer.pause_id}}
    return offer, status_rows(row)


@pytest.mark.real_trust
@pytest.mark.scenario("factory-landing-jobs/shared-landing-operation-boundary/factory-delegates-github-definitions")
@pytest.mark.scenario("gitea-landing/offline-compatibility-evidence/github-transcripts-remain-exact")
@pytest.mark.scenario("factory-landing-jobs/confirmed-landing-handoff/landing-gates-and-head-pin-remain-authoritative")
@pytest.mark.parametrize("explicit_github", [False, True])
def test_factory_preflight_and_handoff_transcript(
    config_path, scripted_runner, monkeypatch, tmp_path, explicit_github,
):
    if explicit_github:
        config_path.write_text('[[forges]]\nname = "upstream"\nkind = "github"\n'
                               '[[repos]]\nname = "owner/project"\nforge = "upstream"\nverify = "true"\n')
    offer, document = confirmation()
    fresh_status = [*STATUS[:-1], offer.change, "--json"]
    remote = ["gh", "pr", "view", URL, "--json", "url,headRefOid"]

    def expect_fresh():
        scripted_runner.expect(fresh_status, document, timeout=20)
        scripted_runner.expect(remote, {"url": URL, "headRefOid": HEAD.upper()}, timeout=20)

    expect_fresh()  # Independent preflight before the actual responses land consumes.
    for argv, response, timeout in operator_landing_script():
        if argv == STATUS and response["runs"][0]["status"] == "paused":
            response = document
        if "PATCH" in argv or "POST" in argv or argv == FINISH:
            expect_fresh()
        scripted_runner.expect(argv, response, timeout=timeout)
    expected = [(argv, {"capture_output": True, "text": True, "check": False,
                       "timeout": timeout}) for argv, _, timeout in scripted_runner.steps]
    classifier, read_head = land.classify_confirmed_command, land.read_confirmed_remote_head
    classified, remote_reads = [], []

    def classify(argv, **identity):
        result = classifier(argv, **identity)
        classified.append((argv, identity, result))
        return result

    def read(pr, read_json):
        remote_reads.append(pr)
        return read_head(pr, read_json)

    def no_credentials(*args, **kwargs):
        pytest.fail("GitHub Factory must not load Gitea credentials")

    monkeypatch.setattr(land, "classify_confirmed_command", classify)
    monkeypatch.setattr(land, "read_confirmed_remote_head", read)
    monkeypatch.setattr(land.config, "parse_env_file", no_credentials)
    monkeypatch.setattr(subprocess, "run", scripted_runner)
    factory.validate_offer(offer, pause_file=tmp_path / "pause")
    assert factory.invoke_confirmed(offer, runner=scripted_runner,
        pause_file=tmp_path / "pause", config_path=config_path) == 0
    assert scripted_runner.calls == expected
    assert remote_reads == [URL] * 5  # Preflight, both source edits, attestation, guarded finish.
    assert classified[-1][0] == FINISH and classified[-1][2] == "finish"
    assert {kind for _, _, kind in classified} == {"pr", "status", "read", "mutation", "finish"}
    assert all(identity == {"repo": "owner/project", "number": "78",
                            "change": offer.change, "head": HEAD}
               for _, identity, _ in classified)


@pytest.mark.scenario("factory-landing-jobs/shared-landing-operation-boundary/factory-delegates-github-definitions")
@pytest.mark.parametrize("response", [None, [], {}, {"url": URL, "headRefOid": "short"},
    {"url": URL + "/", "headRefOid": HEAD}])
def test_delegated_remote_head_preserves_factory_refusal(monkeypatch, response):
    monkeypatch.setattr(factory, "_read_json_command", lambda _: response)
    with pytest.raises(factory.Refusal, match="fresh PR identity is unreadable or contradictory"):
        factory.read_remote_head(URL)


@pytest.mark.scenario("factory-landing-jobs/confirmed-landing-handoff/head-changes-at-handoff")
@pytest.mark.parametrize("field", ["head", "change", "pr", "pause_id", "since"])
@pytest.mark.parametrize("mutation", [pytest.param(None, id="actual"), *PRE_FINISH_WRITES])
def test_delegated_classifier_retains_five_field_response_and_mutation_guards(
    tmp_path, monkeypatch, field, mutation,
):
    offer, document = confirmation()
    changed = deepcopy(document)
    row = changed["runs"][0]
    if field == "pause_id":
        row["action"][field] += "changed"
    elif field == "since":
        row["pause"][field] += 1
    else:
        row[field] = {"head": "f" * 40, "change": "other-change", "pr": URL + "0"}[field]
    calls, classifications = [], []
    classifier = land.classify_confirmed_command

    def classify(argv, **identity):
        kind = classifier(argv, **identity)
        classifications.append(kind)
        return kind

    def runner(argv, **kwargs):
        calls.append(argv)
        assert argv in (VIEW, STATUS)
        data = PR if argv == VIEW else changed if mutation is None else document
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    monkeypatch.setattr(land, "classify_confirmed_command", classify)
    confirmed = factory.ConfirmedRunner(offer, runner=runner,
        read_status=lambda _: document if mutation is None else changed,
        remote_head=lambda _: HEAD, pause_file=tmp_path / "pause")
    confirmed(VIEW)
    if mutation is None:
        with pytest.raises(land.Refusal):
            confirmed(STATUS)
        assert classifications == ["pr", "status"]
    else:
        confirmed(STATUS)
        with pytest.raises(land.Refusal):
            confirmed(mutation)
        assert classifications == ["pr", "status", "finish" if mutation == FINISH else "mutation"]
    assert calls == [VIEW, STATUS]
    assert not confirmed.finished


@pytest.mark.scenario("factory-landing-jobs/confirmed-landing-handoff/head-changes-at-handoff")
def test_actual_remote_head_change_still_refuses_after_delegation(tmp_path):
    offer, document = confirmation()
    confirmed = factory.ConfirmedRunner(offer,
        runner=lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, json.dumps({**PR, "headRefOid": "f" * 40}), ""),
        read_status=lambda _: document, remote_head=lambda _: HEAD,
        pause_file=tmp_path / "pause")
    with pytest.raises(land.Refusal, match="actual PR response changed"):
        confirmed(VIEW)
    assert not confirmed.pr_read


@pytest.mark.scenario("factory-landing-jobs/confirmed-landing-handoff/head-changes-at-handoff")
@pytest.mark.parametrize("mutation", PRE_FINISH_WRITES)
def test_fresh_remote_head_change_refuses_each_delegated_write(tmp_path, mutation):
    offer, document = confirmation()
    calls, remote_reads = [], []

    def runner(argv, **kwargs):
        calls.append(argv)
        assert argv in (VIEW, STATUS)
        return subprocess.CompletedProcess(argv, 0, json.dumps(PR if argv == VIEW else document), "")

    def remote_head(pr):
        remote_reads.append(pr)
        return "f" * 40

    confirmed = factory.ConfirmedRunner(offer, runner=runner,
        read_status=lambda _: document, remote_head=remote_head, pause_file=tmp_path / "pause")
    confirmed(VIEW)
    confirmed(STATUS)
    with pytest.raises(land.Refusal, match="remote PR head differs"):
        confirmed(mutation)
    assert calls == [VIEW, STATUS]
    assert remote_reads == [URL]
    assert not confirmed.finished


@pytest.mark.scenario("factory-landing-jobs/confirmed-landing-handoff/landing-gates-and-head-pin-remain-authoritative")
@pytest.mark.parametrize("argv", [
    [*FINISH, "--force"], [*FINISH[:-1], "f" * 40],
    ["gh", "api", "--method", "POST", "repos/foreign/project/issues/78/comments", "-f", "body=x"],
    ["gh", "api", "--method", "PATCH", "repos/owner/project/issues/comments/0", "-f", "body=x"],
    ["gh", "api", "--method", "PATCH", "repos/owner/project/pulls/78", "-F", "body=x"],
    [*VIEW, "--jq", ".headRefOid"],
])
def test_shared_classifier_refuses_unknown_shapes_even_after_actual_reads(tmp_path, argv):
    offer, document = confirmation()
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        assert command in (VIEW, STATUS)
        return subprocess.CompletedProcess(command, 0, json.dumps(PR if command == VIEW else document), "")

    confirmed = factory.ConfirmedRunner(offer, runner=runner,
        read_status=lambda _: document, remote_head=lambda _: HEAD, pause_file=tmp_path / "pause")
    confirmed(VIEW)
    confirmed(STATUS)
    assert land.classify_confirmed_command(argv, repo="owner/project", number="78",
        change=offer.change, head=offer.head) == "unknown"
    with pytest.raises(land.Refusal, match="unknown pre-finish"):
        confirmed(argv)
    assert calls == [VIEW, STATUS]


@pytest.mark.scenario("forge-interface/factory-exception-retirement/factory-is-no-longer-deferred")
@pytest.mark.scenario("forge-interface/ast-source-boundary/scan-rejects-new-argv-sites")
@pytest.mark.parametrize("prefix", ["gh", "issue", "pr", "api"])
@pytest.mark.parametrize("template", ["argv = [{p}, 'view']", "argv = [{p}, *tail]",
                                       "argv = [\n    {p},\n    'view',\n]"])
def test_forbidden_factory_argv_fixture(tmp_path, prefix, template):
    assert argv_violations(ROOT) == []
    source(tmp_path, "nightshift/factory_land.py", template.format(p=repr(prefix)))
    assert argv_violations(tmp_path) == ["nightshift/factory_land.py:1: GitHub argv outside backend"]


@pytest.mark.scenario("forge-interface/ast-source-boundary/deferred-modules-remain-allowed")
@pytest.mark.scenario("forge-interface/factory-exception-retirement/factory-is-no-longer-deferred")
def test_only_factory_exception_is_retired(tmp_path):
    assert DEFERRED == {"nightshift/queue.py", "nightshift/land.py", "nightshift/arm.py",
                        "nightshift/outcomes.py", "nightshift/preflight.py",
                        "specloop/cli.py", "specloop/critic.py"}
    for path in DEFERRED | {BACKEND}:
        source(tmp_path, path, "argv = ['gh', *tail]")
    assert argv_violations(tmp_path) == []
    for path in ("nightshift/factory_land.py", "nightshift/nested/land.py",
                 "nightshift/daemon.py", "nightshift/specloop_intake.py"):
        source(tmp_path, path, "argv = ['api', endpoint]")
    assert len(argv_violations(tmp_path)) == 4
