from dataclasses import replace
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from nightshift.cli import main
from nightshift import queue, specloop_intake as intake
from nightshift.config import Repo
from specloop import agents, cli, manifest, propose, revise, seal, spec_format, state
from test_specloop_command_json import TASKS, template
from test_specloop_propose import M, draft_evidence_snapshot
import test_specloop_command_json as commands
import test_spec_format as compatibility
from test_spec_format import service, admitted, repo, commit
from specloop.seal import git
import test_owner_qualified_changes as qualified
from test_owner_qualified_changes import template as intake_template
from speckit_front_door_fixtures import (
    CHANGE, HEADING, ITEM, MARKER, SID, SPEC, SPEC_PATH, native_checklist, native_run,
)


@pytest.mark.scenario("merge-readiness/native-committed-human-check-lookup/native-scenarios-with-marker-permit-readiness")
@pytest.mark.parametrize("wrapper", [("", ""), ("```md\n", "```\n"), ("<!--\n", "-->\n"), ("    ", "")])
def test_readiness_resolves_as_scenarios_with_marker(native_run, native_checklist, wrapper):
    env = native_run
    document = wrapper[0] + SPEC + MARKER + "\n2. **AS2** Another " + MARKER + "\n" + wrapper[1]
    head = env.commit({SPEC_PATH: document})
    before = (env.run.dir / "state.json").read_bytes()
    env.commit({SPEC_PATH: SPEC + "unmarked\n"})
    (env.work / SPEC_PATH).write_text("uncommitted source")
    enrollment = env.work / "enrollment.toml"
    enrollment.write_text(env.run.manifest_path.read_text().replace('"speckit"', '"openspec"')
                          .replace('repo = "."', f'repo = "{env.work}"'))
    checks = native_checklist(ITEM + "\n" + ITEM.replace("as1", "as2"))
    assert intake._readiness_human_checks({**env.row, "head": head}, **checks) == []
    result = intake._readiness_assessment(Repo(name="owner/repo", engine="specloop", manifest=enrollment),
        queue.Labels(), env.work, 1, {**env.row, "head": head},
        dict(headRefOid=head, labels=[], body=checks["body"]),
        publication=dict(checks=checks["publication_checks"]),
        runner=lambda args: json.dumps([dict(name="CI", state="COMPLETED", bucket="pass")]))
    assert result["label"] == "merge:ready" and result["reasons"] == []
    assert (env.run.dir / "state.json").read_bytes() == before
    assert "[ ]" in str(checks)


@pytest.mark.scenario("merge-readiness/native-committed-human-check-lookup/unknown-or-unmarked-native-ids-block")
@pytest.mark.parametrize("case", ["unknown", "unmarked", "neighbor", "other", "plan", "duplicate",
    "fenced-duplicate", "html-duplicate", "malformed", "checked", "missing", "decode", "unreadable",
    "double-heading", "fenced-checklist", "html-checklist", "two-citations", "manifest-identity",
    "manifest-missing", "manifest-malformed"])
def test_readiness_blocks_unknown_or_unmarked_id(native_run, native_checklist, case):
    env = native_run
    document, item = SPEC + MARKER + "\n", ITEM
    if case in ("unmarked", "neighbor", "other", "plan", "checked"):
        document = SPEC + "unmarked\n"
        document += {"neighbor": MARKER, "other": "2. **AS2** Another " + MARKER}.get(case, "")
    if case in ("duplicate", "fenced-duplicate", "html-duplicate"):
        document += {"duplicate": "", "fenced-duplicate": "```\n", "html-duplicate": "<!--\n"}[case]
        document += "2. **AS1** Duplicate " + MARKER
    if case in ("unknown", "malformed"):
        item = item.replace("as1", "as99" if case == "unknown" else "AS1")
    if case == "checked":
        item = item.replace("[ ]", "[x]")
    if case == "two-citations":
        item += f" `{SID}`"
    env.commit({SPEC_PATH: None if case == "missing" else document,
                f"specs/{CHANGE}/plan.md": MARKER})
    if case == "decode":
        (env.work / SPEC_PATH).write_bytes(b"\xff")
        env.commit({})
    if case == "manifest-identity":
        path = env.run.manifest_path
        path.write_text(path.read_text().replace(f'change = "{CHANGE}"', 'change = "other"'))
    elif case == "manifest-missing":
        env.run.manifest_path.unlink()
    elif case == "manifest-malformed":
        env.run.manifest_path.write_text("[broken")
    checks = native_checklist(item)
    if case == "double-heading":
        checks["body"] = HEADING + ITEM + "\n" + HEADING + ITEM
    elif case in ("fenced-checklist", "html-checklist"):
        checks = native_checklist(("```\n" if case == "fenced-checklist" else "<!--\n") + item)
    def runner(args, **kwargs):
        if case == "unreadable":
            raise OSError("unreadable committed spec")
        return subprocess.run(args, **kwargs)
    reasons = intake._readiness_human_checks(env.row, **checks, git_runner=runner)
    assert reasons and all("human checks:" in r and "unknown" in r for r in reasons)


