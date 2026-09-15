"""Private, one-use stdio attachment to an owned credential-free Session.

Not native worker dispatch or an authentication boundary for the provider.
The caller must create a fresh provider home containing only reviewed configuration;
never pass an interactive Codex profile. This module checks directory ownership
and permissions, not freshness or configuration contents. A production launcher
factory must enforce that stronger contract.
The caller owns the provider process and must stop/reap it before quiesce().
Only the host owner launches Docker; the provider launcher can connect once to
an already configured local bridge. No Docker socket is exposed to tool code.
"""
from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .container_session import Session, SessionError, ENVIRONMENT

PINNED_VERSION = 'codex-cli 0.153.4'
EXECUTABLE = '/opt/codex/bin/codex'
# The launcher has no Docker arguments, credentials, or recovery privileges.
_LAUNCHER = '''import os, socket, sys, threading
connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.connect(sys.argv[1])
def upload():
    try:
        while True:
            chunk = os.read(0, 65536)
            if not chunk:
                connection.shutdown(socket.SHUT_WR)
                return
            connection.sendall(chunk)
    except OSError:
        pass
threading.Thread(target=upload, daemon=True).start()
try:
    while True:
        chunk = connection.recv(65536)
        if not chunk:
            break
        while chunk:
            count = os.write(1, chunk)
            chunk = chunk[count:]
finally:
    connection.close()
'''


def _private_write(path: Path, data: str):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


