"""Fixture-driven checks of issue arming rules and bounded reads."""

import json
import subprocess

import pytest

from nightshift import arm
from specloop import state
from specloop.seal import IDENTITY, git


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


@pytest.mark.scenario("issue-arming/dependency-completion-lint/unknown-blocker-state-stays-visible")
def test_timeout_finding_identifies_issue_and_actual_read_budget(issue_body, issue_reader):
    cache, calls = issue_reader({
        ("org/repo", 76): issue_record(76, body=issue_body() + "blocked-by: #12"),
        ("org/repo", 12): subprocess.TimeoutExpired(["gh"], 30),
    })
    findings = arm.lint_issue("org/repo", 76, cache)
    assert [(f.code, f.issue, f.related_issue, f.message) for f in findings] == [
        ("incomplete-context", 76, 12, "Issue #12 lookup timed out after 30 seconds."),
    ]
    assert calls == [("org/repo", 76), ("org/repo", 12)]


def test_findings_sort_by_path_before_related_issue_and_message():
    # Reverse the later tie breakers so they cannot disguise a lost path key.
    first = arm.Finding("overlap", 76, "Z evidence", "Add a blocker.",
                        path="docs/arming.md", related_issue=9)
    last = arm.Finding("overlap", 76, "A evidence", "Add a blocker.",
                       path="tests/test_arm.py", related_issue=1)
    assert sorted([last, first], key=arm.Finding.sort_key) == [first, last]


@pytest.mark.parametrize("missing_path", [None, ""])
def test_findings_sort_missing_paths_before_named_paths(missing_path):
    unlocated = arm.Finding("incomplete-context", 76, "Z lookup", "Retry.",
                            path=missing_path, related_issue=9)
    located = arm.Finding("incomplete-context", 76, "A lookup", "Retry.",
                          path="docs/arming.md", related_issue=1)
    assert sorted([located, unlocated], key=arm.Finding.sort_key) == [unlocated, located]


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


@pytest.mark.scenario("issue-arming/issue-body-lint/human-checks-stay-outside-done")
@pytest.mark.scenario("issue-arming/issue-body-lint/named-tests-are-required")
@pytest.mark.parametrize("rule", ["***", "* * *", "___", "_ _ _", "- - -"])
def test_three_marker_rule_ends_list_context_before_setext_heading(issue_body, rule):
    # A thematic break ends the list, allowing the next paragraph to be a
    # Setext heading. Its test name and manual check are outside the section.
    done = f"\n- Check behavior\n{rule}\n`test_outside` visually inspect\n---"
    body = issue_body(done)
    assert [(f.code, f.line) for f in arm.lint_body(76, body)] == [("named-test", 3)]
    section = next(s for s in arm.parse_sections(body) if s.name == "Definition of done")
    assert section.text == f" \n- Check behavior\n{rule}"


@pytest.mark.scenario("issue-arming/issue-body-lint/named-tests-are-required")
@pytest.mark.parametrize("underline", ["---", "==="])
def test_multiline_setext_heading_excludes_entire_title(underline):
    body = ("**Scope:** file\n**What to do:** lint\n**Definition of done:**\n\n"
            f"`test_outside`\nAdditional notes\n{underline}\nOther content\n"
            "**What CI cannot prove:** Nothing.")
    assert [(f.code, f.line) for f in arm.lint_body(76, body)] == [("named-test", 3)]
    section = next(s for s in arm.parse_sections(body) if s.name == "Definition of done")
    assert section.text == "\n"


@pytest.fixture
def concurrent_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path / "run-home")
    root = tmp_path / "checkout"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").touch()

    def add_run(name, text, status="running", repo=None):
        task = root / "openspec" / "changes" / name / "tasks.md"
        task.parent.mkdir(parents=True)
        task.write_text(f"- [ ] 1.1 Edit {text}\n")
        git(root, "add", ".")
        git(root, "commit", "-qm", "tasks", env=IDENTITY)
        run = state.Run.for_change(name)
        run.state.status = status
        run.save()
        git(root, "clone", "-q", "--bare", str(root), str(run.dir / "repo.git"))
        (run.dir / "manifest.toml").write_text(f'''
[run]
repo = "{repo or root}"
change = "{name}"
branch = "main"
[roles]
implement = "p"
review = "p"
[profiles.p]
adapter = "codex"
model = "m"
[paths]
allowed = ["README.md", "src/**", "tests/**"]
[[verify]]
id = "tests"
run = ["true"]
''')
        return run

    return root, add_run


def armed_reader(pages, repository="org/repo"):
    calls = []

    def runner(argv, **kwargs):
        assert kwargs == dict(capture_output=True, text=True, check=True, timeout=30)
        host, repo = (repository.split("/", 1) if repository.count("/") == 2
                      else ("github.com", repository))
        if any("/pulls?" in arg for arg in argv):
            endpoint = f"repos/{repo}/pulls?state=open&per_page=100&page=1"
            assert argv in (["gh", "api", "--hostname", host, endpoint],
                            ["gh", "api", endpoint,
                             *(["--hostname", host] if host != "github.com" else []),
                             "--method", "GET"])
            return subprocess.CompletedProcess(argv, 0, "[]")
        assert argv == ["gh", "api", "--hostname", host,
                        f"repos/{repo}/issues?state=open&labels=spec%3Adraft&per_page=100&page={len(calls) + 1}"]
        calls.append(argv)
        page = pages[len(calls) - 1]
        if isinstance(page, Exception):
            raise page
        return subprocess.CompletedProcess(argv, 0, json.dumps(page))

    return arm.IssueCache(runner=runner), calls