@pytest.mark.scenario("speckit-format/local-feature-api/resolve-a-feature-without-integration")
def test_readiness_integration_keeps_feature_lookup_pure(tmp_path, monkeypatch):
    import importlib
    from specloop import speckit
    def forbidden(*args, **kwargs):
        pytest.fail("pure native lookup performed I/O or format selection")
    with monkeypatch.context() as patch:
        for owner, name in ((Path, "open"), (Path, "stat"), (subprocess, "run"), (spec_format, "get")):
            patch.setattr(owner, name, forbidden)
        importlib.reload(speckit)
        assert speckit.feature_dir(tmp_path, CHANGE) == tmp_path / "specs" / CHANGE
        assert SID in speckit.scenario_ids(CHANGE, speckit.parse_spec(SPEC + MARKER))


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
def test_change_name_pads_to_three_and_grows(number, prefix, title, slug):
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
def test_existing_feature_dir_is_reused(target_repo):
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


@pytest.fixture(params=["github", "gitea"])
def front_door(repo, tmp_path, monkeypatch, request):
    from types import SimpleNamespace
    from manifest_fixtures import manifest_text
    from nightshift import queue
    from nightshift.config import Repo
    from nightshift.forge.github import GitHubForge
    from gitea_dispatch_fixtures import install_gitea
    from test_forge_gitea import make_forge, pull
    from test_specloop_isolation import Pipeline

    path = tmp_path / "intake.toml"
    path.write_text(manifest_text(repo))
    pipeline = Pipeline(Repo("owner/sample", engine="specloop", manifest=path))
    pipeline.issue(7)
    issue = pipeline.issues[7]
    issue.update(title="Add a gate", authorAssociation="OWNER")
    forge = GitHubForge(lambda args: json.dumps({"author_association": "OWNER"})
        if args == ["api", "repos/owner/sample/issues/7"] else pipeline.gh(args))
    remote = None
    if request.param == "gitea":
        installed = install_gitea(monkeypatch, pipeline.repo, issues=[issue])
        remote = installed.remote
        issue = remote["issues"][0]
        forge = make_forge(installed.fake)
    e = SimpleNamespace(source=repo, manifest=path, issue=issue, forge=forge,
                        path=tmp_path / "proposals/owner__sample/7.json",
                        commands=[], heads=[], errors=[], fail=None, push_failure=False, prs=[])
    lookup = forge.latest_pr_number
    def exact(repository, branch, *, strict):
        assert repository == "owner/sample" and strict
        e.heads.append(branch.removeprefix("spec/"))
        if e.fail == branch.removeprefix("spec/"):
            raise RuntimeError("exact lookup unavailable")
        return lookup(repository, branch, strict=strict)
    monkeypatch.setattr(forge, "latest_pr_number", exact)
    def command(args, **kwargs):
        e.commands.append(args)
        if "push" in args and e.push_failure:
            raise RuntimeError("push interrupted")
        if args[0] == "git" and not any(op in args for op in ("push", "ls-remote")):
            return subprocess.run(args, **kwargs)
        return pipeline.command(args, **kwargs)
    def add(change, state="OPEN", head="a" * 40):
        number = 8 + len(e.prs)
        if remote is None:
            pipeline.approved(number)
            pr = pipeline.prs[number]
            pr.update(headRefName=f"spec/{change}", state=state, headRefOid=head)
        else:
            pr = pull(number, f"spec/{change}", labels=[], state="open" if state == "OPEN" else "closed",
                      merged=state == "MERGED", html_url=f"https://forge.example/gitea/owner/sample/pulls/{number}")
            remote["pulls"].append(pr)
            pr["head"]["sha"] = head
        e.prs.append(pr)
    e.add = add
    e.format = lambda value: path.write_text(manifest_text(repo).replace(
        "[run]", "[run]" + (f'\nformat = "{value}"' if value else "")))
    e.poll = lambda **kwargs: intake.propose_pending(pipeline.repo, queue.Labels(), tmp_path, forge=forge,
        **{"specloop_runner": command, "git_runner": command,
           "on_error": lambda n, exc: e.errors.append(exc), **kwargs})
    return e


