"""Fixture-driven checks of issue arming rules and bounded reads."""

import json
import subprocess

import pytest

from nightshift import arm


def issue_record(number, *, body="", state="OPEN", reason=None):
    return {"number": number, "body": body, "state": state, "stateReason": reason,
            "labels": [{"name": "spec:draft"}, {"name": "bug"}]}


@pytest.fixture
def issue_reader():
    def make(records):
        calls = []

        def runner(argv, **kwargs):
            assert kwargs == dict(capture_output=True, text=True, check=True, timeout=30)
            assert argv[:3] == ["gh", "issue", "view"]
            assert argv[4] == "--repo"
            assert argv[6:] == ["--json", "number,body,state,stateReason,labels"]
            key = (argv[5], int(argv[3]))
            calls.append(key)
            result = records[key]
            if isinstance(result, Exception):
                raise result
            if isinstance(result, subprocess.CompletedProcess):
                return result
            return subprocess.CompletedProcess(argv, 0, json.dumps(result))

        return arm.IssueCache(runner=runner), calls
    return make


@pytest.fixture
def issue_body():
    def body(done="`test_expected_behavior`", human="Nothing."):
        return ("**Scope:** `nightshift/arm.py`\n"
                "**What to do:** Add lint.\n"
                f"**Definition of done:** {done}\n"
                f"**What CI cannot prove:** {human}\n")
    return body


@pytest.mark.scenario("issue-arming/dependency-completion-lint/closed-incomplete-blocker-is-reported")
@pytest.mark.parametrize("marker", [
    "blocked-by: #3, #1, #2, #3", "  - BlOcKeD-bY: #3 #1 #2",
    "> * BLOCKED-BY: #3, #1\n> blocked-by: #2",
])
def test_direct_blocker_completion_uses_queue_syntax(issue_body, issue_reader, marker):
    body = issue_body() + marker + "\nsee #99\nprose blocked-by: #98"
    cache, calls = issue_reader({
        ("org/repo", 76): issue_record(76, body=body),
        ("org/repo", 1): issue_record(1),
        ("org/repo", 2): issue_record(2, state="CLOSED", reason="COMPLETED"),
        ("org/repo", 3): issue_record(3, state="CLOSED", reason="NOT_PLANNED"),
    })
    findings = arm.lint_issue("org/repo", 76, cache)
    assert [(f.code, f.issue, f.related_issue) for f in findings] == [
        ("dependency-completion", 76, 3),
    ]
    assert "#3" in findings[0].message and "NOT_PLANNED" in findings[0].message
    assert findings[0].suggestion
    assert calls == [("org/repo", n) for n in (76, 3, 1, 2)]


@pytest.mark.scenario("issue-arming/dependency-completion-lint/closed-incomplete-blocker-is-reported")
@pytest.mark.scenario("issue-arming/dependency-completion-lint/unknown-blocker-state-stays-visible")
@pytest.mark.parametrize("state,reason,code", [
    ("OPEN", None, None), ("OPEN", "REOPENED", None),
    ("OPEN", "NOT_PLANNED", None), ("CLOSED", "COMPLETED", None),
    ("closed", "completed", None), ("CLOSED", "NOT_PLANNED", "dependency-completion"),
    ("CLOSED", None, "incomplete-context"), ("CLOSED", "", "incomplete-context"),
    ("CLOSED", "REOPENED", "incomplete-context"),
    ("CLOSED", "FUTURE_REASON", "incomplete-context"),
    (None, "COMPLETED", "incomplete-context"), ("", None, "incomplete-context"),
    ("FUTURE_STATE", "COMPLETED", "incomplete-context"),
])
def test_blocker_states_and_reasons(issue_reader, state, reason, code):
    cache, calls = issue_reader({
        ("org/repo", 1): issue_record(1, state=state, reason=reason, body="blocked-by: #99"),
    })
    findings = arm.lint_dependencies(76, "blocked-by: #1", "org/repo", cache)
    assert [f.code for f in findings] == ([] if code is None else [code])
    assert all(f.issue == 76 and f.related_issue == 1 and "#1" in f.message for f in findings)
    assert calls == [("org/repo", 1)]  # Completion lint does not follow transitive blockers.


