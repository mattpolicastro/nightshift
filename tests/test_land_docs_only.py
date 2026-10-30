"""Docs exclusion through the real gates and injected, exact forge transcripts."""

from html import escape
import json
from pathlib import Path
import re
import subprocess

import pytest

from nightshift import land
from test_land import (
    ACTOR, BASE, CHECKS, COMMENTS, CONFIRM_MERGED, EMPTY_STATUS, FINISH,
    FINISHED_HEAD, HEAD, MAIN, MAIN_REF, PR, REPO, RUN, STATUS, URL, VIEW,
    FakeClock, attestation, attestation_body, ci_check, merge_command,
    no_live_processes, scripted_runner, status_rows,
)
from test_land_ci_exclusion import DOCS, TRIGGER, file_row, no_sockets, workflow
from test_land_gitea import ROOT, combined, commit_facts, forge_table, opener
from test_factory_land_docs import links_resolve, section


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


class LandingScript:
    def __init__(self, runner, opener, config_path, gitea):
        self.runner, self.opener, self.config_path = runner, opener, config_path
        self.gitea, self.clock, self.budget, self.at = gitea, FakeClock(), 60, 0
        self.head, self.base, self.body, self.row = HEAD, BASE, "", None
        self.url = f"{ROOT}/{REPO}/pulls/78" if gitea else URL
        self.directory = ".gitea/workflows" if gitea else ".github/workflows"

    @property
    def timeout(self):
        return min(30, self.budget - self.at)

    def api(self, path, data, *, method="GET", body=None, timeout=None):
        timeout = self.timeout if timeout is None else timeout
        if self.gitea:
            self.opener.expect(method, f"{ROOT}/api/v1/{path}", data, body=body, timeout=timeout)
        else:
            argv = ["gh", "api", *(["--method", method] if method != "GET" else []), path]
            if body is not None:
                argv += ["-f", f"body={body['body']}"]
            self.runner.expect(argv, data, timeout=timeout)

    def pr(self, *, head=None, draft=False):
        head = self.head if head is None else head
        if self.gitea:
            self.api(f"repos/{REPO}/pulls/78", {
                "number": 78, "html_url": self.url, "state": "open", "body": self.body,
                "head": {"sha": head}, "base": {"ref": "main"},
                "title": "Draft: reviewed" if draft else "Reviewed",
            })
        else:
            self.runner.expect(VIEW, {**PR, "headRefOid": head, "body": self.body,
                                      "isDraft": draft}, timeout=self.timeout)

    def main(self, base=None):
        base = base or self.base
        if self.gitea:
            self.api(f"repos/{REPO}/branches/main", {"name": "main", "commit": {"id": base}})
        else:
            self.runner.expect(MAIN, {**MAIN_REF, "object": {"type": "commit", "sha": base}},
                               timeout=self.timeout)

    def status(self):
        self.runner.expect(STATUS, status_rows(self.row) if self.row else EMPTY_STATUS,
                           timeout=self.timeout)

    def comments(self, *pages):
        if self.gitea:
            for i, page in enumerate([*pages, []], 1):
                self.api(f"repos/{REPO}/issues/78/comments?page={i}&limit=50", page)
        else:
            self.runner.expect(COMMENTS, [*pages, []], timeout=self.timeout)

    def inputs(self):
        self.status()
        self.pr()
        if self.row:
            self.comments()

    def entry(self):
        # Initial discovery and finish validation precede the bounded gate.
        budget = self.budget
        self.budget = 60
        self.pr()
        self.main()
        self.status()
        self.inputs()
        self.budget = budget
        self.start()

    def start(self):
        self.main()
        if self.gitea:
            self.api(f"repos/{REPO}/git/commits/{self.head}", commit_facts(self.head, self.base))
        else:
            self.api(f"repos/{REPO}/compare/{self.base}...{self.head}",
                     {"status": "ahead", "merge_base_commit": {"sha": self.base}})

    def poll(self, state="empty", *, at=None):
        if at is not None:
            self.at = at
        self.pr()
        if self.gitea:
            empty = state in ("empty", "missing")
            state = {"empty": "pending", "missing": "pending", "pass": "success",
                     "fail": "failure", "cancel": "error", "skipping": "skipped"}.get(state, state)
            path = f"repos/{REPO}/commits/{self.head}/status?page="
            self.api(path + "1&limit=50", combined(state, head=self.head, rows=[] if empty else None,
                                           count=0 if empty else 1))
            if not empty:
                self.api(path + "2&limit=50", combined(state, head=self.head, rows=[], count=1))
        else:
            result = (subprocess.CompletedProcess([], 1, "", "no checks reported")
                      if state == "missing" else subprocess.CompletedProcess(
                          [], 8 if state == "pending" else 1 if state in ("fail", "cancel") else 0,
                          json.dumps([] if state == "empty" else [ci_check(state)]), ""))
            self.runner.expect(CHECKS, result, timeout=self.timeout)
        self.pr()

    def proof(self, paths=DOCS, *, text=TRIGGER, after_head=None):
        self.pr()
        self.api(f"repos/{REPO}/pulls/78", {"changed_files": len(paths)} if paths else {})
        if paths:
            key = "limit" if self.gitea else "per_page"
            self.api(f"repos/{REPO}/pulls/78/files?{key}=100&page=1", [file_row(p) for p in paths])
            self.api(f"repos/{REPO}/pulls/78/files?{key}=100&page=2", [])
            row, content = workflow(self.directory, text)
            self.api(f"repos/{REPO}/contents/{self.directory}?ref={self.base}", [row])
            self.api(f"repos/{REPO}/contents/{row['path']}?ref={self.base}", content)
        self.pr(head=after_head)

    def attest(self, mode="new", *, paths=DOCS, actor="operator", grace=10):
        self.api("user", {"login": actor})
        if mode == "actor":
            return
        marker = f"<!-- nightshift:land-ci:78:{self.head}:{self.base} -->"
        body = "\n".join([
            "**nightshift** — CI not required: docs-only", "",
            "@operator recorded the CI exclusion through `land`.", f"PR: {self.url}",
            f"Head: `{self.head}`", f"Base: `{self.base}`", f"Workflow: {self.directory}/ci.yml",
            'Patterns: [["**.md"], ["**.md"]]', "Changed paths:",
            *[f"- {escape(p, quote=False)}" for p in paths], f"Observed grace: {grace}s", "", marker,
        ])
        recorded = attestation(body, user={"login": "operator"})
        if mode in ("unique", "duplicate", "untrusted"):
            found = dict(recorded)
            if mode == "untrusted":
                found.update(author_association="NONE", user={"login": "stranger"})
            self.comments([attestation("unrelated", comment_id=1)], [found],
                          *([[{**recorded, "id": 502}]] if mode == "duplicate" else []))
            if mode != "unique":
                return
        else:
            self.comments()
            self.api(f"repos/{REPO}/issues/78/comments",
                     OSError("lost response") if mode in ("lost", "unreconciled") else recorded,
                     method="POST", body={"body": body})
            if mode not in ("lost", "unreconciled"):
                return
            self.comments([attestation("unrelated", comment_id=1)],
                          *([[recorded]] if mode == "lost" else []))
            if mode == "unreconciled":
                return
        self.api(f"repos/{REPO}/issues/comments/501", {}, method="PATCH", body={"body": body})

    def end(self, *, exemption=True, base=None):
        self.inputs()
        if exemption:
            self.attest()
            self.inputs()
        self.main(base)

    def merge(self, *, confirmed=True):
        if self.gitea:
            self.api(f"repos/{REPO}/pulls/78/merge", {}, method="POST",
                     body={"Do": "merge", "head_commit_id": self.head}, timeout=30)
            self.api(f"repos/{REPO}/pulls/78", {"number": 78, "html_url": self.url,
                     "state": "closed" if confirmed else "open", "merged": confirmed}, timeout=30)
        else:
            self.runner.expect(merge_command(self.head), {})
            self.runner.expect(CONFIRM_MERGED, {"number": 78, "url": self.url,
                                               "state": "MERGED" if confirmed else "OPEN"})

    def update(self, base, head, *, lost=False, unexpected=False):
        self.base = base
        self.main()
        self.pr()
        self.main()
        if self.gitea:
            self.api(f"repos/{REPO}/pulls/78/update?style=merge",
                     OSError("lost response") if lost else {}, method="POST")
        else:
            self.runner.expect(["gh", "api", "--method", "PUT",
                                f"repos/{REPO}/pulls/78/update-branch", "-f",
                                f"expected_head_sha={self.head}"],
                               OSError("lost response") if lost else {}, timeout=self.timeout)
        previous, self.head = self.head, head
        self.pr()
        self.api(f"repos/{REPO}/{'git/' if self.gitea else ''}commits/{head}",
                 commit_facts(head, base, previous) if unexpected else commit_facts(head, previous, base))

    def invoke(self, *, checks_done=False):
        return land.main([self.url, "--config", str(self.config_path), "--timeout", str(self.budget),
                          *(["--checks-done"] if checks_done else [])],
                         runner=self.runner, opener=self.opener,
                         sleep=self.clock.sleep, monotonic=self.clock.monotonic)