@pytest.mark.real_trust
@pytest.mark.scenario("issue-intake/owner-qualified-new-identities/manifest-selects-native-naming")
@pytest.mark.scenario("issue-intake/drafting-from-an-issue/draft-pr-opened")
@pytest.mark.parametrize("format", [None, "openspec", "speckit"])
@pytest.mark.parametrize("files", ["openspec", "specs"])
def test_manifest_selects_intake_naming(front_door, format, files):
    e = front_door
    commit(e.source, {f"{files}/unrelated/README.md": "Unrelated format files"})
    e.format(format)
    rows = e.poll()
    assert not e.errors
    receipt, = rows
    change = "007-add-a-gate" if format == "speckit" else "owner__sample-issue-7-add-a-gate"
    assert receipt["change"] == change and receipt["result"]["branch"] == f"spec/{change}"
    assert e.heads == ([change] if format == "speckit" else []) + [
        "owner__sample-issue-7-add-a-gate", "sample-issue-7-add-a-gate"]
    command, = [c for c in e.commands if c[:2] == ["specloop", "propose"]]
    assert command[command.index("--change") + 1] == change
    assert ("--base-commit" in command) == (format == "speckit")
    assert not e.errors and receipt["delivered"]


@pytest.mark.real_trust
@pytest.mark.scenario("issue-intake/native-spec-pr-description/spec-pr-body-names-feature-directory")
@pytest.mark.parametrize("format", [None, "openspec", "speckit"])
def test_spec_pr_body_names_feature_dir(front_door, monkeypatch, format):
    e = front_door
    e.format(format)
    bodies = []
    create = e.forge.create_draft_pr

    def capture(repository, branch, base, title, body):
        bodies.append(body)
        return create(repository, branch, base, title, body)

    monkeypatch.setattr(e.forge, "create_draft_pr", capture)
    rows = e.poll()
    assert not e.errors
    receipt, = rows
    assert receipt["delivered"]
    body, = bodies
    feature_lines = [line for line in body.splitlines() if line.startswith("spec-kit feature")]
    assert feature_lines == (["spec-kit feature `specs/007-add-a-gate/`"]
                             if format == "speckit" else [])


@pytest.mark.real_trust
@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/legacy-receipt-survives-title-edits")
@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/native-and-legacy-recovery-keep-identity")
@pytest.mark.parametrize("format,change", [(None, "sample-issue-7-old"),
    ("speckit", "sample-issue-7-old"), ("speckit", "007-old")])
