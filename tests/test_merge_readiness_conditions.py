import json
import subprocess

import pytest

from merge_readiness_fixtures import local_run
from nightshift import queue, specloop_intake as intake
from nightshift.config import Repo


HEADING = "## Checks a human must make\n"
SID = "cap/human-checks/observe-deployment"
ITEM = f"- [ ] Observe `{SID}`"
MARKER = "<!-- nightshift:post-deploy -->"
SPEC_PATH = "openspec/changes/c/specs/cap/spec.md"
SPEC = "### Requirement: Human checks\n#### Scenario: Observe deployment\n"


@pytest.mark.parametrize("section", ["", "\n \n", "(none)", ITEM, ITEM.replace("[ ]", "[x]"),
                                    ITEM + "\n" + ITEM, ITEM + " using `curl`"])
@pytest.mark.scenario("merge-readiness/human-and-revision-conditions/post-deploy-checks-permit-readiness")
def test_post_deploy_checks_permit_readiness(local_run, section):
    env = local_run
    env.commit({SPEC_PATH: SPEC + MARKER + "\n"})
    body = HEADING + section + "\n# Next section\nunrelated prose"
    before = (env.run.dir / "state.json").read_bytes()
    assert intake._readiness_human_checks(env.row, body) == []
    assert (env.run.dir / "state.json").read_bytes() == before
    # Only the recorded commit supplies annotations, even after a newer commit.
    head = env.row["head"]
    env.commit({SPEC_PATH: SPEC + "not annotated\n"})
    assert intake._readiness_human_checks({**env.row, "head": head}, body) == []


@pytest.mark.parametrize("body", [None, 12, "", "(none)", " " + HEADING, HEADING * 2,
    HEADING + "(none)\n" + ITEM, HEADING + "prose", HEADING + " " + ITEM,
    HEADING + ITEM.replace("[ ]", "[X]"), HEADING + "- [ ] deploy later",
    HEADING + ITEM + f" `{SID}`", HEADING + ITEM + " `unfinished",
    HEADING + ITEM.replace(f"`{SID}`", f"``{SID}``"),
    HEADING + ITEM.replace("cap/", "unknown/"), HEADING + ITEM.replace("human-checks", "other"),
    HEADING + "```\n" + ITEM + "\n```", HEADING + "<!--\n" + ITEM + "\n-->",
    HEADING + ITEM + "\ncontinued prose", HEADING + f"- [ ] `{SID}/extra`",
    HEADING + f"- [ ] `{SID}` `unknown/req/check`"])
@pytest.mark.scenario("merge-readiness/human-and-revision-conditions/pre-merge-or-unknown-checks-block")
def test_pre_merge_or_unknown_checks_block(local_run, body):
    local_run.commit({SPEC_PATH: SPEC + MARKER + "\n"})
    assert intake._readiness_human_checks(local_run.row, body)


@pytest.mark.parametrize("document", [SPEC, SPEC + " " + MARKER, SPEC + MARKER + " extra",
    SPEC + "# Boundary\n" + MARKER, SPEC + "## Boundary\n" + MARKER,
    MARKER + "\n" + SPEC, SPEC + "Deploy after merge\n",
    SPEC + MARKER + "\n" + SPEC + MARKER, SPEC.replace("####", "###") + MARKER,
    SPEC + MARKER + "\n#### Scenario: Observe---deployment\n" + MARKER,
    SPEC.replace("Human checks", "Other checks") + MARKER])
def test_only_unique_exact_annotation_in_scenario_passes(local_run, document):
    local_run.commit({SPEC_PATH: document})
    for item in (ITEM, ITEM.replace("[ ]", "[x]")):
        assert intake._readiness_human_checks(local_run.row, HEADING + item + " " + MARKER)


def test_line_rules_do_not_interpret_markdown_or_html(local_run):
    local_run.commit({SPEC_PATH: SPEC + "```\n" + MARKER + "\n```\n"})
    # Fences and HTML have no special meaning; exact lines decide the result.
    for prefix in ("```\n", "<!--\n", ""):
        assert intake._readiness_human_checks(local_run.row, prefix + HEADING + ITEM) == []
    assert intake._readiness_human_checks(local_run.row, HEADING + "# End\n" + ITEM) == []


@pytest.mark.parametrize("boundary", ["#Boundary", "## Other", "###Unknown", "##### Unknown"])
def test_unknown_heading_cannot_reuse_previous_requirement(local_run, boundary):
    local_run.commit({SPEC_PATH: SPEC.replace("#### Scenario:", boundary + "\n#### Scenario:") + MARKER})
    assert intake._readiness_human_checks(local_run.row, HEADING + ITEM)


