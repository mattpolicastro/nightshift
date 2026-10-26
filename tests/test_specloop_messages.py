import shlex
import subprocess
from types import SimpleNamespace

import pytest

from nightshift.cli import build_parser, main

from specloop import cli, messages

REASONS = ["phase_gate", "stuck", "blocked", "needs_human", "violation_repeat", "review_invalid", "secret",
           "gate_evidence", "coverage", "acceptance", "infra", "ready_to_publish", "human_checks", "something_new"]
LONG = "first line of the detail\n" + "noise " * 200


def parse_build_hint(command):
    executable, *argv = shlex.split(command)
    assert executable == "nightshift"
    args = build_parser().parse_args(argv)
    assert args.handler is cli.dispatch
    return args


@pytest.mark.parametrize("reason", REASONS)
def test_actions_lead_with_the_ask_and_stay_short(reason):
    m = messages.for_pause("mixer", reason, label="2.3", key="section:2", detail=LONG, attempts=3, done=21,
                           pr="https://github.com/o/r/pull/5")
    text = m.text()
    first, *rest = text.splitlines()
    assert m.kind == messages.ACTION
    assert first.startswith(("🟡 *", "🔴 *")) and first.endswith("(mixer)")
    assert len(text.splitlines()) <= 4 and len(text) < 500, text
    assert "noise noise noise" not in text  # the raw detail stays in `nightshift status`
    assert any(line.startswith("`nightshift ") for line in rest)


def test_specific_asks():
    stuck = messages.for_pause("m", "stuck", label="1.2", detail="[input:x] fails on -0.0", attempts=3).text()
    assert "unstick task 1.2 (3 attempts failed)" in stuck and "fails on -0.0" in stuck and "nightshift accept m" in stuck
    gate = messages.for_pause("m", "phase_gate", key="section:2").text()
    assert gate.splitlines()[0].startswith("🟡 *Review section 2 on the branch*")
    publish = messages.for_pause("m", "ready_to_publish", done=21).text()
    assert "all 21 tasks landed" in publish and "`nightshift publish m`" in publish
    assert messages.for_pause("m", "secret", label="1.1").text().startswith("🔴")
    checks = messages.for_pause("m", "human_checks", pr="https://x/pull/5").text()
    assert checks.splitlines()[-1] == "https://x/pull/5"


def test_usage_limits_are_fyi():
    m = messages.for_pause("m", "usage_limit", resume_at=0)
    assert m.kind == messages.FYI and m.text().startswith("FYI (m): waiting on usage limits")


def test_evidence_line_compresses_the_summary():
    summary = "section 2: 6 scenario(s) covered\nmutation: 9/10 caught\n  survived: a.py:3 int `x`"
    assert messages.evidence_line(2, summary) == \
        "section 2 passed its evidence gate — 6 scenarios covered, 9/10 mutants caught."


def test_fyi_can_be_kept_out_of_slack(monkeypatch, capsys):
    sent = []
    import nightshift.notify as notify
    monkeypatch.setattr(notify, "send", lambda hook, text: sent.append(text) or True)
    send = cli.notifier({"SPECLOOP_SLACK_WEBHOOK": "https://hook", "SPECLOOP_SLACK_FYI": "0"})
    send(messages.fyi("m", "section 1 passed"))
    send(messages.for_pause("m", "phase_gate", key="section:1"))
    assert len(sent) == 1 and sent[0].startswith("🟡")
    assert "FYI (m): section 1 passed" in capsys.readouterr().out  # still logged


