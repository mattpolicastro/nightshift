"""Real fresh Git/claims/journals; synthetic model and verification boundaries."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import outcomes, queue, task, vcs
from nightshift.config import Repo
from nightshift.workers import native_task_lane as module, managed_task, stable_pipeline, snapshot
from nightshift.workers.base import WorkerResult, ReviewerVerdict
from nightshift.workers.chatgpt_admission import ChatGPTIdentity
from nightshift.workers.isolated_verification import ClauseResult, IsolatedVerificationResult
from nightshift.workers.review_context import ReviewPolicy
from nightshift.workers.reviewer import ReviewOutcome
from nightshift.workers.stable_worker import _StableRun
from nightshift.workers.stable_worker_guard import validate_marker
from test_managed_task import values
from test_candidate_pipeline import repository, git
from test_native_persistence import _probe_lock


@pytest.fixture
def harness(values, repository, tmp_path, monkeypatch):
    repo_dir, base = repository
    root = tmp_path / 'worktrees'
    root.mkdir(mode=0o700)
    worktree = root / 'candidate'
    issue = queue.Issue('example/repo', 9, 'Repair', 'Change source')
    repo = Repo(issue.repo, 'true')
    claim = queue.Claim(issue.repo, issue.number, 'candidate/9', str(worktree), 'fixture')
    claim.write()
    profile = managed_task._validated_profile(**values)
    state = SimpleNamespace(profile=profile, issue=issue, repo=repo, claim=claim, root=root,
        worktree=worktree, repository=repo_dir, base=base, failure=None, calls=[])
    state.lock = claim.path.with_name(claim.path.name + '.native.lock')
    def fetch(repository, repo_name, branch, **kwargs):
        state.calls.append(('fetch', branch))
        assert state.lock.exists() and _probe_lock(state.lock) == 'blocked'
        if state.failure == 'fetch': raise RuntimeError('synthetic private error')
        return base
    monkeypatch.setattr(module, '_fetch_base', fetch)
    async def implement(request, files, **kwargs):
        state.calls.append(('implement', request.model))
        assert request.reasoning_effort == 'medium'
        assert validate_marker(kwargs['native_marker'], kwargs['recovery_dir']).phase == 'implementing'
        assert _probe_lock(state.lock) == 'blocked'
        if state.failure == 'cancel': raise asyncio.CancelledError()
        worker = WorkerResult(status='succeeded', requested_model=request.model, observed_model=request.model,
            thread_id='implementation-thread', turn_id='implementation-turn', usage={'inputTokens': 7})
        return _StableRun(worker, (snapshot.SourceFile('source.txt', b'changed'),), True, True, True, True)
    monkeypatch.setattr(stable_pipeline, '_run_stable_chatgpt', implement)
    def verify(repository, sha, command, **kwargs):
        fp = snapshot.fingerprint(snapshot.from_git(repository, sha))
        return IsolatedVerificationResult(sha, command, kwargs['image_id'], status='succeeded',
            source_fingerprint=fp, final_fingerprint=fp, cleanup_succeeded=True,
            clauses=[ClauseResult(('true',), 'succeeded', 0, '', 0)])
    monkeypatch.setattr(stable_pipeline.isolated_verification, 'run', verify)
    async def review(request, files, **kwargs):
        state.calls.append(('review', kwargs['model']))
        assert kwargs['reasoning_effort'] == 'high'
        assert kwargs['image_id'] == profile.review_image_id
        assert _probe_lock(state.lock) == 'blocked'
        result = WorkerResult(status='succeeded', requested_model=kwargs['model'], observed_model=kwargs['model'],
            thread_id='review-thread', turn_id='review-turn', reviewer_verdict=ReviewerVerdict('PASS', (), ()))
        return ReviewOutcome(result, request.review_id, request.candidate_sha,
            request.verification.source_fingerprint, True, True, True)
    from nightshift.workers import stable_controller
    monkeypatch.setattr(stable_controller, '_review_stable_chatgpt', review)
    def forbidden(*args, **kwargs): pytest.fail('legacy or destructive task path reached')
    for target, names in ((queue, ('release', 'escalate', 'complete')),
                          (vcs, ('add_worktree', 'remove_worktree', 'push', 'open_pr', 'fetch')),
                          (task, ('run',))):
        for name in names: monkeypatch.setattr(target, name, forbidden)
    state.call = lambda: module._run_claimed_native(profile,
        identity_reference='operator-account', identity=ChatGPTIdentity('private@example.invalid', 'private-workspace'),
        issue=state.issue, repo=repo, claim=claim, repository=repo_dir,
        worktree_root=root, review_policy=ReviewPolicy('Check correctness'))
    return state


def test_fresh_lane_reaches_review_but_always_retains(harness):
    result = asyncio.run(harness.call())
    assert result.stage == 'complete' and result.result.status == 'reviewed_pending_human'
    assert result.status == 'retained' and not result.ready_for_shipping
    assert harness.worktree.exists() and harness.claim.path.exists() and harness.lock.exists()
    assert _probe_lock(harness.lock) == 'acquired'
    assert outcomes.tasks()[0]['state'] == 'needs_decision' and outcomes.tasks()[0]['native_retained']
    assert git(harness.worktree, 'rev-parse', 'HEAD^') == harness.base


def test_fetch_failure_keeps_preprovider_tombstone_and_claim(harness):
    harness.failure = 'fetch'
    result = asyncio.run(harness.call())
    assert result.stage == 'prepare' and result.result is None
    assert harness.claim.path.exists() and harness.lock.exists() and not harness.worktree.exists()
    assert outcomes.tasks()[0]['native_retained']
    previous = list(harness.calls)
    assert asyncio.run(harness.call()).result is None
    assert harness.calls == previous


def test_cancellation_retains_started_journal_and_attention(harness):
    harness.failure = 'cancel'
    with pytest.raises(asyncio.CancelledError): asyncio.run(harness.call())
    assert harness.worktree.exists() and harness.claim.path.exists() and harness.lock.exists()
    journal = json.loads(next(harness.profile.recovery_root.glob('*/native-run-*.json')).read_text())
    assert journal['implement'] == {'state': 'started', 'final': None}
    assert outcomes.tasks()[0]['native_retained']


def test_existing_worktree_and_branch_are_never_reused_or_removed(harness):
    harness.worktree.mkdir()
    sentinel = harness.worktree / 'sentinel'
    sentinel.write_text('preserve')
    result = asyncio.run(harness.call())
    assert result.result is None and not harness.calls
    assert sentinel.read_text() == 'preserve'


def test_fresh_checkout_never_executes_hooks_or_filters(harness, tmp_path):
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    sentinel = tmp_path / 'executed'
    hook = hooks / 'post-checkout'
    hook.write_text('#!/bin/sh\ntouch ' + str(sentinel) + '\n')
    hook.chmod(0o700)
    git(harness.repository, 'config', 'core.hooksPath', str(hooks))
    git(harness.repository, 'config', 'filter.sentinel.smudge', 'touch ' + str(sentinel))
    (harness.repository / '.git' / 'info' / 'attributes').write_text('* filter=sentinel\n')
    assert asyncio.run(harness.call()).stage == 'complete'
    assert not sentinel.exists()


def test_validation_before_ownership_has_no_sticky_native_attention(harness):
    harness.claim.repo = 'other/repo'
    result = asyncio.run(harness.call())
    assert result.status == 'blocked' and result.result is None
    assert not harness.lock.exists() and outcomes.tasks() == []
    assert not harness.calls


def test_rejected_existing_path_retains_created_tombstone(harness):
    harness.worktree.mkdir()
    result = asyncio.run(harness.call())
    assert result.status == 'retained' and outcomes.tasks()[0]['native_retained']
    assert harness.lock.exists(), 'failed lock preparation evidence remains for inspection'


def test_public_activation_is_blocked_even_with_operator_flags(monkeypatch):
    async def forbidden(*args, **kwargs): pytest.fail('public surface reached private execution')
    monkeypatch.setattr(module, '_run_claimed_native', forbidden)
    result = module.NativeTaskLane().run(enable=True, force=True, native=True)
    assert result.status == 'blocked' and not result.ready_for_shipping


def test_issue_base_is_explicit_and_never_silently_replaced(harness):
    from dataclasses import replace
    harness.issue = replace(harness.issue, body='base: theme/approved\nChange source')
    assert asyncio.run(harness.call()).stage == 'complete'
    assert harness.calls[0] == ('fetch', 'theme/approved')


def test_recording_failure_does_not_remove_successful_local_candidate(harness, monkeypatch):
    def fail(*args, **kwargs): raise OSError('synthetic database failure')
    monkeypatch.setattr(outcomes, 'record', fail)
    result = asyncio.run(harness.call())
    assert result.status == 'retained' and harness.worktree.exists() and harness.lock.exists()


@pytest.mark.parametrize('fault', ['missing-base', 'worktree-failure', 'claim-race'])
def test_preprovider_failures_preserve_partial_state_and_never_run_controller(harness, monkeypatch, fault):
    original = module._fetch_base
    def fetch(*args, **kwargs):
        sha = original(*args, **kwargs)
        if fault == 'missing-base': return 'f' * 40
        if fault == 'claim-race':
            from dataclasses import replace
            replace(harness.claim, started_at='another-owner').write()
        return sha
    monkeypatch.setattr(module, '_fetch_base', fetch)
    if fault == 'worktree-failure':
        def failed(*args, **kwargs):
            harness.worktree.mkdir()
            (harness.worktree/'partial').write_text('retained')
            raise OSError('partial creation')
        monkeypatch.setattr(module, '_fresh_worktree', failed)
    result = asyncio.run(harness.call())
    assert result.result is None
    assert all(call[0] == 'fetch' for call in harness.calls)
    assert harness.lock.exists() and harness.claim.path.exists()
    if fault == 'worktree-failure': assert (harness.worktree/'partial').read_text() == 'retained'
    if fault == 'claim-race': assert json.loads(harness.claim.path.read_text())['started_at'] == 'another-owner'
    assert outcomes.tasks()[0]['native_retained']


@pytest.mark.parametrize('fault', ['implementation-failed', 'accounting-write', 'review-fail', 'review-cancel'])
def test_postintent_faults_retain_real_journal_and_never_ship(harness, monkeypatch, fault):
    from dataclasses import replace
    from nightshift.workers import stable_controller
    from nightshift.workers.native_persistence import NativeAttemptJournal
    if fault == 'implementation-failed':
        original = stable_pipeline._run_stable_chatgpt
        async def failed(*args, **kwargs):
            completed = await original(*args, **kwargs)
            return replace(completed, worker=replace(completed.worker, status='failed'), files=None)
        monkeypatch.setattr(stable_pipeline, '_run_stable_chatgpt', failed)
    elif fault == 'accounting-write':
        def failed(*args, **kwargs): raise OSError('private persistence failure')
        monkeypatch.setattr(NativeAttemptJournal, 'finish', failed)
    else:
        original = stable_controller._review_stable_chatgpt
        async def failed(*args, **kwargs):
            completed = await original(*args, **kwargs)
            if fault == 'review-cancel': raise asyncio.CancelledError()
            return replace(completed, result=replace(completed.result,
                reviewer_verdict=ReviewerVerdict('FAIL', ('Blocking defect',), ())))
        monkeypatch.setattr(stable_controller, '_review_stable_chatgpt', failed)
    if fault == 'review-cancel':
        with pytest.raises(asyncio.CancelledError): asyncio.run(harness.call())
    else:
        result = asyncio.run(harness.call())
        assert result.result.status == 'failed' and not result.ready_for_shipping
    journal = json.loads(next(harness.profile.recovery_root.glob('*/native-run-*.json')).read_text())
    if fault == 'accounting-write': assert journal['implement'] == {'state': 'started', 'final': None}
    elif fault == 'implementation-failed': assert journal['implement']['final']['outcome']['status'] == 'failed'
    elif fault == 'review-cancel': assert journal['review'] == {**journal['review'], 'state': 'started', 'final': None}
    else: assert journal['review']['final']['outcome']['verdict'] == 'FAIL'
    if fault in ('accounting-write', 'implementation-failed'):
        assert git(harness.worktree, 'rev-parse', 'HEAD') == harness.base
        assert not any(call[0] == 'review' for call in harness.calls)
    else: assert git(harness.worktree, 'rev-parse', 'HEAD^') == harness.base
    assert harness.claim.path.exists() and harness.lock.exists()
    assert outcomes.tasks()[0]['state'] == 'needs_decision'
    assert _probe_lock(harness.lock) == 'acquired'


def test_lane_and_controller_have_no_public_activation_switch(harness):
    with pytest.raises(Exception): managed_task.ManagedNativeProfile()
    assert not harness.profile.execution_enabled
    from nightshift.workers.candidate_pipeline import CandidatePipeline
    assert not CandidatePipeline().run().ready_for_shipping
    assert harness.calls == [] and not harness.worktree.exists() and not harness.lock.exists()


_REAL_FETCH_BASE = module._fetch_base


def test_lane_resolves_requested_branch_through_real_local_bare_remote(harness, tmp_path, monkeypatch):
    import subprocess
    (harness.repository/'source.txt').write_text('new upstream base')
    git(harness.repository, 'add', 'source.txt')
    git(harness.repository, 'commit', '-qm', 'new base')
    expected = git(harness.repository, 'rev-parse', 'HEAD')
    git(harness.repository, 'branch', 'lane-base', expected)
    remote = tmp_path/'remote.git'
    subprocess.run(['git', 'clone', '--bare', str(harness.repository), str(remote)],
                   check=True, capture_output=True)
    # Only replace the external transport destination; fetch/import/base
    # resolution and the rest of the lane execute against real local Git.
    actual_git = module._git
    destinations = []
    def local_git(root, *args, **kwargs):
        if args and args[0] == 'fetch' and kwargs.get('https'):
            assert 'https://github.com/example/repo.git' in args
            destinations.append('refs/heads/lane-base:refs/heads/native-base' in args)
            args = tuple(str(remote) if a == 'https://github.com/example/repo.git' else a for a in args)
        return actual_git(root, *args, **kwargs)
    monkeypatch.setattr(module, '_git', local_git)
    monkeypatch.setattr(module, '_fetch_base', _REAL_FETCH_BASE)
    harness.issue = queue.Issue('example/repo', 9, 'Repair', 'Change source\nbase: lane-base')
    result = asyncio.run(harness.call())
    assert result.stage == 'complete' and result.result.status == 'reviewed_pending_human'
    assert destinations == [True]
    assert git(harness.worktree, 'rev-parse', 'HEAD^') == expected
    assert expected != harness.base
    assert not result.ready_for_shipping


def test_every_git_process_runs_only_after_persistent_ownership(harness, monkeypatch):
    import fcntl
    import subprocess
    original = subprocess.Popen
    calls = []
    def checked(argv, *args, **kwargs):
        if Path(argv[0]).name == 'git':
            calls.append(argv)
            with harness.lock.open('rb') as lock:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', checked)
    assert asyncio.run(harness.call()).stage == 'complete'
    assert calls


def test_successful_git_never_signals_reaped_process_group(repository, monkeypatch):
    import time
    def forbidden(*args): pytest.fail('successful Git signaled after reap')
    monkeypatch.setattr(module.os, 'killpg', forbidden)
    assert module._git(repository[0], 'rev-parse', 'HEAD', deadline=time.monotonic() + 5).strip().decode() == repository[1]


def test_timed_out_git_kills_and_reaps_owned_process(repository, monkeypatch):
    import sys
    import time
    original = module.subprocess.Popen
    processes = []
    def silent(argv, **kwargs):
        process = original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(module.subprocess, 'Popen', silent)
    with pytest.raises(ValueError, match='timed out'):
        module._git(repository[0], 'rev-parse', 'HEAD', deadline=time.monotonic() + .05)
    assert len(processes) == 1 and processes[0].returncode is not None


def test_real_lane_intent_and_accounting_order_before_provider_and_commit(harness, monkeypatch):
    from nightshift.workers import stable_controller
    original_implement = stable_pipeline._run_stable_chatgpt
    original_review = stable_controller._review_stable_chatgpt
    original_commit = stable_pipeline.candidate.create
    def current():
        return json.loads(next(harness.profile.recovery_root.glob('*/native-run-*.json')).read_text())
    async def implement(*args, **kwargs):
        assert current()['implement'] == {'state': 'started', 'final': None}
        assert git(harness.worktree, 'rev-parse', 'HEAD') == harness.base
        return await original_implement(*args, **kwargs)
    def commit(*args, **kwargs):
        receipt = current()['implement']['final']
        assert receipt['outcome']['status'] == 'succeeded'
        assert receipt['accounting']['tokens']['input_tokens'] == 7
        return original_commit(*args, **kwargs)
    async def review(*args, **kwargs):
        assert current()['review']['state'] == 'started'
        assert current()['review']['final'] is None
        assert current()['review']['binding']['candidate_sha'] == git(harness.worktree, 'rev-parse', 'HEAD')
        return await original_review(*args, **kwargs)
    monkeypatch.setattr(stable_pipeline, '_run_stable_chatgpt', implement)
    monkeypatch.setattr(stable_pipeline.candidate, 'create', commit)
    monkeypatch.setattr(stable_controller, '_review_stable_chatgpt', review)
    result = asyncio.run(harness.call())
    assert result.result.status == 'reviewed_pending_human'


def test_tombstone_fsync_failure_still_reports_native_retention(harness, monkeypatch):
    import stat
    from nightshift.workers import native_persistence
    original = native_persistence.os.fsync
    def fail_created_lock(fd):
        info = native_persistence.os.fstat(fd)
        if harness.lock.exists() and stat.S_ISREG(info.st_mode) and info.st_ino == harness.lock.stat().st_ino:
            raise OSError('synthetic lock fsync failure')
        return original(fd)
    monkeypatch.setattr(native_persistence.os, 'fsync', fail_created_lock)
    result = asyncio.run(harness.call())
    assert result.status == 'retained' and outcomes.tasks()[0]['native_retained']
    assert harness.lock.exists() and not harness.calls and not harness.worktree.exists()