@pytest.fixture(params=[False, True], ids=["github", "gitea"])
def script(request, tmp_path, monkeypatch, scripted_runner, opener):
    path = tmp_path / "config.toml"
    gitea = request.param
    path.write_text((forge_table() if gitea else "") + f'[[repos]]\nname = "{REPO}"\n'
                    + ('forge = "gitea"\n' if gitea else ""))
    monkeypatch.setenv("LAND_HUMAN", "injected-human-token")
    result = LandingScript(scripted_runner, opener, path, gitea)
    yield result
    if gitea:
        assert all(req.get_header("Authorization") == "token injected-human-token"
                   and req.full_url.startswith(ROOT + "/api/v1/") for req, _ in opener.calls)
        assert all(argv[0] != "gh" for argv, _ in scripted_runner.calls)


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/bounded-docs-only-exemption/docs-only-pr-lands-without-ci")
@pytest.mark.scenario("reviewed-pr-landing/ci-exemption-attestation/exemption-without-human-checklist")
@pytest.mark.scenario("gitea-landing/gitea-exclusion-evidence/gitea-ignored-pr-uses-human-credential")
@pytest.mark.scenario("reviewed-pr-landing/forge-specific-operations-preserve-gates/operation-boundary-retains-behavior")
@pytest.mark.parametrize("missing,checks_done", [(False, False), (True, True)])
def test_docs_only_pr_lands_without_ci(script, capsys, missing, checks_done):
    script.entry()
    script.poll("missing" if missing else "empty")
    script.proof()
    script.poll(at=10)
    script.end()
    script.merge()
    assert script.invoke(checks_done=checks_done) == 0
    assert capsys.readouterr().out == f"No human checks listed\nCI not required: docs-only\nMerged {script.url}\n"
    assert script.clock.sleeps == [10]