@pytest.mark.scenario("issue-arming/concurrent-path-collection/scope-and-path-rules-are-preserved")
def test_scope_collection_preserves_helper_rules(concurrent_checkout, monkeypatch):
    root, add_run = concurrent_checkout
    tokens = "`README.md` `new/nested.py` `src/code.py:helper` `src/code.py::helper` `src/*.py`"
    body = (f"**Scope:** {tokens}\n**What to do:** `outside/action.py`\n"
            "**Definition of done:** `tests/outside.py::test_named`\n"
            "**What CI cannot prove:** `outside/human.py`\n")
    target = arm.Issue(76, body, "OPEN", None, frozenset())
    cache, calls = armed_reader([[issue_record(77, body=body)]])
    add_run("repo-issue-76-old-title", tokens)
    add_run("repo-issue-78-running", tokens)
    add_run("repo-issue-79-paused", "`tests/shared.py`", status="paused")
    add_run("repo-issue-80-expanded", "`src/actual.py`")
    add_run("repo-issue-81-done", tokens, status="done")
    add_run("repo-issue-82-other", tokens, repo=root.parent / "other-checkout")
    (root / "src").mkdir()
    (root / "src" / "actual.py").touch()
    alias = root.parent / "alias"
    alias.symlink_to(root, target_is_directory=True)
    monkeypatch.chdir(root.parent)
    original = arm.propose.in_flight
    flight_calls = []

    def in_flight(source, change):
        flight_calls.append((source, change))
        return original(source, change)

    monkeypatch.setattr(arm.propose, "in_flight", in_flight)
    context = arm.collect_concurrent("org/repo", alias, [target], cache)
    expected = {"README.md", "new/nested.py", "src/code.py", "src/*.py"}
    assert arm.scope_paths(body, alias) == expected
    overlaps = arm.concurrent_overlaps(target, alias, context)
    assert {(e.path, e.issue, e.source) for e in overlaps} == {
        (path, number, source) for path in expected
        for number, source in [(77, "issue #77"), (78, "run repo-issue-78-running")]
    }
    assert arm.PathEvidence("tests/shared.py", 79, "run repo-issue-79-paused") in context.evidence
    assert flight_calls == [(alias, "")] and len(calls) == 1 and not context.notes


@pytest.mark.scenario("issue-arming/concurrent-path-collection/unattributed-run-overlap-is-visible")
@pytest.mark.parametrize("status", ["running", "paused"])
def test_unrecognized_run_overlap_has_no_guessed_blocker(concurrent_checkout, status):
    root, add_run = concurrent_checkout
    name = "repo-issue-99-Custom"
    add_run(name, "`README.md`", status=status)
    target = arm.Issue(76, "**Scope:** `README.md`\nblocked-by: #99", "OPEN", None, frozenset())
    cache, _ = armed_reader([[]])
    context = arm.collect_concurrent("org/repo", root, [target], cache)
    findings = arm.unattributed_run_findings(target, root, context)
    assert len(findings) == 1
    finding = findings[0]
    assert (finding.code, finding.issue, finding.path, finding.related_issue) == ("overlap", 76, "README.md", None)
    assert name in finding.message and "README.md" in finding.message
    assert "Identify" in finding.suggestion and "source issue" in finding.suggestion
    assert "#99" not in str(findings) and "blocked-by:" not in str(findings)


@pytest.mark.scenario("issue-arming/concurrent-path-collection/scope-and-path-rules-are-preserved")
@pytest.mark.parametrize("body", [None, "", "**Scope:** `src/shared.py`"])
def test_armed_rest_body_is_normalized_and_cached(concurrent_checkout, body):
    root, _ = concurrent_checkout
    row = {"number": 77, "body": body, "state": "open", "state_reason": None,
           "labels": [{"name": "spec:draft"}]}
    cache, calls = armed_reader([[row]])
    target = arm.Issue(76, "**Scope:** `src/shared.py`", "OPEN", None, frozenset())
    context = arm.collect_concurrent("org/repo", root, [target], cache)
    issues, notes = cache.armed("org/repo")
    expected = arm.Issue(77, body or "", "OPEN", None, frozenset({"spec:draft"}))
    assert issues == [expected]
    assert cache.get("org/repo", 77) == expected
    assert not notes and not context.notes
    assert arm.concurrent_overlaps(target, root, context) == (
        [arm.PathEvidence("src/shared.py", 77, "issue #77")] if body else [])
    assert len(calls) == 1


@pytest.mark.parametrize("tail", [[], [issue_record(78, body="**Scope:** `src/later.py`")],
                                   subprocess.TimeoutExpired(["gh"], 30), {}])
def test_armed_pages_are_cached_and_retain_evidence(concurrent_checkout, tail):
    root, _ = concurrent_checkout
    first = [issue_record(76, body="**Scope:** `src/self.py`"),
             issue_record(79, body="**Scope:** `src/earlier.py`")]
    first += [{"number": n, "pull_request": {}} for n in range(1, 99)]
    repo = "github.example/org/repo"
    cache, calls = armed_reader([first, tail], repo)
    target = arm.Issue(76, "**Scope:** `src/batch.py`", "OPEN", None, frozenset())
    other = arm.Issue(77, target.body, "OPEN", None, frozenset())
    context = arm.collect_concurrent(repo, root, [target, other], cache)
    assert arm.concurrent_overlaps(target, root, context) == [arm.PathEvidence("src/batch.py", 77, "issue #77")]
    assert all(e.path != "src/self.py" for e in context.evidence)
    assert arm.PathEvidence("src/earlier.py", 79, "issue #79") in context.evidence
    assert cache.get(repo, 76).number == 76  # Listing bodies seed the shared issue cache.
    assert (arm.PathEvidence("src/later.py", 78, "issue #78") in context.evidence) == bool(isinstance(tail, list) and tail)
    assert bool(context.notes) == (not isinstance(tail, list))
    assert cache.armed(repo) is cache.armed(repo) and len(calls) == 2


@pytest.mark.parametrize("change,expected", [
    ("r.po-issue-1-a", 1), ("r.po-issue-123-" + "a" * 40, 123),
    ("r.po-issue-0-a", None), ("r.po-issue-01-a", None), ("r.po-issue-1-", None),
    ("r.po-issue-1-" + "a" * 41, None), ("r.po-issue-1-A", None),
    ("r.po-issue-1-a_b", None), ("rXpo-issue-1-a", None),
    ("spec/r.po-issue-1-a", None), ("r.po-issue-1-a\n", None),
])
def test_front_door_identity_requires_full_producer_name(change, expected):
    assert arm.front_door_issue("github.example/org/r.po", change) == expected


