"""The format boundary preserves OpenSpec's paths, calls and physical-line rules."""

import ast
import hashlib
import json
import re
import shlex
import subprocess
import tomllib
from dataclasses import asdict, fields, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from nightshift.cli import main
from specloop import agents, cli, loop, manifest, openspec, propose, revise, spec_format, state, tasks
from specloop.manifest import Manifest
from specloop.revision_store import RevisionStore
from specloop.seal import SAFE_FLAGS, git
from specloop.state import Run, TaskState
from test_finish_expect_head import service
from test_specloop_act import admitted
from test_specloop_command_json import BRANCH, CHANGE, template, validator_process
import spec_format_legacy_fixtures as legacy


def format_guide_section(text, heading):
    """Select exact column-zero physical lines, without Markdown interpretation."""
    match = re.fullmatch(r"(#{1,6})[ \t]+\S.*", heading)
    assert match is not None, "invalid heading boundary"
    lines = text.split("\n")
    assert lines.count(heading) == 1, "missing/duplicate heading boundary"
    start = lines.index(heading) + 1
    for end in range(start, len(lines)):
        boundary = re.match(r"^(#{1,6})[ \t]+", lines[end])
        if boundary and len(boundary[1]) <= len(match[1]):
            return "\n".join(lines[start:end])
    return "\n".join(lines[start:])


@pytest.mark.scenario("specloop-documentation/format-selection-guidance/compatibility-claims-retain-their-limits")
@pytest.mark.parametrize("wrapper", ["", "```markdown\n", "<!--\n", "    <div>\n"])
@pytest.mark.parametrize("ending", ["", "# Next", "##\tNext", "## Next"])
def test_format_guide_boundaries_are_physical(wrapper, ending):
    heading = "## Selected"
    body = "content\n### Child\nchild\n    ## Indented\n\t# Indented\n####### Not a heading\n##No space"
    text = wrapper + heading + "\n" + body
    if ending:
        text += "\n" + ending + "\nexcluded"
    assert format_guide_section(text, heading) == body
    for bad in (text.replace(heading, ""), text.replace(heading, " " + heading),
                text.replace(heading, heading + " "), text + "\n" + heading,
                text + "\n```\n" + heading, text + "\n<!--\n" + heading):
        with pytest.raises(AssertionError, match="missing/duplicate"):
            format_guide_section(bad, heading)




@pytest.mark.scenario("specloop-documentation/format-selection-guidance/operator-selects-the-supported-format")
@pytest.mark.scenario("specloop-documentation/format-selection-guidance/compatibility-claims-retain-their-limits")
@pytest.mark.parametrize("path,heading", [
    ("docs/specloop/README.md", "## How the engine works"),
    ("docs/specloop/command-json.md", "## Manifest format"),
])
def test_format_guidance_links_and_pending_check(path, heading):
    from doc_commands import argv_cases, parse_command, physical_commands
    from test_factory_land_docs import links_resolve
    from test_specloop_documentation_integrity import LINK, ROOT, REPOSITORY

    section = format_guide_section((ROOT / path).read_text(), heading)
    targets = {target for _, target in LINK.findall(section)}
    assert {f"{REPOSITORY}/issues/187", f"{REPOSITORY}/issues/184",
            "../../examples/specloop/self-hosting.toml"} <= targets
    links_resolve(path, section)
    admissions = []
    for _, command in physical_commands(section):
        if any(argv[1:2] == ["admit"] and "--json" in argv for argv in argv_cases(command)):
            admissions.extend(parse_command(command))
    assert admissions
    for args in admissions:
        assert args.handler is cli.dispatch and args.manifest and args.json
    if path == "docs/specloop/README.md":
        # Inspect checkbox state and its tracking links, not the human-check prose.
        checks = re.findall(r"^- \[([ xX])\] (.*(?:\n[ \t]+[^\n]+)*)", section, re.M)
        tracked = [(state, body) for state, body in checks
                   if f"{REPOSITORY}/issues/184" in {url for _, url in LINK.findall(body)}]
        assert len(tracked) == 1 and tracked[0][0] == " "
        assert f"{REPOSITORY}/issues/187" in {url for _, url in LINK.findall(tracked[0][1])}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, {"README.md": "fixture\n"})
    return root


def commit(repo, files):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(repo, "add", "-A")
    git(repo, *SAFE_FLAGS, "-c", "user.name=Test", "-c", "user.email=test@example.test",
        "commit", "-qm", "fixture")
    return git(repo, "rev-parse", "HEAD").decode().strip()


def run_context(repo, tmp_path):
    run = Run(tmp_path / "run")
    run.state.change = "sample-change"
    run.save()
    git(repo, "clone", "-q", "--bare", str(repo), str(run.dir / "repo.git"))
    m = Manifest(repo, "sample-change", "main", "main", "", {}, (), (), ())
    return loop.Ctx(run, m, {})


@pytest.mark.scenario("spec-format/format-seam/legacy-paths-and-publication-labels")
def test_openspec_adapter_matches_legacy_paths(tmp_path):
    adapter = spec_format.OPENSPEC
    for repo in (tmp_path, Path("relative/repo"), tmp_path / "repo with spaces"):
        assert adapter.change_dir(repo, "sample-change") == repo / "openspec/changes/sample-change"
        assert adapter.change_dir(repo, "sample-change") == openspec.change_dir(repo, "sample-change")
    assert adapter.tasks_path("sample-change") == "openspec/changes/sample-change/tasks.md"
    assert adapter.drafter_root("sample-change") == "openspec/changes/sample-change/"
    assert adapter.pr_kind == "OpenSpec change"
    assert adapter.pr_title("sample-change") == "sample-change: OpenSpec change via specloop"