@pytest.mark.scenario("reviewed-pr-landing/bounded-docs-only-exemption/mixed-pr-still-waits-for-ci")
@pytest.mark.scenario("gitea-landing/gitea-exclusion-evidence/gitea-uncertainty-retains-existing-gates")
@pytest.mark.parametrize("paths,text", [(("README.md", "nightshift/land.py"), TRIGGER),
                                       (None, TRIGGER), (DOCS, "on: [pull_request]\n")])
def test_mixed_pr_still_waits_for_ci(script, capsys, paths, text):
    script.budget = 15
    script.entry()
    script.poll()
    script.proof(paths, text=text)
    script.poll(at=10)
    assert script.invoke() == 1
    output = capsys.readouterr()
    assert "within 15s" in output.err if script.gitea else "deadline expired" in output.err
    assert "CI not required" not in output.out and script.clock.sleeps == [10, 5]


@pytest.mark.scenario("reviewed-pr-landing/bounded-docs-only-exemption/registered-check-on-docs-pr-still-required")
@pytest.mark.scenario("gitea-landing/exact-head-combined-ci/combined-states-control-progress")
@pytest.mark.scenario("reviewed-pr-landing/bounded-ci-wait/failing-ci-refused")
@pytest.mark.scenario("reviewed-pr-landing/bounded-ci-wait/shared-deadline-expires")
@pytest.mark.parametrize("state", ["pending", "skipping", "fail", "cancel", "pass"])
@pytest.mark.parametrize("initial_empty", [False, True])
def test_registered_check_on_docs_pr_still_required(script, capsys, state, initial_empty):
    script.budget = 35
    script.entry()
    if initial_empty:
        script.poll()
        script.proof()
    script.poll(state, at=10 if initial_empty else 0)
    if state in ("pending", "skipping"):
        for at in range(script.at + 10, 35, 10):
            script.poll(at=at)
    elif state == "pass":
        script.end(exemption=False)
        script.merge()
    assert script.invoke() == (0 if state == "pass" else 1)
    assert "CI not required" not in capsys.readouterr().out


@pytest.mark.scenario("reviewed-pr-landing/bounded-docs-only-exemption/grace-does-not-extend-deadline")
@pytest.mark.scenario("gitea-landing/exact-head-combined-ci/ci-uncertainty-and-deadline-refuse")
@pytest.mark.parametrize("budget", [0.5, 9, 10])
def test_short_deadline_never_shortens_grace(script, budget):
    script.budget = budget
    script.entry()
    script.poll()
    script.proof()
    assert script.invoke() == 1
    assert script.clock.sleeps == [budget] and script.clock.now == 100 + budget


