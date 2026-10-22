import json
import shlex
from unittest.mock import Mock

import pytest

from specloop import agents, feasibility, loop, manifest, tasks, verify
from specloop.state import Run


@pytest.mark.parametrize("consumer", ["implementer", "feasibility"])
@pytest.mark.parametrize("with_prepare", [
    pytest.param(True, id="multiple-prepare-steps", marks=pytest.mark.scenario(
        "verify-preparation/preparation-visible-in-agent-recipes/prepare-commands-precede-checks")),
    pytest.param(False, id="no-preparation", marks=pytest.mark.scenario(
        "verify-preparation/preparation-visible-in-agent-recipes/no-preparation-configured")),
])
def test_implementer_recipe_and_feasibility_review_include_prepare_steps(
        tmp_path, monkeypatch, consumer, with_prepare):
    m = manifest.parse({
        "run": {"repo": str(tmp_path / "repo"), "change": "recipe", "branch": "spec/recipe"},
        "profiles": {"p": {"adapter": "codex", "model": "test"}},
        "roles": {"implement": "p", "review": "p"},
        "paths": {"allowed": ["src/**"], "frozen": ["openspec/**"]},
        "vars": {"tool": "/tools with spaces/runner", "label": "owner's $cache; literal",
                 "src": "wrong-source", "build": "wrong-build", "sha": "wrong-revision"},
        "prepare": [
            {"id": "z-copy", "run": ["{tool}", "copy", "{src}/source tree", "{build}/project"]},
            {"id": "a-install", "run": ["{tool}", "install", "{label}", "{sha}"], "expect": 3},
        ] if with_prepare else [],
        "verify": [
            {"id": "z-tests", "run": ["{tool}", "test", "{src}/test suite", "{build}/project", "{sha}"]},
            {"id": "a-negative", "run": ["{tool}", "negative", "{label}", ""], "expect": 1},
        ],
    })

    # Prompt generation may extract a checkout and chmod it, but must never
    # execute any recipe command or invoke a real provider.
    def unexpected_execution(*args, **kwargs):
        pytest.fail("prompt generation executed a recipe step or a real agent")

    monkeypatch.setattr(verify, "run_steps", unexpected_execution)
    monkeypatch.setattr(verify, "run_extracted", unexpected_execution)
    monkeypatch.setattr(agents, "run", unexpected_execution)
    monkeypatch.setattr(agents, "homes_dir", lambda: tmp_path / "homes")
    monkeypatch.setattr(feasibility.subprocess, "Popen", unexpected_execution)
    process = Mock()
    monkeypatch.setattr(feasibility.subprocess, "run", process)
    extract = Mock(side_effect=lambda repo, rev, checkout: checkout.mkdir(parents=True))
    monkeypatch.setattr(verify, "extract", extract)

    if consumer == "implementer":
        ctx = loop.Ctx(Run(tmp_path / "run"), m, {})
        task = tasks.parse("## 1. Recipe\n- [ ] 1.1 Show preparation\n")[0]
        prompt = loop._implement_prompt(ctx, task, False, tmp_path / "context")
        build, revision, exit_label = "build", "HEAD", "expect exit"
        assert "Do not commit, push, create branches or worktrees, use the network, or read credentials." in prompt
        extract.assert_not_called()
        process.assert_not_called()
    else:
        out = tmp_path / "review"
        result = {"contradictions": [], "risks": []}
        agent = Mock(return_value=agents.Result("ok", result))
        assert feasibility.check(m.repo, "candidate-revision", m, out, agent=agent) == result
        agent.assert_called_once()
        call = agent.call_args.args[0]
        prompt = call.prompt
        build, revision, exit_label = "<build dir>", "candidate-revision", "must exit"
        assert call.access == "read"
        assert call.profile == m.roles["review"]
        assert call.cwd == out / "checkout"
        assert "Do not modify anything." in prompt
        extract.assert_called_once_with(m.repo, revision, out / "checkout")
        process.assert_called_once_with(["chmod", "-R", "u+w", str(out / "checkout")], check=False)
        assert not (out / "checkout").exists()
        assert json.loads((out / "feasibility.json").read_text()) == result

    assert "Trusted preparation runs under the harness with network access before offline verification." in prompt
    assert "Steps run from the build directory with read-only extracted source." in prompt
    assert "Source, build, and revision values below illustrate the recipe, not actual sandbox paths." in prompt
    assert "{tool}" not in prompt and "{label}" not in prompt
    for placeholder in ("{src}", "{build}", "{sha}", "wrong-source", "wrong-build", "wrong-revision"):
        assert placeholder not in prompt

    prepare_heading = prompt.index("Prepare:")
    verify_heading = prompt.index("Verify:")
    assert prepare_heading < verify_heading
    expected_prepare = [
        ("z-copy", ["/tools with spaces/runner", "copy", "./source tree", f"{build}/project"], 0),
        ("a-install", ["/tools with spaces/runner", "install", "owner's $cache; literal", revision], 3),
    ] if with_prepare else []
    expected_verify = [
        ("z-tests", ["/tools with spaces/runner", "test", "./test suite", f"{build}/project", revision], 0),
        ("a-negative", ["/tools with spaces/runner", "negative", "owner's $cache; literal", ""], 1),
    ]
    if not with_prepare:
        assert prompt[prepare_heading:verify_heading].split() == ["Prepare:", "(none)"]
        assert "z-copy:" not in prompt and "a-install:" not in prompt
    else:
        assert "(none)" not in prompt[prepare_heading:verify_heading]

    previous = prepare_heading
    for step_id, argv, expected_exit in expected_prepare + expected_verify:
        assert prompt.count(f"{step_id}:") == 1
        position = prompt.index(f"{step_id}:")
        assert previous < position
        assert (position < verify_heading) == (step_id in {"z-copy", "a-install"})
        line = prompt[position:].splitlines()[0]
        command, exit_text = line.split(": ", 1)[1].rsplit(" (", 1)
        assert exit_text == f"{exit_label} {expected_exit})"
        assert shlex.split(command) == argv
        assert command.strip() == shlex.join(argv)
        assert "'/tools with spaces/runner'" in command
        previous = position