@pytest.mark.scenario("issue-arming/dependency-completion-lint/unknown-blocker-state-stays-visible")
@pytest.mark.parametrize("failure", [
    OSError("private diagnostic"), subprocess.TimeoutExpired(["gh"], 30),
    subprocess.CalledProcessError(1, ["gh"], stderr="private diagnostic"),
    subprocess.CompletedProcess(["gh"], 1, "", "private diagnostic"),
    subprocess.CompletedProcess(["gh"], 0, "not JSON"),
    [], {}, {**issue_record(1), "number": 99}, {**issue_record(1), "body": None},
    {**issue_record(1), "state": []}, {**issue_record(1), "stateReason": {}},
    {**issue_record(1), "labels": ["spec:draft"]},
])
def test_blocker_lookup_failures_preserve_other_findings(issue_body, issue_reader, failure):
    body = issue_body("manually run pytest") + "blocked-by: #1 #3 #2\n"
    cache, calls = issue_reader({
        ("org/repo", 76): issue_record(76, body=body),
        ("org/repo", 1): failure,
        ("org/repo", 2): issue_record(2, state="CLOSED", reason="NOT_PLANNED"),
        ("org/repo", 3): issue_record(3, state="CLOSED"),
    })
    findings = arm.lint_issue("org/repo", 76, cache)
    assert [f.code for f in findings] == [
        "dependency-completion", "incomplete-context", "incomplete-context",
        "manual-check", "named-test",
    ]
    assert [f.related_issue for f in findings[:3]] == [2, 1, 3]
    assert all(f.issue == 76 and f.suggestion for f in findings)
    assert "private diagnostic" not in str(findings)
    assert arm.lint_issue("org/repo", 76, cache) == findings
    assert calls == [("org/repo", n) for n in (76, 1, 3, 2)]
    # A shared failed lookup still produces a finding for each affected target.
    other = arm.lint_dependencies(77, "blocked-by: #1", "org/repo", cache)
    assert other[0].issue == 77 and other[0].related_issue == 1
    assert len(calls) == 4


def test_issue_cache_keeps_repository_identity_and_empty_bodies(issue_reader, monkeypatch):
    cache, calls = issue_reader({
        (repo, 1): issue_record(1, body=body)
        for repo, body in [("org/repo", ""), ("other/repo", "other"),
                           ("github.example/org/repo", "enterprise")]
    })
    first = cache.get("org/repo", 1)
    assert first.body == "" and first.state == "OPEN" and first.state_reason is None
    assert first.labels == frozenset({"spec:draft", "bug"})
    assert cache.get("org/repo", 1) is first
    assert cache.get("other/repo", 1).body == "other"
    assert cache.get("github.example/org/repo", 1).body == "enterprise"
    assert [f.code for f in arm.lint_issue("org/repo", 1, cache)] == ["missing-section"] * 4
    assert len(calls) == 3
    # The default subprocess boundary is also injectable by monkeypatching.
    monkeypatch.setattr(arm.subprocess, "run", cache.runner)
    assert arm.read_issue("org/repo", 1) == first
    assert len(calls) == 4


def test_unreadable_target_is_cached_incomplete_context(issue_reader):
    cache, calls = issue_reader({("org/repo", 76): FileNotFoundError("gh unavailable")})
    findings = arm.lint_issue("org/repo", 76, cache)
    assert [(f.code, f.issue, f.related_issue) for f in findings] == [
        ("incomplete-context", 76, None),
    ]
    assert "#76" in findings[0].message
    assert arm.lint_issue("org/repo", 76, cache) == findings
    assert calls == [("org/repo", 76)]


@pytest.mark.scenario("issue-arming/issue-body-lint/missing-sections-accumulate")
@pytest.mark.parametrize("body,missing,other", [
    ("", set(arm.REQUIRED_SECTIONS), []),
    ("**Scope:** No existing tests are modified.",
     {"What to do", "Definition of done", "What CI cannot prove"}, ["discouraged-phrase"]),
    ("**Definition of done:** manually run pytest\n**Scope:** file",
     {"What to do", "What CI cannot prove"}, ["manual-check", "named-test"]),
])
def test_arm_flags_missing_sections(body, missing, other):
    findings = arm.lint_body(76, body)
    assert {f.message for f in findings if f.code == "missing-section"} == {
        f"Missing **{name}:** section." for name in missing
    }
    assert [f.code for f in findings if f.code != "missing-section"] == other
    assert all(f.issue == 76 and f.suggestion for f in findings)


@pytest.mark.scenario("issue-arming/issue-body-lint/named-tests-are-required")
@pytest.mark.parametrize("done,valid", [
    ("uv run pytest", False), ("", False), ('"an unrelated sentence"', False),
    ("test_unquoted", False), ('test "   "', False), ("`test_`", False),
    ("`test_proposed_work`", True), ("'test_quoted'", True), ('"test_quoted"', True),
    ("`tests/test_arm.py::TestArm::test_named`", True),
    ('test "descriptive title"', True), ("test 'descriptive title'", True),
])
def test_arm_requires_a_named_test(issue_body, done, valid):
    findings = arm.lint_body(76, issue_body(done))
    assert [f.code for f in findings] == ([] if valid else ["named-test"])
    if not valid:
        assert findings[0].line == 3


