"""Committed design checks use physical lines and preserve both publication surfaces."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from nightshift import specloop_intake
from nightshift import daemon
from nightshift.config import Repo
from gitea_dispatch_fixtures import install_gitea
from specloop import cli, loop
from test_specloop_base_sync import base_env, commit, controlled_verify, publish_env, sh


DESIGN = "openspec/changes/c/design.md"


def save_design(env, text):
    head = commit(env.src, DESIGN, text)
    sh(env.ctx.repo, "git", "fetch", "-q", str(env.src), f"+spec/c:{env.ctx.ref}")
    return head


@pytest.fixture
def design_env(base_env, monkeypatch):
    monkeypatch.setattr(loop, "prepare_publish", lambda ctx: True)
    return base_env


def render(ctx, surface):
    if surface == "pr":
        return cli._pr_body(ctx)
    assert loop.finals(ctx, ctx.head()) is False
    assert ctx.run.state.pause.reason == "ready_to_publish"
    return ctx.run.state.pause.detail


def rows(text):
    return [line for line in text.splitlines() if line.startswith("- [ ]")]


@pytest.mark.scenario("issue-intake/design-checklist-collection/both-design-headings-contribute")
def test_both_headings_and_physical_boundaries(design_env):
    text = """- [ ] outside
## Human checks
 \t- [ ] first\t
### Deeper
- [ ] second\u2028###### Deepest
- [ ] third
- [x] checked
- [X] checked uppercase
* [ ] other bullet
- [] missing space
- [ ]missing separator
- [ ] \t
- [ ] \u00a0
prose
  continuation
## Other
- [ ] outside again
### Verification and human rollout checklist
- [ ] fourth
#### Deeper
- [ ] fifth
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
- [ ] sixth
#\tEnd
- [ ] outside shallow
### Verification and human rollout checklist
\t- [ ]\tlast  `item`! \t
- [ ] \u00a0keep padding\u00a0"""
    head = save_design(design_env, text.replace("\n", "\r\n"))
    expected = [f"- [ ] {word}" for word in ("first", "second", "third", "fourth", "fifth", "sixth")]
    expected += ["- [ ]\tlast  `item`!", "- [ ] \u00a0keep padding\u00a0"]
    assert loop.design_human_checks(design_env.ctx, head) == expected
    assert rows(render(design_env.ctx, "ready")) == expected


@pytest.mark.scenario("issue-intake/design-checklist-collection/both-design-headings-contribute")
@pytest.mark.parametrize("surface", ["ready", "pr"])
def test_nested_selected_heading_retains_enclosing_section(design_env, surface):
    head = save_design(design_env, """## Human checks
