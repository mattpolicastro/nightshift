"""Execute the documented retry example; validate schema without freezing prose."""

import json
import re
import shlex

import pytest

from specloop import cli, seal
from nightshift import specloop_intake
from test_revise_idempotency import ctx, snapshot  # noqa: F401
from test_specloop_command_json import revision_run, template  # noqa: F401
from test_specloop_documentation_integrity import ROOT


@pytest.mark.scenario(
    "specloop-documentation/keyed-revision-recovery-guidance/operator-retries-a-logical-round"
)
@pytest.mark.scenario(
    "specloop-documentation/keyed-revision-recovery-guidance/recovery-limits-remain-reviewable"
)
@pytest.mark.parametrize("status", ["running", "paused", "aborted", "done"])
def test_documented_keyed_command_and_result(ctx, tmp_path, capfd, status):
    document = (ROOT / "docs/specloop/command-json.md").read_text()
    command, = [line for block in re.findall(r"```sh\n(.*?)```", document, re.S)
                for line in block.splitlines() if line.startswith("specloop revise ")]
    argv = shlex.split(command)[1:]
    assert argv[1] == ctx.m.change
    feedback = tmp_path / argv[argv.index("--feedback") + 1]
    feedback.write_text("Handle empty input.")
    argv[argv.index("--feedback") + 1] = str(feedback)
    example, = [json.loads(block) for block in re.findall(r"```json\n(.*?)```", document, re.S)]
    assert re.fullmatch(r"[0-9a-f]{40}", example["commit"])
    assert cli.main(argv) == 0
    result = json.loads(capfd.readouterr().out)
    assert result == {**example, "commit": result["commit"]}
    row, = [line for line in document.splitlines() if line.startswith("| `revise` |")]
    fields = set(re.findall(r"`([^`]+)`", row.split("|")[3]))
    assert fields == (result.keys() - {"version", "outcome"}) | {"reasons"}
    # The daemon must accept exactly the documented success schema.
    receipt = {"revision_key_version": 1, "round_id": example["round_id"]}
    specloop_intake._validate(result, "revise", receipt)
    ctx.run.reload()
    if status == "paused":
        ctx.run.pause("requested", detail="Later decision")
    else:
        ctx.run.state.status = status
        ctx.run.save()
    descendant = seal.commit_files(ctx.repo, result["commit"], {"later": "work"}, "later")
    seal.git(ctx.repo, "update-ref", ctx.ref, descendant, result["commit"])
    before = snapshot(ctx)
    assert cli.main(argv) == 0
    replay = json.loads(capfd.readouterr().out)
    assert replay == {**result, "replayed": True, "status": status, "pause": replay["pause"]}
    specloop_intake._validate(replay, "revise", receipt)
    assert snapshot(ctx) == before
    feedback.write_text("Changed feedback")
    assert cli.main(argv) == 1
    refusal = json.loads(capfd.readouterr().out)
    assert refusal == {"version": 1, "outcome": "refused", "reasons": refusal["reasons"]}
    assert refusal["reasons"] and snapshot(ctx) == before
