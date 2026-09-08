"""Slack webhook. Silent no-op when unconfigured, so nothing depends on it."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def send(webhook: str, text: str) -> bool:
    """Best effort. A notification failure must never fail a task."""
    if not webhook:
        return False
    try:
        req = urllib.request.Request(
            webhook,
            data=json.dumps({"text": text}).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False