def pr_record(number, issue, *, repo="org/repo", branch=None):
    return {"number": number, "html_url": f"https://github.example/org/repo/pull/{number}",
            "head": {"ref": branch or f"spec/repo-issue-{issue}-old-title",
                     "repo": {"full_name": repo}}}


def context_reader(responses, repository="org/repo"):
    """Serve separate helper/metadata snapshots; reject any mutation or extra read."""
    calls = []

    def runner(argv, **kwargs):
        assert argv[:2] == ["gh", "api"]
        assert kwargs == dict(capture_output=True, text=True, check=True, timeout=30)
        helper = "--method" in argv
        host, repo = (repository.split("/", 1) if repository.count("/") == 2
                      else ("github.com", repository))
        endpoint = argv[2] if helper else argv[4]
        assert argv == (["gh", "api", endpoint,
                         *(["--hostname", host] if host != "github.com" else []),
                         "--method", "GET"] if helper else
                        ["gh", "api", "--hostname", host, endpoint])
        assert endpoint.startswith(f"repos/{repo}/")
        key = ("helper" if helper else "metadata", endpoint.split(f"repos/{repo}/", 1)[1])
        calls.append(key)
        result = responses[key]
        if isinstance(result, Exception):
            raise result
        if isinstance(result, subprocess.CompletedProcess):
            return result
        return subprocess.CompletedProcess(argv, 0, json.dumps(result))

    return arm.IssueCache(runner=runner), calls


@pytest.mark.scenario("issue-arming/concurrent-path-collection/pagination-identity-and-self-exclusion")
@pytest.mark.parametrize("number", [1, 10, 100])
def test_front_door_pr_numbers_starting_with_one_keep_file_evidence(number):
    pr = pr_record(number, 77)
    cache, calls = context_reader({
        ("helper", "pulls?state=open&per_page=100&page=1"): [pr],
        ("metadata", "pulls?state=open&per_page=100&page=1"): [pr],
        ("helper", f"pulls/{number}/files?per_page=100&page=1"):
            [{"filename": "src/shared.py", "status": "modified"}],
    })
    context = cache.open_prs("org/repo")
    assert context.evidence == [arm.PathEvidence(
        "src/shared.py", 77, f"PR #{number} https://github.example/org/repo/pull/{number}",
    )]
    assert context.notes == []
    assert len(calls) == 3


@pytest.mark.scenario("issue-arming/concurrent-path-collection/all-three-sources-contribute")
def test_all_concurrent_sources_preserve_attribution(concurrent_checkout, monkeypatch):
    root, add_run = concurrent_checkout
    paths = ["src/shared.py", "docs/new.md", "docs/old.md", "tests/shared.py"]
    body = "**Scope:** " + " ".join(f"`{p}`" for p in paths)
    target = arm.Issue(76, body, "OPEN", None, frozenset())
    add_run("repo-issue-77-running", "`src/shared.py` `docs/new.md`")
    add_run("repo-issue-78-paused", "`tests/shared.py`", status="paused")
    pr = pr_record(901, 77)
    cache, calls = context_reader({
        ("metadata", "issues?state=open&labels=spec%3Adraft&per_page=100&page=1"):
            [issue_record(77, body=body)],
        ("helper", "pulls?state=open&per_page=100&page=1"): [pr],
        ("metadata", "pulls?state=open&per_page=100&page=1"): [pr],
        ("helper", "pulls/901/files?per_page=100&page=1"):
            [{"filename": p} for p in paths if p != "docs/old.md"]
            + [{"filename": "docs/new.md", "previous_filename": "docs/old.md"}],
    })
    original = arm.critic.open_pr_files
    helper_calls = []

    def open_pr_files(repository, branch, *, runner):
        helper_calls.append((repository, branch, runner))
        return original(repository, branch, runner=runner)

    monkeypatch.setattr(arm.critic, "open_pr_files", open_pr_files)
    context = arm.collect_concurrent("org/repo", root, [target], cache)
    overlaps = arm.concurrent_overlaps(target, root, context)
    assert set(overlaps) == {
        *(arm.PathEvidence(p, 77, "issue #77") for p in paths),
        *(arm.PathEvidence(p, 77, f"PR #901 {pr['html_url']}") for p in paths),
        arm.PathEvidence("src/shared.py", 77, "run repo-issue-77-running"),
        arm.PathEvidence("docs/new.md", 77, "run repo-issue-77-running"),
        arm.PathEvidence("tests/shared.py", 78, "run repo-issue-78-paused"),
    }
    assert len(overlaps) == 11 and not context.notes
    assert cache.open_prs("org/repo") is cache.open_prs("org/repo")
    assert helper_calls == [("org/repo", "", cache.runner)] and len(calls) == 4


