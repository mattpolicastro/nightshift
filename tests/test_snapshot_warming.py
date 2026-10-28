import json
import os
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from specloop import manifest, verify
from test_specloop_seal_verify import M_DATA, sh

SUCCESS = 'cat "$1/lock" > "$3/dependency"; printf "%s\\n" "$1" "$2" > "$3/origin"; chmod 755 "$3/dependency"'


@pytest.fixture
def warming(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    sh(repo, "git", "init", "-q")
    def commit(script=SUCCESS, lock="base"):
        (repo / "warm.sh").write_text(script)
        (repo / "lock").write_text(lock)
        sh(repo, "git", "add", ".")
        sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "fixture")
        return sh(repo, "git", "rev-parse", "HEAD")
    m = manifest.parse({**M_DATA, "snapshot": [{"id": "deps", "lockfiles": ["lock"],
        "warm": ["/bin/sh", "{src}/warm.sh", "{src}", "{sha}", "{snapshot:deps}"]}],
        "prepare": [{"id": "ordinary", "run": ["true"]}]})
    events = []
    context = verify.SnapshotContext(tmp_path / "snapshots", lambda kind, **data: events.append((kind, data)))
    monkeypatch.setattr(verify, "wrap", lambda argv, sb: argv)  # Portable execution, not sandbox evidence.
    return repo, commit, m, context, events


@pytest.mark.scenario("verify-preparation/trusted-base-warming/candidate-changes-cannot-warm")
@pytest.mark.scenario("verify-preparation/trusted-base-warming/matching-base-reuses-completed-work")
@pytest.mark.scenario("verify-preparation/immutable-snapshot-publication/warm-is-confined-and-payload-is-read-only")
def test_snapshot_warms_only_on_base_commit(warming, tmp_path):
    repo, commit, m, context, events = warming
    base = commit()
    candidate = commit("exit 99", "candidate")
    integrated = commit("exit 98", "integrated")
    verify.warm_snapshots(repo, base, m, context)
    entry, metadata = verify._snapshot_entries(context.root)[0]
    assert (entry / "payload/dependency").read_text() == "base"
    origin = (entry / "payload/origin").read_text().splitlines()
    assert Path(origin[0]).name == "src" and origin[1] == base
    assert metadata["base_sha"] == base and metadata["sequence"] == 1
    unrelated = commit("exit 97", "base")
    verify.warm_snapshots(repo, unrelated, m, verify.SnapshotContext(context.root, context.event))
    assert [kind for kind, _ in events] == ["snapshot_warmed", "snapshot_reused"]
    for sha in (candidate, integrated):
        receipts = verify.run_steps(repo, sha, m.verify, m, tmp_path / sha)
        assert [r.id for r in receipts] == ["ordinary", "t"] and all(r.passed for r in receipts)
    assert len(events) == 2  # Ordinary verification never calls warm.
    verify.warm_snapshots(repo, commit(SUCCESS, "next-base"), m, context)
    assert sorted(d["sequence"] for _, d in verify._snapshot_entries(context.root)) == [1, 2]
    assert all(not p.stat().st_mode & 0o222 for p in verify._payload_paths(entry))
    assert (entry / "payload/dependency").stat().st_mode & 0o111 == 0o111
    assert [r["id"] for r in json.loads(next((context.root / "warms").glob("*/receipts.json")).read_text())] == ["warm"]


@pytest.mark.scenario("verify-preparation/immutable-snapshot-publication/failed-or-interrupted-warm-is-not-reusable")
@pytest.mark.parametrize("bad", ['mkdir "$3/nested"; touch "$3/nested/file"; chmod 000 "$3/nested" "$3"; exit 3',
    "kill -9 $$", 'ln -s "$1" "$3/link"',
    'mkfifo "$3/fifo"', 'touch "$3/a"; ln "$3/a" "$3/b"', 'rmdir "$3"; ln -s "$1" "$3"', "interrupt", "finalize", "published"])
def test_failed_payload_and_interrupted_publication(warming, monkeypatch, bad):
    repo, commit, m, context, events = warming
    base = commit()
    verify.warm_snapshots(repo, base, m, context)
    sha = commit(SUCCESS if bad in ("interrupt", "finalize", "published") else bad, "new")
    rename = Path.rename
    def interrupted(path, target):
        if path.name == "entry":
            if bad == "finalize":
                rename(path, target)  # Crash after rename, before read-only finalization.
            raise KeyboardInterrupt("interrupted publication")
        return rename(path, target)
    def published(kind, **data):
        events.append((kind, data))
        if kind == "snapshot_warmed":
            raise KeyboardInterrupt("interrupted after complete publication")
    with monkeypatch.context() as patch:
        if bad in ("interrupt", "finalize", "published"):
            if bad == "published":
                patch.setattr(context, "event", published)
            else:
                patch.setattr(Path, "rename", interrupted)
            with pytest.raises(KeyboardInterrupt):
                verify.warm_snapshots(repo, sha, m, context)
        else:
            verify.warm_snapshots(repo, sha, m, context)
    assert events[-1][0] == "snapshot_warm_failed"
    assert (Path(events[-1][1]["diagnostics"]) / "failure.txt").exists()
    assert len(verify._snapshot_entries(context.root)) == (2 if bad == "published" else 1)
    abandoned = set(context.root.glob(".staging-*"))
    verify.warm_snapshots(repo, sha if bad == "published" else base, m, context)
    assert events[-1][0] == "snapshot_reused" and not any(p.exists() for p in abandoned)
    verify.warm_snapshots(repo, commit(SUCCESS, "retry"), m, context)
    assert len(verify._snapshot_entries(context.root)) == (3 if bad == "published" else 2)