@pytest.mark.parametrize("pending", [False, True])
def test_saved_proposal_keeps_identity(front_door, monkeypatch, format, change, pending):
    e = front_door
    e.format(format)
    e.issue.update(title="Edited title", body="blocked-by: #99\nspecs/007-missing/")
    result = dict(outcome="drafted", branch=f"refs/specloop/proposals/{change}", commit="a" * 40)
    saved = dict(change=change, job="saved-job", title="Old title", idea="Old idea", result=result)
    if pending:
        saved.pop("result")
        results = iter([None, result])
        monkeypatch.setattr(intake.specloop_job, "_collect_locked", lambda *a: next(results))
    (e.path.parent / "7.jobs/saved-job").mkdir(parents=True)
    intake._save(e.path, saved)
    monkeypatch.setattr(e.forge, "open_blockers", lambda *a: pytest.fail("delivery consulted blockers"))
    if pending:
        assert e.poll() == [] and json.loads(e.path.read_text()) == saved
    rows = e.poll()
    assert not e.errors
    receipt, = rows
    assert receipt == {**saved, "result": result, "delivered": True}
    assert e.heads == [change] and not e.errors
    push, = e.commands
    assert push[-1] == f"{'a' * 40}:refs/heads/spec/{change}"


@pytest.mark.real_trust
@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/legacy-pr-is-recovered")
@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/native-and-legacy-recovery-keep-identity")
@pytest.mark.parametrize("format,change", [(None, "sample-issue-7-add-a-gate"),
    ("speckit", "007-add-a-gate"), ("speckit", "007-old"),
    ("speckit", "owner__sample-issue-7-add-a-gate"), ("speckit", "sample-issue-7-add-a-gate")])
@pytest.mark.parametrize("authorized", [False, True])
def test_exact_pr_recovery_precedes_blockers(front_door, monkeypatch, format, change, authorized):
    e = front_door
    e.format(format)
    e.issue["body"] = "blocked-by: #99\nspecs/007-old/ specs/007-old/"
    e.add(change)
    monkeypatch.setattr(e.forge, "open_blockers", lambda *a: pytest.fail("recovery consulted blockers"))
    if not authorized:
        events = e.forge.label_event_pages("owner/sample", 7)
        for page in events:
            for event in page:
                event["actor"] = {}
        monkeypatch.setattr(e.forge, "label_event_pages", lambda *a: events)
    assert e.poll() == [] and not e.errors
    assert not e.path.exists() and not e.commands
    assert bool(e.issue["labels"]) != authorized
    if authorized:
        assert "/8" in e.issue["comments"][-1]["body"]
    assert e.heads == (["007-add-a-gate", "007-old"] if format == "speckit" else []) + [
        "owner__sample-issue-7-add-a-gate", "sample-issue-7-add-a-gate"]


@pytest.mark.real_trust
@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/lookup-failure-does-not-create-duplicate-work")
@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/recovery-ambiguity-blocks-new-work")
@pytest.mark.parametrize("format,failed", [(None, "owner__sample-issue-7-add-a-gate"),
    (None, "sample-issue-7-add-a-gate"), ("speckit", "007-add-a-gate"),
    ("speckit", "007-old"), ("speckit", "owner__sample-issue-7-add-a-gate"),
    ("speckit", "sample-issue-7-add-a-gate"), ("speckit", "ambiguous")])
def test_uncertain_recovery_preserves_history(front_door, format, failed):
    e = front_door
    e.format(format)
    e.issue["body"] = "specs/007-old/"
    intake._save(e.path, dict(change="old", idea="old", delivered=True, result={"outcome": "needs_input"}))
    before = e.path.read_bytes()
    if format == "speckit":
        e.add("007-old")
    if failed == "ambiguous":
        e.add("sample-issue-7-add-a-gate")
    else:
        e.fail = failed
    assert e.poll() == [] and len(e.errors) == 1
    assert ("ambiguous" if failed == "ambiguous" else "lookup unavailable") in str(e.errors[0])
    assert e.path.read_bytes() == before and e.issue["labels"] and not e.issue["comments"]
    assert not e.commands