@pytest.mark.scenario("issue-arming/concurrent-path-collection/pagination-identity-and-self-exclusion")
def test_paginated_context_uses_source_issue_identity(concurrent_checkout):
    root, add_run = concurrent_checkout
    body = "**Scope:** `src/shared.py`"
    target = arm.Issue(76, body, "OPEN", None, frozenset())
    add_run("repo-issue-76-old-title", "`src/shared.py`")
    prs = [pr_record(900, 76), pr_record(901, 77, branch="feature/ordinary"),
           pr_record(902, 78, repo="fork/repo"),
           pr_record(903, 79, branch="spec/repo-issue-79-Invalid")]
    prs += [pr_record(903, 79, branch="ordinary")] * 96
    later = pr_record(76, 80, repo="ORG/REPO")  # PR #76 is not source issue #76.
    issues = [issue_record(76, body=body)] + [{"pull_request": {}}] * 99
    responses = {
        ("metadata", "issues?state=open&labels=spec%3Adraft&per_page=100&page=1"): issues,
        ("metadata", "issues?state=open&labels=spec%3Adraft&per_page=100&page=2"):
            [issue_record(81, body=body)],
    }
    for source in ("helper", "metadata"):
        responses[source, "pulls?state=open&per_page=100&page=1"] = prs
        responses[source, "pulls?state=open&per_page=100&page=2"] = [later]
    for number in (900, 901, 902, 903):
        responses["helper", f"pulls/{number}/files?per_page=100&page=1"] = [{"filename": "src/shared.py"}]
    responses["helper", "pulls/76/files?per_page=100&page=1"] = [{"filename": "unrelated/file.py"}] * 100
    responses["helper", "pulls/76/files?per_page=100&page=2"] = [{"filename": "src/shared.py"}]
    cache, calls = context_reader(responses, "github.example/org/repo")
    context = arm.collect_concurrent("github.example/org/repo", root, [target], cache)
    assert arm.concurrent_overlaps(target, root, context) == [
        arm.PathEvidence("src/shared.py", 80, f"PR #76 {later['html_url']}"),
        arm.PathEvidence("src/shared.py", 81, "issue #81"),
    ]
    assert not context.notes
    assert len(calls) == 12 and len(set(calls)) == 12


@pytest.mark.scenario("issue-arming/concurrent-path-collection/partial-context-retains-evidence")
@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired(["gh"], 30), OSError("private diagnostic"),
    subprocess.CompletedProcess(["gh"], 1, "private diagnostic"),
    subprocess.CompletedProcess(["gh"], 0, "not JSON"), {},
])
def test_partial_context_keeps_evidence_and_distinct_findings(concurrent_checkout, failure):
    root, add_run = concurrent_checkout
    body = "**Scope:** `docs/shared.md`"
    target = arm.Issue(76, body, "OPEN", None, frozenset())
    add_run("repo-issue-78-running", "`docs/shared.md`")
    known, missing, malformed = pr_record(901, 77), pr_record(902, 79), pr_record(903, 80)
    malformed["head"]["repo"] = None
    cache, calls = context_reader({
        ("metadata", "issues?state=open&labels=spec%3Adraft&per_page=100&page=1"):
            [issue_record(77, body=body)] * 100,
        ("metadata", "issues?state=open&labels=spec%3Adraft&per_page=100&page=2"): failure,
        ("helper", "pulls?state=open&per_page=100&page=1"): [known, missing, malformed],
        ("helper", "pulls/901/files?per_page=100&page=1"): [{"filename": "docs/shared.md"}] * 100,
        ("helper", "pulls/901/files?per_page=100&page=2"): OSError("private diagnostic"),
        ("helper", "pulls/902/files?per_page=100&page=1"): [{"filename": "docs/shared.md"}],
        ("helper", "pulls/903/files?per_page=100&page=1"): [{"filename": "docs/shared.md"}],
        ("metadata", "pulls?state=open&per_page=100&page=1"): [known] * 99 + [malformed],
        ("metadata", "pulls?state=open&per_page=100&page=2"): failure,
    })
    context = arm.collect_concurrent("org/repo", root, [target], cache)
    assert set(arm.concurrent_overlaps(target, root, context)) == {
        arm.PathEvidence("docs/shared.md", 77, "issue #77"),
        arm.PathEvidence("docs/shared.md", 77, f"PR #901 {known['html_url']}"),
        arm.PathEvidence("docs/shared.md", 78, "run repo-issue-78-running"),
    }
    findings = arm.context_findings(76, context)
    assert {f.message for f in findings} == {
        "Armed issue page 2 could not be read.",
        "Open PR #901 files unavailable: GitHub request failed.",
        "Open PR metadata page 2 could not be read.",
        "Open PR #903 metadata has an unknown head repository or branch.",
        f"Open PR metadata unavailable for #902 {missing['html_url']}.",
        f"Open PR metadata unavailable for #903 {malformed['html_url']}.",
    }
    assert len(findings) == 6
    assert all(f.code == "incomplete-context" and f.issue == 76 and f.suggestion for f in findings)
    assert "private diagnostic" not in str(context)
    assert findings == sorted(findings, key=arm.Finding.sort_key)
    assert cache.open_prs("org/repo") is cache.open_prs("org/repo") and len(calls) == 9


@pytest.fixture
def batch_reader(tmp_path, monkeypatch, issue_reader):
    monkeypatch.setattr(state, "ROOT", tmp_path / "run-home")

    def make(records):
        cache, calls = issue_reader(records)
        read = cache.runner
        context_calls = []

        def runner(argv, **kwargs):
            if argv[:3] == ["gh", "issue", "view"]:
                return read(argv, **kwargs)
            assert argv[:2] == ["gh", "api"]  # Reject edits and any other mutation.
            endpoint = argv[2] if "--method" in argv else argv[4]
            assert endpoint in (
                "repos/org/repo/issues?state=open&labels=spec%3Adraft&per_page=100&page=1",
                "repos/org/repo/pulls?state=open&per_page=100&page=1",
            )
            context_calls.append(endpoint)
            return subprocess.CompletedProcess(argv, 0, "[]")

        cache.runner = runner
        return cache, calls, context_calls
    return make


@pytest.mark.scenario("issue-arming/dependency-aware-overlap-decisions/unchained-overlap-suggests-a-blocker")
def test_arm_reports_unchained_overlap(tmp_path, issue_body, issue_reader):
    cache, calls = issue_reader({
        ("org/repo", n): issue_record(n, body=issue_body()) for n in (76, 77, 78)
    })
    target = cache.get("org/repo", 76)
    path = "nightshift/arm.py"
    sources = ["issue #77", "run repo-issue-77-old-title",
               "PR #901 https://github.example/org/repo/pull/901"]
    context = arm.ConcurrentContext([
        *(arm.PathEvidence(path, 77, source) for source in sources),
        arm.PathEvidence(path, 77, sources[0]),
        arm.PathEvidence(path, 78, "run repo-issue-78-paused"),
        arm.PathEvidence(path, 76, "run repo-issue-76-self"),
        arm.PathEvidence(path, None, "run custom-name"),
    ], [])
    findings = arm.overlap_findings(target, tmp_path, context, "org/repo", cache)
    assert [(f.code, f.path, f.related_issue) for f in findings] == [
        ("overlap", path, n) for n in (None, 77, 78)
    ]
    assert "Identify" in findings[0].suggestion
    for finding, number in zip(findings[1:], (77, 78)):
        assert f"#{number}" in finding.message and path in finding.message
        assert f"blocked-by: #{number}" in finding.suggestion
    assert all(source in findings[1].message for source in sources)
    assert arm.overlap_findings(target, tmp_path, context, "org/repo", cache) == findings
    assert calls == [("org/repo", n) for n in (76, 77, 78)]


