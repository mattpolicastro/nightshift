"""What a run is doing RIGHT NOW, read from a transcript still being written.

Until 2026-09-05 `worker._run` used `subprocess.run(capture_output=True)`, so
the whole stream sat in the daemon's memory until the phase returned. Two
consequences, and the second is the worse one: a forty minute implement pass
reported its phase and elapsed time and nothing else, and a worker killed by a
reboot, an OOM or a SIGKILL took its entire transcript with it — the crash
cases where the evidence matters most produced none of it.

`progress` is deliberately separate from `parse`. `parse` answers "how did this
end" and returns None without a terminal `result` event; a run in flight has no
result event and is not a failure.
"""

from __future__ import annotations

import json

from nightshift import trace


def event(**kw) -> str:
    return json.dumps(kw)


def assistant(tool: str | None = None, command: str = "") -> str:
    content = [{"type": "text", "text": "thinking"}]
    if tool:
        block = {"type": "tool_use", "name": tool, "input": {}}
        if command:
            block["input"] = {"command": command}
        content.append(block)
    return event(type="assistant", message={"content": content})


INIT = event(type="system", subtype="init", cwd="/wt")


def test_an_empty_transcript_says_nothing_has_happened():
    assert trace.progress("").line == "no events yet"


def test_a_started_run_is_not_the_same_as_a_stalled_one():
    """The first minute of every run looks like this: the CLI has emitted its
    init header and is thinking. Counting only assistant turns made that
    indistinguishable from a run that never started."""
    p = trace.progress(INIT)
    assert p.turns == 0
    assert p.events_seen == 1
    assert p.line == "started, no turns yet"


def test_it_reports_the_turn_count_and_the_latest_tool():
    events = "\n".join([INIT, assistant("Read"), assistant("Bash", "pnpm -r test")])
    p = trace.progress(events)

    assert p.turns == 2
    assert p.last_tool == "Bash"
    assert "pnpm -r test" in p.line
    assert p.line.startswith("turn 2, last: Bash")


def test_a_long_command_is_truncated_for_one_status_line():
    p = trace.progress(assistant("Bash", "pnpm -r test " + "x" * 200))
    assert len(p.last_command) <= 60


def test_a_half_written_final_line_is_tolerated():
    """The file is flushed per line, but a reader can still arrive mid-write."""
    events = "\n".join([INIT, assistant("Read")]) + '\n{"type": "assist'
    p = trace.progress(events)

    assert p.turns == 1
    assert p.last_tool == "Read"


def test_a_non_json_stderr_line_is_skipped():
    """`stderr` is merged into the stream now, so the CLI's
    `[claude-code:unrecognized_model]` warning lands in the middle of it."""
    events = "\n".join([
        INIT,
        "[claude-code:unrecognized_model] {\"model\":\"glm-4.7-flash\"}",
        assistant("Write"),
    ])
    p = trace.progress(events)

    assert p.turns == 1
    assert p.last_tool == "Write"


def test_progress_and_parse_answer_different_questions():
    """An in-flight run has no `result` event. That is not a failure, and the
    two readers must not be confused for one another."""
    events = "\n".join([INIT, assistant("Read")])

    assert trace.parse(events) is None       # "how did it end" — it has not
    assert trace.progress(events).turns == 1  # "what is it doing" — reading


# --- transcripts must not collide across repos ------------------------------


def test_a_transcript_name_carries_its_repo(tmp_path):
    """Issue numbers are per repo. Without the repo in the name, sample #11 and
    second-project #11 both resolved to `11-impl-1.jsonl` and one silently
    overwrote the other — and both of those issues exist today, with sample's
    transcripts for that number already on disk.
    """
    from nightshift import task

    sample = task._transcript_path(tmp_path, "matt/sample", "11-impl-1")
    hypno = task._transcript_path(tmp_path, "matt/second-project", "11-impl-1")

    assert sample != hypno
    assert sample.name == "matt__sample#11-impl-1.jsonl"


def test_no_transcript_directory_means_no_transcript(tmp_path):
    from nightshift import task

    assert task._transcript_path(None, "matt/sample", "11-impl-1") is None
