from dataclasses import replace
import json
from pathlib import Path

import pytest

from nightshift.cli import main
from specloop import agents, cli, manifest, propose, spec_format, state
from test_specloop_command_json import TASKS, template
from test_specloop_propose import M, draft_evidence_snapshot
import test_specloop_command_json as commands
import test_spec_format as compatibility
from test_spec_format import service, admitted


def isolated_proposals(template, monkeypatch, capfd, repositories, reverse=False):
    path, _ = template
    original = path.read_text()
    outputs = []
    monkeypatch.setattr(cli, "notifier", lambda _: lambda message: None)
    def agent(call):
        if call.access == "read":
            return agents.Result("ok", {"contradictions": [], "risks": []})
        root = spec_format.get(m).change_dir(call.cwd, m.change)
        root.mkdir(parents=True)
        if m.format == "speckit":
            for source in (Path(__file__).parent / "fixtures/speckit/specs/042-add-a-gate").glob("*.md"):
                (root / source.name).write_text(source.read_text().replace("042-add-a-gate", m.change))
        else:
            (root / "tasks.md").write_text(TASKS)
        return agents.Result("ok", {"status": "drafted", "questions": [], "summary": ""})
    monkeypatch.setattr(agents, "run", agent)
    for repository, format, relative in repositories:
        m = replace(M, github=repository, format=format, change=Path(relative).name)
        out = state.proposals_dir() / relative
        assert propose.draft_output(m) == out
        outputs.append(out)
        if format == "speckit":
            flat = state.proposals_dir() / m.change
            flat.mkdir(parents=True, exist_ok=True)
            (flat / "sentinel").write_text("old flat native draft")
    for i in ([1, 0] if reverse else [0, 1]) * 3:
        repository, format, relative = repositories[i]
        path.write_text(original.replace('github = "example/repo"', f'github = "{repository}"\nformat = "{format}"'))
        m, _ = manifest.template(path, change=Path(relative).name, branch=f"spec/{Path(relative).name}")
        before = draft_evidence_snapshot(outputs[1 - i] / "sentinel")
        assert main(["propose", str(path), "--change", m.change, "--branch", m.branch,
                     "--idea", "Add a gate", "--json"]) == 0
        assert json.loads(capfd.readouterr().out)["outcome"] == "drafted"
        assert draft_evidence_snapshot(outputs[1 - i] / "sentinel") == before
        assert not (outputs[i] / "sentinel").exists()
        (outputs[i] / "sentinel").write_text(repository)
        if format == "speckit":
            assert (state.proposals_dir() / m.change / "sentinel").read_text() == "old flat native draft"


@pytest.mark.scenario("spec-drafting/repository-qualified-native-draft-workspaces/native-workspaces-are-isolated")
@pytest.mark.parametrize("reverse", [False, True])
def test_native_draft_workspaces_are_repository_qualified(template, monkeypatch, capfd, reverse):
    isolated_proposals(template, monkeypatch, capfd, [
        (r, "speckit", f"@speckit/{r}/007-add-a-gate") for r in ("a/x", "b/x")], reverse)


@pytest.mark.scenario("spec-drafting/repository-qualified-native-draft-workspaces/native-workspaces-are-isolated")
@pytest.mark.parametrize("reverse", [False, True])
def test_native_draft_repository_components_do_not_collide(template, monkeypatch, capfd, reverse):
    isolated_proposals(template, monkeypatch, capfd, [
        ("a__b/c", "speckit", "@speckit/a__b/c/007-add-a-gate"),
        ("a/b__c", "speckit", "@speckit/a/b__c/007-add-a-gate")], reverse)


@pytest.mark.scenario("spec-drafting/repository-qualified-native-draft-workspaces/openspec-workspaces-remain-flat-and-intact")
@pytest.mark.parametrize("reverse", [False, True])
def test_cross_format_draft_replacement_is_isolated(template, monkeypatch, capfd, reverse):
    assert state.SAFE_CHANGE.fullmatch("@speckit") is None
    isolated_proposals(template, monkeypatch, capfd, [
        ("a/x", "speckit", "@speckit/a/x/007-add-a-gate"),
        ("1-a/1-x__007", "openspec", "1-a__1-x__007-issue-7-add-a-gate")], reverse)