@pytest.mark.scenario("issue-arming/dependency-aware-overlap-decisions/unchained-overlap-suggests-a-blocker")
def test_shared_prerequisite_does_not_chain_candidates(tmp_path, issue_body, batch_reader):
    records = {("org/repo", n): issue_record(n, body=issue_body() + "blocked-by: #90")
               for n in (76, 77)}
    records["org/repo", 90] = issue_record(90)
    cache, calls, _ = batch_reader(records)
    results = arm.check_candidates("org/repo", [76, 77], tmp_path, cache)
    assert [[(f.code, f.related_issue) for f in results[n]] for n in (76, 77)] == [
        [("overlap", 77)], [("overlap", 76)],
    ]
    assert len(calls) == len(set(calls)) == 3


@pytest.mark.scenario("issue-arming/dependency-aware-overlap-decisions/transitive-chains-work-in-both-directions")
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("intermediate", [False, True])
def test_arm_accepts_transitively_chained_overlap(tmp_path, issue_body, batch_reader,
                                                 reverse, intermediate):
    source, target = (77, 76) if reverse else (76, 77)
    records = {("org/repo", n): issue_record(n, body=issue_body()) for n in (76, 77)}
    records["org/repo", source]["body"] = (
        issue_body("`test_expected_behavior` manually inspect")
        + f"blocked-by: #{90 if intermediate else target} #91"
    )
    records["org/repo", 90] = {
        **issue_record(90, body=f"> * BlOcKeD-bY: #{target}"), "labels": [],
    }
    records["org/repo", 91] = issue_record(91, state="CLOSED", reason="NOT_PLANNED")
    cache, calls, _ = batch_reader(records)
    results = arm.check_candidates("org/repo", [76, 77], tmp_path, cache)
    assert results[target] == []
    assert [f.code for f in results[source]] == ["dependency-completion", "manual-check"]
    assert results[source][0].related_issue == 91
    assert (("org/repo", 90) in calls) == intermediate
    assert len(calls) == len(set(calls))


@pytest.mark.scenario("issue-arming/dependency-aware-overlap-decisions/cycles-terminate-and-failures-do-not-exempt")
@pytest.mark.parametrize("failed", [False, True])
def test_cycles_and_failed_intermediates_keep_overlap(tmp_path, issue_body, batch_reader, failed):
    records = {
        ("org/repo", 76): issue_record(76, body=issue_body() + "blocked-by: #90"),
        ("org/repo", 77): issue_record(77, body=issue_body()),
        ("org/repo", 90): issue_record(90, body="blocked-by: #91"),
        ("org/repo", 91): (OSError("private diagnostic") if failed else
                           issue_record(91, body="blocked-by: #90")),
    }
    cache, calls, _ = batch_reader(records)
    results = arm.check_candidates("org/repo", [76, 77], tmp_path, cache)
    for number, other in ((76, 77), (77, 76)):
        assert [(f.code, f.related_issue) for f in results[number]] == (
            ([("incomplete-context", 91)] if failed else []) + [("overlap", other)]
        )
        assert f"blocked-by: #{other}" in results[number][-1].suggestion
    assert "private diagnostic" not in str(results)
    assert arm.check_candidates("org/repo", [77, 76], tmp_path, cache) == results
    assert len(calls) == len(set(calls)) == 4


@pytest.mark.scenario("issue-arming/dependency-aware-overlap-decisions/cycles-terminate-and-failures-do-not-exempt")
def test_cycle_search_does_not_cache_partial_negative_proofs(issue_reader):
    cache, calls = issue_reader({
        (repo, n): issue_record(n, body=body)
        for repo in ("org/repo", "other/repo")
        for n, body in ((1, "blocked-by: #2 #3"), (2, "blocked-by: #1"),
                        (3, "blocked-by: #4"))
    })
    assert cache.reaches("org/repo", 1, 4) == (True, ())
    assert cache.reaches("org/repo", 2, 4) == (True, ())
    assert cache.reaches("other/repo", 2, 4) == (True, ())
    assert len(calls) == len(set(calls)) == 6


@pytest.mark.parametrize("outcome", ["reachable", "unreachable", "failed"])
def test_reachability_cache_avoids_repeating_graph_traversal(issue_reader, monkeypatch, outcome):
    cache, calls = issue_reader({
        ("org/repo", 1): issue_record(1, body="blocked-by: #2"),
        ("org/repo", 2): (OSError("unavailable") if outcome == "failed" else
                         issue_record(2, body="blocked-by: #3" if outcome == "reachable" else "")),
    })
    parsed = []
    original = arm.blockers

    def blockers(body):
        parsed.append(body)
        return original(body)

    monkeypatch.setattr(arm, "blockers", blockers)
    expected = (outcome == "reachable",
                ((2, "Issue #2 lookup failed."),) if outcome == "failed" else ())
    assert cache.reaches("org/repo", 1, 3) == expected
    assert parsed == (["blocked-by: #2"] if outcome == "failed" else
                      ["blocked-by: #2", "blocked-by: #3" if outcome == "reachable" else ""])
    parsed.clear()
    assert cache.reaches("org/repo", 1, 3) == expected
    # Issue reads alone cannot detect retraversal: the issue cache masks it.
    assert parsed == []
    assert calls == [("org/repo", 1), ("org/repo", 2)]


