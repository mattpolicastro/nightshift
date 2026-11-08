from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from nightshift.cli import main
from nightshift import specloop_intake as intake
from specloop import agents, cli, manifest, propose, spec_format, state
from test_specloop_command_json import TASKS, template
from test_specloop_propose import M, draft_evidence_snapshot
import test_specloop_command_json as commands
import test_spec_format as compatibility
from test_spec_format import service, admitted, repo, commit
from specloop.seal import git
import test_owner_qualified_changes as qualified
from test_owner_qualified_changes import template as intake_template


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


@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/different-owners-have-distinct-changes")
def test_format_aware_names_keep_distinct_openspec_delivery(intake_template):
    qualified.test_change_names_are_owner_qualified(intake_template)


@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/safe-punctuation-is-preserved")
@pytest.mark.parametrize("title,slug", [("Add a gate", "add-a-gate"), ("🔥 λ", "issue"),
                                        ("A" * 39 + " B", "a" * 39)])
def test_format_aware_names_keep_punctuation(intake_template, title, slug):
    qualified.test_punctuation_and_bounded_slug(intake_template, "owner", title, slug)


@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/unsafe-spelling-holds-new-work")
def test_format_aware_names_keep_health_hold(intake_template, monkeypatch):
    qualified.test_unsafe_name_is_repository_health_hold(intake_template, monkeypatch, "owner/widget..js")
    qualified.test_unsupported_name_allows_saved_safe_draft_delivery(intake_template, True)


@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/native-issue-numbers-pad-and-grow")
@pytest.mark.parametrize("number,prefix", [(7, "007"), (42, "042"), (1234, "1234")])
@pytest.mark.parametrize("title,slug", [("Add a gate", "add-a-gate"), ("CAFÉ !!", "caf"),
                                        ("🔥", "issue"), ("A" * 50, "a" * 40)])
def test_native_names_pad_and_grow(number, prefix, title, slug):
    for repository in ("a/x", "b/x"):
        name = intake._change_name(repository, number, title, format="speckit")
        assert name == f"{prefix}-{slug}" and intake._safe_change(name)
        assert intake._change_name(repository, number, title) == intake._change_name(
            repository, number, title, format="openspec")
    with pytest.raises(ValueError, match="positive"):
        intake._change_name("a/x", 0, title, format="speckit")


@pytest.fixture
def target_repo(repo):
    document = "# Gate\n**Status**: Draft\n\n### User Story 1 - Gate (Priority: P1)\n"
    document += "**Acceptance Scenarios**:\n1. **AS1** Retain this identity.\n"
    commit(repo, {"specs/042-add-a-gate/spec.md": document,
                  "specs/042-add-a-gate/tasks.md": "- [ ] T001 Retain task identity\n"})
    return repo, git(repo, "rev-parse", "HEAD").decode().strip()


def select_target(target_repo, body, title="Changed title", **kwargs):
    source, head = target_repo
    return intake._native_change(source, head, "owner/repo", 42, title, body, **kwargs)


@pytest.mark.scenario("issue-intake/native-existing-feature-selection/existing-feature-directory-is-reused")
def test_native_target_reuses_pinned_directory(target_repo):
    source, head = target_repo
    before = git(source, "ls-tree", "-r", head)
    commit(source, {"specs/042-add-a-gate/spec.md": "# Gate\n**Status**: Complete\n"})
    body = "Revise `specs/042-add-a-gate/` and specs/042-add-a-gate/"
    assert select_target(target_repo, body) == "042-add-a-gate"
    assert git(source, "ls-tree", "-r", head) == before
    with pytest.raises(intake.DeliveryError, match="Complete"):
        select_target((source, git(source, "rev-parse", "HEAD").decode().strip()), body)
    local = source / "specs/042-only-local/spec.md"
    local.parent.mkdir()
    local.write_text("# Uncommitted feature")
    with pytest.raises(intake.DeliveryError, match="unavailable"):
        select_target(target_repo, "specs/042-only-local/")


@pytest.mark.scenario("issue-intake/native-existing-feature-selection/ambiguous-or-unavailable-target-refuses")
@pytest.mark.parametrize("wrapper", ["{}", "`{}`", "```md\n{}\n```", "<!-- {} -->", "    {}"])
def test_native_tokens_are_physical_and_deduplicated(target_repo, wrapper):
    body = wrapper.format("specs/042-add-a-gate/")
    assert intake._native_targets(body + "\n" + body, 42) == ["042-add-a-gate"]
    assert select_target(target_repo, body) == "042-add-a-gate"
    with pytest.raises(intake.DeliveryError, match="ambiguous"):
        select_target(target_repo, body + "\n" + wrapper.format("specs/042-another/"))
    assert intake._native_targets(wrapper.format("specs/00042-add-a-gate/"), 42) == ["00042-add-a-gate"]


