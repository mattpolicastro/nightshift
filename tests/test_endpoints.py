"""Subscription classification remains readable in historical traces."""

import json

from nightshift import trace


def _run_result(*, local: bool) -> trace.Result:
    usage = (
        {"glm-4.7-flash": {"costUSD": 0.32, "costBasis": "unknown"}}
        if local
        else {"claude-sonnet-5": {"costUSD": 2.0}}
    )
    return trace.parse(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 3,
                "total_cost_usd": 0.32 if local else 2.0,
                "modelUsage": usage,
                "result": "OK",
            }
        )
    )


def test_a_run_priced_on_an_unknown_basis_is_not_subscription_billed():
    assert _run_result(local=True).subscription_billed is False
    assert _run_result(local=False).subscription_billed is True


def test_a_run_that_reported_a_window_is_billed_whatever_it_priced():
    """`rate_limit_info` arrives on SUCCESSFUL runs, not just on exhaustion —
    a run that reported one consumed the window it reported on."""
    r = _run_result(local=True)
    r.rate_limit = trace.RateLimit("allowed", "five_hour", 1, False)
    assert r.subscription_billed is True
