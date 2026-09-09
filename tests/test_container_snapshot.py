"""Opt-in synthetic Docker transfer tests; never mount an existing repository.

Set NIGHTSHIFT_TEST_CODEX_EXEC_IMAGE (immutable image with /bin/sh and /bin/tar)
and NIGHTSHIFT_TEST_DOCKER_HOST (local unix socket). Normal CI skips these tests.
"""
import os
import re
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from nightshift.workers import candidate
from nightshift.workers import snapshot as s

IMAGE = os.environ.get('NIGHTSHIFT_TEST_CODEX_EXEC_IMAGE', '')
HOST = os.environ.get('NIGHTSHIFT_TEST_DOCKER_HOST', '')
pytestmark = pytest.mark.skipif(not IMAGE or not HOST, reason='requires explicit local Docker qualification opt-in')


@pytest.fixture
def docker(tmp_path):
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', IMAGE)
    assert HOST.startswith('unix:///')
    env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(tmp_path),
           'DOCKER_CONFIG': str(tmp_path), 'DOCKER_HOST': HOST}
    def call(*args, data=None, check=True):
        # Inputs and output are small fixed synthetic fixtures. Production
        # transfer needs a streaming byte limit, not this fixture capture.
        result = subprocess.run(['docker', *args], input=data, env=env,
                                capture_output=True, timeout=15)
        if check and result.returncode:
            pytest.fail(result.stderr.decode(errors='replace')[:2048])
        return result
    return call


@contextmanager
def container(docker, *, source=None, volume=False):
    name = 'nightshift-transfer-test-' + uuid.uuid4().hex
    store = name + '-source'
    created = False
    volume_created = False
    try:
        if volume:
            docker('volume', 'create', '--label', 'nightshift.synthetic=true',
                   '--driver', 'local', '--opt', 'type=tmpfs', '--opt', 'device=tmpfs',
                   '--opt', 'o=size=64m,uid=1000,gid=1000,mode=0700', store)
            volume_created = True
        args = ['create', '--name', name, '--label', 'nightshift.synthetic=true',
                '--pull', 'never', '--network', 'none', '--user', '1000:1000',
                '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--pids-limit', '64', '--memory', '128m', '--cpus', '1',
                '--log-driver', 'none', '--init', '--no-healthcheck',
                '--tmpfs', '/tmp:rw,nosuid,nodev,size=16m,uid=1000,gid=1000']
        if volume:
            args += ['--mount', 'type=volume,src=' + store + ',dst=/workspace,volume-nocopy']
        elif source is not None:
            args += ['--mount', 'type=bind,src=' + str(source) + ',dst=/workspace,readonly']
        args += ['--entrypoint', '/bin/sh', IMAGE, '-c', 'sleep 300']
        docker(*args)
        created = True
        docker('start', name)
        yield name
    finally:
        if created:
            docker('rm', '--force', name)
        if volume_created:
            docker('volume', 'rm', store)


def test_paused_export_matches_complete_candidate(docker):
    initial = [s.SourceFile('nested/source', b'initial'), s.SourceFile('README', b'fixture')]
    with container(docker, volume=True) as name:
        docker('exec', '-i', name, '/bin/tar', '-xf', '-', '-C', '/workspace', data=s.encode(initial))
        docker('exec', name, '/bin/sh', '-c',
               'printf changed > /workspace/nested/source; printf added > /workspace/nested/new')
        docker('pause', name)
        data = docker('cp', name + ':/workspace', '-').stdout
        expected = [s.SourceFile('nested/source', b'changed'), s.SourceFile('README', b'fixture'),
                    s.SourceFile('nested/new', b'added')]
        assert s.decode_container_archive(data) == s.validate(expected)


def test_materialized_reviewer_source_is_immutable(docker):
    initial = [s.SourceFile('nested/source', b'candidate'), s.SourceFile('README', b'fixture')]
    # Create only synthetic files under a caller-selected VM-shared parent.
    parent = Path(os.environ.get('NIGHTSHIFT_TEST_SNAPSHOT_PARENT', str(Path.cwd())))
    with s.materialize(initial, parent=parent) as source, container(docker, source=source) as name:
        docker('exec', name, '/bin/sh', '-c',
               'test "$(cat /workspace/nested/source)" = candidate; printf scratch > /tmp/check')
        for command in ('printf changed > /workspace/nested/source',
                        'rm /workspace/nested/source',
                        'mv /workspace/nested/source /workspace/moved',
                        'chmod 777 /workspace/nested/source',
                        'ln /workspace/nested/source /workspace/hardlink'):
            assert docker('exec', name, '/bin/sh', '-c', command, check=False).returncode != 0
        assert (source/'nested/source').read_bytes() == b'candidate'
        assert sorted(str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()) == ['README', 'nested/source']


def test_candidate_roundtrip_commits_and_verifies_exact_snapshot(docker, tmp_path):
    """Join the primitives without invoking a provider or dispatching a task."""
    repository = tmp_path / "repository"
    repository.mkdir()
    env = {"PATH": os.defpath, "HOME": str(tmp_path),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "Synthetic fixture", "GIT_COMMITTER_NAME": "Synthetic fixture",
           "GIT_AUTHOR_EMAIL": "fixture@localhost", "GIT_COMMITTER_EMAIL": "fixture@localhost"}

    def git(*args):
        return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull,
                               "-c", "commit.gpgSign=false", *args], cwd=repository,
                              env=env, capture_output=True, text=True,
                              check=True, timeout=10).stdout.strip()

    git("init", "--quiet")
    (repository / "answer").write_bytes(b"41\n")
    (repository / "verify.sh").write_text(
        '#!/bin/sh\nset -eu\ntest "$(cat /workspace/answer)" = 42\n')
    git("add", "--", "answer", "verify.sh")
    git("commit", "--quiet", "-m", "Synthetic baseline")
    base = git("rev-parse", "HEAD")
    initial = s.from_git(repository, base)
    with container(docker, volume=True) as name:
        docker("exec", "-i", name, "/bin/tar", "-xf", "-", "-C", "/workspace", data=s.encode(initial))
        # The known-bad baseline must fail the same verification command.
        assert docker("exec", name, "/bin/sh", "/workspace/verify.sh", check=False).returncode == 1
        docker("exec", name, "/bin/sh", "-c", "printf '42\\n' > /workspace/answer")
        docker("pause", name)
        returned = s.decode_container_archive(docker("cp", name + ":/workspace", "-").stdout)
    committed = candidate.create(repository, base, returned)
    assert committed.base_sha == base
    assert committed.changed_paths == ("answer",)
    exact = s.from_git(repository, committed.candidate_sha)
    assert exact == returned
    assert git("rev-parse", "HEAD") == committed.candidate_sha
    assert git("rev-parse", "HEAD^") == base

    parent = Path(os.environ.get("NIGHTSHIFT_TEST_SNAPSHOT_PARENT", str(Path.cwd())))
    with s.materialize(exact, parent=parent) as source, container(docker, source=source) as name:
        # Verify only the host-committed source, using no host script execution.
        docker("exec", name, "/bin/sh", "/workspace/verify.sh")
        assert docker("exec", name, "/bin/sh", "-c",
                      "printf tampered > /workspace/answer", check=False).returncode != 0
        docker("exec", name, "/bin/sh", "-c", "test ! -e /workspace/.git")
        assert (source / "answer").read_bytes() == b"42\n"
    assert s.from_git(repository, committed.candidate_sha) == exact
    assert git("status", "--porcelain") == ""
