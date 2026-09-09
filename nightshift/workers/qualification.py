"""Offline schema inventory; never authorizes native worker execution.

Run with ``python -m nightshift.workers.qualification SCHEMA_DIR --runtime-version VERSION``.
Only explicitly supplied schema files are read. No runtime, credentials, or config is loaded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

_FILES = ("ThreadStartParams.json", "ThreadStartResponse.json", "CommandExecParams.json")
_LIMIT = 4 * 1024 * 1024


def _field(schema: dict, name: str, kind: str) -> str:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return "unknown"
    field = properties.get(name)
    if field is None:
        return "absent"
    if not isinstance(field, dict):
        return "unknown"
    types = field.get("type", [])
    return "present" if kind == types or isinstance(types, list) and kind in types else "unknown"


def inspect_schemas(directory: Path, runtime_version: str) -> dict:
    """Report schema surfaces separately from untested enforcement controls."""
    schemas: dict[str, dict] = {}
    evidence = []
    for name in _FILES:
        source = directory / "v2" / name
        try:
            with source.open("rb") as stream:
                raw = stream.read(_LIMIT + 1)
            if len(raw) > _LIMIT:
                raise ValueError("schema exceeds size limit")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("schema must be an object")
            schemas[name] = parsed
            evidence.append({"file": f"v2/{name}", "sha256": hashlib.sha256(raw).hexdigest()})
        except (OSError, ValueError, RecursionError):
            evidence.append({"file": f"v2/{name}", "error": "missing, unreadable, oversized, or invalid schema"})

    thread = schemas.get("ThreadStartParams.json", {})
    command = schemas.get("CommandExecParams.json", {})
    definitions = command.get("definitions", {})
    policy = definitions.get("SandboxPolicy", {}) if isinstance(definitions, dict) else {}
    variants = policy.get("oneOf", []) if isinstance(policy, dict) else []
    legacy = {}
    for variant in variants if isinstance(variants, list) else []:
        if not isinstance(variant, dict):
            continue
        props = variant.get("properties", {})
        if not isinstance(props, dict):
            continue
        tag = props.get("type", {})
        names = tag.get("enum", []) if isinstance(tag, dict) else []
        if isinstance(names, list) and len(names) == 1 and isinstance(names[0], str):
            legacy[names[0]] = variant
    write = legacy.get("workspaceWrite", {})
    read = legacy.get("readOnly", {})
    expected_read = {"type", "networkAccess"}
    expected_write = {"type", "networkAccess", "writableRoots", "excludeSlashTmp", "excludeTmpdirEnvVar"}
    # Only the exact known legacy shapes justify an absence finding. A changed
    # schema is unknown and requires review, never guessed to provide isolation.
    read_props = read.get("properties", {})
    write_props = write.get("properties", {})
    legacy_reads = "unknown"
    if read_props and write_props and set(read_props) <= expected_read and set(write_props) <= expected_write:
        legacy_reads = "absent"
    surfaces = {
        "thread_named_profile": _field(thread, "permissions", "string"),
        "command_named_profile": _field(command, "permissionProfile", "string"),
        "legacy_writable_roots": _field(write, "writableRoots", "array"),
        "legacy_network_toggle": _field(write, "networkAccess", "boolean"),
        "legacy_restricted_read_policy": legacy_reads,
    }
    return {
        "qualified": False,
        "decision": "blocked",
        "reason": "Schema inspection cannot qualify runtime enforcement; offline and live acceptance evidence is required.",
        "runtime_version": runtime_version,
        "runtime_version_verified": False,
        "evidence": evidence,
        "schema_surfaces": surfaces,
        "enforcement_controls": {name: "unknown" for name in (
            "worktree_only_writes", "git_control_protection", "credential_read_isolation",
            "provider_credential_hidden_from_tools", "tool_network_denial",
            "provider_transport_separation", "inherited_capabilities_disabled",
            "repository_config_cannot_expand_permissions", "child_process_cleanup",
        )},
        "next_step": "Qualify the actual resolved tool policy using synthetic positive and negative controls; otherwise provide external isolation and a host credential broker.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schema_directory", type=Path)
    parser.add_argument("--runtime-version", required=True, help="Operator-supplied version label; not independently verified")
    args = parser.parse_args()
    print(json.dumps(inspect_schemas(args.schema_directory, args.runtime_version), indent=2))
    # This is a failed qualification gate even when the inventory succeeded.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