### Verification and human rollout checklist
- [ ] Rollout
### Other subsection
- [ ] Pending
### Verification and human rollout checklist
- [ ] Repeated rollout
### Another subsection
- [ ] Still pending
## Boundary
- [ ] Excluded
""")
    expected = [f"- [ ] {item}" for item in
                ("Rollout", "Pending", "Repeated rollout", "Still pending")]
    assert loop.design_human_checks(design_env.ctx, head) == expected
    assert rows(render(design_env.ctx, surface)) == expected


@pytest.mark.scenario("issue-intake/design-checklist-collection/task-checks-precede-design-checks")
@pytest.mark.parametrize("surface, separator", [("ready", ":"), ("pr", " —")])
def test_task_checks_keep_order_before_design_checks(design_env, surface, separator):
    ctx = design_env.ctx
    ctx.m = replace(ctx.m, coverage_exempt={"z/r/s": "Last.", "a/r/s": "Keep  `Case`."})
    existing = [f"- [ ] `a/r/s`{separator} Keep  `Case`.", f"- [ ] `z/r/s`{separator} Last."]
    additions = ["- [ ] Design.", existing[0].replace("Case", "case"),
                 existing[0].replace(".", "!"), existing[0].replace("Keep  ", "Keep "),
                 existing[0].replace("`Case`", "Case")]
    commit(design_env.src, ctx.tasks_path, "## 1. Core\n- [ ] 1.1 Ordinary implementation task\n")
    save_design(design_env, "\n".join([
        "## Human checks", " \t" + existing[0] + "\t", additions[0],
        "## Other", "- [ ] excluded", "### Verification and human rollout checklist",
        *additions, existing[1]]))
    assert rows(render(ctx, surface)) == existing + additions
    # Earlier sources' whitespace and duplicate rows must survive verbatim.
    originals = ["  " + existing[0] + "\t", existing[0], existing[1]]
    assert loop.append_design_checks(ctx, ctx.head(), originals) == originals + additions
    assert originals == ["  " + existing[0] + "\t", existing[0], existing[1]]


@pytest.mark.scenario("issue-intake/design-checklist-collection/literal-examples-remain-pending")
@pytest.mark.parametrize("opening, closing, indent", [("```md", "```", ""), ("<!--", "-->", ""), ("", "", "    ")])
@pytest.mark.parametrize("post_deploy", [False, True])
def test_literal_examples_remain_pending(design_env, opening, closing, indent, post_deploy):
    ctx = design_env.ctx
    annotation = "<!-- nightshift:post-deploy -->" if post_deploy else ""
    commit(design_env.src, "openspec/changes/c/specs/cap/spec.md",
           "### Requirement: Checks\n#### Scenario: Observe\n" + annotation)
    item = "- [ ] Observe `cap/checks/observe`"
    save_design(design_env, "\n".join(indent + line for line in [
        opening, "## Human checks", item, "# Boundary", "- [ ] excluded", closing]))
    detail = render(ctx, "ready")
    assert rows(detail) == [item]
    row = {"change": "c", "head": ctx.head(), "pr": ctx.run.state.pr}
    reasons = specloop_intake._readiness_human_checks(row, "", publication_checks=detail)
    assert bool(reasons) is not post_deploy


@pytest.mark.scenario("issue-intake/design-checklist-collection/committed-head-supplies-checks")
def test_integrated_head_supplies_both_outputs(base_env, monkeypatch):
    env, ctx = base_env, base_env.ctx
    save_design(env, "## Human checks\n- [ ] Run check\n")
    sh(env.upstream, "git", "fetch", "-q", str(env.src), "spec/c")
    sh(env.upstream, "git", "merge", "--ff-only", "FETCH_HEAD")
    commit(env.src, "out/feature.txt", "independent task work\n")
    sh(ctx.repo, "git", "fetch", "-q", str(env.src), f"+spec/c:{ctx.ref}")
    old = ctx.head()
    commit(env.upstream, DESIGN, "## Human checks\n- [ ] Integrated check\n")
    sh(env.upstream, "git", "push", "-q", "origin", "release/stable")
    (env.src / DESIGN).write_text("## Human checks\n- [ ] Dirty source\n")
    controlled_verify(monkeypatch, ctx)
    assert loop.finals(ctx, old) is False
    assert ctx.head() != old
    assert loop.design_human_checks(ctx, old) == ["- [ ] Run check"]
    for text in (ctx.run.state.pause.detail, cli._pr_body(ctx)):
        assert rows(text) == ["- [ ] Integrated check"]
    assert ctx.run.state.pause.reason == "ready_to_publish"
    assert ctx.head()[:12] in ctx.run.state.pause.detail


@pytest.mark.scenario("issue-intake/design-checklist-collection/absent-or-empty-design-preserves-checks")
@pytest.mark.parametrize("design", [None, "", "## Human checks\n- [x] done\n## Other\n- [ ] excluded"])
@pytest.mark.parametrize("has_checks", [False, True])
def test_absent_or_empty_design_preserves_output(design_env, design, has_checks):
    ctx = design_env.ctx
    if has_checks:
        ctx.m = replace(ctx.m, coverage_exempt={"a/r/s": "Check."})
    if design is not None:
        save_design(design_env, design)
    check = "- [ ] `a/r/s`: Check." if has_checks else ""
    assert render(ctx, "ready") == (
        f"all tasks landed at {ctx.head()[:12]}; human checks after publishing:\n{check}\n"
        "Next: `specloop publish c`")
    body = render(ctx, "pr")
    assert body.split("## Checks a human must make\n\n")[1].split("\n\n")[0] == (
        "- [ ] `a/r/s` — Check." if has_checks else "(none)")
    assert rows(body) == (["- [ ] `a/r/s` — Check."] if has_checks else [])


@pytest.mark.scenario("issue-intake/design-checklist-collection/read-failure-cannot-erase-checks")
@pytest.mark.parametrize("failure", ["lookup", "read", "decode"])
def test_design_failure_cannot_establish_readiness(design_env, monkeypatch, failure):
    ctx = design_env.ctx
    save_design(design_env, "## Human checks\n- [ ] Pending\n")
    original = loop.git

    def fail(repo, *args, **kwargs):
        if args[0] == "ls-tree" and failure == "lookup":
            raise RuntimeError("tree lookup failed")
        if args[0] == "show" and args[1].endswith(":" + DESIGN):
            if failure == "read":
                raise RuntimeError("blob read failed")
            if failure == "decode":
                return b"\xff"
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(loop, "git", fail)
    with pytest.raises(RuntimeError, match="could not collect human checks.*design.md"):
        loop.finals(ctx, ctx.head())
    assert ctx.run.state.pause is None
    with pytest.raises(RuntimeError, match="could not collect human checks.*design.md"):
        cli._pr_body(ctx)


@pytest.mark.scenario("issue-intake/design-checks-survive-publication/new-pr-body-lists-design-checks")
def test_new_pr_body_lists_design_checks(publish_env, monkeypatch):
    env, ctx = publish_env, publish_env.ctx
    save_design(env, "## Human checks\n- [ ] Design check\n- [ ] Design check\n")
    assert loop.finals(ctx, ctx.head()) is False
    created = []

    def gh(*args):
        if args[0] == "api":
            return "[]"
        assert args[:3] == ("pr", "create", "--draft")
        created.append(args[args.index("--body") + 1])
        return env.url

    monkeypatch.setattr(cli, "_gh", gh)
    assert cli._publish(SimpleNamespace(change="c", json=False), ctx.run) == 0
    assert len(created) == 1
    assert rows(created[0]) == ["- [ ] `cap/req/manual` — inspect the result",
                               "- [ ] `cap/req/other` — check the UI", "- [ ] Design check"]
    assert ctx.run.state.pr == env.url


@pytest.mark.real_trust
@pytest.mark.scenario("issue-intake/design-checks-survive-publication/new-pr-body-lists-design-checks")
def test_new_gitea_pr_body_retains_design_checks(publish_env, monkeypatch):
    env, ctx = publish_env, publish_env.ctx
    save_design(env, "## Human checks\n- [ ] Design check\n- [ ] Design check\n")
    assert loop.finals(ctx, ctx.head()) is False
    e = install_gitea(monkeypatch, Repo("example/repo"))
    forge = daemon.forge_for(e.repo, cfg=e.cfg)
    url = forge.create_draft_pr(e.repo.name, ctx.m.branch, ctx.m.pr_base,
                               "Design checks", cli._pr_body(ctx)).stdout
    pr, = e.remote["pulls"]
    assert pr["html_url"] == url and pr["title"] == "Draft: Design checks"
    assert rows(pr["body"]) == ["- [ ] `cap/req/manual` — inspect the result",
                                "- [ ] `cap/req/other` — check the UI", "- [ ] Design check"]
    assert [call.method for call in e.fake.calls] == ["POST"]