@pytest.mark.scenario("spec-format/manifest-format-selection/omitted-and-explicit-openspec-agree")
@pytest.mark.parametrize("format_value", [None, "openspec"])
@pytest.mark.parametrize("overrides", [False, True])
def test_manifest_format_defaults_to_openspec(template, monkeypatch, capfd, format_value, overrides):
    path, repo = template
    text = path.read_text()
    if format_value is not None:
        text = text.replace("[run]", f'[run]\nformat = "{format_value}"')
    if not overrides:
        text = text.replace('change = "x"', f'change = "{CHANGE}"').replace(
            'branch = "spec/x"', f'branch = "{BRANCH}"')
    path.write_text(text)
    assert manifest.load(path).format == "openspec"
    kwargs = {"change": CHANGE, "branch": BRANCH} if overrides else {}
    m, effective = manifest.template(path, **kwargs)
    assert m.format == "openspec"
    assert (m.change, m.branch) == (CHANGE, BRANCH)
    assert spec_format.get(m) is spec_format.OPENSPEC
    direct = Manifest(repo, CHANGE, BRANCH, "main", "", {}, (), (), ())
    assert direct.format == "openspec" and spec_format.get(direct) is spec_format.get(m)
    validator = Mock(return_value=[])
    agent = Mock(side_effect=AssertionError("admission with --skip-feasibility must not run an agent"))
    monkeypatch.setattr(openspec, "validate", validator)
    monkeypatch.setattr(agents, "run", agent)
    flags = ["--change", CHANGE, "--branch", BRANCH] if overrides else []
    assert main(["admit", str(path), *flags, "--skip-feasibility", "--json"]) == 0
    document = json.loads(capfd.readouterr().out)
    assert document["version"] == 1 and document["outcome"] == "admitted"
    validator.assert_called_once()
    assert validator.call_args.args[1] == CHANGE
    agent.assert_not_called()
    run = Run.for_change(CHANGE, require_existing=True)
    assert run.manifest_path.read_text() == effective
    assert ("format" in tomllib.loads(effective)["run"]) is (format_value is not None)
    assert path.read_text() == text
    saved = cli._manifest(run)
    assert saved == m and spec_format.get(saved) is spec_format.OPENSPEC
    ctx = loop.Ctx(run, saved, {})
    assert ctx.tasks_path == f"openspec/changes/{CHANGE}/tasks.md"
    assert ctx.head() == document["head"]
    assert cli._tasks_path(ctx.repo, ctx.head(), CHANGE, spec_format.get(saved)) == ctx.tasks_path
    assert git(ctx.repo, "show", f"{ctx.head()}:{ctx.tasks_path}") == (repo / ctx.tasks_path).read_bytes()




@pytest.fixture(params=["old", "new-omitted", "new-explicit"])
def saved_openspec_run(request, template, capfd):
    """An old writer's paused run, or a new admission, isolated from live state."""
    path, repo = template
    text = path.read_text().replace('change = "x"', f'change = "{CHANGE}"').replace(
        'branch = "spec/x"', f'branch = "{BRANCH}"')
    if request.param == "new-explicit":
        text = text.replace("[run]", '[run]\nformat = "openspec"')
    path.write_text(text)
    run = Run.for_change(CHANGE)
    if request.param == "old":
        run.dir.mkdir(parents=True)
        run.manifest_path.write_text(text)
        git(repo, "clone", "-q", "--bare", str(repo), str(run.dir / "repo.git"))
        schema = legacy
    else:
        assert main(["admit", str(path), "--skip-feasibility", "--json"]) == 0
        assert json.loads(capfd.readouterr().out)["outcome"] == "admitted"
        schema = state
    head = git(repo, "rev-parse", BRANCH).decode().strip()
    task = tasks.parse((repo / f"openspec/changes/{CHANGE}/tasks.md").read_text())[0]
    saved = schema.RunState(
        change=CHANGE, status="paused", pause=schema.Pause("human_checks", task.key, "Review PR"),
        tasks={task.key: schema.TaskState(attempts=2, sessions=3, status="passed", candidate=head,
                                        candidate_base=head, note="Keep task identity", arbitrated=1)},
        gates_passed=[1], created=1700000000, pr="https://github.com/example/repo/pull/7",
        section_base={"1": head}, evidenced=[1], evidence_summary={"1": "verified"},
    )
    if request.param == "old":
        (run.dir / "state.json").write_text(json.dumps(asdict(saved), indent=2))
    else:
        run.state = saved
        run.save()
    return run, asdict(saved), task, head


