"""Keyed CLI envelopes and argv-bound success fixtures for revision intake."""

import json
from copy import deepcopy
from dataclasses import asdict

import pytest

from nightshift.cli import main

from revision_result_fixtures import revision_success
from specloop import cli, seal
from test_revise_idempotency import ctx, snapshot
from test_specloop_build_revision import BuildCommands, REVISED
from test_specloop_command_json import revision_run, template
from test_specloop_spec_revision import CHANGE, RevisionGh


@pytest.mark.scenario("specloop-interface/keyed-revision-cli/keyed-cli-reports-replay-accurately")
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("status", ["running", "paused", "aborted", "done"])
def test_keyed_append_and_replay(ctx, tmp_path, capfd, json_mode, status):
    feedback = tmp_path / "feedback.json"
    feedback.write_text('["Handle empty input."]')
    argv = ["revise", ctx.m.change, "--feedback", str(feedback), "--feedback-format", "json",
            "--round-id", "round:1", *(["--json"] if json_mode else [])]
    assert main(argv) == 0
    output = capfd.readouterr().out
    commit = seal.git(ctx.repo, "rev-parse", ctx.ref).decode().strip()
    expected = dict(version=1, outcome="revised", commit=commit, round_id="round:1",
                    replayed=False, status="running", pause=None)
    if json_mode:
        assert json.loads(output) == expected
    else:
        assert "committed" in output and "running" in output and "round:1" in output
        assert commit[:12] in output and "replayed" not in output
    ctx.run.reload()
    if status == "paused":
        ctx.run.pause("stuck", detail="Later human decision")
    else:
        ctx.run.state.status = status
        ctx.run.save()
    descendant = seal.commit_files(ctx.repo, commit, {"later": "later work"}, "later")
    seal.git(ctx.repo, "update-ref", ctx.ref, descendant, commit)
    before = snapshot(ctx)
    expected.update(replayed=True, status=status,
                    pause=asdict(ctx.run.state.pause) if ctx.run.state.pause else None)
    assert main(argv) == 0
    output = capfd.readouterr().out
    assert snapshot(ctx) == before
    if json_mode:
        assert json.loads(output) == expected  # Exactly one complete document.
    else:
        assert "replayed" in output and "round:1" in output and commit[:12] in output
        assert ("finished" if status == "done" else status) in output
        if status != "running":
            assert "running" not in output
        if status == "paused":
            assert "stuck" in output


@pytest.mark.scenario("specloop-interface/keyed-revision-cli/invalid-key-and-unkeyed-compatibility")
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("key", ["", "x" * 129, "../round", "café", "has space", None])
def test_invalid_keys_and_legacy_calls(ctx, tmp_path, capfd, key, json_mode):
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("Handle empty input.")
    argv = ["revise", ctx.m.change, "--feedback", str(feedback)]
    if key is not None:
        argv += ["--round-id", key]
    if json_mode:
        argv += ["--json"]
    before = snapshot(ctx)
    assert main(argv) == (0 if key is None else 1)
    output = capfd.readouterr()
    if key is not None:
        assert snapshot(ctx) == before
        if json_mode:
            document = json.loads(output.out)
            assert document == {"version": 1, "outcome": "refused", "reasons": document["reasons"]}
            assert document["reasons"] and "round" in document["reasons"][0]
        else:
            assert "revision refused" in output.err
    else:
        first = seal.git(ctx.repo, "rev-parse", ctx.ref).decode().strip()
        if json_mode:
            assert json.loads(output.out) == dict(version=1, outcome="revised", commit=first)
        else:
            assert "run is running" in output.out
        assert main(argv) == 0
        capfd.readouterr()
        assert seal.git(ctx.repo, "rev-parse", ctx.ref).decode().strip() != first
        assert not list((ctx.run.dir / "revision-rounds").glob("*.json"))
        ctx.run.pause("stuck")
        before = snapshot(ctx)
        assert main(argv) == 1
        output = capfd.readouterr()
        assert snapshot(ctx) == before
        assert "stuck" in (json.loads(output.out)["reasons"][0] if json_mode else output.err)


@pytest.mark.scenario("specloop-interface/keyed-revision-cli/invalid-key-and-unkeyed-compatibility")
@pytest.mark.parametrize("feedback_text", ["", "Different feedback"])
def test_keyed_feedback_refusal_preserves_original(ctx, tmp_path, capfd, feedback_text):
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("Original feedback")
    key = "A" + "a0._:-" * 21 + "z"  # All allowed punctuation, exactly 128 ASCII characters.
    argv = ["revise", ctx.m.change, "--feedback", str(feedback), "--round-id", key, "--json"]
    assert main(argv) == 0
    original = json.loads(capfd.readouterr().out)
    assert original["round_id"] == key and original["replayed"] is False
    before = snapshot(ctx)
    feedback.write_text(feedback_text)
    assert main(argv) == 1
    document = json.loads(capfd.readouterr().out)
    assert document == {"version": 1, "outcome": "refused", "reasons": document["reasons"]}
    assert document["reasons"] and snapshot(ctx) == before


@pytest.mark.scenario("pr-revision/keyed-result-acknowledgement/result-identity-gates-consumption")
@pytest.mark.parametrize("invalid", [{"round_id": "wrong"}, {"replayed": "yes"}, {"replayed": 0},
                                   {"status": "unknown"},
                                   {"pause": {}}, {"outcome": "admitted"}, {"commit": ""},
                                   "round_id", "commit"])
def test_success_fixtures_bind_each_argv_without_repairing_invalid_results(tmp_path, invalid):
    commands = BuildCommands(RevisionGh())
    feedback = tmp_path / "feedback.json"
    feedback.write_text('["Handle empty input."]')
    prefix = ["specloop", "revise", CHANGE, "--feedback", str(feedback), "--feedback-format", "json"]
    legacy = deepcopy(commands.result)
    first = json.loads(commands([*prefix, "--round-id", "first", "--json"]).stdout)
    assert first == dict(version=1, outcome="revised", commit=REVISED, round_id="first",
                         replayed=False, status="running", pause=None)
    saved = commands.result
    second = json.loads(commands([*prefix, "--round-id", "second", "--json"]).stdout)
    assert second == {**first, "round_id": "second"} and commands.result == second
    assert saved == first and saved is not commands.result
    if isinstance(invalid, str):
        del commands.result[invalid]
    else:
        commands.result.update(invalid)
    bad = deepcopy(commands.result)
    emitted = json.loads(commands([*prefix, "--round-id", "third", "--json"]).stdout)
    assert json.dumps(emitted, sort_keys=True) == json.dumps(bad, sort_keys=True)
    commands.result = legacy
    assert json.loads(commands([*prefix, "--json"]).stdout) == legacy
    assert revision_success([*prefix, "--json"], legacy) == legacy
    spec = {"version": 1, "outcome": "drafted", "commit": REVISED}
    assert revision_success(["propose", "--json"], spec) == spec