@pytest.mark.scenario("issue-arming/dependency-aware-overlap-decisions/cycles-terminate-and-failures-do-not-exempt")
@pytest.mark.scenario("issue-arming/dependency-aware-overlap-decisions/transitive-chains-work-in-both-directions")
def test_failed_branch_keeps_context_finding_but_does_not_hide_proven_chain(
        tmp_path, issue_body, batch_reader):
    cache, calls, _ = batch_reader({
        ("org/repo", 76): issue_record(76, body=issue_body() + "blocked-by: #90 #91"),
        ("org/repo", 77): issue_record(77, body=issue_body()),
        ("org/repo", 90): OSError("unreadable branch"),
        ("org/repo", 91): issue_record(91, body="blocked-by: #77"),
    })
    results = arm.check_candidates("org/repo", [76, 77], tmp_path, cache)
    for findings in results.values():
        # The direct-blocker and traversal failure produce one diagnostic.
        assert [(f.code, f.related_issue) for f in findings] == [("incomplete-context", 90)]
    assert len(calls) == len(set(calls)) == 4


@pytest.mark.scenario("issue-arming/dependency-aware-overlap-decisions/batch-candidates-are-compared-together")
@pytest.mark.parametrize("numbers", [[76, 77, 76], [77, 76, 77]])
def test_unlabelled_batch_overlap_is_checked_before_writes(tmp_path, issue_body, batch_reader, numbers):
    cache, calls, context_calls = batch_reader({
        ("org/repo", n): {**issue_record(n, body=issue_body()), "labels": []}
        for n in (76, 77)
    })
    results = arm.check_candidates("org/repo", numbers, tmp_path, cache)
    assert list(results) == numbers[:2]
    assert calls == [("org/repo", n) for n in numbers[:2]]
    assert len(context_calls) == 3  # One shared armed/PR snapshot for the batch.
    for number, other in ((76, 77), (77, 76)):
        assert [(f.code, f.related_issue) for f in results[number]] == [("overlap", other)]
        assert f"blocked-by: #{other}" in results[number][0].suggestion
        assert cache.get("org/repo", number).labels == frozenset()


@pytest.fixture
def command_reader(tmp_path, monkeypatch, issue_reader):
    monkeypatch.setattr(state, "ROOT", tmp_path / "run-home")
    state.ROOT.mkdir()
    (state.ROOT / "sentinel").write_text("Existing local state must be preserved.\n")

    def snapshot():
        return {str(path.relative_to(tmp_path)): (path.read_bytes(), path.stat().st_mtime_ns)
                if path.is_file() else None for path in tmp_path.rglob("*")}

    before = snapshot()

    def make(records, host="github.com", *, edit_failures=None, label_reads=None):
        repository = "org/repo" if host == "github.com" else f"{host}/org/repo"
        cache, _ = issue_reader({(repository, n): row for n, row in records.items()})
        calls = []
        context = [
            ["gh", "api", "--hostname", host,
             "repos/org/repo/issues?state=open&labels=spec%3Adraft&per_page=100&page=1"],
            ["gh", "api", "repos/org/repo/pulls?state=open&per_page=100&page=1",
             *(["--hostname", host] if host != "github.com" else []), "--method", "GET"],
            ["gh", "api", "--hostname", host,
             "repos/org/repo/pulls?state=open&per_page=100&page=1"],
        ]

        def runner(argv, **kwargs):
            calls.append(argv)
            expected = dict(capture_output=True, text=True, check=True, timeout=30)
            if argv == ["gh", "repo", "view", "--json", "nameWithOwner,url"]:
                expected["cwd"] = tmp_path.resolve()
                output = json.dumps({"nameWithOwner": "org/repo", "url": f"https://{host}/org/repo"})
            elif argv == ["git", "rev-parse", "--show-toplevel"]:
                output = str(tmp_path) + "\n"
            elif argv[:3] == ["gh", "issue", "view"] and argv[-1] == "labels":
                assert argv == ["gh", "issue", "view", argv[3], "--repo", repository,
                                "--json", "labels"]
                assert kwargs == expected
                result = label_reads[int(argv[3])]
                if isinstance(result, Exception):
                    raise result
                if isinstance(result, subprocess.CompletedProcess):
                    return result
                output = json.dumps(result)
            elif argv[:3] == ["gh", "issue", "view"]:
                return cache.runner(argv, **kwargs)
            elif argv[:3] == ["gh", "issue", "edit"]:
                assert argv == ["gh", "issue", "edit", argv[3], "--repo", repository,
                                "--add-label", "spec:draft"]
                assert int(argv[3]) in records
                assert all(read in calls for read in context)  # Snapshot precedes writes.
                assert kwargs == expected
                failure = (edit_failures or {}).get(int(argv[3]))
                if isinstance(failure, Exception):
                    raise failure
                if failure is not None:
                    return failure
                records[int(argv[3])]["labels"].append({"name": "spec:draft"})
                output = ""
            else:
                assert argv in context  # No unexpected reads or mutations.
                output = "[]"
            assert kwargs == expected
            return subprocess.CompletedProcess(argv, 0, output)

        monkeypatch.setattr(arm.subprocess, "run", runner)
        return calls, context, repository
    yield make
    assert snapshot() == before  # No receipts, run-state edits, or other local writes.


def unarmed_record(number, body):
    return {**issue_record(number, body=body), "labels": [{"name": "bug"}]}


@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/clean-issue-is-armed")
@pytest.mark.parametrize("host", ["github.com", "github.example"])
def test_arm_labels_when_clean(command_reader, issue_body, capsys, host):
    calls, context, repository = command_reader({76: unarmed_record(76, issue_body())}, host)
    assert arm.main(["#76", "76"]) == 0
    assert calls == [
        ["git", "rev-parse", "--show-toplevel"],
        ["gh", "repo", "view", "--json", "nameWithOwner,url"],
        ["gh", "issue", "view", "76", "--repo", repository,
         "--json", "number,body,state,stateReason,labels"],
        *context,
        ["gh", "issue", "edit", "76", "--repo", repository, "--add-label", "spec:draft"],
    ]
    assert capsys.readouterr().out == "#76: armed\n"