@pytest.mark.real_trust
@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/closed-legacy-pr-starts-a-qualified-round")
@pytest.mark.scenario("issue-intake/recorded-identities-remain-authoritative/native-and-legacy-recovery-keep-identity")
@pytest.mark.parametrize("format,target", [(None, None), ("speckit", None), ("speckit", "007-old")])
@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
@pytest.mark.parametrize("native_pr", [False, True])
@pytest.mark.parametrize("target_closed", [False, True])
@pytest.mark.parametrize("recorded", [False, True])
def test_closed_round_retains_replacement_lease(front_door, format, target, state, native_pr, target_closed, recorded):
    e = front_door
    e.format(format)
    old_change = "007-add-a-gate" if native_pr and format == "speckit" else "sample-issue-7-add-a-gate"
    e.add(old_change, state)
    if recorded:
        intake._save(e.path, dict(change=old_change, title="Add a gate", idea="Old idea", delivered=True,
            result=dict(outcome="drafted", branch=f"spec/{old_change}", commit="a" * 40)))
    before = e.path.read_bytes() if recorded else None
    if target:
        commit(e.source, {f"specs/{target}/spec.md": "# Old\n**Status**: Draft\n"})
        e.issue["body"] = f"specs/{target}/"
        if target_closed:
            e.add(target, "CLOSED", head="b" * 40)
    replacement = {"number": 9, "head": "b" * 40} if target and target_closed else {"number": 8, "head": "a" * 40}
    labels, e.issue["labels"] = e.issue["labels"], []
    assert e.poll() == [] and not e.commands
    e.issue["labels"] = labels
    if state == "CLOSED":
        e.push_failure = True
        assert e.poll() == [] and str(e.errors.pop()) == "push interrupted"
        saved = json.loads(e.path.read_text())
        assert saved["replaces_pr"] == replacement
        e.issue["title"] = "Edited before retry"
        e.push_failure = False
    rows = e.poll()
    assert not e.errors
    if state == "MERGED":
        assert rows == [] and not e.commands
        assert (e.path.read_bytes() if e.path.exists() else None) == before
    else:
        receipt, = rows
        expected = target or ("007-add-a-gate" if format == "speckit" else "owner__sample-issue-7-add-a-gate")
        assert receipt["change"] == expected
        assert receipt["replaces_pr"] == replacement
        assert receipt == {**saved, "delivered": True}
        assert sum(c[:2] == ["specloop", "propose"] for c in e.commands) == 1
        push = [c for c in e.commands if "push" in c][-1]
        assert f"--force-with-lease=refs/heads/spec/{expected}:{replacement['head']}" in push
        assert receipt["delivered"]


@pytest.fixture
def three_phase_revision(repo, tmp_path):
    path = f"specs/{CHANGE}/tasks.md"
    original = ("## Phase 1: Setup\n- [X] T001 Prepare\n\n"
                "## Phase 2: Foundational\n- [X] T005 Support\n\n"
                f"## Phase 3: User Story 1 - Gate\n- [X] T009 [US1] Gate; `{SID}`\n")
    parent = commit(repo, {path: original, SPEC_PATH: SPEC + "\n"})
    git(repo, "branch", f"spec/{CHANGE}")
    ctx = compatibility.run_context(repo, tmp_path)
    m = replace(ctx.m, change=CHANGE, branch=f"spec/{CHANGE}", format="speckit")
    ctx.run.state.change = CHANGE
    ctx.run.state.gates_passed = [1, 2, 3]
    ctx.run.pause("human_checks", detail="Review the native PR")
    return SimpleNamespace(run=ctx.run, m=m, repo=ctx.repo, ref=f"refs/heads/{m.branch}",
                           path=path, original=original, parent=parent)


def native_revision(ctx, blocks=None, key="native-review-1"):
    if blocks is None:
        blocks = [f'Keep the "café" gate; `{SID}`.\n- [ ] Verify it.', "Handle empty input."]
    return revise.revise_result(state.Run(ctx.run.dir, require_existing=True), ctx.m,
                                json.dumps(blocks), feedback_format="json", round_id=key)


def native_revision_snapshot(ctx):
    return (git(ctx.repo, "show-ref"),
            {p.relative_to(ctx.run.dir): p.read_bytes() for p in ctx.run.dir.rglob("*")
             if p.is_file() and "repo.git" not in p.relative_to(ctx.run.dir).parts
             and p.name != "lock"})


