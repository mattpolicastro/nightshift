"""Native discovery, CLI pinning and delivery through both forge transports."""

import json
from pathlib import Path
import subprocess

import pytest

from nightshift import specloop_intake as intake
from nightshift.cli import main
from specloop import agents, cli, propose
from test_draft_base import sh
from test_spec_format import commit, repo
from test_speckit_front_door import front_door

pytestmark = pytest.mark.real_trust


@pytest.fixture
def delivery(front_door, monkeypatch):
    e = front_door
    e.format("speckit")
    e.created, e.reviewed = [], []
    create, relabel = e.forge.create_draft_pr, e.forge.relabel_pr
    def creating(*args):
        e.created.append(args)
        return create(*args)
    def reviewing(*args, **kwargs):
        e.reviewed.append(kwargs)
        return relabel(*args, **kwargs)
    monkeypatch.setattr(e.forge, "create_draft_pr", creating)
    monkeypatch.setattr(e.forge, "relabel_pr", reviewing)
    return e


@pytest.mark.scenario("issue-intake/native-existing-feature-selection/existing-feature-directory-is-reused")
def test_dispatch_revises_upstream_feature_at_the_discovered_commit(delivery, tmp_path, monkeypatch, capfd):
    e = delivery
    local = sh(e.source, "rev-parse", "main")
    origin, seed = tmp_path / "origin.git", tmp_path / "seed"
    sh(tmp_path, "clone", "-q", "--bare", str(e.source), str(origin))
    sh(e.source, "remote", "add", "origin", str(origin))
    sh(tmp_path, "clone", "-q", str(origin), str(seed))
    change = "007-add-a-gate"
    root = f"specs/{change}"
    files = {f"{root}/{p.name}": p.read_text().replace("042-add-a-gate", change)
             for p in (Path(__file__).parent / "fixtures/speckit/specs/042-add-a-gate").glob("*.md")}
    pin = commit(seed, files)
    sh(seed, "push", "-q", "origin", "main")
    e.issue.update(title="Changed title", body=f"Revise `{root}/`\n<!-- {root}/ -->")
    e.manifest.write_text(e.manifest.read_text().replace("[run]", '[run]\ngithub = "owner/sample"'))
    monkeypatch.setattr(cli, "notifier", lambda _: lambda message: None)
    monkeypatch.setattr(propose.critic, "open_pr_files", lambda *a: ({}, []))
    agent_calls = []
    def agent(call):
        if call.access == "read":
            return agents.Result("ok", {"contradictions": [], "risks": []})
        agent_calls.append(call)
        assert sh(call.cwd, "rev-parse", "HEAD") == pin
        assert sh(call.cwd, "branch", "--show-current") == f"spec/{change}"
        assert (call.cwd / root / "tasks.md").read_text() == files[f"{root}/tasks.md"]
        (call.cwd / root / "plan.md").write_text("Revised plan\n")
        return agents.Result("ok", {"status": "drafted", "questions": []})
    monkeypatch.setattr(agents, "run", agent)
    def dispatch(args, **kwargs):
        assert args[args.index("--base-commit") + 1] == pin
        commit(seed, {f"{root}/spec.md": "**Status**: Complete\n"})
        sh(seed, "push", "-q", "origin", "main")
        monkeypatch.setattr(propose, "start_commit", lambda *a, **kw: pytest.fail("resolved base twice"))
        code = main(args[1:])
        output = capfd.readouterr()
        return subprocess.CompletedProcess(args, code, output.out, output.err)
    receipt, = e.poll(specloop_runner=dispatch)
    assert not e.errors and receipt["delivered"] and len(agent_calls) == 1
    head = receipt["result"]["commit"]
    assert sh(e.source, "rev-parse", "main") == local
    assert sh(e.source, "rev-parse", head + "^") == pin
    assert sh(e.source, "diff", "--name-only", pin, head) == f"{root}/plan.md"
    assert receipt["change"] == change and e.created[0][1] == f"spec/{change}"