@pytest.mark.scenario("spec-format/manifest-format-selection/saved-openspec-runs-remain-compatible")
def test_saved_openspec_runs_remain_compatible(saved_openspec_run):
    run, expected, task, head = saved_openspec_run
    before_manifest = run.manifest_path.read_bytes()
    before_state = (run.dir / "state.json").read_bytes()
    raw = tomllib.loads(before_manifest.decode())
    old_manifest = legacy.parse(raw)
    m = manifest.load(run.manifest_path)
    assert m.format == "openspec" and spec_format.get(m) is spec_format.OPENSPEC
    assert asdict(old_manifest) == {k: v for k, v in asdict(m).items() if k != "format"}
    assert not hasattr(old_manifest, "format")
    loaded = Run(run.dir, require_existing=True)
    assert asdict(loaded.state) == asdict(legacy.load_state(run.dir)) == expected
    ctx = loop.Ctx(loaded, m, {})
    assert ctx.head() == head
    committed_tasks = git(ctx.repo, "show", f"{head}:{ctx.tasks_path}").decode()
    assert tasks.parse(committed_tasks)[0] == task
    assert loaded.state.task(task.key).candidate == head
    assert loaded.state.pause.reason == "human_checks"
    assert run.manifest_path.read_bytes() == before_manifest
    assert (run.dir / "state.json").read_bytes() == before_state

    # A keyed review round remains usable after a save and reader restart.
    # RevisionStore is unchanged by the seam; inspect its persisted wire shape too.
    result = revise.revise_result(loaded, m, "Handle empty input", round_id="review-1")
    store = RevisionStore(run.dir, CHANGE, BRANCH, "review-1", ["Handle empty input"])
    record = store.load()
    assert set(record) == {"version", "change", "branch", "round_id", "payload_digest", "tasks",
                           "parent", "commit", "round", "status", "state_digest", "events_digest"}
    assert record["version"] == 1 and record["status"] == "completed"
    assert record["parent"] == head and record["commit"] == result.commit
    assert asdict(legacy.load_state(run.dir)) == {**expected, "status": "running", "pause": None}
    restarted = Run(run.dir, require_existing=True)
    assert asdict(restarted.state) == asdict(legacy.load_state(run.dir))
    snapshot = {p: p.read_bytes() for p in [run.manifest_path, run.dir / "state.json",
                                           run.dir / "events.jsonl", store.path]}
    replay = revise.revise_result(restarted, manifest.load(run.manifest_path),
                                  "Handle empty input", round_id="review-1")
    assert replay.replayed and replay.commit == result.commit
    assert {p: p.read_bytes() for p in snapshot} == snapshot
    assert run.manifest_path.read_bytes() == before_manifest
    assert git(ctx.repo, "rev-parse", ctx.ref).decode().strip() == result.commit
    revised = tasks.parse(git(ctx.repo, "show", f"{result.commit}:{ctx.tasks_path}").decode())
    assert revised[0] == task and len(revised) == 2


@pytest.mark.scenario("spec-format/format-seam/delegation-stays-late-bound")
@pytest.mark.parametrize("method, backend, tail, result", [
    ("change_dir", "change_dir", (), Path("replacement")),
    ("validate", "validate", (), ["problem"]),
    ("scenarios", "scenarios", (), {"cap/req/example": "text"}),
    ("finish", "archive", (False,), {"archive": False}),
    ("finish", "archive", (True,), {"archive": True}),
])
def test_delegation_is_late_bound(tmp_path, monkeypatch, method, backend, tail, result):
    call = getattr(spec_format.OPENSPEC, method)
    assert spec_format.SpecError is openspec.SpecError
    assert spec_format.ValidatorUnavailable is openspec.ValidatorUnavailable
    replacement = Mock(return_value=result)
    monkeypatch.setattr(openspec, backend, replacement)
    args = (tmp_path, "sample-change", *tail)
    backend_args = args[:2] if method == "finish" else args
    backend_kwargs = {"all_tasks_done": tail[0]} if method == "finish" else {}
    assert call(*args) is result
    replacement.assert_called_once_with(*backend_args, **backend_kwargs)
    for cls in (openspec.SpecError, openspec.ValidatorUnavailable, OSError):
        error = cls("same exception")
        replacement = Mock(side_effect=error)
        monkeypatch.setattr(openspec, backend, replacement)
        with pytest.raises(cls) as raised:
            call(*args)
        assert raised.value is error
        replacement.assert_called_once_with(*backend_args, **backend_kwargs)


@pytest.mark.scenario("spec-format/format-seam/delegation-stays-late-bound")
@pytest.mark.parametrize("failure", [None, "timeout", "ENOTFOUND", "domain"])
def test_validator_invocation_and_classification(tmp_path, monkeypatch, validator_process, failure):
    process = validator_process
    process.return_value = subprocess.CompletedProcess([], 0, '{"items": []}', "")
    if failure == "timeout":
        process.side_effect = subprocess.TimeoutExpired(openspec.resolve_binary(), 120)
    elif failure:
        process.return_value = subprocess.CompletedProcess([], 1, "not JSON", failure)
    monkeypatch.setenv("FORMAT_TEST_ENV", "inherited")
    if failure:
        expected = openspec.ValidatorUnavailable if failure == "timeout" else openspec.SpecError
        with pytest.raises(expected) as raised:
            spec_format.OPENSPEC.validate(tmp_path, "sample-change")
        assert type(raised.value) is expected
        if failure == "timeout":
            assert raised.value.__cause__ is process.side_effect
    else:
        assert spec_format.OPENSPEC.validate(tmp_path, "sample-change") == []
    process.assert_called_once()
    assert process.call_args.args == ([
        openspec.resolve_binary(),
        "validate", "sample-change", "--strict", "--json",
    ],)
    kwargs = process.call_args.kwargs
    process.probe.assert_called_once_with([openspec.resolve_binary(), "--version"], **kwargs)
    assert kwargs["cwd"] == tmp_path
    assert kwargs["timeout"] == 120
    assert kwargs["capture_output"] is True and kwargs["text"] is True
    assert kwargs["env"]["FORMAT_TEST_ENV"] == "inherited"
    assert all(kwargs["env"][key] == value for key, value in openspec.ENV.items())


