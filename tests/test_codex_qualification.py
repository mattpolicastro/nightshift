import json
from pathlib import Path

from nightshift.workers.qualification import inspect_schemas


def schemas(tmp_path: Path) -> Path:
    v2 = tmp_path / "v2"
    v2.mkdir()
    (v2 / "ThreadStartParams.json").write_text(json.dumps({"properties": {"permissions": {"type": ["string", "null"]}}}))
    (v2 / "ThreadStartResponse.json").write_text("{}")
    (v2 / "CommandExecParams.json").write_text(json.dumps({
        "properties": {"permissionProfile": {"type": ["string", "null"]}},
        "definitions": {"SandboxPolicy": {"oneOf": [
            {"properties": {"type": {"enum": ["readOnly"]}, "networkAccess": {"type": "boolean"}}},
            {"properties": {"type": {"enum": ["workspaceWrite"]}, "networkAccess": {"type": "boolean"}, "writableRoots": {"type": "array"}}},
        ]}},
    }))
    return tmp_path


def test_inventory_never_qualifies_runtime(tmp_path):
    report = inspect_schemas(schemas(tmp_path), "synthetic-1")
    assert report["qualified"] is False
    assert report["decision"] == "blocked"
    assert report["runtime_version_verified"] is False
    assert set(report["enforcement_controls"].values()) == {"unknown"}
    assert report["schema_surfaces"]["thread_named_profile"] == "present"
    assert report["schema_surfaces"]["legacy_writable_roots"] == "present"
    assert report["schema_surfaces"]["legacy_restricted_read_policy"] == "absent"
    assert all(len(item["sha256"]) == 64 for item in report["evidence"])


def test_missing_and_corrupt_schema_cannot_qualify(tmp_path):
    (tmp_path / "v2").mkdir()
    (tmp_path / "v2/CommandExecParams.json").write_text("not json")
    report = inspect_schemas(tmp_path, "unknown")
    assert report["qualified"] is False
    assert set(report["schema_surfaces"].values()) == {"unknown"}
    assert all("error" in item for item in report["evidence"])


def test_changed_policy_shape_requires_review(tmp_path):
    schemas(tmp_path)
    path = tmp_path / "v2/CommandExecParams.json"
    value = json.loads(path.read_text())
    value["definitions"]["SandboxPolicy"]["oneOf"][0]["properties"]["newReadPolicy"] = {}
    path.write_text(json.dumps(value))
    report = inspect_schemas(tmp_path, "future")
    assert report["schema_surfaces"]["legacy_restricted_read_policy"] == "unknown"
    assert report["qualified"] is False


def test_nonexperimental_schema_records_missing_selection(tmp_path):
    schemas(tmp_path)
    path = tmp_path / "v2/ThreadStartParams.json"
    path.write_text('{"properties": {}}')
    report = inspect_schemas(tmp_path, "synthetic")
    assert report["schema_surfaces"]["thread_named_profile"] == "absent"
    assert report["enforcement_controls"]["credential_read_isolation"] == "unknown"


def test_cli_is_a_blocked_gate_even_for_known_schema(tmp_path, monkeypatch, capsys):
    from nightshift.workers.qualification import main

    schemas(tmp_path)
    monkeypatch.setattr("sys.argv", ["qualification", str(tmp_path), "--runtime-version", "synthetic"])
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["qualified"] is False
