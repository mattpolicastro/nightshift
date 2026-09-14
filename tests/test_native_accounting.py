"""Native usage remains exact evidence, never Claude cost or scheduling credit."""
import copy
from dataclasses import asdict

import pytest

from nightshift import daemon, native_accounting as accounting, queue, trace
from nightshift.workers.base import WorkerResult

MARKER = {'version': 1, 'run_id': 'a'*32, 'recovery_dir': '/tmp/native-owned'}


def claim():
    return queue.Claim('example/repo', 7, 'candidate/7', '/tmp/worktree', 'fixture',
                       native_recovery=MARKER.copy())


def legacy(tally):
    return {key: value for key, value in asdict(tally).items() if key != 'native_usage'}


def test_missing_and_explicit_zero_remain_distinct():
    unknown = accounting.NativeTokens.from_usage({})
    zero = accounting.NativeTokens.from_usage({'outputTokens': 0})
    assert unknown.output_tokens is None and zero.output_tokens == 0
    assert zero.input_tokens is None and zero.total_tokens is None
    assert asdict(unknown) == dict.fromkeys(asdict(unknown))


def test_reported_counts_are_copied_without_derived_totals_or_prices():
    result = WorkerResult(status='failed', usage={'inputTokens': 5, 'outputTokens': 7,
        'cachedInputTokens': 2, 'reasoningOutputTokens': 3})
    evidence = accounting.from_worker(MARKER, 'implement', result)
    result.usage['inputTokens'] = 99
    assert evidence.tokens.input_tokens == 5
    assert evidence.tokens.total_tokens is None
    assert not hasattr(evidence, 'cost_usd')
    assert not hasattr(evidence, 'subscription_billed')
    assert not hasattr(evidence, 'rate_limit')


@pytest.mark.parametrize('usage', [None, [], True, {'outputTokens': True}, {'inputTokens': -1},
    {'totalTokens': 1.0}, {'outputTokens': '1'}, {'inputTokens': None}, {'tokens': 1},
    {'outputTokens': {'total': 1}}, {'cost_usd': 0}, {'rate_limit': {}}, {1: 2}])
def test_unknown_or_invalid_usage_rejected(usage):
    with pytest.raises(ValueError):
        accounting.NativeTokens.from_usage(usage)


@pytest.mark.parametrize('value', [True, -1, 1.0, '2'])
def test_typed_tokens_also_reject_invalid_values(value):
    with pytest.raises(ValueError):
        accounting.NativeTokens(output_tokens=value)


@pytest.mark.parametrize('marker', [None, {}, {**MARKER, 'version': True},
    {**MARKER, 'version': 2}, {**MARKER, 'run_id': 'unknown'},
    {**MARKER, 'recovery_dir': 'relative'}, {**MARKER, 'unexpected': 1}])
def test_unprepared_or_unknown_marker_rejected(marker):
    with pytest.raises(ValueError):
        accounting.from_worker(marker, 'implement', WorkerResult())


def test_legacy_result_cannot_be_relabelled_as_native():
    with pytest.raises(ValueError):
        accounting.from_worker(MARKER, 'implement', WorkerResult(runtime='claude-code'))


def test_recording_native_usage_preserves_all_legacy_tally_fields():
    quota = trace.RateLimit('allowed', 'five_hour', 1234, False)
    tally = daemon.Tally(shipped=2, escalated=1, cost=4.5, turns=12, lines=['legacy'], quota=quota, unbilled=1)
    before = legacy(tally)
    result = WorkerResult(usage={'inputTokens': 1000, 'outputTokens': 0})
    daemon._record_native_accounting(tally, claim(), 'implement', result)
    assert legacy(tally) == before
    assert tally.quota is quota
    assert tally.billed == 2
    assert len(tally.native_usage) == 1
    daemon._record_native_accounting(tally, claim(), 'implement', result)
    assert len(tally.native_usage) == 1


def test_unknown_native_usage_grants_no_unbilled_credit():
    source = daemon.Tally(escalated=1)
    daemon._record_native_accounting(source, claim(), 'implement', WorkerResult())
    target = daemon.Tally()
    daemon._merge(target, source)
    assert target.unbilled == 0 and target.billed == 1
    assert target.cost == 0 and target.turns == 0 and target.quota is None
    assert target.native_usage[('a'*32, 'implement')].tokens.output_tokens is None


def test_claude_and_ollama_tally_merge_unchanged():
    quota = trace.RateLimit('allowed', 'five_hour', 1234, False)
    claude = daemon.Tally(shipped=1, cost=2.5, turns=9, quota=quota, lines=['claude'])
    ollama = daemon.Tally(escalated=1, cost=0, turns=17, unbilled=1, lines=['ollama'])
    daemon._merge(claude, ollama)
    assert claude.shipped == 1 and claude.escalated == 1
    assert claude.cost == 2.5 and claude.turns == 26
    assert claude.quota is quota and claude.unbilled == 1 and claude.billed == 1
    assert claude.lines == ['claude', 'ollama']
    assert claude.native_usage == {}


def test_conflicting_final_evidence_rejected_before_any_tally_mutation():
    target, source = daemon.Tally(cost=1, turns=2), daemon.Tally(shipped=1, cost=4, unbilled=1)
    daemon._record_native_accounting(target, claim(), 'review', WorkerResult(usage={'outputTokens': 1}))
    daemon._record_native_accounting(source, claim(), 'review', WorkerResult(usage={'outputTokens': 2}))
    before = copy.deepcopy(target)
    with pytest.raises(ValueError, match='Conflicting'):
        daemon._merge(target, source)
    assert target == before
    with pytest.raises(ValueError, match='Conflicting'):
        daemon._record_native_accounting(target, claim(), 'review', WorkerResult(usage={'outputTokens': 2}))
    assert target == before


@pytest.mark.parametrize('phase', ['verify', '', None, True])
def test_unknown_phase_rejected(phase):
    with pytest.raises(ValueError):
        accounting.from_worker(MARKER, phase, WorkerResult())


def test_invalid_native_collection_cannot_partially_merge_legacy_state():
    target = daemon.Tally(cost=3, turns=4)
    source = daemon.Tally(shipped=1, cost=9, turns=10, unbilled=1)
    source.native_usage = {('a'*32, 'implement'): {'output_tokens': 1}}
    before = copy.deepcopy(target)
    with pytest.raises(ValueError):
        daemon._merge(target, source)
    assert target == before
