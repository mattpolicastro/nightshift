import pytest

from nightshift import daemon, outcomes


@pytest.fixture(autouse=True)
def isolate_outcome_store(tmp_path_factory, monkeypatch):
    state = tmp_path_factory.mktemp("nightshift-state")
    monkeypatch.setattr(outcomes, "DB_PATH", state / "outcomes.sqlite3")
    # Loop tests must never touch the live service's heartbeat.
    monkeypatch.setattr(daemon, "HEARTBEAT_FILE", state / "heartbeat.json")
