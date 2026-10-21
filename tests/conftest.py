import pytest

from nightshift import daemon, outcomes, queue, specloop_intake


def pytest_configure(config):
    config.addinivalue_line("markers", "scenario(id): OpenSpec scenario exercised by this test")
    config.addinivalue_line("markers", "real_trust: use the production author-association filter unmodified")


@pytest.fixture(autouse=True)
def trust_fakes_without_associations(request, monkeypatch):
    """GitHub fakes written before the author filter carry no association field.

    Production treats a missing association as untrusted (queue.trusted). Here,
    an item with no association key at all is treated as a collaborator's, and
    the issue-author lookup is stubbed, so those fakes keep testing what they
    were written for. Items that do carry an association are still judged by
    the real rule, and tests marked `real_trust` get production behaviour.
    """
    if request.node.get_closest_marker("real_trust"):
        return
    real = queue.trusted

    def trusted(item, key="authorAssociation"):
        if "authorAssociation" not in item and "author_association" not in item:
            return True
        return real(item, key)

    monkeypatch.setattr(queue, "trusted", trusted)
    monkeypatch.setattr(specloop_intake, "_issue_author_trusted", lambda repo, number, gh: True)


@pytest.fixture(autouse=True)
def isolate_outcome_store(tmp_path_factory, monkeypatch):
    state = tmp_path_factory.mktemp("nightshift-state")
    monkeypatch.setattr(outcomes, "DB_PATH", state / "outcomes.sqlite3")
    # Loop tests must never touch the live service's heartbeat.
    monkeypatch.setattr(daemon, "HEARTBEAT_FILE", state / "heartbeat.json")
    # The daemon's kill switch must also be isolated from the live service.
    monkeypatch.setattr(daemon, "PAUSE_FILE", state / "pause")