@pytest.mark.scenario("issue-intake/native-existing-feature-selection/ambiguous-or-unavailable-target-refuses")
@pytest.mark.parametrize("kind", ["missing", "file", "link", "spec-link", "no-spec", "unreadable", "complete"])
def test_native_unavailable_targets_refuse(target_repo, kind):
    source, _ = target_repo
    target = source / "specs/042-target"
    if kind == "file":
        target.write_text("not a tree")
    elif kind == "link":
        target.symlink_to("042-add-a-gate", target_is_directory=True)
    elif kind != "missing":
        target.mkdir()
        if kind == "spec-link":
            (target / "spec.md").symlink_to("../042-add-a-gate/spec.md")
        elif kind == "no-spec":
            (target / "plan.md").write_text("No spec")
        else:
            (target / "spec.md").write_bytes(b"\xff" if kind == "unreadable" else
                                            b"# Gate\n**Status**: Complete\n**Status**: Draft\n")
    if kind != "missing":
        commit(source, {})
    pinned = source, git(source, "rev-parse", "HEAD").decode().strip()
    with pytest.raises(intake.DeliveryError):
        select_target(pinned, "specs/042-target/")


@pytest.mark.scenario("issue-intake/native-existing-feature-selection/untargeted-native-issue-uses-title")
@pytest.mark.parametrize("body", ["", "specs/043-add-a-gate/", "specs/042-add-a-gate/spec.md",
    "./specs/042-add-a-gate/", "xspecs/042-add-a-gate/", "specs/042-add-a-gate/_child",
    "specs/42-add-a-gate/", "specs/042-Upper/", "specs/042-a--b/", "specs/042-a..b/",
    "specs/042-" + "a" * 41 + "/", "specs/000-zero/", "specs/042-add-\na-gate/"])
def test_native_untargeted_uses_title(target_repo, body):
    assert intake._native_targets(body, 42) == []
    assert select_target(target_repo, body) == "042-changed-title"


@pytest.mark.scenario("issue-intake/native-existing-feature-selection/untargeted-native-issue-uses-title")
def test_native_generated_path_cannot_overwrite_complete(target_repo):
    source, _ = target_repo
    commit(source, {"specs/042-add-a-gate/spec.md": "# Gate\n**Status**: Complete\n"})
    pinned = source, git(source, "rev-parse", "HEAD").decode().strip()
    with pytest.raises(intake.DeliveryError, match="Complete"):
        select_target(pinned, "", "Add a gate")


@pytest.mark.scenario("issue-intake/native-existing-feature-selection/untargeted-native-issue-uses-title")
@pytest.mark.parametrize("root_kind", ["absent", "file", "link"])
def test_native_discovery_checks_root_without_scanning(repo, root_kind):
    if root_kind != "absent":
        root = repo / "specs"
        if root_kind == "link":
            root.symlink_to("missing", target_is_directory=True)
        else:
            root.write_text("not a directory")
        commit(repo, {})
    pinned = repo, git(repo, "rev-parse", "HEAD").decode().strip()
    reads = []
    def runner(args, **kwargs):
        reads.append(args[3:])
        return subprocess.run(args, **kwargs)
    if root_kind == "absent":
        assert select_target(pinned, "", git_runner=runner) == "042-changed-title"
    else:
        with pytest.raises(intake.DeliveryError, match="unsafe"):
            select_target(pinned, "", git_runner=runner)
    assert reads == [["cat-file", "-t", pinned[1]], ["ls-tree", "-z", pinned[1], "--", "specs"]]


@pytest.mark.scenario("issue-intake/native-existing-feature-selection/ambiguous-or-unavailable-target-refuses")
@pytest.mark.scenario("issue-intake/native-existing-feature-selection/untargeted-native-issue-uses-title")
@pytest.mark.parametrize("operation", ["ls-tree", "cat-file"])
@pytest.mark.parametrize("failure", ["read", "timeout", "pause"])
def test_native_uncertain_reads_propagate(target_repo, operation, failure):
    def runner(args, **kwargs):
        assert kwargs["timeout"] == 30 and kwargs["check"]
        if operation in args and "-t" not in args:
            if failure == "pause":
                raise intake.Paused("paused")
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 30)
            raise subprocess.CalledProcessError(128, args)
        return subprocess.run(args, **kwargs)
    expected = {"pause": intake.Paused, "timeout": RuntimeError, "read": subprocess.CalledProcessError}[failure]
    with pytest.raises(expected):
        select_target(target_repo, "", "Add a gate", git_runner=runner)


@pytest.mark.parametrize("pin", ["HEAD", "main", "0" * 40, "tree"])
def test_native_discovery_requires_existing_exact_commit(target_repo, pin):
    source, head = target_repo
    if pin == "tree":
        pin = git(source, "rev-parse", head + "^{tree}").decode().strip()
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        select_target((source, pin), "")