@pytest.mark.scenario("reviewed-pr-landing/bounded-docs-only-exemption/grace-does-not-extend-deadline")
@pytest.mark.parametrize("early_wake", [False, True])
def test_grace_measures_elapsed_time_and_proof_precedes_sleep(script, early_wake):
    script.entry()
    script.poll()
    script.proof()
    steps = script.opener.steps if script.gitea else script.runner.steps
    # The final evidence head read runs before any polling sleep.
    step = list(steps.pop())
    index = 2 if script.gitea else 1
    response = step[index]

    def proof_completed():
        assert script.clock.sleeps == []
        if not early_wake:
            script.clock.now += 3
        return response

    step[index] = proof_completed
    steps.append(tuple(step))
    if early_wake:
        def sleep(seconds):
            script.clock.sleeps.append(seconds)
            script.clock.now += 5
        script.clock.sleep = sleep
        script.poll(at=5)
    script.poll(at=10 if early_wake else 13)
    script.inputs()
    script.attest(grace=10 if early_wake else 13)
    script.inputs()
    script.main()
    script.merge(confirmed=False)
    assert script.invoke() == 1  # An exemption still requires confirmed merge.
    assert script.clock.sleeps == ([10, 10] if early_wake else [10])


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/ci-exemption-attestation/attestation-uncertainty-blocks-merge")
@pytest.mark.parametrize("mode", ["unique", "lost", "duplicate", "untrusted", "unreconciled", "actor"])
def test_ci_comment_recovery(script, capsys, mode):
    script.entry()
    script.poll()
    script.proof()
    script.poll(at=10)
    script.inputs()
    script.attest(mode, actor="" if mode == "actor" else "operator")
    if mode in ("unique", "lost"):
        script.inputs()
        script.main()
        script.merge()
    assert script.invoke() == (0 if mode in ("unique", "lost") else 1)
    output = capsys.readouterr()
    assert ("CI not required" in output.out) == (mode in ("unique", "lost"))


@pytest.mark.scenario("reviewed-pr-landing/bounded-docs-only-exemption/successor-and-base-movement-recheck-proof")
@pytest.mark.parametrize("stage", ["proof", "next-poll", "after-attestation"])
def test_unexpected_head_refuses(script, stage, capsys):
    script.entry()
    script.poll()
    script.proof(after_head=FINISHED_HEAD if stage == "proof" else None)
    if stage == "next-poll":
        script.at = 10
        script.pr(head=FINISHED_HEAD)
    elif stage == "after-attestation":
        script.poll(at=10)
        script.inputs()
        script.attest()
        script.status()
        script.pr(head=FINISHED_HEAD)
    assert script.invoke() == 1
    assert "head changed" in capsys.readouterr().err


@pytest.mark.scenario("reviewed-pr-landing/bounded-docs-only-exemption/successor-and-base-movement-recheck-proof")
@pytest.mark.scenario("reviewed-pr-landing/main-movement-restarts-ci/main-moved-while-waiting")
@pytest.mark.scenario("reviewed-pr-landing/exact-update-provenance/exact-merge-accepted")
@pytest.mark.scenario("reviewed-pr-landing/exact-update-provenance/lost-update-response")
@pytest.mark.scenario("gitea-landing/exact-head-combined-ci/main-movement-requires-fresh-ci")
@pytest.mark.parametrize("outcome", ["docs", "mixed", "three-moves"])
def test_base_rounds_require_new_proof(script, outcome, capsys):
    script.budget = 45
    script.entry()
    for round_number in range(1, 4 if outcome == "three-moves" else 3):
        script.poll(at=(round_number - 1) * 10)
        mixed = outcome == "mixed" and round_number == 2
        script.proof(("README.md", "nightshift/land.py") if mixed else DOCS)
        script.poll(at=round_number * 10)
        if mixed:
            script.poll(at=30)
            script.poll(at=40)
            break
        moved = str(round_number) * 40
        script.end(base=moved if round_number == 1 or outcome == "three-moves" else None)
        if round_number == 2 and outcome == "docs":
            script.merge()
        elif round_number < 3:
            script.update(moved, str(round_number + 3) * 40, lost=True)
    assert script.invoke() == (0 if outcome == "docs" else 1)
    output = capsys.readouterr()
    assert output.out.count("CI not required: docs-only") == (3 if outcome == "three-moves" else 2 if outcome == "docs" else 1)
    if outcome == "three-moves":
        assert "refusing a fourth round" in output.err
    if outcome == "mixed":
        assert script.clock.now == 145


