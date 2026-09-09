"""Private native preparation is durable; restart never guesses work disposable."""
import json
from pathlib import Path

import pytest

from nightshift import config, daemon, queue, recovery


@pytest.fixture
def claim(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, 'CLAIM_DIR', tmp_path / 'claims')
    value = queue.Claim('example/repo', 7, 'candidate/7', str(tmp_path/'worktree'), 'fixture')
    Path(value.worktree).mkdir()
    (Path(value.worktree)/'candidate').write_text('retained')
    value.write()
    return value


def prepare(claim, tmp_path):
    claim._prepare_native('a'*32, tmp_path/'owned-recovery')


def test_legacy_claim_loads_without_native_field(claim):
    data = json.loads(claim.path.read_text())
    data.pop('native_recovery')
    claim.path.write_text(json.dumps(data))
    records, repairs = queue._load_claims(claim.repo)
    assert records[7].native_recovery is None
    assert not repairs


def test_native_marker_is_synced_before_hypothetical_launch(claim, tmp_path, monkeypatch):
    events = []
    fsync = queue.os.fsync
    def synced(fd):
        events.append('fsync')
        fsync(fd)
    monkeypatch.setattr(queue.os, 'fsync', synced)
    prepare(claim, tmp_path)
    events.append('launch permitted only after preparation')
    assert events == ['fsync', 'fsync', 'launch permitted only after preparation']
    loaded, _ = queue._load_claims(claim.repo)
    assert loaded[7].native_recovery == {'version': 1, 'run_id': 'a'*32,
                                        'recovery_dir': str(tmp_path/'owned-recovery')}
    assert claim.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match='already prepared'):
        prepare(claim, tmp_path)


@pytest.mark.parametrize('failure_at', [1, 2])
def test_persistence_failure_prevents_launch_and_keeps_ambiguous_marker(claim, tmp_path, monkeypatch, failure_at):
    calls = 0
    launched = False
    def fail(fd):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError('simulated disk failure')
    monkeypatch.setattr(queue.os, 'fsync', fail)
    with pytest.raises(OSError):
        prepare(claim, tmp_path)
        launched = True
    assert not launched
    assert claim.native_recovery is not None
    loaded, _ = queue._load_claims(claim.repo)
    assert (loaded[7].native_recovery is not None) == (failure_at == 2)
    assert not list(claim.path.parent.glob('*.tmp'))


@pytest.mark.parametrize('working,exists', [(True, True), (True, False), (False, True), (False, False)])
def test_reconcile_retains_marker_regardless_labels_or_worktree(claim, tmp_path, monkeypatch, working, exists):
    prepare(claim, tmp_path)
    monkeypatch.setattr(queue, '_list', lambda *a, **k:
                        [queue.Issue(claim.repo, 7, 'title', '')] if working else [])
    def forbidden(*a, **k): pytest.fail('native state must not release, clear or probe labels')
    monkeypatch.setattr(queue, 'release', forbidden)
    monkeypatch.setattr(queue, 'labels_of', forbidden)
    repairs = queue.reconcile(claim.repo, worktree_exists=lambda p: exists)
    assert [(r.repair, r.number) for r in repairs] == [(queue.Repair.RETAINED, 7)]
    assert claim.path.exists()


@pytest.mark.parametrize('committed,pushed,pr', [(False, False, None), (True, False, None),
                                               (True, True, 'https://example.invalid/pr/7')])
def test_native_recovery_precedes_git_and_existing_pr(committed, pushed, pr):
    plan = recovery.decide('shipping', has_commits=committed, branch_pushed=pushed,
                           pr_url=pr, native_pending=True)
    assert plan.action is recovery.Action.RETAIN


@pytest.mark.parametrize('marker', [{}, {'version': 99}, {'cleanup_pending': True}, False])
def test_daemon_retains_even_unknown_marker_before_probes(claim, monkeypatch, marker):
    claim.native_recovery = marker
    claim.write()
    def forbidden(*a, **k): pytest.fail('native recovery must not probe or remove Git/GitHub state')
    for name in ('has_commits', 'pr_for_branch', 'branch_on_remote', 'remove_worktree', 'open_pr'):
        monkeypatch.setattr(daemon.vcs, name, forbidden)
    for name in ('release', 'complete', 'escalate'):
        monkeypatch.setattr(queue, name, forbidden)
    monkeypatch.setattr(daemon.outcomes, 'record', lambda *a, **k: None)
    repo = config.Repo(name=claim.repo, verify='true')
    daemon._recover(config.Config(repos=[repo]), repo, Path(claim.worktree).parent, 7)
    assert claim.path.exists()
    assert (Path(claim.worktree)/'candidate').read_text() == 'retained'


def test_failed_attention_write_does_not_escape_retention(claim, tmp_path, monkeypatch):
    prepare(claim, tmp_path)
    def fail(*a, **k): raise OSError('attention database unavailable')
    monkeypatch.setattr(daemon.outcomes, 'record', fail)
    monkeypatch.setattr(daemon.vcs, 'has_commits', lambda *a: pytest.fail('Git probe'))
    repo = config.Repo(name=claim.repo, verify='true')
    daemon._recover(config.Config(repos=[repo]), repo, tmp_path, 7)
    assert claim.path.exists()