@pytest.mark.scenario("verify-preparation/immutable-snapshot-publication/failed-or-interrupted-warm-is-not-reusable")
def test_invalid_completed_metadata_is_not_reused(warming):
    repo, commit, m, context, events = warming
    sha = commit()
    verify.warm_snapshots(repo, sha, m, context)
    entry, _ = verify._snapshot_entries(context.root)[0]
    metadata = entry / "metadata.json"
    metadata.chmod(0o644)
    metadata.write_text('{}')
    metadata.chmod(0o444)
    verify.warm_snapshots(repo, sha, m, context)
    assert events[-1][0] == "snapshot_warmed" and verify._snapshot_entries(context.root)[0][1]["sequence"] == 2


@pytest.mark.scenario("verify-preparation/immutable-snapshot-publication/failed-or-interrupted-warm-is-not-reusable")
@pytest.mark.parametrize("interrupt", [False, True])
def test_warm_timeout_and_process_cleanup(warming, monkeypatch, interrupt):
    repo, commit, m, context, events = warming
    sha = commit('sleep 30 & echo $! > "$3/child"; wait')
    wait, killpg = subprocess.Popen.wait, os.killpg
    killed, children = [], []
    def short_wait(process, timeout=None):
        if timeout == 600:
            children.append(process)
            if interrupt:
                raise KeyboardInterrupt("interrupted wait")
            timeout = 0.1
        return wait(process, timeout=timeout)
    monkeypatch.setattr(subprocess.Popen, "wait", short_wait)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: (killed.append((pid, sig)), killpg(pid, sig))[-1])
    if interrupt:
        with pytest.raises(KeyboardInterrupt):
            verify.warm_snapshots(repo, sha, m, context)
    else:
        verify.warm_snapshots(repo, sha, m, context)
        receipt = json.loads((Path(events[-1][1]["diagnostics"]) / "receipts.json").read_text())[0]
        assert receipt["exit"] is None and not receipt["passed"]
    assert killed and killed[-1][1] == signal.SIGKILL and not verify._snapshot_entries(context.root)
    assert children and all(p.returncode == -signal.SIGKILL for p in children)


@pytest.mark.scenario("verify-preparation/immutable-snapshot-publication/warm-is-confined-and-payload-is-read-only")
def test_warm_sandbox_and_stored_payload(warming, monkeypatch, tmp_path):
    if sys.platform != "darwin" or verify.already_sandboxed():
        pytest.skip("real sandbox enforcement requires macOS outside an enclosing sandbox")
    monkeypatch.setattr(verify, "wrap", lambda argv, sb: ["/usr/bin/sandbox-exec", "-f", str(sb), *argv])
    repo, commit, m, context, events = warming
    secret = tmp_path / "credential"
    secret.write_text("synthetic credential")
    m = replace(m, deny_read=(str(secret),), writable=(str(tmp_path),))
    script = 'chmod u+w "$1" && exit 91; touch "$1/new" && exit 92; '
    script += f'cat "{secret}" && exit 93; touch "{tmp_path}/outside" && exit 94; ' + SUCCESS
    sha = commit(script)
    verify.warm_snapshots(repo, sha, m, context)
    assert events[-1][0] == "snapshot_warmed"
    entry, _ = verify._snapshot_entries(context.root)[0]
    for prepare in (False, True):
        sb = tmp_path / "probe.sb"
        sb.write_text(verify.profile(tmp_path / "build", m, prepare=prepare, snapshots=context))
        for argv in (["/bin/chmod", "u+w", str(entry / "payload/dependency")],
                     ["/usr/bin/touch", str(entry / "payload/new")], ["/bin/rm", "-rf", str(entry)]):
            assert subprocess.run(verify.wrap(argv, sb), capture_output=True).returncode != 0
    assert not (tmp_path / "outside").exists()
    probe = ('chmod u+w "$1/dependency" && exit 91; touch "$1/new" && exit 92; '
             'rm -rf "$1" && exit 93; touch "$2/private"')
    argv = ("/bin/sh", "-c", probe, "sh", str(entry / "payload"), "{snapshot:deps}")
    m = replace(m, prepare=(manifest.Step("prepare-probe", argv, 0, 10),),
                verify=(manifest.Step("verify-probe", argv, 0, 10),))
    receipts = verify.run_steps(repo, sha, m.verify, m, tmp_path / "consumer", snapshots=context)
    assert verify.ok(receipts, m.prepare + m.verify), verify.summary(receipts)
    assert (entry / "payload/dependency").read_text() == "base"
