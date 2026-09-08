# The implement phase's `--max-turns` used to be hardcoded 100 in two places:
# `worker.implement`'s default and `task.py`'s call site. On 2026-08-08 three
# sample runs died at exactly 101 turns in one day (#65, #67, #106), each
# spending its whole budget and producing no branch, so the value became
# configurable and the default moved to 140.
#
# These tests pin the two things that were actually wrong: the value was not
# reachable from config, and the call site did not consult it. A default that
# merely exists on the dataclass while `task.py` passes a literal is the bug
# this file exists to prevent coming back.
from __future__ import annotations

import tomllib
from pathlib import Path

from nightshift import config as config_mod


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(
        '[[repos]]\nname = "o/r"\nverify = "true"\nbase = "main"\n\n' + body
    )
    return p


def test_defaults_are_the_raised_implement_cap_and_an_untouched_review_cap(tmp_path):
    cfg = config_mod.load(_write(tmp_path, ""))
    # 140, not 100: a truncation costs the entire run and yields nothing, so the
    # marginal turns are cheap against the downside.
    assert cfg.implement_max_turns == 140
    # The reviewer has never truncated. Raising the implementer must not quietly
    # widen the phase that is working.
    assert cfg.review_max_turns == 60


def test_both_caps_are_overridable_from_the_daemon_table(tmp_path):
    cfg = config_mod.load(
        _write(tmp_path, "[daemon]\nimplement_max_turns = 200\nreview_max_turns = 90\n")
    )
    assert cfg.implement_max_turns == 200
    assert cfg.review_max_turns == 90


def test_task_passes_the_configured_caps_rather_than_a_literal():
    """The regression that mattered: a knob nothing reads is not a knob.

    Asserted against the source text rather than by running a task, because the
    failure mode is precisely that the call site ignores config — which a mocked
    worker would happily hide.
    """
    src = (Path(__file__).parent.parent / "nightshift" / "task.py").read_text()
    # The configured cap, scaled by the endpoint serving the phase — a local
    # model is slower per turn, so `Assignment.max_turns` multiplies it. The
    # thing being pinned is unchanged: the config value reaches the call site.
    assert "max_turns=implementing.max_turns(cfg.implement_max_turns)" in src
    assert "max_turns=reviewing.max_turns(cfg.review_max_turns)" in src
    assert "max_turns=100" not in src


def test_the_example_config_documents_both_knobs():
    """config.example.toml is the only place a human learns these exist."""
    example = (
        Path(__file__).parent.parent / "config.example.toml"
    ).read_text()
    assert "implement_max_turns" in example
    assert "review_max_turns" in example
    # It must still parse — a comment block with a typo'd table header would
    # break every fresh install and nothing else here would catch it.
    tomllib.loads(example)