@pytest.mark.scenario("spec-format/committed-task-discovery/live-and-archived-task-precedence")
def test_committed_task_candidates_preserve_precedence(repo, monkeypatch):
    change = "sample.change+extra"
    live = f"openspec/changes/{change}/tasks.md"
    archive = "openspec/changes/archive/"
    newest = f"{archive}2026-11-07-{change}/tasks.md"
    oldest = f"{archive}2025-12-31-{change}/tasks.md"
    head = commit(repo, dict.fromkeys([
        newest, oldest,
        f"{archive}2026-11-08-{change}-other/tasks.md",
        f"{archive}2026-11-08-other-{change}/tasks.md",
        f"{archive}2026-11-08-sampleXchangeextra/tasks.md",
        f"{archive}2026-1-08-{change}/tasks.md",
        f"{archive}2026-11-08-{change}/tasksXmd",
        f"{archive}2026-11-08-{change}/nested/tasks.md",
    ], "archived tasks\n"))
    spy = Mock(wraps=git)
    monkeypatch.setattr(spec_format, "git", spy)
    assert spec_format.OPENSPEC.tasks_paths_at(repo, head, change) == [newest, oldest]
    spy.assert_called_once_with(repo, "ls-tree", "-r", "--name-only", head, live, archive)
    assert cli._tasks_path(repo, head, change) == newest
    live_head = commit(repo, {live: "live tasks\n"})
    assert spec_format.OPENSPEC.tasks_paths_at(repo, live_head, change) == [live]
    assert cli._tasks_path(repo, live_head, change) == live
    # The requested commit, not the working tree, controls selection.
    assert spec_format.OPENSPEC.tasks_paths_at(repo, head, change) == [newest, oldest]


@pytest.mark.scenario("spec-format/committed-task-discovery/missing-tasks-retain-read-failure")
def test_missing_tasks_return_the_live_read_failure(repo):
    head = commit(repo, {"openspec/changes/archive/2026-11-07-other/tasks.md": "other"})
    live = "openspec/changes/sample-change/tasks.md"
    (repo / live).parent.mkdir(parents=True)
    (repo / live).write_text("uncommitted tasks must not be used")
    paths = spec_format.OPENSPEC.tasks_paths_at(repo, head, "sample-change")
    assert paths == [live]
    assert paths[0] == cli._tasks_path(repo, head, "sample-change")
    with pytest.raises(RuntimeError):
        git(repo, "show", f"{head}:{paths[0]}")


@pytest.mark.scenario("spec-format/context-and-human-checks/existing-context-files-are-copied")
@pytest.mark.parametrize("mask", range(8))
@pytest.mark.parametrize("relative", [False, True])
def test_context_entries(tmp_path, monkeypatch, mask, relative):
    monkeypatch.chdir(tmp_path)
    repo = Path("repo") if relative else tmp_path / "repo"
    src = repo / "openspec/changes/sample-change"
    src.mkdir(parents=True)
    expected = []
    for index, name in enumerate(("proposal.md", "design.md", "specs")):
        if mask & (1 << index):
            path = src / name
            if name == "specs":
                (path / "cap").mkdir(parents=True)
                (path / "cap/spec.md").write_text("scenario content")
            else:
                path.write_text(name)
            expected.append((path, name))
    (src / "tasks.md").write_text("not context")
    entries = spec_format.OPENSPEC.context_files(repo, "sample-change")
    assert entries == expected
    for path, name in entries:
        assert path.is_absolute() is not relative
        if name == "specs":
            assert path.is_dir()
            assert (path / "cap/spec.md").read_text() == "scenario content"
        else:
            assert path.read_text() == name

    # All three context consumers preserve optional entries and merge specs.
    m = Manifest(repo, "sample-change", "main", "main", "", {}, (), (), ())
    ctx = SimpleNamespace(repo=repo, m=m, run=Run(tmp_path / "run"))
    task = tasks.parse("## 1. Core\n- [ ] 1.1 Implement `cap/req/example`\n")[0]
    adapter = Mock(wraps=spec_format.OPENSPEC)
    adapter.scenarios.return_value = {"cap/req/example": "scenario from adapter"}
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    monkeypatch.setattr(loop, "git", Mock(return_value=b""))
    for kind in ("implementation", "gate", "review"):
        context = tmp_path / kind
        (context / "specs").mkdir(parents=True)
        (context / "specs/retained.md").write_text("retained")
        if kind == "implementation":
            loop._context(ctx, task, TaskState(), repo, context, False)
        elif kind == "gate":
            loop._gate_context(ctx, 1, "head", [task], TaskState(), repo, context)
        else:
            loop._review_context(ctx, task, TaskState(), "head", "candidate", repo, context)
        for name in ("proposal.md", "design.md", "specs/cap/spec.md"):
            source, copied = src / name, context / name
            assert copied.exists() == source.exists()
            if source.exists():
                assert copied.read_bytes() == source.read_bytes()
        assert (context / "specs/retained.md").read_text() == "retained"
        assert not (context / "tasks.md").exists()
        rendered = (context / ("section.md" if kind == "gate" else "task.md")).read_text()
        assert "### `cap/req/example`\nscenario from adapter" in rendered
    assert adapter.context_files.call_args_list == [call(repo, m.change)] * 3
    assert adapter.scenarios.call_args_list == [call(repo, m.change)] * 3
    assert selection.call_args_list == [call(m)] * 6


@pytest.mark.scenario("spec-format/context-and-human-checks/literal-committed-checks-remain-pending")
def test_committed_checklist_physical_lines(repo):
    path = "openspec/changes/sample-change/design.md"
    text = """- [ ] outside
## Human checks
 \t- [ ] first\t
### Verification and human rollout checklist
- [ ] nested
### Other subsection
- [ ] still pending\u2028###### Deepest
- [ ] deepest
- [x] checked
- [X] checked uppercase
* [ ] other bullet
- [] missing space
- [ ]missing separator
- [ ] \t
- [ ] \u00a0
- [ ]\u00a0wrong separator
## Other
- [ ] outside again
### Verification and human rollout checklist
- [ ] rollout
#### Deeper
- [ ] deeper rollout
### Other
- [ ] outside third-level
## human checks
- [ ] wrong case
## Human checks ##
- [ ] closing hashes
\u00a0## Human checks
- [ ] non-ASCII heading padding
### Verification and human rollout checklist ###
- [ ] decorated rollout
## Human checks
####### Not a boundary
#No heading separator
- [ ] retained
#\tEnd
- [ ] outside shallow
### Verification and human rollout checklist
\t- [ ]\tlast  `item`! \t
- [ ] \u00a0keep padding\u00a0
- [ ] first"""
    head = commit(repo, {path: text.replace("\n", "\r\n")})
    (repo / path).write_text("## Human checks\n- [ ] dirty source\n")
    expected = [f"- [ ] {item}" for item in (
        "first", "nested", "still pending", "deepest", "rollout", "deeper rollout", "retained")]
    expected += ["- [ ]\tlast  `item`!", "- [ ] \u00a0keep padding\u00a0", "- [ ] first"]
    assert spec_format.OPENSPEC.human_checks(repo, head, "sample-change") == expected
    ctx = SimpleNamespace(repo=repo, m=SimpleNamespace(change="sample-change", format="openspec"))
    assert loop.design_human_checks(ctx, head) == expected