@pytest.mark.scenario("issue-intake/native-existing-feature-selection/ambiguous-or-unavailable-target-refuses")
@pytest.mark.scenario("issue-intake/native-existing-feature-selection/untargeted-native-issue-uses-title")
@pytest.mark.parametrize("kind", ["ambiguous", "missing", "local", "link", "unreadable", "complete", "generated-complete"])
def test_dispatch_refuses_targets_without_alternative_work(delivery, kind):
    e = delivery
    root = e.source / "specs/007-add-a-gate"
    e.issue["body"] = "```\nspecs/007-add-a-gate/\n```"
    if kind == "ambiguous":
        e.issue["body"] += "\n    specs/007-another/"
    elif kind != "missing":
        root.parent.mkdir()
        if kind == "link":
            root.symlink_to("elsewhere", target_is_directory=True)
        else:
            root.mkdir()
            (root / "spec.md").write_bytes(b"\xff" if kind == "unreadable" else b"**Status**: Complete\n")
        if kind != "local":
            commit(e.source, {})
    if kind == "generated-complete":
        e.issue["body"] = "specs/008-other/ and specs/007-add-a-gate/spec.md"
    assert e.poll() == [] and len(e.errors) == 1
    assert isinstance(e.errors[0], intake.DeliveryError)
    assert not e.path.exists() and not e.created
    assert not any(c[0] == "specloop" or "push" in c for c in e.commands)
    assert {l["name"] for l in e.issue["labels"]} == {"spec:draft"}


@pytest.mark.scenario("issue-intake/native-existing-feature-selection/untargeted-native-issue-uses-title")
@pytest.mark.parametrize("gate", ["blocked", "unauthorized", "unhealthy", "pause", "author"])
def test_new_discovery_waits_for_eligibility_gates(delivery, monkeypatch, gate):
    e = delivery
    monkeypatch.setattr(propose, "start_commit", lambda *a, **kw: pytest.fail("discovery before gate"))
    options = {}
    if gate == "blocked":
        e.issue["body"] = "blocked-by: #99"
        monkeypatch.setattr(e.forge, "open_blockers", lambda *a: [99])
    elif gate == "unauthorized":
        events = e.forge.label_event_pages("owner/sample", 7)
        for page in events:
            for event in page:
                event["actor"] = {}
        monkeypatch.setattr(e.forge, "label_event_pages", lambda *a: events)
    elif gate == "unhealthy":
        options["readiness"] = intake.ConfigurationError("unhealthy enrollment")
    elif gate == "author":
        author = e.forge.issue_author
        monkeypatch.setattr(e.forge, "issue_author", lambda *a: {**author(*a), "author_association": "NONE"})
    else:
        def paused(*args):
            raise intake.Paused()
        monkeypatch.setattr(e.forge, "open_blockers", paused)
        with pytest.raises(intake.Paused):
            e.poll()
        return
    e.poll(**options)
    assert not e.commands and not e.created
    assert bool(e.errors) == (gate == "unhealthy")


@pytest.mark.scenario("issue-intake/native-existing-feature-selection/untargeted-native-issue-uses-title")
@pytest.mark.parametrize("operation", ["fetch", "rev-parse", "ls-tree", "blob"])
@pytest.mark.parametrize("failure", ["pause", "timeout", "read"])
def test_discovery_uncertainty_never_dispatches(delivery, operation, failure):
    e = delivery
    commit(e.source, {"specs/007-add-a-gate/spec.md": "**Status**: Draft\n"})
    def runner(args, **kwargs):
        assert kwargs["timeout"] == 30
        if operation in args:
            if failure == "pause":
                raise intake.Paused()
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 30)
            if operation == "fetch":
                return subprocess.CompletedProcess(args, 1)
            raise subprocess.CalledProcessError(128, args)
        return subprocess.run(args, **kwargs)
    if failure == "pause":
        with pytest.raises(intake.Paused):
            e.poll(git_runner=runner)
        assert not e.errors
    elif operation == "fetch" and failure == "read":
        receipt, = e.poll(git_runner=lambda args, **kw: subprocess.CompletedProcess(args, 0, "", "")
                         if "push" in args else runner(args, **kw))
        assert receipt["delivered"] and not e.errors  # Ordinary fetch failure uses local base.
        return
    else:
        assert e.poll(git_runner=runner) == [] and len(e.errors) == 1
        assert not isinstance(e.errors[0], intake.DeliveryError)
    assert not e.path.exists() and not e.created and not e.commands
    assert {l["name"] for l in e.issue["labels"]} == {"spec:draft"}


@pytest.mark.scenario("issue-intake/native-spec-pr-description/spec-pr-body-names-feature-directory")
@pytest.mark.scenario("issue-intake/drafting-from-an-issue/draft-pr-opened")
@pytest.mark.parametrize("format,change", [("speckit", "007-add-a-gate"),
    ("speckit", "sample-issue-7-old"), ("openspec", "owner__sample-issue-7-add-a-gate")])
