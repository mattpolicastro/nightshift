"""Real candidate CLI/wrapper exits, followed by disk-only intake recovery."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import specloop_job as jobs
from specloop import seal, tasks
from specloop.state import Run
from test_revision_feedback_consumption import read, receipt_path
from test_revision_feedback_recovery import restart, tick
from test_revision_job_identity import stopped
from test_specloop_build_revision import setup as build_setup
from test_specloop_command_json import revision_run, template
from test_specloop_compaction import snapshot
from test_specloop_spec_revision import BRANCH, CHANGE

pytestmark = pytest.mark.real_trust
SOURCE = Path(__file__).resolve().parents[1]
PROVENANCE = """
import os, sys
from pathlib import Path
from specloop import cli, revise, state
from nightshift import specloop_job as jobs
root = Path(os.environ['CANDIDATE_SOURCE'])
for module in (cli, revise, state, jobs):
    assert Path(module.__file__).resolve() == root.joinpath(*module.__name__.split('.')).with_suffix('.py')
assert Path(sys.executable) == Path(os.environ['CANDIDATE_PYTHON'])
assert state.ROOT.resolve() == Path(os.environ['HOME']) / '.specloop'
assert state.Run.for_change(os.environ['CHANGE']).dir.resolve() == Path(os.environ['RUN_DIR'])
"""
CRASH = PROVENANCE + """
from specloop import seal
from specloop.revision_store import RevisionStore
boundary = os.environ['CRASH_BOUNDARY']
owner, name = {'prepared': (RevisionStore, 'prepare'), 'ref': (seal, 'git')}[boundary]
original = getattr(owner, name)
def interrupt(*args, **kwargs):
    result = original(*args, **kwargs)
    if boundary != 'ref' or args[1] == 'update-ref':
        os._exit(86)
    return result
setattr(owner, name, interrupt)
sys.exit(cli.main(sys.argv[1:]))
"""
WRAPPER = PROVENANCE + """
call = jobs.specloop_client.call
def bound_call(args, *, runner):
    def candidate(argv, **kwargs):
        assert argv[0] == 'specloop'
        prefix = ([sys.executable, os.environ['CRASH_SCRIPT']] if os.environ['CRASH_BOUNDARY'] in ('prepared', 'ref')
                  else [sys.executable, '-m', 'specloop.cli'])
        result = runner(prefix + argv[1:], **kwargs)
        if result.returncode == 86:
            os._exit(86)  # The wrapper also dies, leaving no result.json.
        return result
    result = call(args, runner=candidate)
    if os.environ['CRASH_BOUNDARY'] == 'completed':
        assert result['outcome'] == 'revised' and result['replayed'] is False
        os._exit(86)  # Completed CLI result decoded, but not persisted by the wrapper.
    return result