@pytest.mark.scenario("pr-revision/native-front-door-review-rounds/native-round-appends-phase-and-next-task-ids")
@pytest.mark.scenario("pr-revision/native-front-door-review-rounds/native-keyed-replay-returns-same-commit")
def test_revision_round_appends_phase_with_next_task_ids(three_phase_revision):
    ctx = three_phase_revision
    result = native_revision(ctx)
    text = git(ctx.repo, "show", f"{result.commit}:{ctx.path}").decode()
    parsed = spec_format.SPECKIT.parse_tasks(text)
    assert text.startswith(ctx.original + "\n## Phase 4: Review round 1\n\n")
    assert parsed[:3] == spec_format.SPECKIT.parse_tasks(ctx.original)
    assert [(t.section, t.section_title, t.label, t.done) for t in parsed[3:]] == [
        (4, "Review round 1", "T010", False), (4, "Review round 1", "T011", False)]
    assert [t.text for t in parsed[3:]] == [
        "Address review feedback 1: " + json.dumps(f'Keep the "café" gate; `{SID}`.\n- [ ] Verify it.'),
        "Address review feedback 2: " + json.dumps("Handle empty input.")]
    assert git(ctx.repo, "diff-tree", "--no-commit-id", "--name-only", "-r", result.commit).decode().splitlines() == [ctx.path]
    assert git(ctx.repo, "show", f"{result.commit}:{SPEC_PATH}") == (SPEC + "\n").encode()
    assert git(ctx.repo, "rev-parse", result.commit + "^").decode().strip() == ctx.parent
    assert not result.replayed and (result.status, result.pause) == ("running", None)
    before = native_revision_snapshot(ctx)
    assert native_revision(ctx) == replace(result, replayed=True)
    assert native_revision_snapshot(ctx) == before


@pytest.mark.scenario("pr-revision/native-front-door-review-rounds/native-round-appends-phase-and-next-task-ids")
@pytest.mark.parametrize("root,next_label", [("T009.2", "T010"), ("T999", "T1000"), ("T1001.7", "T1002")])
@pytest.mark.parametrize("wrapper", [("", ""), ("```md\n", "```\n"), ("<!--\n", "-->\n")])
def test_native_revision_uses_physical_maxima(three_phase_revision, root, next_label, wrapper):
    ctx = three_phase_revision
    original = (ctx.original.replace("T009", root) + wrapper[0]
                + "## Phase 9: Review round 7\n## Phase 20: Review round 2\n"
                "## Phase 12: Review round 99 extra\n"
                "    ## Phase 900: Review round 900\n"
                "## 999. Review round 999\n    - [ ] T9999 Outside native phase\n" + wrapper[1])
    parent = seal.commit_files(ctx.repo, ctx.parent, {ctx.path: original}, "physical fixture")
    git(ctx.repo, "update-ref", ctx.ref, parent)
    result = native_revision(ctx)
    text = git(ctx.repo, "show", f"{result.commit}:{ctx.path}").decode()
    assert text.startswith(original + "\n## Phase 21: Review round 8\n\n")
    added = spec_format.SPECKIT.parse_tasks(text)[-2:]
    assert [t.label for t in added] == [next_label, f"T{int(next_label[1:]) + 1:03d}"]
    assert all(t.section == 21 and t.section_title == "Review round 8" for t in added)
    assert native_revision(ctx).commit == result.commit


@pytest.mark.scenario("pr-revision/native-front-door-review-rounds/native-round-appends-phase-and-next-task-ids")
@pytest.mark.parametrize("extra", [
    "## Phase 03: Duplicate\n", "- [ ] T0009 Duplicate numeric root\n",
    "- [ ] T010.1 First child\n- [ ] T010.01 Duplicate child\n",
    "- [ ] T010 Same title\n- [ ] T011 Same title\n",
    "```md\n- [ ] T009 Fenced duplicate\n```\n",
    "<!--\n- [ ] T009 HTML duplicate\n-->\n", "    - [ ] T009 Indented duplicate\n",
])
def test_native_revision_refuses_ambiguous_ids(three_phase_revision, extra):
    ctx = three_phase_revision
    parent = seal.commit_files(ctx.repo, ctx.parent, {ctx.path: ctx.original + extra}, "ambiguous fixture")
    git(ctx.repo, "update-ref", ctx.ref, parent)
    before = native_revision_snapshot(ctx)
    with pytest.raises(spec_format.SpecError, match="duplicate|ambiguous"):
        native_revision(ctx)
    assert native_revision_snapshot(ctx) == before