def test_annotation_must_be_committed_at_run_head(local_run):
    env = local_run
    head = env.commit({SPEC_PATH: SPEC})
    env.commit({SPEC_PATH: SPEC + MARKER})
    assert intake._readiness_human_checks({**env.row, "head": head}, HEADING + ITEM)
    assert intake._readiness_human_checks({**env.row, "head": "missing"}, HEADING + ITEM)
    assert intake._readiness_human_checks({**env.row, "pr": "other"}, HEADING + ITEM)

    def timeout(args, **kwargs):
        assert kwargs["timeout"] == 30
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    assert intake._readiness_human_checks(env.row, HEADING + ITEM, git_runner=timeout)


@pytest.mark.parametrize("missing", ["state.json", "repo.git", "spec"])
def test_missing_local_inputs_block_checks(local_run, missing):
    local_run.commit({SPEC_PATH: SPEC + MARKER})
    if missing == "spec":
        local_run.commit({SPEC_PATH: None})
    else:
        (local_run.run.dir / missing).rename(local_run.run.dir / (missing + ".hidden"))
    assert intake._readiness_human_checks(local_run.row, HEADING + ITEM)


def test_every_check_must_resolve_under_its_requirement(local_run):
    local_run.commit({SPEC_PATH: SPEC.replace("Observe deployment", "OBSERVE---Deployment!") + MARKER
                     + "\n### Requirement: Other checks\n#### Scenario: Observe deployment\n"})
    other = ITEM.replace("human-checks", "other-checks")
    assert intake._readiness_human_checks(local_run.row, HEADING + ITEM) == []
    assert intake._readiness_human_checks(local_run.row, HEADING + ITEM + "\n" + other)


@pytest.mark.parametrize("case,blocked", [
    ("absent", False), ("delivered", False), ("refused", False), ("collected", False), ("empty", False),
    ("other-change", False), ("propose", False), ("trigger", True), ("undelivered", True),
    ("no-result", True), ("unknown-result", True), ("no-commit", True), ("uncollected", True),
    ("stopped", True), ("bad-collection", True), ("bad-job", True), ("bad-json", True),
    ("bad-receipt", True), ("bad-delivered", True), ("no-command", True), ("no-change", True),
    ("error-result", True), ("bad-version", True), ("bad-change", True),
])
@pytest.mark.scenario("merge-readiness/human-and-revision-conditions/pending-revision-blocks")
def test_pending_revision_blocks(tmp_path, monkeypatch, case, blocked):
    path = tmp_path / "revisions/owner__repo/1.json"
    path.parent.mkdir(parents=True)
    receipt = dict(change="c", command="revise", delivered=True,
                   result=dict(outcome="revised", commit="a" * 40), future_field={})
    if case in ("collected", "uncollected", "stopped", "bad-collection", "bad-job"):
        receipt["job"] = "a" * 32
        job = path.parent / "1.jobs" / receipt["job"]
        job.mkdir(parents=True)
        if case != "uncollected":
            (job / "collected.json").write_text(json.dumps({"collected_at": "now"}))
        if case == "stopped":
            (job / "stop.json").write_text('{"state": "stopped"}')
        if case == "bad-collection":
            (job / "collected.json").write_text("[]")
        if case == "bad-job":
            receipt["job"] = "../escape"
    if case == "other-change":
        receipt.update(change="other", delivered=False)
    elif case == "bad-change":
        receipt["change"] = ""
    elif case == "propose":
        receipt.update(command="propose", delivered=False)
    elif case == "empty":
        del receipt["command"]
        receipt["result"] = {"outcome": "empty"}
    elif case in ("undelivered", "bad-delivered"):
        receipt["delivered"] = False if case == "undelivered" else 1
    elif case.startswith("no-"):
        if case == "no-commit":
            del receipt["result"]["commit"]
        else:
            del receipt[case.removeprefix("no-")]
    elif case in ("refused", "unknown-result"):
        receipt["result"] = {"outcome": "refused" if case == "refused" else "running"}
    elif case == "error-result":
        receipt["result"]["error"] = "failed"
    elif case == "bad-version":
        receipt["result"]["version"] = True
    if case != "absent":
        path.write_text("{" if case == "bad-json" else json.dumps([] if case == "bad-receipt" else receipt))
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("readiness must not authorize, collect, migrate or deliver revisions")
    for name in ("_collect", "_save", "_finish_delivery", "_initialize_revision_feedback",
                 "_acknowledge_revision_feedback", "_authorize_label_events", "_recover_item_cleanup"):
        monkeypatch.setattr(intake, name, forbidden)
    labels = queue.Labels(revise="custom:revise")
    result = intake._readiness_revision(Repo(name="owner/repo"), labels, tmp_path, 1,
                                        {"change": "c"}, {labels.revise} if case == "trigger" else set())
    assert bool(result) is blocked
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