@pytest.mark.scenario("reviewed-pr-landing/exact-update-provenance/intervening-push-refused")
def test_exemption_cannot_authorize_unexpected_update(script, capsys):
    script.entry()
    script.poll()
    script.proof()
    script.poll(at=10)
    script.end(base="e" * 40)
    script.update("e" * 40, FINISHED_HEAD, unexpected=True)
    assert script.invoke() == 1
    assert "parent" in capsys.readouterr().err


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/ci-exemption-attestation/human-and-ci-evidence-coexist")
@pytest.mark.scenario("reviewed-pr-landing/bounded-docs-only-exemption/successor-and-base-movement-recheck-proof")
def test_finished_head_gets_separate_ci_evidence(script):
    script.row = {**RUN, "pr": script.url}
    script.body = "- [x] Human check"
    script.pr()
    script.main()
    script.status()
    script.comments()
    script.api("user", {"login": "operator"})
    human = attestation_body([("PR body", ["Human check"])]).replace(URL, script.url)
    script.comments()
    script.api(f"repos/{REPO}/issues/78/comments", attestation(human, user={"login": "operator"}),
               method="POST", body={"body": human})
    script.inputs()
    script.runner.expect(FINISH, {}, timeout=300)
    script.head = FINISHED_HEAD
    script.row = {**script.row, "status": "done", "pause": None, "head": FINISHED_HEAD}
    script.status()
    script.pr()
    script.start()
    script.poll()
    paths = ("README.md", "openspec/archive/finished/tasks.md")
    script.proof(paths)
    script.poll(at=10)
    script.inputs()
    script.attest(paths=paths)
    script.inputs()
    script.main()
    script.merge()
    assert script.invoke(checks_done=True) == 0


@pytest.mark.scenario("reviewed-pr-landing/bounded-docs-only-exemption/grace-does-not-extend-deadline")
@pytest.mark.parametrize("stage", ["proof", "attestation", "comment-write"])
def test_reads_and_writes_consume_original_budget(script, stage):
    script.budget = 15
    script.entry()
    script.poll()
    if stage == "proof":
        script.pr()
        script.api(f"repos/{REPO}/pulls/78", {})
    else:
        script.proof()
        script.poll(at=10)
        script.inputs()
        if stage == "comment-write":
            script.attest()
        else:
            script.api("user", {"login": "operator"})
    steps = script.opener.steps if script.gitea else script.runner.steps
    step = list(steps.pop())
    index = 2 if script.gitea else 1
    response = step[index]

    def expires():
        script.clock.now = 115
        return response

    step[index] = expires
    steps.append(tuple(step))
    assert script.invoke() == 1
    assert script.clock.now == 115


@pytest.mark.scenario("gitea-landing/gitea-exclusion-evidence/gitea-ignored-pr-uses-human-credential")
@pytest.mark.parametrize("actor", ["automation", "outsider"])
def test_gitea_exemption_requires_listed_human(tmp_path, monkeypatch, scripted_runner, opener, actor):
    path = tmp_path / "config.toml"
    path.write_text(forge_table() + f'[[repos]]\nname = "{REPO}"\nforge = "gitea"\n')
    monkeypatch.setenv("LAND_HUMAN", "injected-human-token")
    script = LandingScript(scripted_runner, opener, path, True)
    script.entry()
    script.poll()
    script.proof()
    script.poll(at=10)
    script.inputs()
    script.attest("actor", actor=actor)
    assert script.invoke() == 1


@pytest.mark.scenario("reviewed-pr-landing/docs-only-operator-guidance/operator-understands-the-exemption")
def test_docs_guide_structure_and_workflow_example():
    guide = "docs/landing.md"
    text = (Path(__file__).resolve().parents[1] / guide).read_text()
    for heading in ("Docs-only CI exclusion", "Recovering from a refusal", "Remaining human validation"):
        selected = section(text, heading)
        assert selected.strip()
        links_resolve(guide, selected)
    example, = re.findall(r"^```yaml\n(.*?)^```$", section(text, "Docs-only CI exclusion"), re.M | re.S)
    assert land.all_paths_ignored(DOCS, land.ignored_paths(example))
    checks = land.CheckSource(section(text, "Remaining human validation")).checks
    assert checks and all(not check.checked for check in checks)