@pytest.mark.scenario("issue-arming/issue-body-lint/named-tests-are-required")
@pytest.mark.parametrize("boundary", [
    "**What CI cannot prove:**", "**Notes:**", "# Notes", "### Notes",
    "Notes\n=====", "Notes\n-----",
])
def test_named_test_outside_done_does_not_count(boundary):
    body = f"`test_before`\n**Definition of done:**\n{boundary}\n`test_after`"
    assert [f.line for f in arm.lint_body(76, body) if f.code == "named-test"] == [2]


@pytest.mark.scenario("issue-arming/issue-body-lint/discouraged-phrases-have-replacements")
def test_arm_flags_discouraged_phrasing(issue_body):
    phrases = [
        f"No existing {noun} {verb}{edit}"
        for noun in ("test", "tests")
        for verb in ("", "is ", "are ", "to be ")
        for edit in ("modified", "changed", "edited")
    ] + [
        f"{prohibition} {edit} existing tests"
        for prohibition in ("Do not", "Don't") for edit in ("modify", "change", "edit")
    ] + ["Existing tests must remain unchanged"]
    # Every occurrence, including two on one line and quoted/fenced examples.
    lines = [f'"{phrase.swapcase()}"; {phrase.upper()}' for phrase in phrases]
    findings = arm.lint_body(76, issue_body() + "```text\n" + "\n".join(lines) + "\n```")
    assert len(findings) == 2 * len(phrases)
    assert [f.line for f in findings] == [n for n in range(6, 6 + len(phrases)) for _ in range(2)]
    assert all(f.code == "discouraged-phrase" for f in findings)
    assert all(f.suggestion == "existing assertions are preserved" for f in findings)
    assert arm.lint_body(76, issue_body() + "existing assertions are preserved") == []


@pytest.mark.scenario("issue-arming/issue-body-lint/human-checks-stay-outside-done")
def test_manual_checks_only_flag_done(issue_body):
    checks = "Manually inspect, then check BY HAND and visually."
    body = issue_body(f"`test_named` {checks}\n{checks}", checks)
    findings = arm.lint_body(76, body)
    assert [f.line for f in findings] == [3, 3, 3, 4, 4, 4]
    assert all(f.code == "manual-check" for f in findings)
    assert all(f.suggestion == "Move this check under **What CI cannot prove:**." for f in findings)
    assert arm.lint_body(76, issue_body("`test_named` semimanually visuallyless by handy")) == []


@pytest.mark.scenario("issue-arming/issue-body-lint/human-checks-stay-outside-done")
@pytest.mark.parametrize("fence", ["```", "~~~~", "````"])
def test_fenced_markers_do_not_satisfy_sections(issue_body, fence):
    body = f"{fence}markdown\n{issue_body()}{fence}\n"
    assert arm.parse_sections(body) == []
    assert [f.code for f in arm.lint_body(76, body)] == ["missing-section"] * 4
    body = "**Definition of done:** `test_named`\n" + body + "manually inspect\n"
    assert [f.line for f in arm.lint_body(76, body) if f.code == "manual-check"] == [8]


@pytest.mark.parametrize("fake_close", ["~~~", "``", "``` trailing text"])
def test_fences_require_matching_closers(fake_close):
    assert arm.parse_sections(f"````markdown\n{fake_close}\n**Scope:** example") == []


def test_section_markers_are_case_insensitive_and_line_anchored():
    body = ("prose **Scope:** ignored\n> **What to do:** ignored\n"
            "  **sCoPe:** inline\ncontinued\n**Other:** boundary\n"
            "**Scope:** repeated\n## Heading\noutside")
    assert arm.parse_sections(body) == [
        arm.Section("Scope", 3, " inline\ncontinued"), arm.Section("Scope", 6, " repeated"),
    ]


def test_repeated_done_sections_keep_all_checks():
    body = ("**Definition of done:** manually inspect\n**Notes:** boundary\n"
            "**Definition of done:** `test_named` visually inspect")
    findings = arm.lint_body(76, body)
    assert not any(f.code == "named-test" for f in findings)
    assert [f.line for f in findings if f.code == "manual-check"] == [1, 3]