@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/check-mode-never-labels")
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("failing", [False, True])
def test_arm_check_mode_never_labels(command_reader, issue_body, capsys, force, failing):
    records = {76: unarmed_record(76, issue_body())}
    if failing:
        records[77] = unarmed_record(77, "")
    calls, _, _ = command_reader(records)
    args = ["--check", *(["--force"] if force else []), *map(str, records)]
    assert arm.main(args) == int(failing)
    assert not any(call[:3] == ["gh", "issue", "edit"] for call in calls)
    output = capsys.readouterr().out
    assert output.startswith("#76: check clean\n")
    assert output.count("[missing-section]") == (4 if failing else 0)
    if failing:
        for section in arm.REQUIRED_SECTIONS:
            assert f"Missing **{section}:** section." in output
        assert output.endswith("#77: check failed\n")


@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/findings-prevent-default-arming")
@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/force-labels-and-reports")
@pytest.mark.parametrize("force", [False, True], ids=["default-refusal", "force"])
def test_arm_force_labels_and_reports(command_reader, issue_body, capsys, force):
    body = issue_body("manually inspect") + "Do not edit existing tests.\nblocked-by: #90"
    calls, _, _ = command_reader({76: unarmed_record(76, body),
                                 77: unarmed_record(77, issue_body()),
                                 90: OSError("private diagnostic")})
    assert arm.main([*(["--force"] if force else []), "77", "#76", "77"]) == (0 if force else 1)
    edits = [call for call in calls if call[:3] == ["gh", "issue", "edit"]]
    assert edits == ([["gh", "issue", "edit", str(n), "--repo", "org/repo",
                      "--add-label", "spec:draft"] for n in (77, 76)] if force else [])
    output = capsys.readouterr().out
    assert "private diagnostic" not in output
    codes = [line.split("[")[1].split("]")[0] for line in output.splitlines() if "[" in line]
    assert codes == ["incomplete-context", "overlap", "discouraged-phrase",
                     "incomplete-context", "manual-check", "named-test", "overlap"]
    for text in ("existing assertions are preserved", "Move this check under **What CI cannot prove:**",
                 "Name a test", "Issue #90 lookup failed.", "blocked-by: #76", "blocked-by: #77",
                 "line 5", "line 3", "path nightshift/arm.py"):
        assert text in output
    disposition = "forced arming" if force else "unarmed (findings)"
    assert output.count(f": {disposition}\n") == 2
    assert output.index(f"#77: {disposition}\n") < output.index("#76 line 5")
    assert output.endswith(f"#76: {disposition}\n")


@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/existing-labels-are-idempotent")
@pytest.mark.parametrize("mode", [[], ["--force"], ["--check"], ["--check", "--force"]])
@pytest.mark.parametrize("findings", [False, True])
def test_arm_existing_label_respects_mode(command_reader, issue_body, capsys, mode, findings):
    record = issue_record(76, body="" if findings else issue_body())
    calls, _, _ = command_reader({76: record})
    failed = findings and ("--check" in mode or "--force" not in mode)
    assert arm.main([*mode, "76", "#76"]) == int(failed)
    assert not any(call[:3] == ["gh", "issue", "edit"] for call in calls)
    assert len([call for call in calls if call[:3] == ["gh", "issue", "view"]]) == 1
    assert record["labels"] == [{"name": "spec:draft"}, {"name": "bug"}]
    output = capsys.readouterr().out
    assert output.count("[missing-section]") == (4 if findings else 0)
    if "--check" in mode:
        disposition = "check failed" if failed else "check clean"
    else:
        disposition = "unarmed (findings)" if failed else "already armed"
    assert output.endswith(f"#76: {disposition}\n")


@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/target-and-label-failures-remain-failures")
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("failure", ["unreadable", "malformed", "closed", "unknown-state"])
def test_arm_target_failure_does_not_stop_batch(command_reader, issue_body, capsys, force, failure):
    bad = unarmed_record(76, issue_body().replace("nightshift/arm.py", "docs/other.md"))
    if failure == "unreadable":
        bad = OSError("private diagnostic")
    elif failure == "malformed":
        bad = subprocess.CompletedProcess([], 0, "not json")
    else:
        bad["state"] = "CLOSED" if failure == "closed" else "UNKNOWN"
        bad["labels"].append({"name": "spec:draft"})
    records = {76: bad, 77: unarmed_record(77, issue_body())}
    calls, _, _ = command_reader(records)
    assert arm.main([*(["--force"] if force else []), "76", "77", "#76"]) == 1
    assert [call[3] for call in calls if call[:3] == ["gh", "issue", "view"]] == ["76", "77"]
    assert [call[3] for call in calls if call[:3] == ["gh", "issue", "edit"]] == ["77"]
    assert records[77]["labels"] == [{"name": "bug"}, {"name": "spec:draft"}]
    output = capsys.readouterr().out
    disposition = "issue unreadable" if failure in ("unreadable", "malformed") else "issue is not open"
    assert f"#76: unarmed ({disposition})\n" in output
    assert output.endswith(f"#77: {'forced arming' if force else 'armed'}\n")
    assert "private diagnostic" not in output


@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/target-and-label-failures-remain-failures")
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("write_failure", [
    OSError("private diagnostic"), subprocess.TimeoutExpired("gh", 30),
    subprocess.CalledProcessError(1, "gh"), subprocess.CompletedProcess([], 1, ""),
], ids=["os-error", "lost-response", "exit-error", "returned-error"])
@pytest.mark.parametrize("confirmation", [
    {"labels": [{"name": "bug"}, {"name": "spec:draft"}]},
    {"labels": [{"name": "bug"}]}, OSError("private diagnostic"),
    subprocess.TimeoutExpired("gh", 30), subprocess.CompletedProcess([], 1, ""),
    subprocess.CompletedProcess([], 0, "not json"), {"labels": ["spec:draft"]}, {}, [],
], ids=["confirmed", "absent", "unreadable", "timeout", "exit-error", "invalid-json",
        "invalid-labels", "missing-labels", "invalid-object"])