@pytest.mark.scenario("worker-retirement/client-command-boundaries-remain-stable/existing-clients-use-surviving-commands")
@pytest.mark.scenario("run-actions/canonical-command-hints/messages-and-status-agree")
@pytest.mark.parametrize("reason", [*REASONS, "seal"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_rendered_notification_commands_parse_and_preserve_exit(monkeypatch, reason, exit_code):
    message = messages.for_pause("retained-change", reason, label="1.2", key="section:2")
    commands = [line[1:-1] for line in message.text().splitlines() if line.startswith("`nightshift ")]
    assert commands == [message.command]
    parsed = []

    def dispatch(args):
        parsed.append(args)
        return exit_code

    def forbidden(*args, **kwargs):
        pytest.fail("command parsing must not launch a model, service or shell")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    # Keep the real CLI parser/dispatch; handlers are the action injection seam.
    for verb in ("resume", "accept", "publish", "finish", "status"):
        monkeypatch.setattr(cli, verb, dispatch)
    for suggestion in commands[0].split("   or   "):
        executable, *argv = shlex.split(suggestion)
        assert executable == "nightshift"
        assert argv[0] == "status" or argv[0] in messages.actions_for_pause(reason)
        assert main(argv) == exit_code
        args = parsed[-1]
        assert args.cmd == argv[0] and args.change == "retained-change"
        if args.cmd == "resume":
            assert args.note == ("…" if "--note" in argv else "")
            assert args.no_run is False
    assert len(parsed) == len(commands[0].split("   or   "))


@pytest.mark.scenario("run-actions/canonical-command-hints/messages-and-status-agree")
@pytest.mark.parametrize("questions", [[], ["Which input?"]])
def test_draft_notifications_keep_manifest_change_and_branch(tmp_path, monkeypatch, questions):
    from specloop import propose

    path = str(tmp_path / "manifest with spaces.toml")
    manifest = SimpleNamespace(repo=tmp_path, change="new-change", branch="draft/custom", env_file=None)
    draft = SimpleNamespace(commit="a" * 40, questions=questions, contradictions=[], risks=[], problems=[])
    sent = []
    monkeypatch.setattr(cli.manifest, "template", lambda *a, **kw: (manifest, ""))
    monkeypatch.setattr(propose, "draft", lambda *a, **kw: draft)
    monkeypatch.setattr(propose, "publish_branch", lambda *a: manifest.branch)
    monkeypatch.setattr(cli, "load_env", lambda *a: {})
    monkeypatch.setattr(cli, "notifier", lambda *a: sent.append)
    assert main(["propose", path, "--change", manifest.change, "--branch", manifest.branch,
                 "--idea", "A feature"]) == (2 if questions else 0)
    assert len(sent) == 1 and sent[0].kind == messages.ACTION
    args = parse_build_hint(sent[0].command)
    assert args.manifest == path and args.change == manifest.change and args.branch == manifest.branch
    assert args.cmd == ("propose" if questions else "admit")
    if questions:
        assert args.idea_file == "<idea + answers>"
    else:
        assert args.head == draft.commit


@pytest.mark.scenario("run-actions/canonical-command-hints/messages-and-status-agree")
def test_busy_and_waived_hints_parse(capsys):
    from specloop.state import Run

    run = Run.for_change("hint-change")
    run.save()
    before = (run.dir / "state.json").read_bytes()
    with run.lock() as mine:
        assert mine
        assert main(["resume", "hint-change"]) == 1
    output = capsys.readouterr()
    hint = parse_build_hint(output.err.split("`")[1])
    assert hint.cmd == "pause" and hint.change == "hint-change"
    assert (run.dir / "state.json").read_bytes() == before

    assert main(["waive", "hint-change", "task-key", "known finding"]) == 0
    hint = parse_build_hint(capsys.readouterr().out.split("`")[1])
    assert hint.cmd == "resume" and hint.change == "hint-change" and hint.note == ""
    run.reload()
    assert run.state.task("task-key").waived == ["known finding"]


@pytest.mark.scenario("run-actions/canonical-command-hints/messages-and-status-agree")
def test_missed_drill_hint_parses(monkeypatch):
    from specloop import drill

    sent = []
    manifest = SimpleNamespace(drills=[dict(id="example", task="1.1", path="x", old="a", new="b")])
    monkeypatch.setattr(cli, "_ctx", lambda run: SimpleNamespace(m=manifest, notify=sent.append))
    monkeypatch.setattr(drill, "run_all", lambda *a: [drill.Outcome("example", "missed")])
    assert main(["drill", "hint-change"]) == 1
    assert len(sent) == 1 and sent[0].kind == messages.ACTION
    hint = parse_build_hint(sent[0].command)
    assert hint.cmd == "drill" and hint.change == "hint-change"
