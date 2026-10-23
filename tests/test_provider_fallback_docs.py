"""Offline guide checks; diff scope and green suites remain acceptance checks.

Live login wording, reference status and readability remain the design's human
PR checklist. These tests neither contact providers nor verify GitHub state.
"""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from specloop import tasks
from test_specloop_documentation_integrity import LINK, heading_ids


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/specloop/README.md"
CHANGE = "nightshift-issue-72-providers-fall-back-to-the-next-profile"


def usage_paragraph():
    matches = re.findall(r"^\*\*Usage limits\*\*[^\n]*(?:\n[^\n]+)*", GUIDE.read_text(), re.MULTILINE)
    assert len(matches) == 1
    return " ".join(matches[0].split())


@pytest.mark.scenario(
    "specloop-documentation/provider-fallback-guidance/reader-understands-provider-recovery"
)
def test_usage_paragraph_explains_fallback_and_recovery_boundaries():
    paragraph = usage_paragraph()
    for guidance in (
        "first usable profile in the role's configured fallback chain",
        "Recognized authentication or inactive/expired/cancelled plan failures do so immediately",
        "including offline local backends, retry the same profile until failures exceed `max_infra`",
        "Retry budgets belong to each task/profile pair, shared across roles",
        "a new fallback gets its own allowance",
        "15-minute cooldown, shared across roles and retained across restarts",
        "At expiry it becomes eligible in the original chain order with a fresh retry allowance",
        "same task, pending candidate or gate continues without charging a coding attempt",
        "`unavailable` event records the diagnostic and an FYI names the successor",
        "pauses only when every profile in that role's chain is blocked",
        "its earliest deadline as the retry time",
        "Quota-only chains use `usage_limit`, with uncapped automatic resumes by `serve`",
        "any unavailable profile, including a mix with quota blocks, uses `infra`",
        "limited to two automatic resumes per task",
        "Cooldown expiry alone cannot lift a pause once that allowance is spent",
        "manual resume can retry a single unavailable profile early",
        "multi-profile chains retain their deadlines",
        "quota deadlines and the automatic-resume cap remain unchanged",
        "drafting, feasibility, the spec critic and the one-shot arbiter keep their existing behavior",
    ):
        assert guidance in paragraph


@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/scope-and-regression-checks-remain-intact"
)
def test_guide_local_links_and_acceptance_command_remain_intact():
    # Reuse the existing integrity checker’s link/heading rules without changing
    # it. This check works on extracted source without git or network access.
    guide = GUIDE.read_text()
    local_links = []
    for _, target in LINK.findall(guide):
        url = urlsplit(target)
        if url.scheme or url.netloc:
            continue
        destination = GUIDE.parent / unquote(url.path) if url.path else GUIDE
        assert destination.is_file(), f"Broken guide link: {target}"
        if url.fragment:
            assert unquote(url.fragment) in heading_ids(destination.read_text()), target
        local_links.append(target)
    assert "../../README.md#enroll-a-repository" in local_links
    assert "command-json.md" in local_links
    assert "Run `uv run pytest` for implementation acceptance." in guide


@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/references-and-reader-checks-are-reviewable"
)
def test_fallback_cites_tracking_issue_and_retains_human_pr_checklist():
    paragraph = usage_paragraph()
    assert LINK.findall(paragraph) == [
        ("issue #72", "https://github.com/mattpolicastro/nightshift/issues/72")
    ]
    assert not re.search(r"\bPR\s*#|\blive[- ]provider.{0,40}\bvalidated\b", paragraph, re.I)
    # Match both the active change and its eventual archive location.
    designs = list((ROOT / "openspec/changes").glob(f"**/*{CHANGE}/design.md"))
    assert len(designs) == 1
    design = " ".join(designs[0].read_text().split())
    checklist = design.split("Human checklist for the implementation PR (never implementing-agent tasks):", 1)[1]
    checklist = checklist.split("## Risks", 1)[0]
    for check in (
        "first naturally occurring lapsed provider login",
        "actual retained diagnostic and `events.jsonl`",
        "an `unavailable` event",
        "a subsequent call on the next usable profile, without repeated infra retries",
        "Synthetic fixtures cannot prove the CLI's real error wording",
        "record this check as pending until observed",
        "retain a sanitized fixture for a follow-up matcher fix",
        "Read the changed provider paragraph once against the actual fallback and pause messages",
        "Check the cited issue link and any eventual implementation PR citation",
        "retain #67's reference/readability checks for docs refreshes",
    ):
        assert check in checklist
    implementation = tasks.parse(designs[0].with_name("tasks.md").read_text())
    assert implementation
    for task in implementation:
        assert not re.search(
            r"first naturally occurring lapsed provider login|inspect its actual retained diagnostic|"
            r"Read the changed provider paragraph once|Check the cited issue link",
            task.text,
        )