class NativeExecutor:
    def __init__(self, session: Session, provider_home: Path, *,
                 max_transport_bytes: int = 64 * 1024 * 1024, stderr_limit: int = 16384):
        if type(max_transport_bytes) is not int or max_transport_bytes <= 0:
            raise ValueError('Positive transport byte limit required')
        if type(stderr_limit) is not int or stderr_limit < 0:
            raise ValueError('Nonnegative stderr byte limit required')
        self.session, self.provider_home = session, Path(provider_home)
        self.max_transport_bytes, self.stderr_limit = max_transport_bytes, stderr_limit
        self._spawn_lock = threading.Lock()
        self._stop = threading.Event()
        self._done = threading.Event()
        self._thread = None
        self._process = None
        self._listener = None
        self._connection = None
        self._temporary = None
        self._error = None
        self._entered = False
        self._connected = False
        self.quiesced = False
        self.transport_bytes = 0
        self.stderr_bytes = 0

    def __enter__(self):
        if self._entered:
            raise SessionError('Executor attachment cannot be reused')
        self._entered = True
        if not self.session._entered or self.session.closed or self.session._poisoned:
            raise SessionError('An active owned session is required')
        info = self.provider_home.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SessionError('Provider home must be a private owned directory')
        docker = shutil.which('docker', path=self.session._env['PATH'])
        if docker is None or not os.path.isabs(docker):
            raise SessionError('An absolute Docker executable is required')
        self._docker = docker
        self.session._healthy()
        if self.session._processes() != self.session._baseline_processes:
            raise SessionError('Executor has preexisting operations')
        version = self.session._call(self._argv()[1:-3] + ['--version'],
                                     stdout_limit=256, stderr_limit=16384)
        if (version.status != 'completed' or version.returncode != 0
                or version.stdout.strip() != PINNED_VERSION.encode()):
            raise SessionError('Pinned executor version could not be confirmed')
        try:
            # A short private path avoids AF_UNIX path length truncation.
            self._temporary = tempfile.TemporaryDirectory(prefix='ns-exec-')
            self.socket_path = Path(self._temporary.name) / 'bridge.sock'
            self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._listener.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            self._listener.listen(1)
            self._listener.settimeout(0.05)
            launcher = self.provider_home / 'nightshift-executor-launcher.py'
            _private_write(launcher, _LAUNCHER)
            configuration = ('default="remote"\ninclude_local=false\n[[environments]]\n'
                'id="remote"\nprogram=' + json.dumps(sys.executable) + '\nargs=' +
                json.dumps([str(launcher), str(self.socket_path)]) + '\ninitialize_timeout_sec=5\n')
            _private_write(self.provider_home / 'environments.toml', configuration)
            self.session._healthy()
            if self.session._processes() != self.session._baseline_processes:
                raise SessionError("Executor has preexisting operations")
            self._thread = threading.Thread(target=self._serve, daemon=True, name='nightshift-owned-executor')
            self._thread.start()
            return self
        except BaseException:
            self.cancel()
            raise

    def _argv(self):
        environment = dict(ENVIRONMENT)
        environment.update(PATH='/opt/codex/bin:/opt/codex/codex-path:' + ENVIRONMENT['PATH'],
                           CODEX_HOME='/tmp/codex', SHELL='/bin/sh')
        return [self._docker, 'exec', '-i', '--workdir', '/workspace', '--', self.session.name,
                '/usr/bin/env', '-i', *[k + '=' + v for k, v in environment.items()],
                EXECUTABLE, 'exec-server', '--listen', 'stdio']

    def _serve(self):
        try:
            while not self._stop.is_set() and time.monotonic() < self.session.deadline:
                try:
                    self._connection, _ = self._listener.accept()
                    break
                except socket.timeout:
                    continue
            if self._connection is None:
                raise SessionError('Executor attachment was not established')
            self._connected = True
            self._listener.close()  # One connection; no restart or local fallback.
            with self._spawn_lock:
                if self._stop.is_set():
                    raise SessionError("Executor attachment cancelled")
                self._process = subprocess.Popen(self._argv(), env=dict(self.session._env),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True)
            self._pump()
            while self._process.poll() is None:
                if self._stop.wait(0.02) or time.monotonic() >= self.session.deadline:
                    raise SessionError('Executor server exit deadline exhausted')
            if self._process.returncode != 0:
                raise SessionError('Executor server did not exit successfully')
        except BaseException as exc:
            # Never retain or report raw stderr/protocol content, which may be sensitive.
            self._error = str(exc) if isinstance(exc, SessionError) else type(exc).__name__
            self.session._poisoned = True
        finally:
            try:
                self._kill()
            except BaseException:
                self._error = 'Owned executor process cleanup could not be confirmed'
                self.session._poisoned = True
            finally:
                try:
                    if self._connection is not None:
                        self._connection.close()
                    if self._listener is not None:
                        self._listener.close()
                finally:
                    self._done.set()

    def _pump(self):
        process, connection = self._process, self._connection
        connection.setblocking(False)
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        pending_in, pending_out = bytearray(), bytearray()
        socket_eof = False
        pipe_eof = set()
        while True:
            if self._stop.is_set() or time.monotonic() >= self.session.deadline:
                raise SessionError('Executor transport cancelled or timed out')
            if socket_eof and not pending_in and not process.stdin.closed:
                process.stdin.close()
            if len(pipe_eof) == 2 and not pending_out:
                return
            with selectors.DefaultSelector() as selector:
                events = (0 if socket_eof or len(pending_in) >= 65536 else selectors.EVENT_READ)
                if pending_out:
                    events |= selectors.EVENT_WRITE
                if events:
                    selector.register(connection, events, 'socket')
                if pending_in and not process.stdin.closed:
                    selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
                if 'stdout' not in pipe_eof and len(pending_out) < 65536:
                    selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
                if 'stderr' not in pipe_eof:
                    selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
                for key, events in selector.select(0.05):
                    name = key.data
                    try:
                        if name == 'socket':
                            if events & selectors.EVENT_READ:
                                data = connection.recv(65536 - len(pending_in))
                                if not data:
                                    socket_eof = True
                                pending_in.extend(data)
                                self.transport_bytes += len(data)
                            if events & selectors.EVENT_WRITE:
                                sent = connection.send(pending_out)
                                del pending_out[:sent]
                        elif name == 'stdin':
                            sent = os.write(key.fd, pending_in)
                            del pending_in[:sent]
                        else:
                            data = os.read(key.fd, 65536 - len(pending_out) if name == 'stdout' else 65536)
                            if not data:
                                pipe_eof.add(name)
                            elif name == 'stdout':
                                pending_out.extend(data)
                                self.transport_bytes += len(data)
                            else:
                                self.stderr_bytes += len(data)
                        if self.transport_bytes > self.max_transport_bytes or self.stderr_bytes > self.stderr_limit:
                            raise SessionError('Executor transport byte budget exhausted')
                    except BlockingIOError:
                        continue

    def _kill(self):
        if self._process is None:
            return
        try:
            os.killpg(self._process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            if self._process.poll() is None:
                self._process.kill()
        try:
            self._process.wait(timeout=1)
        finally:
            for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
                if not stream.closed:
                    stream.close()

    def quiesce(self):
        """Require provider shutdown, clean server exit and no leftover tool jobs."""
        if self.quiesced:
            return
        if self._thread is None:
            raise SessionError('Executor attachment was not started')
        self._thread.join(timeout=max(0, self.session.deadline - time.monotonic()))
        if self._thread.is_alive():
            self.cancel()
            raise SessionError('Executor transport failed to quiesce before deadline')
        if self._error or not self._connected:
            raise SessionError('Executor transport failed; candidate is ineligible: ' + (self._error or 'not connected'))
        try:
            self.session._healthy()
            if self.session._processes() != self.session._baseline_processes:
                raise SessionError('Executor left unfinished tool operations')
        except BaseException:
            self.session._poisoned = True
            raise
        self.quiesced = True
        self._dispose()

    def _dispose(self):
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    def cancel(self):
        with self._spawn_lock:
            self._stop.set()
        self.session._poisoned = True
        if self._thread is not None:
            self._thread.join(timeout=1)
            if self._thread.is_alive():
                raise SessionError("Owned executor transport thread has not stopped")
        self._dispose()

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc is None:
                self.quiesce()
            else:
                self.cancel()
        finally:
            if self._thread is None or not self._thread.is_alive():
                self._dispose()
        return False