@pytest.mark.scenario("state-migration/separate-draft-namespace/unsafe-draft-replacement-refuses")
@pytest.mark.parametrize("level", range(5))
@pytest.mark.parametrize("kind", ["link", "dangling", "file"])
def test_native_unsafe_ancestors_refuse(tmp_path, monkeypatch, level, kind):
    m = replace(M, format="speckit", github="a/x", change="007-add-a-gate")
    out = propose.draft_output(m)
    target = [*reversed(out.parents[:4]), out][level]
    target.parent.mkdir(parents=True, exist_ok=True)
    if kind == "file":
        target.write_text("retain")
    else:
        target.symlink_to(tmp_path / "missing" if kind == "dangling" else tmp_path)
    before = draft_evidence_snapshot(state.state_root())
    monkeypatch.setattr(propose, "start_commit", lambda *a: pytest.fail("resolved before refusal"))
    with pytest.raises(ValueError, match="unsafe draft path"):
        propose.draft(tmp_path, "main", m.change, "idea", [], m, out)
    assert draft_evidence_snapshot(state.state_root()) == before


@pytest.mark.scenario("spec-drafting/repository-qualified-native-draft-workspaces/native-workspaces-are-isolated")
@pytest.mark.parametrize("component", ["", ".", "..", "a..b", "a.", "a.lock", "a\\b", "a b", "é", "@a"])
@pytest.mark.parametrize("owner", [False, True])
def test_invalid_repository_components(component, owner):
    repository = f"{component}/x" if owner else f"a/{component}"
    with pytest.raises(ValueError, match="unsafe native repository"):
        propose.draft_output(replace(M, format="speckit", github=repository))
    m = replace(M, format="speckit", github="forge.example/a__b/c")
    assert propose.draft_output(m) == state.proposals_dir() / "@speckit/a__b/c" / m.change


@pytest.mark.parametrize("repository", [None, "", "owner", "a/b/c/d", "/a/b", "a//b"])
def test_missing_or_malformed_native_repository(repository):
    with pytest.raises(ValueError, match="unsafe native repository"):
        propose.draft_output(replace(M, format="speckit", github=repository))


@pytest.mark.scenario("state-migration/separate-draft-namespace/draft-replacement-preserves-daemon-evidence")
@pytest.mark.scenario("state-migration/separate-draft-namespace/generated-names-cannot-collide-with-another-repository")
@pytest.mark.parametrize("generated", [False, True])
def test_migration_preserves_evidence_with_native_namespace(template, monkeypatch, capfd, generated):
    native = propose.draft_output(replace(M, format="speckit", github="a/x"))
    native.mkdir(parents=True)
    (native / "sentinel").write_text("native workspace")
    before = draft_evidence_snapshot(native)
    commands.test_cli_draft_replacement_preserves_daemon_evidence(template, monkeypatch, capfd, generated)
    assert draft_evidence_snapshot(native) == before


@pytest.mark.scenario("spec-format/engine-routing-compatibility/publication-and-interrupted-finish-retain-behavior")
@pytest.mark.parametrize("failure", ["push", "ready"])
def test_namespace_keeps_interrupted_finish(service, monkeypatch, capfd, failure):
    compatibility.test_finish_routes_and_reuses_archive_after_interruption(service, monkeypatch, capfd, failure)


@pytest.mark.scenario("spec-format/engine-routing-compatibility/drafting-and-revision-retain-behavior")
@pytest.mark.scenario("spec-format/engine-routing-compatibility/consumers-contain-no-direct-format-operations")
@pytest.mark.scenario("spec-format/engine-routing-compatibility/openspec-planned-edits-exclude-reserved-specs-root")
def test_namespace_keeps_format_boundary(tmp_path):
    compatibility.test_no_direct_openspec_path_literals_outside_adapter()
    compatibility.test_drafting_prompt_retains_exact_bytes()
    for root in (None, tmp_path):
        for extract in (propose.paths, propose.edited_paths):
            assert extract("Edit `specs/helper.py` and `src/helper.py`", root) == {"src/helper.py"}