@pytest.mark.parametrize("interruption", ["push", "comment"])
def test_saved_result_delivery_and_recovered_pr_body(delivery, monkeypatch, format, change, interruption):
    e = delivery
    e.format(format)
    drafting_labels = list(e.issue["labels"])
    result = dict(outcome="drafted", branch=f"refs/specloop/proposals/{change}",
                  commit="b" * 40, risks=["Timing needs a human check."])
    saved = dict(change=change, title="Add a gate", idea="Old idea", result=result)
    intake._save(e.path, saved)
    monkeypatch.setattr(propose, "start_commit", lambda *a, **kw: pytest.fail("rediscovered saved result"))
    e.issue["body"] = "blocked-by: #99\nspecs/007-missing/"
    monkeypatch.setattr(e.forge, "open_blockers", lambda *a: pytest.fail("delivery checked blockers"))
    original = e.forge.create_comment
    def interrupted(*args):
        original(*args)
        raise RuntimeError("comment response lost")
    if interruption == "push":
        e.push_failure = True
    else:
        monkeypatch.setattr(e.forge, "create_comment", interrupted)
    assert e.poll() == [] and len(e.errors) == 1
    assert json.loads(e.path.read_text()) == saved
    e.push_failure = False
    monkeypatch.setattr(e.forge, "create_comment", original)
    e.poll()
    assert len(e.errors) == 1 and len(e.created) == 1
    repository, branch, base, title, body = e.created[0]
    line = f"spec-kit feature `specs/{change}/`\n" if format == "speckit" else ""
    assert (repository, branch, base, title) == ("owner/sample", f"spec/{change}", "main", "Spec: Add a gate")
    assert body == "Closes #7\n" + ("\n" + line if line else "") + "\nFeasibility risks:\n- Timing needs a human check."
    assert e.commands and all(c[-1] == f"{'b' * 40}:refs/heads/spec/{change}" for c in e.commands)
    assert json.loads(e.path.read_text())["result"] == result
    assert e.reviewed[-1] == dict(add=["spec:review"], remove=[])
    assert len(e.issue["comments"]) == 1 and not e.issue["labels"]
    e.issue["labels"] = drafting_labels
    e.poll()  # Exact recovery never creates or rewrites a PR to add attribution.
    assert len(e.created) == 1 and len(e.issue["comments"]) == 1 and not e.issue["labels"]


@pytest.mark.scenario("issue-intake/native-spec-pr-description/spec-pr-body-names-feature-directory")
@pytest.mark.parametrize("change", ["007-add-a-gate", "sample-issue-7-add-a-gate"])
def test_recovery_leaves_existing_pr_description_alone(delivery, change):
    e = delivery
    e.add(change)
    e.prs[-1]["body"] = "Previously reviewed description"
    e.poll()
    assert not e.errors and not e.commands and not e.created
    assert e.prs[-1]["body"] == "Previously reviewed description"
    assert e.reviewed and not e.issue["labels"]


@pytest.mark.scenario("issue-intake/drafting-from-an-issue/questions-posted")
@pytest.mark.scenario("issue-intake/drafting-from-an-issue/unfixable-draft-escalated")
@pytest.mark.scenario("issue-intake/native-existing-feature-selection/untargeted-native-issue-uses-title")
@pytest.mark.parametrize("outcome,field", [("needs_input", "questions"), ("rejected", "problems"),
                                         ("rejected", "contradictions")])
@pytest.mark.parametrize("format", ["speckit", "openspec"])
def test_dispatch_escalates_unsuccessful_drafts(delivery, outcome, field, format):
    e = delivery
    e.format(format)
    e.issue["body"] = "Context: specs/008-other/ and specs/007-old/spec.md"
    def dispatch(args, **kwargs):
        change = "007-add-a-gate" if format == "speckit" else "owner__sample-issue-7-add-a-gate"
        assert args[args.index("--change") + 1] == change
        assert ("--base-commit" in args) == (format == "speckit")
        return subprocess.CompletedProcess(args, 2 if outcome == "needs_input" else 1,
            json.dumps(dict(version=1, outcome=outcome, **{field: ["Resolve the gate behavior"]})), "")
    receipt, = e.poll(specloop_runner=dispatch)
    assert not e.errors and receipt["delivered"] and not e.created
    assert {l["name"] for l in e.issue["labels"]} == {"needs-human"}
    assert "Resolve the gate behavior" in e.issue["comments"][0]["body"]
    assert not any("push" in c for c in e.commands)
