"""Reviewer setup consumes the same deadline as provider execution."""
import asyncio
from types import SimpleNamespace

import pytest

from nightshift.workers import reviewer
from nightshift.workers.base import WorkerBudgets
from test_reviewer import fixture_input, mocks


@pytest.mark.parametrize('setup_elapsed', [6, 10, 11])
def test_provider_only_receives_remaining_review_time(tmp_path, monkeypatch, setup_elapsed):
    state = mocks(monkeypatch)
    request, files = fixture_input()
    instants = iter([100, 100 + setup_elapsed, 100 + setup_elapsed])
    monkeypatch.setattr(reviewer, 'time', SimpleNamespace(monotonic=lambda: next(instants)))
    result = asyncio.run(reviewer._run_isolated(request, files, image_id='sha256:' + 'b' * 64,
        docker_host='unix:///tmp/fixture.sock', recovery_dir=tmp_path / 'recovery',
        provider_argv=['/fixture/codex', 'app-server', '--stdio'], provider_config='fixture=true',
        provider_env={}, model='fixture', budgets=WorkerBudgets(max_runtime_s=10)))
    if setup_elapsed < 10:
        assert result.ok
        assert state['request'].budgets.max_runtime_s == 4
    else:
        assert not result.ok
        assert 'request' not in state
        assert 'setup exhausted' in result.detail
