"""Label event selection preserves the evidence needed for actor authorization."""

import json

import pytest

from nightshift import queue, specloop_intake
from nightshift.config import Repo
from test_specloop_approval import ApprovalGh, Commands


pytestmark = pytest.mark.real_trust


def label_event(event_id=1, *, label="spec:approved", event="labeled", actor="owner"):
    return {"id": event_id, "event": event, "label": {"name": label},
            "actor": {"login": actor}, "created_at": "2026-10-01T00:00:00Z"}


class EventHistory:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        assert args == ["api", "repos/owner/sample/issues/8/events", "--paginate", "--slurp"]
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.scenario("issue-intake/latest-label-actor-authorization/latest-application-wins")
@pytest.mark.parametrize("label", ["spec:draft", "agent:ready", "spec:approved", "agent:revise", "custom:trigger"])
def test_latest_application_retains_new_actor_across_pages(label):
    trusted = label_event(1, label=label)
    removed = label_event(2, label=label, event="unlabeled")
    untrusted = label_event(3, label=label, actor="outsider")
    gh = EventHistory(json.dumps([
        [trusted], [removed], [],
        [untrusted, label_event(4, label="unrelated"), {"id": 5, "event": "commented"}],
    ]))

    selected = specloop_intake._latest_label_events("owner/sample", 8, {label}, runner=gh)

    # The actual principal and receipt ID come from the same newer event, even
    # though the older event was applied by the repository owner.
    assert selected == {label: untrusted}
    assert selected[label]["actor"]["login"] == "outsider"
    assert len(gh.calls) == 1


def test_selects_each_requested_label_and_preserves_id_only_interfaces():
    draft = label_event(1, label="spec:draft")
    ready = label_event(2, label="agent:ready")
    approval = label_event(4)
    gh = EventHistory(json.dumps([
        [draft, label_event(3)],
        [ready, label_event(5, event="unlabeled"), approval],
    ]))
    labels = {"spec:draft", "agent:ready", "spec:approved"}

    assert specloop_intake._latest_label_events("owner/sample", 8, labels, runner=gh) == {
        "spec:draft": draft, "agent:ready": ready, "spec:approved": approval,
    }
    # Chronological page order controls selection, not sorting by event IDs.
    ids = specloop_intake._label_events("owner/sample", 8, labels, runner=gh)
    assert ids == {"spec:draft": 1, "agent:ready": 2, "spec:approved": 4}
    assert all(type(event_id) is int for event_id in ids.values())
    assert specloop_intake._approval_event("owner/sample", 8, "spec:approved", runner=gh) == 4
    assert len(gh.calls) == 3


INVALID_IDS = [None, False, True, 0, -1, 1.5, 1.0, "2", "", [], {}]


@pytest.mark.scenario("issue-intake/actor-relationship-resolution/lookup-failure-defers-action")
@pytest.mark.parametrize("event_id", INVALID_IDS)
@pytest.mark.parametrize("interface", ["_latest_label_events", "_label_events", "_approval_event"])
def test_invalid_latest_id_never_falls_back_to_an_older_application(event_id, interface):
    gh = EventHistory(json.dumps([[label_event()], [label_event(event_id)]]))
    label = "spec:approved" if interface == "_approval_event" else {"spec:approved"}
    with pytest.raises(RuntimeError, match="could not confirm spec:approved event for #8"):
        getattr(specloop_intake, interface)("owner/sample", 8, label, runner=gh)


def failure_responses():
    missing_id = label_event()
    del missing_id["id"]
    return [
        pytest.param(queue.GhError("connection reset", transient=True), id="transport"),
        pytest.param(queue.GhError("HTTP 401: unauthorized", transient=False), id="unauthorized"),
        pytest.param(queue.GhError("HTTP 403: forbidden", transient=False), id="forbidden"),
        pytest.param(queue.GhError("HTTP 429: rate limit", transient=True), id="rate-limit"),
        pytest.param("not json", id="invalid-json"),
        pytest.param("null", id="null-history"),
        pytest.param("{}", id="object-history"),
        pytest.param(json.dumps([[label_event()], {}]), id="malformed-later-page"),
        pytest.param(json.dumps([[label_event()], [None]]), id="malformed-later-event"),
        pytest.param(json.dumps([[label_event()], [{"event": "labeled", "label": None}]]),
                     id="malformed-label"),
        pytest.param(json.dumps([[label_event()], [{"event": "labeled", "label": {"name": []}}]]),
                     id="malformed-label-name"),
        pytest.param("[]", id="empty-history"),
        pytest.param(json.dumps([[label_event(label="unrelated")]]), id="missing-label"),
        pytest.param(json.dumps([[label_event()], [missing_id]]), id="missing-latest-id"),
        pytest.param(json.dumps([[label_event()], [label_event(2, event="unlabeled")]]),
                     id="latest-is-unlabel"),
        *[pytest.param(json.dumps([[label_event()], [label_event(event_id)]]),
                       id=f"invalid-id-{event_id!r}") for event_id in INVALID_IDS],
    ]


@pytest.mark.scenario("issue-intake/actor-relationship-resolution/lookup-failure-defers-action")
@pytest.mark.parametrize("response", failure_responses())
def test_event_lookup_failure_preserves_admission_state_for_retry(tmp_path, response):
    manifest = tmp_path / "manifest.toml"
    manifest.write_text('[run]\nrepo = "/unused/source"\n')
    repo = Repo("owner/sample", engine="specloop", manifest=manifest)
    gh, commands = ApprovalGh(), Commands()
    history = EventHistory(response)
    labels_before = gh.labels.copy()
    saved = {manifest: manifest.read_bytes()}
    for stage, receipt in [
        ("approvals", {"event": 1, "head": "previous-head"}),
        ("admissions", {"approval_event": 1, "result": {"outcome": "refused"}}),
    ]:
        path = tmp_path / stage / "owner__sample/8.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(receipt))
        saved[path] = path.read_bytes()

    def runner(args):
        if args[0] == "api" and args[1].endswith("/events"):
            return history(args)
        return gh(args)

    errors = []
    for _ in range(2):
        assert specloop_intake.admit_pending(
            repo, queue.Labels(), tmp_path, runner=runner,
            specloop_runner=commands, git_runner=commands, launcher=commands,
            on_error=lambda number, exc: errors.append((number, exc)),
        ) == []

    assert len(errors) == 2
    assert all(number == 8 and isinstance(exc, (RuntimeError, ValueError)) for number, exc in errors)
    if isinstance(response, Exception):
        assert all(exc is response for _, exc in errors)
    assert not commands.calls
    assert not gh.comments
    assert gh.labels == labels_before
    assert all(args[:2] in (["pr", "list"], ["pr", "view"]) for args in gh.calls)
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == saved