@pytest.mark.scenario("spec-format/context-and-human-checks/literal-committed-checks-remain-pending")
@pytest.mark.parametrize("opening, closing, indent", [
    ("```md", "```", ""), ("<!--", "-->", ""), ("", "", "    "),
])
def test_literal_examples_stay_pending(repo, tmp_path, monkeypatch, opening, closing, indent):
    text = "\n".join(indent + line for line in [
        opening, "## Human checks", "- [ ] example", "# Boundary", "- [ ] excluded", closing])
    path = "openspec/changes/sample-change/design.md"
    head = commit(repo, {path: text, "openspec/changes/sample-change/tasks.md":
                         "## 1. Core\n- [x] 1.1 Implement\n"})
    assert spec_format.OPENSPEC.human_checks(repo, head, "sample-change") == ["- [ ] example"]
    (repo / path).write_text("## Human checks\n- [ ] uncommitted\n")
    ctx = run_context(repo, tmp_path)
    monkeypatch.setattr(loop, "prepare_publish", lambda ctx: True)
    assert loop.finals(ctx, head) is False
    assert ctx.run.state.pause.reason == "ready_to_publish"
    for rendered in (ctx.run.state.pause.detail, cli._pr_body(ctx)):
        assert [line for line in rendered.splitlines() if line.startswith("- [ ]")] == ["- [ ] example"]


@pytest.mark.scenario("spec-format/context-and-human-checks/unreadable-design-blocks-readiness")
@pytest.mark.parametrize("failure", ["lookup", "read", "decode", "os", "subprocess"])
def test_design_read_failures_keep_diagnostic(repo, tmp_path, monkeypatch, failure):
    path = "openspec/changes/sample-change/design.md"
    head = commit(repo, {path: "## Human checks\n- [ ] Pending\n"})
    ctx = run_context(repo, tmp_path)
    monkeypatch.setattr(loop, "prepare_publish", lambda ctx: True)
    error = {"lookup": RuntimeError("tree lookup failed"), "read": RuntimeError("blob read failed"),
             "os": OSError("read failed"), "subprocess": subprocess.SubprocessError("read failed")}.get(failure)

    def fail(root, *args):
        if args[0] == "ls-tree" and failure == "lookup":
            raise error
        if args[0] == "show":
            if failure == "decode":
                return b"\xff"
            raise error
        return git(root, *args)

    monkeypatch.setattr(spec_format, "git", fail)
    with pytest.raises(RuntimeError) as raised:
        spec_format.OPENSPEC.human_checks(repo, head, "sample-change")
    assert str(raised.value).startswith(f"could not collect human checks from {head}:{path}: ")
    if failure == "decode":
        assert isinstance(raised.value.__cause__, UnicodeDecodeError)
    else:
        assert raised.value.__cause__ is error
        assert str(raised.value) == f"could not collect human checks from {head}:{path}: {error}"
    for render in (lambda: loop.finals(ctx, head), lambda: cli._pr_body(ctx)):
        with pytest.raises(RuntimeError) as blocked:
            render()
        assert str(blocked.value) == str(raised.value)
        assert ctx.run.state.pause is None


@pytest.mark.scenario("spec-format/context-and-human-checks/unreadable-design-blocks-readiness")
@pytest.mark.parametrize("design", [None, "", "## Human checks\n- [x] done\n## Other\n- [ ] excluded"])
def test_absent_or_empty_committed_design(repo, design):
    path = "openspec/changes/sample-change/design.md"
    head = (git(repo, "rev-parse", "HEAD").decode().strip() if design is None
            else commit(repo, {path: design}))
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text("## Human checks\n- [ ] uncommitted\n")
    assert spec_format.OPENSPEC.human_checks(repo, head, "sample-change") == []


@pytest.mark.scenario("spec-format/context-and-human-checks/literal-committed-checks-remain-pending")
def test_human_check_wrapper_routes_and_preserves_existing_rows(repo, tmp_path, monkeypatch):
    ctx = run_context(repo, tmp_path)
    adapter = Mock(spec=spec_format.SpecFormat)
    adapter.human_checks.return_value = ["- [ ] existing", "- [ ] new", "- [ ] new"]
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    existing = [" \t- [ ] existing\t", "- [ ] existing"]
    before = list(existing)
    assert loop.append_design_checks(ctx, "evaluated-head", existing) == existing + ["- [ ] new"]
    assert existing == before
    selection.assert_called_once_with(ctx.m)
    adapter.human_checks.assert_called_once_with(ctx.repo, "evaluated-head", ctx.m.change)


