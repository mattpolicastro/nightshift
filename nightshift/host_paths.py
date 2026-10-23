"""Literal host paths from trusted process settings, resolved only when consumed."""

import os
from collections.abc import Mapping
from pathlib import Path


def host_path(name: str, default: str, env: Mapping[str, str] | None = None,
              home: Path | None = None) -> str:
    """Expand only leading ~/; do not probe, normalize or create the location."""
    value = (os.environ if env is None else env).get(name, default)
    if value.startswith("~/"):
        value = str(Path.home() if home is None else home) + value[1:]
    if (not value.strip() or not value.startswith("/")
            or any(c in value for c in "\0\n\r")
            or (name in ("NIGHTSHIFT_BIN_DIR", "NIGHTSHIFT_NODE_BIN") and ":" in value)):
        raise ValueError(f"invalid host path setting: {name}")
    return value


def tool_path(system_dirs: str = "/usr/bin:/bin:/usr/sbin:/sbin",
              env: Mapping[str, str] | None = None, home: Path | None = None) -> str:
    """Replace the host-tool prefix without inheriting arbitrary PATH entries."""
    return host_path("NIGHTSHIFT_BIN_DIR", "/opt/homebrew/bin", env, home) + ":" + system_dirs
