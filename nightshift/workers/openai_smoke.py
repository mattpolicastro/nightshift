"""Explicit no-tools Responses API probe, not a coding-worker qualification.

One request, no retries/proxies/redirects; 30-second socket-operation timeout
(not a total wall-clock deadline), 1 MiB response cap. No raw response is logged.
Official schema: https://developers.openai.com/api/reference/cli/resources/responses/methods/create
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shlex
import ssl
import stat
from pathlib import Path

KEY_NAME = "NIGHTSHIFT_OPENAI_API_KEY"
MARKER = "NIGHTSHIFT_OPENAI_OK"
RESPONSE_LIMIT = 1024 * 1024
SOCKET_TIMEOUT = 30
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class CredentialError(ValueError):
    """A safe category, never a credential value or filesystem error string."""


def load_key(*, use_nightshift_env: bool = False) -> str:
    """Read only explicitly selected dedicated credentials, never personal auth."""
    if not use_nightshift_env:
        value = os.environ.get(KEY_NAME, "")
    else:
        path = Path.home() / ".config" / "nightshift" / "env"
        fd = None
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_uid != os.getuid():
                raise CredentialError("credential_file_not_private")
            with os.fdopen(fd, "r", encoding="utf-8") as source:
                fd = None
                data = source.read(65537)
            if len(data) > 65536:
                raise CredentialError("credential_file_too_large")
            values = []
            for line in data.splitlines():
                name, separator, rhs = line.strip().removeprefix("export ").partition("=")
                if separator and name.strip() == KEY_NAME:
                    parsed = shlex.split(rhs, comments=True)
                    if len(parsed) != 1:
                        raise CredentialError("invalid_dedicated_credential")
                    values.append(parsed[0])
            if len(values) != 1:
                raise CredentialError("missing_or_duplicate_dedicated_credential")
            value = values[0]
        except CredentialError:
            raise
        except (OSError, ValueError, UnicodeError):
            raise CredentialError("credential_file_unavailable_or_invalid") from None
        finally:
            if fd is not None:
                os.close(fd)
    if not value or len(value) > 4096 or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise CredentialError("missing_or_invalid_dedicated_credential")
    return value


def _number(value):
    return value if type(value) is int and value >= 0 else None


def _safe_model(value, key):
    return value if isinstance(value, str) and _MODEL.fullmatch(value) and key not in value else None


def _report(model, key):
    return {"status": "protocol_error", "requested_model": _safe_model(model, key),
            "observed_model": None, "http_status": None,
            "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None,
                      "cached_input_tokens": None, "reasoning_output_tokens": None}}


def _parse_response(body, report, key):
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return report
    if not isinstance(value, dict):
        return report
    report["observed_model"] = _safe_model(value.get("model"), key)
    usage = value.get("usage")
    if isinstance(usage, dict):
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            report["usage"][name] = _number(usage.get(name))
        for container, native, name in (("input_tokens_details", "cached_tokens", "cached_input_tokens"),
                                        ("output_tokens_details", "reasoning_tokens", "reasoning_output_tokens")):
            if isinstance(usage.get(container), dict):
                report["usage"][name] = _number(usage[container].get(native))
    if value.get("error") is not None:
        report["status"] = "response_failed"
        return report
    if value.get("status") != "completed":
        report["status"] = "incomplete" if value.get("status") == "incomplete" else "response_failed"
        return report
    output = value.get("output")
    if not isinstance(output, list) or not output:
        return report
    texts = []
    for item in output:
        if not isinstance(item, dict):
            return report
        if item.get("type") == "reasoning":
            continue  # Not a tool call. Reasoning contents are never reported.
        if item.get("type") != "message":
            report["status"] = "unexpected_output"
            return report
        if item.get("role") != "assistant" or item.get("status") != "completed":
            return report
        content = item.get("content")
        if not isinstance(content, list) or not content:
            return report
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                report["status"] = "unexpected_output"
                return report
            texts.append(part["text"])
    report["status"] = "succeeded" if "".join(texts) == MARKER else "marker_mismatch"
    return report


def run(model: str, *, use_nightshift_env: bool = False) -> dict:
    """Make exactly one explicitly requested call with a fixed harmless prompt."""
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        return {"status": "invalid_model", "requested_model": None}
    try:
        key = load_key(use_nightshift_env=use_nightshift_env)
    except CredentialError as exc:
        return {"status": str(exc), "requested_model": model}
    report = _report(model, key)
    if report["requested_model"] is None:
        report["status"] = "invalid_model"
        return report
    payload = json.dumps({"model": model, "input": "Reply with exactly NIGHTSHIFT_OPENAI_OK and nothing else.",
                          "tools": [], "tool_choice": "none", "store": False,
                          "max_output_tokens": 1024}).encode()
    connection = None
    try:
        connection = http.client.HTTPSConnection("api.openai.com", port=443,
                                                timeout=SOCKET_TIMEOUT, context=ssl.create_default_context())
        connection.request("POST", "/v1/responses", body=payload,
                           headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                    "Accept": "application/json", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        report["http_status"] = response.status
        if response.status != 200:
            report["status"] = ("auth_failed" if response.status in (401, 403) else
                                "rate_limited" if response.status == 429 else
                                "redirect_rejected" if 300 <= response.status < 400 else "http_error")
            return report  # Do not read, print, or follow error bodies/locations.
        body = response.read(RESPONSE_LIMIT + 1)
        if len(body) > RESPONSE_LIMIT:
            report["status"] = "response_too_large"
            return report
        return _parse_response(body, report, key)
    except TimeoutError:
        report["status"] = "socket_timeout"
    except (OSError, http.client.HTTPException, ValueError):
        report["status"] = "transport_error"
    finally:
        if connection is not None:
            connection.close()
    return report


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid arguments; use --help.\n")  # Never echo accidental secret arguments.


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--use-nightshift-env", action="store_true",
                        help="Read the dedicated key from private ~/.config/nightshift/env instead of the environment")
    args = parser.parse_args(argv)
    report = run(args.model, use_nightshift_env=args.use_nightshift_env)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