@pytest.mark.scenario("spec-format/engine-routing-compatibility/drafting-and-revision-retain-behavior")
@pytest.mark.parametrize("error", [None, spec_format.SpecError, spec_format.ValidatorUnavailable])
def test_committed_scenario_lookup_routes_and_cleans_up(repo, tmp_path, monkeypatch, error):
    head = commit(repo, {"scenario-source": "evaluated"})
    commit(repo, {"scenario-source": "newer"})
    ctx = run_context(repo, tmp_path)
    extracted = []
    result = {"cap/req/example": "scenario text"}

    def scenarios(source, change):
        assert change == ctx.m.change
        assert (source / "scenario-source").read_text() == "evaluated"
        extracted.append(source)
        if error:
            raise error("scenario lookup failed")
        return result

    adapter = Mock(spec=spec_format.SpecFormat)
    adapter.scenarios.side_effect = scenarios
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    assert loop.openspec is openspec
    if error:
        with pytest.raises(error, match="scenario lookup failed"):
            loop._scenarios_at(ctx, head)
    else:
        assert loop._scenarios_at(ctx, head) is result
    selection.assert_called_once_with(ctx.m)
    assert len(extracted) == 1 and not extracted[0].parent.exists()




@pytest.mark.scenario("spec-format/engine-routing-compatibility/consumers-contain-no-direct-format-operations")
def test_no_direct_openspec_path_literals_outside_adapter():
    for module in (cli, loop, revise, propose):
        source = Path(module.__file__).read_text()
        assert "openspec/changes/" not in source, module.__name__
        assert not any(isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                       and node.value.id == "openspec" for node in ast.walk(ast.parse(source))), module.__name__
        assert not any(isinstance(node, ast.ImportFrom) and node.module == "specloop.openspec"
                       for node in ast.walk(ast.parse(source))), module.__name__
    assert cli.openspec is openspec


@pytest.mark.scenario("spec-format/engine-routing-compatibility/consumers-contain-no-direct-format-operations")
@pytest.mark.scenario("spec-format/engine-routing-compatibility/drafting-and-revision-retain-behavior")
@pytest.mark.scenario("spec-drafting/native-feature-drafting/openspec-prompt-remains-unchanged")
def test_drafting_prompt_retains_exact_bytes():
    rendered = propose.PROMPT.format(
        change="sample-change", drafter_root=spec_format.OPENSPEC.drafter_root("sample-change"),
        idea="An idea\nwith details", sources="- README.md", in_flight="", feedback="")
    # SHA-256 of the complete rendered pre-extraction prompt with these inputs.
    assert hashlib.sha256(rendered.encode()).hexdigest() == (
        "42095800fc5d9f88e7e8e971d48b90efe0c1269a291524178baffcdb7b463ec1")


@pytest.mark.scenario("spec-format/format-seam/delegation-stays-late-bound")
@pytest.mark.parametrize("error", [None, spec_format.SpecError, spec_format.ValidatorUnavailable])
def test_admission_and_lint_retain_late_bound_scenario_errors(repo, monkeypatch, error):
    path = "openspec/changes/sample-change/tasks.md"
    head = commit(repo, {path: "## 1. Core\n- [ ] 1.1 Implement `cap/req/example`\n"})
    m = Manifest(repo, "sample-change", "main", "main", "", {}, (), (), ())
    validator = Mock(return_value=["validation problem"])
    lookup = Mock(return_value={"cap/req/example": "text"})
    if error:
        lookup.side_effect = error("scenario failure")
    monkeypatch.setattr(openspec, "validate", validator)
    monkeypatch.setattr(openspec, "scenarios", lookup)
    for operation in (lambda: cli.problems_with(repo, head, m), lambda: propose.lint(repo, m.change)):
        if error is spec_format.ValidatorUnavailable:
            with pytest.raises(error) as raised:
                operation()
            assert raised.value is lookup.side_effect
        else:
            assert operation() == ["validation problem"] + (["scenario failure"] if error else [])
    assert validator.call_count == lookup.call_count == 2
    assert validator.call_args_list == lookup.call_args_list
    assert all(c.args[1] == m.change for c in validator.call_args_list)


@pytest.mark.scenario("spec-format/engine-routing-compatibility/drafting-and-revision-retain-behavior")
@pytest.mark.scenario("spec-drafting/native-feature-drafting/openspec-prompt-remains-unchanged")
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("root", ["openspec/changes/sample-change/", "format-fixture/sample-change/"])
def test_drafting_routes_paths_validation_confinement_and_staging(repo, tmp_path, monkeypatch, root, explicit):
    from test_specloop_propose import M

    M = replace(M, format="openspec") if explicit else M
    task_path = root + "work-items.md"
    adapter = Mock(spec=spec_format.SpecFormat)
    adapter.tasks_path.return_value = task_path
    adapter.parse_tasks.side_effect = tasks.parse
    adapter.drafter_root.return_value = root
    adapter.validate.side_effect = lambda *a: []
    adapter.scenarios.return_value = {"cap/req/example": "text"}
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    monkeypatch.setattr(propose, "in_flight", lambda *a: {"tests/shared.py": ["other"]})
    monkeypatch.setattr(propose, "start_commit", lambda *a: "main")
    monkeypatch.setattr(propose.critic, "open_pr_files", lambda *a: ({}, []))
    events = []

    def draft(call):
        events.append("draft")
        assert f"the change, under {root}. Do not edit any other file." in call.prompt
        assert call.prompt == propose.PROMPT.format(
            change="sample-change", drafter_root=root, idea="idea", sources="- (no extra sources named)",
            in_flight=propose._in_flight_section({"tests/shared.py": ["other"]}), feedback="")
        path = call.cwd / task_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("## 1. Core\n- [ ] 1.1 Implement `cap/req/example` in `tests/new.py`; "
                        "do not edit `tests/shared.py`.\n")
        return agents.Result("ok", {"status": "drafted", "questions": []})

    def review(kind):
        def check(*a, **kw):
            events.append(kind)
            return {"contradictions": [], "risks": []}
        return check

    monkeypatch.setattr(propose.feasibility, "check", review("feasibility"))
    monkeypatch.setattr(propose.critic, "check", review("critic"))
    spy = Mock(wraps=git)
    monkeypatch.setattr(propose, "git", spy)
    result = propose.draft(repo, "main", "sample-change", "idea", [], M, tmp_path / "draft", agent=draft)
    assert result.commit and not result.problems and not result.risks
    assert events == ["draft", "feasibility", "critic"]
    selection.assert_called_once_with(M)
    adapter.validate.assert_called_once_with(result.work, "sample-change")
    adapter.scenarios.assert_called_once_with(result.work, "sample-change")
    assert adapter.tasks_path.call_args_list == [call("sample-change")] * 3
    assert adapter.parse_tasks.call_count == 3
    assert [c for c in spy.call_args_list if c.args[1] == "add"] == [
        call(result.work, "add", "-A", root.removesuffix("/"))]
    assert git(result.work, "show", "-s", "--format=%s", "HEAD") == (
        b"Draft OpenSpec change sample-change (specloop propose, round 1)\n")
    assert git(result.work, "show", f"HEAD:{task_path}") == (result.work / task_path).read_bytes()
    (result.work / "outside.py").write_text("stray")
    assert propose.lint(result.work, "sample-change", adapter) == [
        f"outside.py: only files under {root} may change"]
    path = result.work / task_path
    path.write_text(path.read_text().replace("tests/new.py", "tests/shared.py"))
    assert propose.overlaps(result.work, "sample-change", {"tests/shared.py": ["other"]}, adapter) == [
        "task 1.1: `tests/shared.py` is also being changed by other; put these tests in a new file"]