@pytest.mark.scenario("issue-arming/issue-body-lint/human-checks-stay-outside-done")
@pytest.mark.parametrize("boundary", ["**Notes:**", "## Notes", "Notes\n==="])
def test_manual_checks_stop_at_section_boundaries(boundary):
    body = f"**Definition of done:** `test_named`\n{boundary}\nmanually inspect by hand visually"
    assert not any(f.code == "manual-check" for f in arm.lint_body(76, body))


def test_fenced_headings_keep_done_open_and_longer_fences_close():
    body = ("**Definition of done:** `test_named`\n```markdown\n"
            "## Example heading\n**Notes:** example\n````\nvisually inspect\n"
            "**What CI cannot prove:** manually inspect")
    assert [f.line for f in arm.lint_body(76, body) if f.code == "manual-check"] == [6]


def test_wrapped_phrases_report_the_first_source_line(issue_body):
    body = issue_body("`test_named` by\nhand", "No existing tests\nare changed")
    findings = arm.lint_body(76, body)
    assert [(f.code, f.line) for f in findings] == [
        ("discouraged-phrase", 5), ("manual-check", 3),
    ]


@pytest.mark.scenario("issue-arming/issue-body-lint/human-checks-stay-outside-done")
@pytest.mark.scenario("issue-arming/issue-body-lint/named-tests-are-required")
@pytest.mark.parametrize("prefix", ["- [ ]", "* [x]", "+", "1.", "1)", ">"])
@pytest.mark.parametrize("done,expected_lines", [
    ("`test_named`\n{prefix} Manually inspect behavior", [4]),
    ("\n{prefix} `test_named`", []),
])
def test_horizontal_rule_preserves_done_items(issue_body, prefix, done, expected_lines):
    done = done.format(prefix=prefix) + "\n---\nVisually inspect behavior"
    body = issue_body(done, "Manually inspect by hand visually.")
    findings = arm.lint_body(76, body)
    assert [(f.code, f.line) for f in findings] == [
        ("manual-check", line) for line in expected_lines + [6]
    ]
    section = next(s for s in arm.parse_sections(body) if s.name == "Definition of done")
    assert section.text == " " + done


@pytest.mark.scenario("issue-arming/issue-body-lint/human-checks-stay-outside-done")
@pytest.mark.scenario("issue-arming/issue-body-lint/named-tests-are-required")
@pytest.mark.parametrize("prefix", ["- [ ]", "* [x]", "+", "1.", "1)", ">"])
@pytest.mark.parametrize("indent", ["", "  "])
@pytest.mark.parametrize("done,expected_lines", [
    ("`test_named`\n{prefix} Check behavior\n{indent}Manually inspect behavior", [5]),
    ("\n{prefix} Check behavior\n{indent}`test_named`", []),
])
def test_horizontal_rule_preserves_wrapped_items(issue_body, prefix, indent, done, expected_lines):
    done = done.format(prefix=prefix, indent=indent) + "\n---\nVisually inspect output"
    body = issue_body(done, "Manually inspect by hand visually.")
    assert [(f.code, f.line) for f in arm.lint_body(76, body)] == [
        ("manual-check", line) for line in expected_lines + [7]
    ]
    section = next(s for s in arm.parse_sections(body) if s.name == "Definition of done")
    assert section.text == " " + done


@pytest.mark.scenario("issue-arming/issue-body-lint/human-checks-stay-outside-done")
@pytest.mark.scenario("issue-arming/issue-body-lint/named-tests-are-required")
@pytest.mark.parametrize("separator", ["\n", "---\n", "**Notes:**\n"])
def test_setext_heading_after_list_context_ends(issue_body, separator):
    done = "\n- Check behavior\n  with a continuation\n" + separator
    body = issue_body(done + "Notes\n---\n`test_outside` visually inspect")
    assert [(f.code, f.line) for f in arm.lint_body(76, body)] == [("named-test", 3)]


@pytest.mark.scenario("issue-arming/issue-body-lint/named-tests-are-required")
@pytest.mark.parametrize("underline", ["---", "==="])
def test_multiline_setext_heading_excludes_entire_title(underline):
    body = ("**Scope:** file\n**What to do:** lint\n**Definition of done:**\n\n"
            f"`test_outside`\nAdditional notes\n{underline}\nOther content\n"
            "**What CI cannot prove:** Nothing.")
    assert [(f.code, f.line) for f in arm.lint_body(76, body)] == [("named-test", 3)]
    section = next(s for s in arm.parse_sections(body) if s.name == "Definition of done")
    assert section.text == "\n"