def test_arm_reconciles_label_write_once(command_reader, issue_body, capsys,
                                         force, write_failure, confirmation):
    body = issue_body("manually inspect" if force else "`test_expected_behavior`")
    records = {76: unarmed_record(76, body),
               77: unarmed_record(77, issue_body().replace("nightshift/arm.py", "docs/other.md"))}
    calls, context, repository = command_reader(
        records, edit_failures={76: write_failure}, label_reads={76: confirmation})
    confirmed = isinstance(confirmation, dict) and confirmation.get("labels") == [
        {"name": "bug"}, {"name": "spec:draft"}]
    assert arm.main([*(["--force"] if force else []), "76", "77", "#76"]) == int(not confirmed)
    # A fresh labels-only read follows the failed write, with no repeat write.
    assert calls == [
        ["git", "rev-parse", "--show-toplevel"],
        ["gh", "repo", "view", "--json", "nameWithOwner,url"],
        *[["gh", "issue", "view", str(n), "--repo", repository,
           "--json", "number,body,state,stateReason,labels"] for n in (76, 77)],
        *context,
        ["gh", "issue", "edit", "76", "--repo", repository, "--add-label", "spec:draft"],
        ["gh", "issue", "view", "76", "--repo", repository, "--json", "labels"],
        ["gh", "issue", "edit", "77", "--repo", repository, "--add-label", "spec:draft"],
    ]
    assert records[77]["labels"] == [{"name": "bug"}, {"name": "spec:draft"}]
    output = capsys.readouterr().out
    disposition = ("armed (label confirmed after write failure)" if confirmed else
                   "unconfirmed (label write failed)")
    assert f"#76: {disposition}\n" in output
    assert output.endswith(f"#77: {'forced arming' if force else 'armed'}\n")
    assert output.count("[manual-check]") == int(force)
    assert output.count("[named-test]") == int(force)
    assert "private diagnostic" not in output


@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/batch-results-retain-failures")
@pytest.mark.parametrize("numbers", [[77, 76, 77, 76], [76, 77, 76, 77]])
def test_arm_batch_keeps_success_and_all_findings(command_reader, issue_body, capsys, numbers):
    body = issue_body("visually inspect manually by hand") + "Do not edit existing tests.\n"
    records = {76: unarmed_record(76, body),
               77: unarmed_record(77, issue_body().replace("nightshift/arm.py", "docs/other.md"))}
    calls, _, _ = command_reader(records)
    assert arm.main([str(n) if i % 2 else f"#{n}" for i, n in enumerate(numbers)]) == 1
    assert [int(call[3]) for call in calls if call[:3] == ["gh", "issue", "view"]] == numbers[:2]
    assert [call[3] for call in calls if call[:3] == ["gh", "issue", "edit"]] == ["77"]
    assert records[76]["labels"] == [{"name": "bug"}]
    assert records[77]["labels"] == [{"name": "bug"}, {"name": "spec:draft"}]
    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if ": " in line and "[" not in line] == [
        "#77: armed" if n == 77 else "#76: unarmed (findings)" for n in numbers[:2]]
    findings = [line for line in lines if "[" in line]
    assert [line.split(": ", 1)[0] for line in findings] == [
        "#76 line 5 [discouraged-phrase]", *(["#76 line 3 [manual-check]"] * 3),
        "#76 line 3 [named-test]",
    ]
    for line, phrase in zip(findings[1:4], ("by hand", "manually", "visually")):
        assert f"Human check in Definition of done: {phrase}" in line
        assert "Move this check under **What CI cannot prove:**" in line
    assert "existing assertions are preserved" in findings[0]
    assert "Name a test" in findings[-1]
    assert lines.index(findings[-1]) < lines.index("#76: unarmed (findings)")


@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/invalid-arguments-fail-before-reads")
@pytest.mark.parametrize("args", [[], ["--check"], ["0"], ["#0"], ["-1"], ["abc"],
                                  ["76", "1.5"], ["org/repo#1"], ["##1"], ["1", "--unknown"]])
def test_arm_invalid_arguments_fail_before_reads(monkeypatch, capsys, args):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid arguments must fail before any subprocess call")

    monkeypatch.setattr(arm.subprocess, "run", unexpected)
    with pytest.raises(SystemExit) as error:
        arm.main(args)
    assert error.value.code == 2
    assert "usage: python -m nightshift.arm" in capsys.readouterr().err


def test_arm_module_entry_point(monkeypatch, capsys):
    import runpy

    monkeypatch.setattr("sys.argv", ["nightshift.arm"])
    monkeypatch.setattr(arm.subprocess, "run", lambda *a, **kw: pytest.fail("unexpected read"))
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        with pytest.raises(SystemExit) as error:
            runpy.run_module("nightshift.arm", run_name="__main__")
    assert error.value.code == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize("stage", ["git", "gh"])
@pytest.mark.parametrize("failure", [OSError("private diagnostic"),
                                    subprocess.CompletedProcess([], 1, ""),
                                    subprocess.CompletedProcess([], 0, "")])
def test_arm_resolution_failure_stops_before_collection(monkeypatch, tmp_path, capsys, stage, failure):
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        assert argv in (["git", "rev-parse", "--show-toplevel"],
                        ["gh", "repo", "view", "--json", "nameWithOwner,url"])
        if argv[0] == stage:
            if isinstance(failure, Exception):
                raise failure
            return failure
        return subprocess.CompletedProcess(argv, 0, str(tmp_path))

    monkeypatch.setattr(arm.subprocess, "run", runner)
    assert arm.main(["76"]) == 1
    assert len(calls) == (1 if stage == "git" else 2)
    assert capsys.readouterr().err == "Could not resolve the checkout and GitHub repository.\n"