@pytest.mark.scenario("spec-format/engine-routing-compatibility/drafting-and-revision-retain-behavior")
def test_in_flight_selects_each_runs_manifest_adapter(tmp_path, monkeypatch):
    manifests, adapters, reads = {}, {}, {}
    for change in ("first", "second"):
        run = Run.for_change(change)
        run.pause("requested")
        m = Manifest(tmp_path, change, f"branch/{change}", "main", "", {}, (), (), ())
        manifests[run.dir / "manifest.toml"] = m
        adapter = Mock(spec=spec_format.SpecFormat)
        adapter.tasks_path.return_value = f"format-fixture/{change}/work-items.md"
        adapters[change] = adapter
        reads[(run.dir / "repo.git", "show", f"{m.branch}:{adapter.tasks_path.return_value}")] = (
            f"## 1. Core\n- [ ] 1.1 Update `tests/{change}.py`; do not edit `tests/shared.py`.\n".encode())
    monkeypatch.setattr(propose.manifest, "load", manifests.__getitem__)
    selection = Mock(side_effect=lambda m: adapters[m.change])
    monkeypatch.setattr(spec_format, "get", selection)
    reader = Mock(side_effect=lambda *args: reads[args])
    monkeypatch.setattr(propose, "git", reader)
    assert propose.in_flight(tmp_path, "new") == {
        "tests/first.py": ["first"], "tests/second.py": ["second"]}
    assert selection.call_args_list == [call(m) for m in manifests.values()]
    assert reader.call_args_list == [call(*args) for args in reads]
    for change, adapter in adapters.items():
        adapter.tasks_path.assert_called_once_with(change)


@pytest.mark.scenario("spec-format/engine-routing-compatibility/drafting-and-revision-retain-behavior")
@pytest.mark.parametrize("root", ["openspec/changes/sample-change/", "format-fixture/sample-change/"])
def test_draft_ready_notification_routes_root_and_preserves_text(tmp_path, monkeypatch, root):
    m = Manifest(tmp_path, "sample-change", "draft/custom", "main", "", {}, (), (), ())
    draft = propose.Draft(m.change, tmp_path / "work", commit="a" * 40, branch=m.branch)
    adapter = Mock(wraps=spec_format.OPENSPEC)
    adapter.drafter_root.return_value = root
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    monkeypatch.setattr(cli.manifest, "template", lambda *a, **kw: (m, ""))
    monkeypatch.setattr(propose, "draft", lambda *a, **kw: draft)
    monkeypatch.setattr(propose, "publish_branch", lambda *a: m.branch)
    monkeypatch.setattr(cli, "load_env", lambda *a: {})
    sent = []
    monkeypatch.setattr(cli, "notifier", lambda *a: sent.append)
    path = str(tmp_path / "manifest with spaces.toml")
    assert cli.main(["propose", path, "--idea", "A feature"]) == 0
    assert sent == [cli.messages.Message(
        cli.messages.ACTION, m.change, "Review the drafted change, then admit it",
        f"draft ready on {m.branch} in {m.repo} ({draft.commit[:12]}); review {root} there",
        shlex.join(["nightshift", "admit", path, "--change", m.change,
                    "--branch", m.branch, "--head", draft.commit]))]
    selection.assert_called_once_with(m)
    adapter.drafter_root.assert_called_once_with(m.change)


