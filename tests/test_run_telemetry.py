"""What a run's own telemetry is allowed to do to the daemon's accounting.

All three pin behaviour observed against a local Ollama endpoint on
2026-09-04 (SPEC-endpoints.md §1.2, §2.1, §2.2). Two were already right by
accident; the third — unpriced runs reporting list-price dollars — was wrong
in the direction that throttles free work.
"""

from __future__ import annotations

import json

from nightshift import daemon, task, trace
from nightshift.trace import RateLimit


def result_event(**kw) -> str:
    event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "stop_reason": "end_turn",
        "num_turns": 3,
        "duration_ms": 4000,
        "total_cost_usd": 0.32589,
        "usage": {"output_tokens": 1683, "cache_read_input_tokens": 0},
        "result": "OK",
        "modelUsage": {},
    }
    event.update(kw)
    return json.dumps(event)


LOCAL_USAGE = {
    "glm-4.7-flash": {
        "inputTokens": 56763,
        "outputTokens": 1683,
        "costUSD": 0.32589,
        "contextWindow": 200000,
        "provider": "firstParty",
        "costBasis": "unknown",
    }
}

CLOUD_USAGE = {
    "claude-sonnet-5": {"inputTokens": 160, "outputTokens": 78693, "costUSD": 6.229},
    "claude-haiku-4-5-20251001": {"inputTokens": 1637, "outputTokens": 18, "costUSD": 0.0017},
}


# --- §1.2: stderr rides in the transcript -----------------------------------


def test_parse_tolerates_a_non_json_stderr_line():
    # worker._run concatenates stdout + stderr. The CLI writes its
    # unrecognized-model warning to stderr, and this is the second time such
    # a line was read as a failed run by a human. The parser must not agree.
    warning = (
        "Warning: unrecognized model 'glm-4.7-flash'; assuming a 200k "
        "context window. Set CLAUDE_CODE_MAX_CONTEXT_TOKENS to override."
    )
    events = "\n".join([warning, result_event(), "{not json either"])
    r = trace.parse(events)
    assert r is not None
    assert r.ok
    assert r.turns == 3


# --- §2.1: a local run must not erase a learned window ----------------------


def test_merge_keeps_the_window_a_cloud_run_learned():
    window = RateLimit(status="allowed", type="five_hour", resets_at=1_700_000_000, using_overage=False)
    loop = daemon.Tally(quota=window)
    local = daemon.Tally(shipped=1)  # ran on Ollama: no rate_limit_event at all
    daemon._merge(loop, local)
    assert loop.shipped == 1
    assert loop.quota is window


def test_record_quota_ignores_runs_that_reported_no_window():
    window = RateLimit(status="allowed", type="five_hour", resets_at=1_700_000_000, using_overage=False)
    tally = daemon.Tally(quota=window)
    local_run = trace.parse(result_event(modelUsage=LOCAL_USAGE))
    assert local_run is not None and local_run.rate_limit is None
    report = task.Report(
        issue=None,
        step=task.Step.SHIP,
        reason="",
        attempts=[task.Attempt(implement=local_run, review=local_run)],
    )
    daemon._record_quota(report, tally)
    assert tally.quota is window


# --- §2.2: invented money never reaches the tally ---------------------------


def test_unpriced_run_contributes_nothing_to_cost():
    r = trace.parse(result_event(modelUsage=LOCAL_USAGE))
    assert r is not None
    assert r.cost_usd == 0.0
    # The token counts are still there for whoever wants a consumption proxy.
    assert r.model_usage["glm-4.7-flash"]["inputTokens"] == 56763


def test_subscription_run_cost_is_unchanged():
    # Real runs carry no costBasis field; the list-price figure passes through.
    r = trace.parse(result_event(total_cost_usd=6.2307695, modelUsage=CLOUD_USAGE))
    assert r is not None
    assert r.cost_usd == 6.2307695


def test_unpriced_cost_stays_out_of_the_report_total():
    priced = trace.parse(result_event(total_cost_usd=2.0, modelUsage=CLOUD_USAGE))
    unpriced = trace.parse(result_event(modelUsage=LOCAL_USAGE))
    report = task.Report(
        issue=None,
        step=task.Step.SHIP,
        reason="",
        attempts=[task.Attempt(implement=priced, review=unpriced)],
    )
    assert report.cost == 2.0
