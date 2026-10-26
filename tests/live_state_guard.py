"""Pre-access protection for live state, also loaded by child sitecustomize."""

import os
import stat
import sys
import threading


def install(roots):
    if hasattr(os, "_nightshift_test_guard_roots"):
        return  # A child pytest process already loaded its sitecustomize guard.
    roots = tuple(os.path.abspath(p) for p in roots)
    os._nightshift_test_guard_roots = roots
    resolving = threading.local()
    original_readlink = os.readlink

    def descriptor_path(fd):
        # Resolve the descriptor itself, without opening or listing its target.
        mode = os.fstat(fd).st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            return None  # Pipes, sockets and devices have no state-tree path.
        if sys.platform == "darwin":
            import fcntl

            return os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024).split(b"\0", 1)[0])
        if sys.platform.startswith("linux"):
            return original_readlink(f"/proc/self/fd/{fd}")
        raise AssertionError("cannot guard descriptor-relative filesystem access on this platform")

    def lexical(path):
        if isinstance(path, (str, bytes, os.PathLike)):
            path = os.path.abspath(os.fsdecode(path))
            if any(path == root or path.startswith(root + os.sep) for root in roots):
                raise AssertionError(f"test attempted live state access: {path}")
            return path

    def check(path, dir_fd=None):
        if isinstance(path, int):
            path = descriptor_path(path)
        elif isinstance(path, (str, bytes, os.PathLike)) and not os.path.isabs(path):
            # Audit events use -1 for the default cwd, not an actual descriptor.
            if dir_fd is not None and dir_fd >= 0:
                parent = descriptor_path(dir_fd)
                if parent is not None:
                    path = os.path.join(parent, os.fsdecode(path))
        path = lexical(path)
        if path is not None and not getattr(resolving, "active", False):
            # lstat/readlink below check each component before realpath follows it.
            resolving.active = True
            try:
                lexical(os.path.realpath(path))
            finally:
                resolving.active = False

    # The open audit event omits dir_fd, so os.open must be checked here too.
    for name in ("stat", "lstat", "readlink", "access", "open"):
        original = getattr(os, name)

        def guarded(path, *args, _original=original, **kwargs):
            check(path, kwargs.get("dir_fd"))
            return _original(path, *args, **kwargs)

        setattr(os, name, guarded)

    def audit(event, args):
        if event in ("open", "os.listdir", "os.scandir", "os.chdir"):
            check(args[0])
        elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.chown", "os.utime"):
            check(args[0], args[-1])
        elif event in ("os.rename", "os.link"):
            check(args[0], args[2])
            check(args[1], args[3])
        elif event == "os.symlink":
            check(args[0])
            check(args[1], args[2])

    sys.addaudithook(audit)