@pytest.mark.scenario("spec-format/engine-routing-compatibility/publication-and-interrupted-finish-retain-behavior")
@pytest.mark.parametrize("custom_labels", [False, True])
def test_publication_routes_labels_and_preserves_body(service, monkeypatch, custom_labels):
    run = service.run
    run.pause("ready_to_publish")
    adapter = Mock(wraps=spec_format.OPENSPEC)
    adapter.pr_kind = "Fixture change" if custom_labels else "OpenSpec change"
    title = f"{CHANGE}: OpenSpec change via specloop"
    if custom_labels:
        title = "Fixture title"
        adapter.pr_title.return_value = title
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    monkeypatch.setattr(loop, "_fetch_publish_base", lambda *a, **kw: service.local_head())
    monkeypatch.setattr(loop, "_publish_needs_prepare", lambda *a: False)
    run.state.pr = ""
    monkeypatch.setattr(cli, "_existing_pr", lambda *a: "")
    gh = Mock(return_value=service.url)
    monkeypatch.setattr(cli, "_gh", gh)
    assert cli._publish(SimpleNamespace(change=CHANGE), run) == 0
    body = (f"{adapter.pr_kind} `{CHANGE}`, driven by specloop: 1 tasks, each sealed, verified by the "
            "harness and independently reviewed.\n\n## Checks a human must make\n\n"
            "- [ ] `cap/req/manual` — Human checks this\n\n"
            "## Non-blocking review findings\n\n(none)\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n")
    gh.assert_called_once_with("pr", "create", "--draft", "-R", "example/repo", "--head", BRANCH,
                               "--base", "main", "--title", title, "--body", body)
    adapter.pr_title.assert_called_once_with(CHANGE)
    assert all(c.args[0].change == CHANGE for c in selection.call_args_list)
    assert run.state.pr == service.url and run.state.pause.reason == "human_checks"


@pytest.mark.scenario("spec-format/engine-routing-compatibility/publication-and-interrupted-finish-retain-behavior")
@pytest.mark.parametrize("failure", ["push", "ready"])
def test_finish_routes_and_reuses_archive_after_interruption(service, monkeypatch, capfd, failure):
    adapter = Mock(wraps=spec_format.OPENSPEC)
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    original = service.local_head()
    saved = (service.run.dir / "state.json").read_bytes()
    args = SimpleNamespace(change=CHANGE, expect_head="0" * 40)
    assert cli._finish(args, service.run) == 1
    adapter.tasks_paths_at.assert_not_called()
    adapter.finish.assert_not_called()
    assert f"expected head {'0' * 40}; observed run branch head {original}; refusing to finish" in capfd.readouterr().err
    args.expect_head = original
    service.failure = failure
    with pytest.raises(RuntimeError, match=f"{failure} unavailable"):
        cli._finish(args, service.run)
    archived = service.local_head()
    assert archived != original
    assert (service.run.dir / "state.json").read_bytes() == saved
    adapter.finish.assert_called_once()
    assert adapter.finish.call_args.args[1] == CHANGE
    assert adapter.finish.call_args.kwargs == {"all_tasks_done": True}
    assert git(service.bare, "show", "-s", "--format=%s", archived) == (
        f"specloop: archive {CHANGE} into openspec/specs\n".encode())
    assert service.archives == [CHANGE]
    service.failure = None
    args.expect_head = archived
    assert cli._finish(args, service.run) == 0
    assert service.local_head() == service.remote_head() == archived
    assert service.run.state.status == "done" and service.run.state.pause is None
    assert asdict(Run(service.run.dir).state) == asdict(service.run.state)
    assert adapter.finish.call_count == 1
    assert adapter.parse_tasks.call_count == 2
    assert adapter.tasks_paths_at.call_args_list == [
        call(service.bare, original, CHANGE), call(service.bare, archived, CHANGE)]
    assert capfd.readouterr().out == f"{CHANGE}: archived and marked ready for review — {service.url}\n"


@pytest.mark.scenario("spec-format/committed-task-discovery/missing-tasks-retain-read-failure")
@pytest.mark.scenario("spec-format/engine-routing-compatibility/publication-and-interrupted-finish-retain-behavior")
@pytest.mark.parametrize("json_output", [False, True])
def test_status_routes_each_manifest_and_isolates_missing_tasks(repo, tmp_path, monkeypatch, capsys, json_output):
    broken = run_context(repo, tmp_path / "broken")
    head = commit(repo, {"openspec/changes/sample-change/tasks.md": "## 1. Core\n- [x] 1.1 Implement\n"})
    healthy = run_context(repo, tmp_path / "healthy")
    contexts = {"broken": broken, "healthy": healthy}
    monkeypatch.setattr(cli.Run, "for_change", lambda name, **kw: contexts[name].run)
    monkeypatch.setattr(cli, "iter_run_dirs", lambda: [Path(name) for name in contexts])
    monkeypatch.setattr(cli, "_manifest", lambda run: next(c.m for c in contexts.values() if c.run is run))
    from specloop import serve
    monkeypatch.setattr(serve, "liveness", lambda: "not running")
    adapter = Mock(wraps=spec_format.OPENSPEC)
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    saved = {c.run.dir / "state.json": (c.run.dir / "state.json").read_bytes() for c in contexts.values()}
    for method in ("save", "event", "pause", "lock"):
        monkeypatch.setattr(Run, method, Mock(side_effect=AssertionError("status mutated state")))
    monkeypatch.setattr(cli, "_finish", Mock(side_effect=AssertionError("status retried finish")))
    assert cli.status(SimpleNamespace(json=json_output, change=None, events=0)) == 0
    output = capsys.readouterr().out
    if json_output:
        entries = json.loads(output)["runs"]
        assert entries[0]["pause"]["reason"] == "status_error"
        assert "tasks.md" in entries[0]["error"]
        assert entries[1]["tasks"] == {"done": 1, "total": 1}
        assert entries[1]["head"] == head
    else:
        assert "(could not read tasks:" in output
        assert f"  tasks: 1/1 done on main @ {head[:12]}\n" in output
    assert selection.call_args_list == [call(broken.m), call(healthy.m)]
    assert all(c.args[0] is ctx.m for c, ctx in zip(selection.call_args_list, contexts.values()))
    assert adapter.tasks_paths_at.call_args_list == [
        call(c.repo, c.head(), c.m.change) for c in contexts.values()]
    assert {p: p.read_bytes() for p in saved} == saved
