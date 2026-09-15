import io
import subprocess
import tarfile

import pytest

from nightshift.workers import snapshot as s


def archive(name='source', kind=tarfile.REGTYPE, data=b'candidate', **fields):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as tar:
        item = tarfile.TarInfo(name)
        item.type = kind
        item.size = len(data) if kind == tarfile.REGTYPE else 0
        for key, value in fields.items():
            setattr(item, key, value)
        tar.addfile(item, io.BytesIO(data))
    return out.getvalue()


def test_roundtrip_preserves_content_and_executable_mode():
    files = [s.SourceFile('bin/check', b'hello', True), s.SourceFile('README.md', b'docs')]
    assert s.decode(s.encode(files)) == s.validate(files)
    assert s.fingerprint(files) == s.fingerprint(list(reversed(files)))


@pytest.mark.parametrize('path', ['/absolute', '../outside', 'a/../outside', './relative',
    '.git/config', 'nested/.GIT/config', '.codex/config.toml', '.agents/x', 'a\\b', 'a//b'])
def test_unsafe_paths_rejected(path):
    with pytest.raises(ValueError):
        s.decode(archive(path))


@pytest.mark.parametrize('kind', [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE,
                                  tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.DIRTYPE])
def test_links_and_special_entries_rejected(kind):
    with pytest.raises(ValueError):
        s.decode(archive(kind=kind, linkname='/outside'))


@pytest.mark.parametrize('paths', [('same', 'same'), ('Name', 'name'), ('a', 'a/file')])
def test_duplicate_alias_and_parent_conflicts_rejected(paths):
    with pytest.raises(ValueError):
        s.encode([s.SourceFile(p, b'') for p in paths])


def test_content_and_file_count_limits(monkeypatch):
    monkeypatch.setattr(s, 'MAX_CONTENT_BYTES', 2)
    with pytest.raises(ValueError):
        s.decode(archive(data=b'big'))
    monkeypatch.setattr(s, 'MAX_FILES', 0)
    with pytest.raises(ValueError):
        s.decode(archive(data=b''))


def test_setuid_and_truncation_rejected():
    with pytest.raises(ValueError):
        s.decode(archive(mode=0o4755))
    with pytest.raises(ValueError):
        s.decode(archive(data=b'x'*1024)[:600])


def test_committed_git_snapshot_excludes_untracked_and_working_edits(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args]).decode().strip()
    git('init', '-q')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (tmp_path/'source').write_text('committed')
    git('add', 'source'); git('commit', '-qm', 'fixture')
    sha = git('rev-parse', 'HEAD')
    (tmp_path/'source').write_text('uncommitted')
    (tmp_path/'secret').write_text('synthetic')
    assert s.from_git(tmp_path, sha) == [s.SourceFile('source', b'committed')]
    with pytest.raises(ValueError):
        s.from_git(tmp_path, 'HEAD')
    (tmp_path/'link').symlink_to('source')
    git('add', 'link'); git('commit', '-qm', 'link')
    with pytest.raises(ValueError):
        s.from_git(tmp_path, git('rev-parse', 'HEAD'))


@pytest.mark.parametrize('path', ['.', '.git./config', '.codex /config',
    'nested/.g\u200cit/config', 'nested/.co\ufeffdex/config', 'bad\x7fpath'])
def test_portable_runtime_control_aliases_rejected(path):
    with pytest.raises(ValueError):
        s.encode([s.SourceFile(path, b'')])


@pytest.mark.parametrize('paths', [('caf\u00e9', 'cafe\u0301'),
    ('caf\u00e9', 'cafe\u0301/child')])
def test_macos_unicode_alias_and_parent_conflicts_rejected(paths):
    with pytest.raises(ValueError):
        s.encode([s.SourceFile(path, b'') for path in paths])


def test_git_tree_output_is_bounded_before_buffering_all_records(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args]).decode().strip()
    git('init', '-q')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    for number in range(20):
        (tmp_path / str(number)).write_text('x')
    git('add', '.')
    git('commit', '-qm', 'many files')
    sha = git('rev-parse', 'HEAD')
    monkeypatch.setattr(s, 'MAX_FILES', 1)
    with pytest.raises(ValueError, match='Git output exceeds transfer limit'):
        s.from_git(tmp_path, sha)


def docker_archive(entries):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name, kind, data in entries:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.mode = 0o755 if kind == tarfile.DIRTYPE else 0o644
            item.size = len(data) if kind == tarfile.REGTYPE else 0
            tar.addfile(item, io.BytesIO(data))
    return out.getvalue()


def test_materialize_is_fresh_private_and_cleans_up(tmp_path):
    entries = [s.SourceFile('nested/plain', b'plain'), s.SourceFile('check', b'exec', True)]
    with s.materialize(entries, parent=tmp_path) as root:
        private = root.parent
        assert private.stat().st_mode & 0o777 == 0o700
        assert root.stat().st_mode & 0o777 == 0o755
        assert (root/'nested').stat().st_mode & 0o777 == 0o755
        assert (root/'nested/plain').stat().st_mode & 0o777 == 0o644
        assert (root/'check').stat().st_mode & 0o777 == 0o755
        assert (root/'nested/plain').read_bytes() == b'plain'
        with s.materialize(entries, parent=tmp_path) as other:
            assert other != root
    assert not private.exists()


def test_materialize_validates_before_creating_any_directory(tmp_path):
    with pytest.raises(ValueError):
        with s.materialize([s.SourceFile('../escape', b'x')], parent=tmp_path):
            pytest.fail('must not yield unsafe snapshot')
    assert list(tmp_path.iterdir()) == []