@pytest.mark.scenario("pr-revision/native-front-door-review-rounds/native-keyed-replay-returns-same-commit")
@pytest.mark.parametrize("later", ["commit", "control", "both"])
def test_native_keyed_replay_preserves_later_state(three_phase_revision, later):
    ctx = three_phase_revision
    first = native_revision(ctx)
    tip = first.commit
    if later in ("commit", "both"):
        tip = native_revision(ctx, ["A later review."], key="native-review-2").commit
    ctx.run.reload()
    assert ctx.run.state.gates_passed == [1, 2, 3]
    if later in ("control", "both"):
        ctx.run.pause("stuck", detail="Later operator decision")
        ctx.run.request_pause()
    before = native_revision_snapshot(ctx)
    replay = native_revision(ctx)
    assert replay.replayed and replay.commit == first.commit
    assert replay.status == ("running" if later == "commit" else "paused")
    if later != "commit":
        assert replay.pause["reason"] == "stuck" and ctx.run.pause_requested()
    assert git(ctx.repo, "rev-parse", ctx.ref).decode().strip() == tip
    assert native_revision_snapshot(ctx) == before
    with pytest.raises(ValueError, match="revision record conflict"):
        native_revision(ctx, ["Conflicting feedback."])
    assert native_revision_snapshot(ctx) == before


@pytest.mark.real_trust
@pytest.mark.scenario("pr-revision/native-front-door-review-rounds/native-keyed-replay-returns-same-commit")
@pytest.mark.parametrize("moved", [False, True])
def test_native_spec_revision_binds_reviewed_head(tmp_path, moved):
    from manifest_fixtures import manifest_text
    from test_specloop_spec_revision import Commands, RevisionGh, HEAD, REVISED

    path = tmp_path / "native.toml"
    path.write_text(manifest_text(tmp_path / "source").replace("[run]", '[run]\nformat = "speckit"'))
    target = Repo("owner/sample", engine="specloop", manifest=path)
    gh = RevisionGh()
    gh.branch = f"spec/{CHANGE}"
    commands = Commands(gh)
    commands.result["branch"] = gh.branch
    commands.fetch_head = "c" * 40 if moved else HEAD
    pushes = []
    def runner(args, **kwargs):
        if args[0] == "git" and args[3] == "push":
            pushes.append(args)
            gh.head = REVISED
            return subprocess.CompletedProcess(args, 0, "", "")
        return commands(args, **kwargs)
    def poll():
        return intake.revise_pending(target, queue.Labels(), tmp_path, runner=gh,
                                     specloop_runner=runner, git_runner=runner)
    fetched_ref = f"refs/nightshift/revise/{CHANGE}"
    if moved:
        with pytest.raises(RuntimeError, match="changed during revision"):
            poll()
        assert not commands.proposals and not pushes
        assert gh.head == HEAD and "agent:revise" in gh.labels
    else:
        poll()
        poll()
        proposal, = commands.proposals
        assert proposal[:9] == ["specloop", "propose", str(path), "--change", CHANGE,
                                "--branch", gh.branch, "--from-branch", fetched_ref]
        assert "--base-commit" not in proposal
        assert pushes[0][3:] == ["push", "origin", f"{REVISED}:refs/heads/{gh.branch}"] and len(pushes) == 1
        receipt = json.loads((tmp_path / "revisions/owner__sample/8.json").read_text())
        assert receipt["head"] == HEAD and receipt["change"] == CHANGE and receipt["delivered"]
        assert gh.head == REVISED and gh.labels == {"spec:review"}
    assert [c[3:] for c in commands.calls if c[0] == "git"] == [
        ["fetch", "origin", f"+refs/heads/{gh.branch}:{fetched_ref}"], ["rev-parse", fetched_ref]]