jobs.specloop_client.call = bound_call
jobs.main()
"""


@pytest.fixture
def process_case(build_setup, tmp_path, monkeypatch, request):
    # Reuse admission and intake fixtures with the same admitted change identity.
    import test_specloop_command_json as fixture
    assert Path(jobs.__file__).resolve() == SOURCE / 'nightshift/specloop_job.py'
    monkeypatch.setattr(fixture, 'CHANGE', CHANGE)
    monkeypatch.setattr(fixture, 'BRANCH', BRANCH)
    admitted, _ = request.getfixturevalue('revision_run')
    home = tmp_path / 'child-home'
    directory = home / '.specloop' / 'runs' / CHANGE
    directory.parent.mkdir(parents=True)
    shutil.move(str(admitted.dir), directory)
    sentinel = tmp_path / 'bin'
    sentinel.mkdir()
    hit = sentinel / 'used'
    executable = sentinel / 'specloop'
    executable.write_text('#!/bin/sh\ntouch "$(dirname "$0")/used"\nexit 97\n')
    executable.chmod(0o755)
    crash = tmp_path / 'crash.py'
    crash.write_text(CRASH)
    env = {**os.environ, 'HOME': str(home), 'PYTHONPATH': str(SOURCE),
           'PATH': str(sentinel) + os.pathsep + os.environ['PATH'],
           'CANDIDATE_SOURCE': str(SOURCE), 'CANDIDATE_PYTHON': sys.executable,
           'CHANGE': CHANGE, 'RUN_DIR': str(directory), 'CRASH_SCRIPT': str(crash)}
    assert shutil.which('specloop', path=env['PATH']) == str(executable)
    monkeypatch.setattr(jobs, '_children', {})
    launched = []

    def poll(boundary=''):
        def launch(argv, **kwargs):
            child = subprocess.Popen([sys.executable, '-c', WRAPPER, *argv[-2:]],
                                     cwd=SOURCE, env={**env, 'CRASH_BOUNDARY': boundary}, **kwargs)
            launched.append(Path(argv[-2]))
            try:
                assert child.wait(timeout=30) == (86 if boundary else 0), launched[-1].joinpath('log').read_text()
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
            return child
        jobs._children.clear()  # Restart has only persisted attempt evidence.
        return tick(build_setup, tmp_path, module=restart(), background=True, launcher=launch)

    def cli(args):
        return subprocess.run([sys.executable, '-m', 'specloop.cli', *args, '--json'],
                              cwd=SOURCE, env=env, capture_output=True, text=True, timeout=30)

    yield SimpleNamespace(run=Run(directory), repo=directory / 'repo.git', poll=poll,
                          cli=cli, launched=launched, gh=build_setup[1])
    assert not hit.exists(), 'PATH specloop was executed instead of the candidate'


def journal(case):
    path, = (case.run.dir / 'revision-rounds').glob('*.json')
    return json.loads(path.read_text())


def run_snapshot(case):
    return {p: data for p, data in snapshot(case.run.dir).items() if p != case.run.dir / 'lock'}


def assert_round(case, commit, blocks, count=1):
    text = seal.git(case.repo, 'show', f'{commit}:openspec/changes/{CHANGE}/tasks.md').decode()
    parsed = tasks.parse(text)
    assert text.count('Review round ') == count and len(parsed) == 1 + count * len(blocks)
    assert [json.loads(t.text.split(': ', 1)[1]) for t in parsed[-len(blocks):]] == blocks


@pytest.mark.scenario('specloop-interface/idempotent-revision-recovery/retry-after-commit-does-not-duplicate-round')
@pytest.mark.scenario('specloop-interface/idempotent-revision-recovery/prepared-candidate-resumes-once')
@pytest.mark.scenario('specloop-interface/idempotent-revision-recovery/later-control-state-takes-precedence')
@pytest.mark.scenario('background-job-control/revision-attempt-provenance/crashed-attempt-detaches-normally')
@pytest.mark.scenario('pr-revision/stable-build-round-identity/ordinary-retry-keeps-the-snapshot')
@pytest.mark.parametrize('expired', [False, True])
@pytest.mark.parametrize('boundary,later', [('prepared', None), ('ref', None), ('completed', None),
    ('ref', 'paused'), ('ref', 'aborted'), ('completed', 'done'), ('ref', 'event_only')])
def test_revise_retry_after_commit_does_not_duplicate_round(
    process_case, tmp_path, monkeypatch, boundary, later, expired,
):
    c = process_case
    c.poll(boundary)
    original, record, old = read(tmp_path), journal(c), c.launched[0]
    assert not (old / 'result.json').exists()
    request = json.loads((old / 'request.json').read_text())
    source = Path(request['args'][3])
    retained = source.read_bytes()
    assert source.is_relative_to(tmp_path) and json.loads(retained) == original['feedback']
    head = seal.git(c.repo, 'rev-parse', BRANCH).decode().strip()
    assert head == record['parent' if boundary == 'prepared' else 'commit']
    assert record['status'] == ('completed' if boundary == 'completed' else 'prepared')
    assert_round(c, record['commit'], original['feedback'])
    if boundary == 'prepared':
        before = run_snapshot(c)
        with c.run.lock() as mine:
            assert mine
            busy = c.cli(request['args'])
        assert busy.returncode == 1 and 'already being driven' in json.loads(busy.stdout)['reasons'][0]
        assert run_snapshot(c) == before
    c.run.reload()
    if later == 'paused':
        c.run.pause('stuck', detail='Later human decision')
    elif later in ('aborted', 'done'):
        c.run.state.status, c.run.state.pause = later, None
        c.run.save()
    elif later == 'event_only':
        c.run.event('control', detail='Later decision without a state change')
    if later:
        c.run.request_pause()
    if later == 'done':
        tree = seal.git(c.repo, 'mktree', input=b'').decode().strip()
        head = seal.git(c.repo, 'commit-tree', tree, '-p', head, '-m', 'archive', env=seal.IDENTITY).decode().strip()
        seal.git(c.repo, 'update-ref', f'refs/heads/{BRANCH}', head, record['commit'])
    state_before = (c.run.dir / 'state.json').read_bytes()
    history = snapshot(old)
    with monkeypatch.context() as patch:
        patch.setattr(jobs.time, 'time', lambda: request['deadline_at'] + (1 if expired else -1))
        with pytest.raises(RuntimeError, match='exited without a result'):
            c.poll()
    assert read(tmp_path) == {k: v for k, v in original.items() if k != 'job'}
    assert snapshot(old) == history and not c.gh.comments
    c.gh.inline[0]['body'] = 'Edited after the crash'
    c.gh.inline.append({**c.gh.inline[0], 'id': 4, 'body': 'Later feedback'})
    c.gh.label_event('agent:revise', 'unlabeled')
    c.gh.label_event('agent:revise', 'labeled')
    c.poll()
    retried = read(tmp_path)
    successor = c.launched[1]
    assert retried == {**original, 'job': successor.name, 'round_job': successor.name}
    assert successor != old and snapshot(old) == history
    retry_request = json.loads((successor / 'request.json').read_text())
    assert retry_request['round_id'] == request['round_id'] == original['round_id']
    retry_source = Path(retry_request['args'][3])
    assert retry_source != source and retry_source.read_bytes() == source.read_bytes() == retained
    c.poll()
    saved = read(tmp_path)
    expected_status = c.run.state.status if later else 'running'
    expected_pause = asdict(c.run.state.pause) if later and c.run.state.pause else None
    assert saved['result'] == dict(version=1, outcome='revised', commit=record['commit'],
        round_id=original['round_id'], replayed=True, status=expected_status, pause=expected_pause)
    assert saved['delivered'] and saved['consumed_feedback_ids'] == sorted(
        {*original['consumed_feedback_ids'], *original['feedback_ids']})
    assert seal.git(c.repo, 'rev-parse', BRANCH).decode().strip() == (record['commit'] if boundary == 'prepared' else head)
    if later:
        assert (c.run.dir / 'state.json').read_bytes() == state_before and c.run.pause_requested()
    else:
        expected_state = json.loads(state_before)
        expected_state.update(status='running', pause=None)
        assert json.loads((c.run.dir / 'state.json').read_text()) == expected_state
    events = [json.loads(line) for line in (c.run.dir / 'events.jsonl').read_text().splitlines()]
    assert sum(e['event'] == 'revised' and e.get('round_id') == original['round_id'] for e in events) == 1
    assert_round(c, record['commit'], original['feedback'])
    assert len(c.gh.comments) == 1 and record['commit'] in c.gh.comments[0]['body']
    assert c.gh.comments[0]['body'].endswith(f"<!-- nightshift:revise:{CHANGE}:{original['round_id']} -->")
    assert 'agent:revise' in c.gh.labels  # Old delivery cannot remove the newer application.
    before = run_snapshot(c)
    replay = c.cli(retry_request['args'])
    assert replay.returncode == 0 and json.loads(replay.stdout) == saved['result']
    assert run_snapshot(c) == before and len(c.gh.comments) == 1


@pytest.mark.scenario('pr-revision/stable-build-round-identity/replacement-selection-creates-a-new-identity')
@pytest.mark.scenario('pr-revision/keyed-result-acknowledgement/refusal-and-replacement-have-distinct-comments')
@pytest.mark.parametrize('replacement', ['release', 'refusal'])
def test_explicit_replacement_has_a_distinct_key(process_case, tmp_path, replacement):
    c = process_case
    jobs.save(receipt_path(tmp_path), {**read(tmp_path), 'future': {'keep': [1, 2]}})
    if replacement == 'refusal':
        c.run.pause('stuck')
        c.poll()
        c.gh.fail_comment = True
        with pytest.raises(RuntimeError, match='response lost'):
            c.poll()
        c.poll()
    else:
        c.poll('completed')
        stopped(c.launched[0])
        before = read(tmp_path)
        c.poll()
        assert read(tmp_path) == before and len(c.launched) == 1 and not c.gh.comments
    original, old = read(tmp_path), c.launched[0]
    if replacement == 'release':
        args = json.loads((old / 'request.json').read_text())['args']
        stopped_result = json.loads(c.cli(args).stdout)
        jobs.finalize(old, {'result': stopped_result})  # Late success must stay cancelled.
        c.poll()
        assert read(tmp_path) == original and not c.gh.comments and len(c.launched) == 1
        assert jobs.release_job(tmp_path, old.name) == 'released'
    else:
        c.gh.label_event('needs-human', 'unlabeled')
        c.run.pause('human_checks')
    history = snapshot(old)
    c.poll()
    selected = read(tmp_path)
    assert selected['round_id'] != original['round_id'] and selected['event'] == original['event']
    assert selected['feedback'] == original['feedback'] and selected['future'] == original['future']
    assert selected['consumed_feedback_ids'] == original['consumed_feedback_ids']
    inputs = [Path(json.loads((d / 'request.json').read_text())['args'][3]) for d in c.launched]
    assert inputs[0] != inputs[1] and inputs[0].read_bytes() == inputs[1].read_bytes()
    c.gh.fail_comment = True
    with pytest.raises(RuntimeError, match='response lost'):
        c.poll()
    for _ in range(3):
        c.poll()
    saved = read(tmp_path)
    assert saved['delivered'] and not c.gh.labels and len(c.launched) == 2
    assert saved['consumed_feedback_ids'] == sorted({*original['consumed_feedback_ids'], *original['feedback_ids']})
    assert saved['result']['round_id'] == selected['round_id'] and not saved['result']['replayed']
    assert_round(c, saved['result']['commit'], original['feedback'], 2 if replacement == 'release' else 1)
    if replacement == 'release':
        assert snapshot(old) == history and not (old / 'collected.json').exists()
        assert saved['result']['commit'] != stopped_result['commit']
        assert json.loads((old / 'stop.json').read_text())['state'] == 'stopped'
    assert len(c.gh.comments) == (1 if replacement == 'release' else 2)
    for receipt, comment in zip(([original] if replacement == 'refusal' else []) + [saved], c.gh.comments):
        marker = f"<!-- nightshift:revise:{CHANGE}:{receipt['round_id']} -->"
        assert comment['body'].endswith(marker) and sum(marker in item['body'] for item in c.gh.comments) == 1
    assert saved['result']['commit'] in c.gh.comments[-1]['body']
    if replacement == 'refusal':
        assert saved['result']['commit'] not in c.gh.comments[0]['body']