def test_materialize_cleans_up_on_caller_error(tmp_path):
    with pytest.raises(RuntimeError):
        with s.materialize([s.SourceFile('file', b'x')], parent=tmp_path) as root:
            private = root.parent
            raise RuntimeError('caller failed')
    assert not private.exists()


def test_container_archive_accepts_only_wrapped_data():
    data = docker_archive([('workspace/', tarfile.DIRTYPE, b''),
        ('workspace/nested/', tarfile.DIRTYPE, b''),
        ('workspace/nested/source', tarfile.REGTYPE, b'candidate')])
    assert s.decode_container_archive(data) == [s.SourceFile('nested/source', b'candidate')]


@pytest.mark.parametrize('entry', [
    ('other/file', tarfile.REGTYPE, b'x'),
    ('workspace/../escape', tarfile.REGTYPE, b'x'),
    ('/workspace/file', tarfile.REGTYPE, b'x'),
    ('workspace/.git/config', tarfile.REGTYPE, b'x'),
    ('workspace/.codex/', tarfile.DIRTYPE, b''),
    ('workspace/link', tarfile.SYMTYPE, b''),
    ('workspace/link', tarfile.LNKTYPE, b''),
    ('workspace/pipe', tarfile.FIFOTYPE, b''),
    ('workspace/device', tarfile.CHRTYPE, b''),
    ('workspace/meta', tarfile.XHDTYPE, b''),
    ('workspace/meta', tarfile.GNUTYPE_LONGNAME, b''),
    ('workspace/meta', tarfile.GNUTYPE_SPARSE, b''),
])
def test_container_archive_rejects_unsafe_entries(entry):
    value = docker_archive([('workspace/', tarfile.DIRTYPE, b''), entry])
    with pytest.raises(ValueError):
        s.decode_container_archive(value)


@pytest.mark.parametrize('entries', [
    [('workspace/', tarfile.DIRTYPE, b'')],
    [('workspace/Dir/', tarfile.DIRTYPE, b''), ('workspace/dir', tarfile.REGTYPE, b'')],
    [('workspace/caf\u00e9/', tarfile.DIRTYPE, b''), ('workspace/cafe\u0301', tarfile.REGTYPE, b'')],
    [('workspace/a', tarfile.REGTYPE, b''), ('workspace/a/b/', tarfile.DIRTYPE, b'')],
    [('workspace/a', tarfile.REGTYPE, b''), ('workspace/a', tarfile.REGTYPE, b'')],
])
def test_container_archive_rejects_duplicate_or_conflicting_paths(entries):
    value = docker_archive([('workspace/', tarfile.DIRTYPE, b''), *entries])
    with pytest.raises(ValueError):
        s.decode_container_archive(value)


def test_container_archive_requires_wrapper_and_clean_trailer():
    with pytest.raises(ValueError):
        s.decode_container_archive(docker_archive([('workspace/file', tarfile.REGTYPE, b'x')]))
    valid = docker_archive([('workspace/', tarfile.DIRTYPE, b'')])
    with pytest.raises(ValueError):
        s.decode_container_archive(valid + b'unparsed payload')
    with pytest.raises(ValueError):
        s.decode_container_archive(valid[:512])


def test_container_archive_bounds_directory_headers_and_payload(monkeypatch):
    monkeypatch.setattr(s, 'MAX_FILES', 0)
    with pytest.raises(ValueError):
        s.decode_container_archive(docker_archive([('workspace/', tarfile.DIRTYPE, b''),
            ('workspace/dir/', tarfile.DIRTYPE, b'')]))
    monkeypatch.setattr(s, 'MAX_FILES', 10)
    monkeypatch.setattr(s, 'MAX_CONTENT_BYTES', 1)
    with pytest.raises(ValueError):
        s.decode_container_archive(docker_archive([('workspace/', tarfile.DIRTYPE, b''),
            ('workspace/file', tarfile.REGTYPE, b'xx')]))


@pytest.mark.parametrize('paths', [('A/one', 'a/two'), ('caf\u00e9/one', 'cafe\u0301/two')])
def test_implicit_directory_spelling_aliases_rejected(paths):
    with pytest.raises(ValueError):
        s.validate([s.SourceFile(path, b'') for path in paths])
    with pytest.raises(ValueError):
        s.decode_container_archive(docker_archive([('workspace/', tarfile.DIRTYPE, b''),
            *[('workspace/' + path, tarfile.REGTYPE, b'') for path in paths]]))


@pytest.mark.parametrize("deadline", [True, float("inf"), float("nan"), "30"])
def test_git_snapshot_rejects_invalid_deadline_before_launch(tmp_path, monkeypatch, deadline):
    monkeypatch.setattr(s.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="finite absolute"):
        s.from_git(tmp_path, "a" * 40, deadline=deadline)


def test_git_snapshot_expired_deadline_never_launches(tmp_path, monkeypatch):
    monkeypatch.setattr(s.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="timed out"):
        s.from_git(tmp_path, "a" * 40, deadline=0)


def test_git_snapshot_deadline_is_shared_across_blob_reads(tmp_path, monkeypatch):
    from types import SimpleNamespace
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args]).decode().strip()
    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    for name in ("one", "two"):
        (tmp_path / name).write_text(name)
    git("add", ".")
    git("commit", "-qm", "fixture")
    sha = git("rev-parse", "HEAD")
    clock = [0.0]
    processes = []
    launch = s.subprocess.Popen
    def popen(*args, **kwargs):
        process = launch(*args, **kwargs)
        processes.append(process)
        clock[0] += 20
        return process
    monkeypatch.setattr(s, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(s.subprocess, "Popen", popen)
    with pytest.raises(ValueError, match="timed out"):
        s.from_git(tmp_path, sha, deadline=50)
    assert len(processes) == 3
    assert all(process.poll() is not None for process in processes)
