"""The reviewer's skim block.

Reviews had grown to ~2000 words of rubric and numbered concerns, so deciding
whether to open a PR meant reading all of it. The reviewer now ends with a
précis — written last so it cannot pre-commit a verdict, hoisted to the top of
the PR body because that is the order a human reads in.
"""

from __future__ import annotations

from nightshift import trace

FULL = """\
Lots of rubric prose here.

**1. NON-BLOCKING** — the worklog test count is off by two.

VERDICT: PASS
SUMMARY: appendVoice now takes the lowest free MIDI channel instead of voices.length + 1.
NEEDS-HUMAN:
- whether a 16-entry rail still reads as a rail
- the OP-XY listening pass for remove-then-add while notes sustain
"""


def test_it_reads_the_summary_and_the_bullets():
    s = trace.skim(FULL)
    assert s.summary.startswith("appendVoice now takes the lowest free")
    assert len(s.needs_human) == 2
    assert "16-entry rail" in s.needs_human[0]


def test_the_verdict_still_parses_with_the_block_after_it():
    """The block sits below VERDICT, so the gate must be unaffected by it."""
    assert trace.verdict(FULL) is True


def test_nothing_means_nothing():
    s = trace.skim("VERDICT: PASS\nSUMMARY: a rename.\nNEEDS-HUMAN:\n- nothing\n")
    assert s.needs_human == []
    assert s.summary == "a rename."


def test_nothing_on_the_same_line_also_means_nothing():
    s = trace.skim("SUMMARY: a rename.\nNEEDS-HUMAN: nothing\n")
    assert s.needs_human == []


def test_prose_after_the_bullets_does_not_get_swept_in():
    s = trace.skim(
        "SUMMARY: x.\nNEEDS-HUMAN:\n- one real item\n\nRun metadata: 40 turns\n"
    )
    assert s.needs_human == ["one real item"]


def test_a_missing_block_is_empty_not_an_assertion_of_cleanliness():
    """Absent is not the same as "nothing needs a human".

    A reviewer that ran out of turns emits no block at all. Degrading to an
    empty skim is right; reporting it as "nothing for you" would be a claim
    the reviewer never made — the same trap as treating a missing VERDICT as
    a pass.
    """
    s = trace.skim("no block at all, just prose")
    assert s.summary == ""
    assert s.needs_human == []
