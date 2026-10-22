import json
from dataclasses import asdict
from pathlib import Path

import pytest

from specloop import state


@pytest.fixture
def run_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path)
    return tmp_path / "runs" / "example"


@pytest.fixture(params=["constructor", "for_change"])
def make_run(request, run_directory):
    if request.param == "for_change":
        return lambda **kwargs: state.Run.for_change("example", **kwargs)
    return lambda **kwargs: state.Run(run_directory, **kwargs)


def write_state(directory, raw):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "state.json").write_text(json.dumps(raw))


@pytest.mark.scenario("specloop-interface/strict-existing-state-reads/strict-absence-preserves-admission-defaults")
@pytest.mark.parametrize("directory_exists", [False, True])
def test_missing_state_preserves_admission_defaults(make_run, run_directory, directory_exists):
    if directory_exists:
        run_directory.mkdir(parents=True)
    existing_paths = set(state.ROOT.rglob("*"))

    with pytest.raises(FileNotFoundError):
        make_run(require_existing=True)
    for kwargs in ({}, {"require_existing": False}):
        run = make_run(**kwargs)
        assert run.state == state.RunState(change="example", created=run.state.created)
        assert run.state.created > 0

    assert run_directory.exists() == directory_exists
    assert set(state.ROOT.rglob("*")) == existing_paths


@pytest.mark.scenario("specloop-interface/strict-existing-state-reads/strict-absence-preserves-admission-defaults")
@pytest.mark.parametrize("require_existing", [False, True])
def test_reload_retains_missing_state_policy(make_run, run_directory, require_existing):
    write_state(run_directory, {"change": "example", "status": "done"})
    run = make_run(require_existing=require_existing)
    previous = run.state
    (run_directory / "state.json").unlink()

    if require_existing:
        with pytest.raises(FileNotFoundError):
            run.reload()
        assert run.state is previous
    else:
        run.reload()
        assert run.state == state.RunState(change="example", created=run.state.created)
        assert run.state.created > 0
    assert list(run_directory.iterdir()) == []


@pytest.mark.scenario("specloop-interface/strict-existing-state-reads/corruption-and-forward-compatibility")
@pytest.mark.parametrize("require_existing", [False, True])
@pytest.mark.parametrize("contents,error", [
    pytest.param("{", json.JSONDecodeError, id="malformed-json"),
    pytest.param('{"change":"example","status":"paused","pause":"broken"}', AttributeError, id="pause-string"),
    pytest.param('{"change":"example","status":"paused","pause":[]}', AttributeError, id="pause-list"),
    pytest.param('{"change":"example","status":"paused","pause":false}', AttributeError, id="pause-bool"),
    pytest.param('{"change":"example","status":"paused","pause":{}}', TypeError, id="pause-missing-reason"),
    pytest.param('{"change":"example","status":"running","tasks":[]}', AttributeError, id="tasks-list"),
    pytest.param('{"change":"example","status":"running","tasks":null}', AttributeError, id="tasks-null"),
    pytest.param('{"change":"example","status":"running","tasks":{"t":null}}', AttributeError, id="task-null"),
    pytest.param('{"change":"example","status":"running","tasks":{"t":[]}}', AttributeError, id="task-list"),
])
def test_malformed_state_raises_on_load_and_reload(make_run, run_directory, require_existing, contents, error):
    write_state(run_directory, {"change": "example", "status": "done"})
    run = make_run(require_existing=require_existing)
    previous = run.state
    path = run_directory / "state.json"
    path.write_text(contents)

    with pytest.raises(error):
        make_run(require_existing=require_existing)
    with pytest.raises(error):
        run.reload()

    assert run.state is previous
    assert path.read_text() == contents
    assert list(run_directory.iterdir()) == [path]


@pytest.mark.scenario("specloop-interface/strict-existing-state-reads/corruption-and-forward-compatibility")
@pytest.mark.parametrize("require_existing", [False, True])
@pytest.mark.parametrize("state_exists", [False, True])
def test_permission_failure_propagates(make_run, run_directory, monkeypatch, require_existing, state_exists):
    write_state(run_directory, {"change": "example", "status": "done"})
    run = make_run(require_existing=require_existing)
    previous = run.state
    path = run_directory / "state.json"
    contents = path.read_bytes()
    if not state_exists:
        path.unlink()
    read_text = Path.read_text
    failure = PermissionError("state read denied")

    def deny_state_read(self, *args, **kwargs):
        if self == path:
            raise failure
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_state_read)
    with pytest.raises(PermissionError) as loading:
        make_run(require_existing=require_existing)
    with pytest.raises(PermissionError) as reloading:
        run.reload()

    assert loading.value is reloading.value is failure
    assert run.state is previous
    assert list(run_directory.iterdir()) == ([path] if state_exists else [])
    if state_exists:
        assert path.read_bytes() == contents


@pytest.mark.scenario("specloop-interface/strict-existing-state-reads/corruption-and-forward-compatibility")
@pytest.mark.parametrize("require_existing", [False, True])
def test_unknown_fields_are_ignored_on_load_and_reload(make_run, run_directory, require_existing):
    expected = state.RunState(
        change="example", status="paused", created=123, pr="https://example.test/pull/1",
        pause=state.Pause(reason="stuck", task="t", detail="review needed", resume_at=456),
        tasks={"t": state.TaskState(attempts=2, status="stuck", arbitrated=1)},
    )
    raw = asdict(expected)
    raw["future_run_field"] = {"anything": [1, None]}
    raw["pause"]["future_pause_field"] = True
    raw["tasks"]["t"]["future_task_field"] = 42
    write_state(run_directory, raw)
    path = run_directory / "state.json"
    contents = path.read_bytes()

    run = make_run(require_existing=require_existing)
    assert run.state == expected
    run.state.pr = "in-memory edit"
    run.reload()
    assert run.state == expected
    assert path.read_bytes() == contents
    assert list(run_directory.iterdir()) == [path]
