"""A failing item or discovery stage must not starve independent work."""

import json
import subprocess

import pytest

from manifest_fixtures import manifest_text
from merge_readiness_fixtures import ReadinessTransport

from nightshift import daemon, queue, specloop_intake
from nightshift.config import Config, Repo


HEAD = "a" * 40
CHECKS = "Audition the gate and check hosted CI."


class Pipeline:
    def __init__(self, repo):
        self.repo = repo
        self.issues = {}
        self.prs = {}
        self.runs = {}
        self.calls = []
        self.readiness = ReadinessTransport(self._gh, self.calls)
        self.fail = None
        self.reporting_fails = None

    def issue(self, number):
        self.issues[number] = dict(number=number, title="Gate", body="Please draft it.",
                                   state="OPEN", labels=[{"name": "spec:draft"}], comments=[])

    def approved(self, number):
        change = f"{self.repo.name.split('/')[-1]}-issue-{number}-gate"
        self.prs[number] = dict(number=number, state="OPEN", headRefName=f"spec/{change}",
                                headRefOid=HEAD, isCrossRepository=False,
                                url=f"https://github.com/{self.repo.name}/pull/{number}",
                                labels=[{"name": "spec:approved"}], comments=[])
        return change

    def gh(self, args):
        return self.readiness(args)

    def _gh(self, args):
        self.calls.append(args)
        if args[0] == "api":
            if args == ["api", f"repos/{self.repo.name}"]:
                return json.dumps({"owner": {"login": "owner", "type": "User"}})
            parts = args[1].split("/")
            if parts[-1] == "events":
                return json.dumps([[{"id": 1, "event": "labeled", "label": {"name": label},
                                     "actor": {"login": "owner"}}
                                    for label in ("spec:draft", "spec:approved")]])
            if parts[-1] == "comments":
                number = int(parts[-2])
                item = self.issues.get(number) or self.prs[number]
                return json.dumps([item["comments"]])
            assert "PATCH" in args
            for item in [*self.issues.values(), *self.prs.values()]:
                for comment in item["comments"]:
                    if comment["id"] == int(parts[-1]):
                        comment["body"] = args[-1].removeprefix("body=")
                        return "{}"
            pytest.fail(f"Unknown comment: {args}")
        kind, action = args[:2]
        items = self.issues if kind == "issue" else self.prs
        if action == "list":
            # This fixture injects intake discovery failures. Worker-recovery
            # discovery now has its own repository hold and focused tests.
            if (self.fail == f"{kind}-list" and "--label" in args
                    and args[args.index("--label") + 1] != "agent:working"):
                raise queue.GhError("discovery unavailable", transient=True)
            if "--label" in args:
                label = args[args.index("--label") + 1]
                return json.dumps([item for item in items.values() if {"name": label} in item["labels"]])
            branch = args[args.index("--head") + 1]
            return json.dumps([item for item in items.values() if item["headRefName"] == branch])
        if action == "create":
            number = max(self.prs, default=100) + 100
            self.approved(number)
            self.prs[number].update(headRefName=args[args.index("--head") + 1], labels=[])
            return self.prs[number]["url"]
        number = int(args[2].split("/")[-1])
        item = items[number]
        if action == "view":
            if self.fail == "item-read" and number == 1:
                raise queue.GhError("item unavailable", transient=True)
            return json.dumps(item)
        if action == "comment":
            if self.reporting_fails == "comment" and number == 1:
                raise queue.GhError("comment unavailable", transient=True)
            item["comments"].append({"id": number * 1000 + len(item["comments"]), "body": args[-1]})
            return ""
        assert action == "edit"
        if self.reporting_fails == "edit" and number == 1:
            raise queue.GhError("label unavailable", transient=True)
        for i, arg in enumerate(args):
            if arg == "--add-label" and {"name": args[i + 1]} not in item["labels"]:
                item["labels"].append({"name": args[i + 1]})
            if arg == "--remove-label":
                item["labels"] = [label for label in item["labels"] if label["name"] != args[i + 1]]
        return ""

    def command(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == "git":
            rejected = self.fail == "push" and "push" in args and "-issue-1-" in args[-1]
            return subprocess.CompletedProcess(args, int(rejected), HEAD, "[rejected] non-fast-forward")
        command = args[1]
        if command == "status":
            result = {"runs": list(self.runs.values())}
        else:
            change = args[2] if command == "publish" else args[args.index("--change") + 1]
            if self.fail == command and "-issue-1-" in change:
                return subprocess.CompletedProcess(args, 1, json.dumps({
                    "version": 1, "error": f"{command} failed for {change}",
                }), "")
            if command == "propose":
                result = {"outcome": "drafted", "branch": f"spec/{change}", "commit": HEAD}
            elif command == "admit":
                self.runs[change] = {"change": change, "pr": args[args.index("--pr") + 1],
                                     "status": "running", "head": HEAD}
                result = {"outcome": "admitted", "head": HEAD}
            else:
                assert command == "publish"
                run = self.runs[change]
                run["pause"] = {"reason": "human_checks", "detail": CHECKS}
                result = {"outcome": "published", "pr": run["pr"]}
        return subprocess.CompletedProcess(args, 0, json.dumps({"version": 1, **result}), "")

    def ready(self, number, state_dir):
        change = self.approved(number)
        specloop_intake.admit_pending(self.repo, queue.Labels(), state_dir,
                                     runner=self.gh, specloop_runner=self.command, git_runner=self.command)
        self.runs[change].update(status="paused", pause={"reason": "ready_to_publish", "detail": CHECKS})


@pytest.fixture
def pipelines(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon, "PAUSE_FILE", tmp_path / "pause")
    pipelines = []
    for name in ("first", "second"):
        manifest = tmp_path / f"{name}.toml"
        manifest.write_text(manifest_text(tmp_path / name))
        pipelines.append(Pipeline(Repo(f"owner/{name}", engine="specloop", manifest=manifest)))

    def git(args, **kwargs):
        return next(p for p in pipelines if args[2].endswith(p.repo.name.split("/")[-1])).command(args, **kwargs)

    monkeypatch.setattr(specloop_intake, "run_git", git)
    return pipelines


def poll(pipelines, health):
    def gh(args):
        name = args[1].split("/")[2] if args[0] == "api" else args[args.index("--repo") + 1].split("/")[1]
        return next(p for p in pipelines if p.repo.name == f"owner/{name}").gh(args)

    def cli(args, **kwargs):
        if args[1] == "status":
            return subprocess.CompletedProcess(args, 0, json.dumps({
                "version": 1, "runs": [run for p in pipelines for run in p.runs.values()],
            }), "")
        change = args[2] if args[1] == "publish" else args[args.index("--change") + 1]
        pipeline = next(p for p in pipelines if change.startswith((
            p.repo.name.replace("/", "__") + "-", p.repo.name.split("/")[-1] + "-",
        )))
        return pipeline.command(args, **kwargs)

    daemon.poll_specloop(Config(repos=[p.repo for p in pipelines]), health, runner=gh, specloop_runner=cli)


@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
@pytest.mark.scenario("issue-intake/publication/ready-run-published")
@pytest.mark.parametrize("failure", ["push", "propose", "admit", "publish", "issue-list", "pr-list",
                                     "item-read", "corrupt-receipt"])
def test_failures_do_not_starve_later_items_stages_or_repositories(pipelines, tmp_path, failure):
    first, second = pipelines
    for pipeline in pipelines:
        pipeline.ready(30, tmp_path)
    if failure in ("push", "propose", "item-read"):
        first.issue(1)
    elif failure == "admit":
        first.approved(1)
    elif failure in ("publish", "corrupt-receipt"):
        first.ready(1, tmp_path)
        if failure == "corrupt-receipt":
            receipt_path = tmp_path / "admissions/owner__first/1.json"
            saved_receipt = receipt_path.read_text()
            receipt_path.write_text("broken receipt")
    first.fail = failure
    health = daemon.RepoHealth()
    for attempt in range(1, 4):
        # New work must keep progressing on every poll, not just the first.
        for pipeline in pipelines:
            pipeline.issue(10 + attempt)
            pipeline.approved(40 + attempt)
            if attempt > 1:
                # The already admitted PR reaches readiness between polls.
                previous = pipeline.prs[39 + attempt]
                change = previous["headRefName"].removeprefix("spec/")
                if change in pipeline.runs:
                    pipeline.runs[change].update(status="paused", pause={
                        "reason": "ready_to_publish", "detail": CHECKS,
                    })
        poll(pipelines, health)
        assert health.failures == {first.repo.name: attempt}
        for pipeline in pipelines:
            if pipeline is not first or failure != "issue-list":
                issue = pipeline.issues[10 + attempt]
                assert not issue["labels"]
                assert "Spec draft ready" in issue["comments"][0]["body"]
            if pipeline is not first or failure != "pr-list":
                pr = pipeline.prs[40 + attempt]
                change = pr["headRefName"].removeprefix("spec/")
                assert pipeline.runs[change]["pr"] == pr["url"]
                assert "build has started" in pr["comments"][0]["body"]
                if attempt > 1:
                    assert CHECKS in pipeline.prs[39 + attempt]["comments"][-1]["body"]
            published = pipeline.prs[30]
            assert len(published["comments"]) == 2
            assert CHECKS in published["comments"][-1]["body"]
            assert published["url"] in published["comments"][-1]["body"]
        if failure == "push":
            # Re-arm the same rejected draft to exercise repeated delivery.
            assert {"name": "needs-human"} in first.issues[1]["labels"]
            first.issues[1]["labels"].remove({"name": "needs-human"})
    if failure in ("propose", "admit", "publish", "corrupt-receipt"):
        item = first.issues[1] if failure == "propose" else first.prs[1]
        assert {"name": "needs-human"} in item["labels"]
        assert "Human input is needed" in item["comments"][-1]["body"]
    if failure == "push":
        assert len(first.issues[1]["comments"]) == 1  # Upsert failure explanations.
        assert sum(call[:2] == ["specloop", "propose"] and "owner__first-issue-1-gate" in call
                   for call in first.calls) == 1  # Reuse the durable draft.
    # A human resolves the problem and removes the hold; pending work resumes.
    first.fail = None
    if failure == "corrupt-receipt":
        receipt_path.write_text(saved_receipt)
    for item in [*first.issues.values(), *first.prs.values()]:
        item["labels"] = [label for label in item["labels"] if label["name"] != "needs-human"]
    poll(pipelines, health)
    assert not health.failures and not health.item_failures
    if failure == "push":
        assert "Spec draft ready" in first.issues[1]["comments"][-1]["body"]
        assert sum(call[:2] == ["specloop", "propose"] and "owner__first-issue-1-gate" in call
                   for call in first.calls) == 1


@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
@pytest.mark.scenario("issue-intake/publication/ready-run-published")
@pytest.mark.parametrize("operation", ["comment", "edit"])
def test_failed_escalation_does_not_stop_items_or_stages(pipelines, tmp_path, operation):
    first, _ = pipelines
    first.ready(30, tmp_path)
    first.issue(1)
    first.issue(2)
    first.approved(40)
    first.fail = "push"
    first.reporting_fails = operation
    health = daemon.RepoHealth()
    for attempt in range(1, 4):
        poll(pipelines, health)
        assert health.failures == {first.repo.name: attempt}
    assert not first.issues[2]["labels"]
    assert "build has started" in first.prs[40]["comments"][0]["body"]
    assert CHECKS in first.prs[30]["comments"][-1]["body"]
    # A failed comment leaves the item armed until the reason can be delivered.
    assert any(call[:3] == ["issue", "comment", "1"] for call in first.calls)
    assert any(call[:3] == ["issue", "edit", "1"] for call in first.calls) == (operation == "edit")
    first.reporting_fails = None
    poll(pipelines, health)
    assert {"name": "needs-human"} in first.issues[1]["labels"]
    assert len(first.issues[1]["comments"]) == 1
    poll(pipelines, health)
    assert not health.failures and not health.item_failures


@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
@pytest.mark.scenario("issue-intake/publication/ready-run-published")
def test_multiple_item_failures_count_as_one_failed_poll(pipelines, tmp_path, monkeypatch):
    first, _ = pipelines
    first.ready(30, tmp_path)
    for number in (1, 2, 3):
        first.issue(number)
    first.approved(40)
    first.fail = "propose"
    original = first.command

    def fail_second_draft(args, **kwargs):
        if args[:2] == ["specloop", "propose"] and "owner__first-issue-2-gate" in args:
            raise OSError("second draft could not start")
        return original(args, **kwargs)

    monkeypatch.setattr(first, "command", fail_second_draft)
    health = daemon.RepoHealth()
    for attempt in range(1, 4):
        poll(pipelines, health)
        assert health.failures == {first.repo.name: attempt}
        for number in (1, 2):
            assert health.item_failures[(first.repo.name, "draft", number)] == attempt
            assert ({"name": "needs-human"} in first.issues[number]["labels"]) == (attempt == 3)
        assert not first.issues[3]["labels"]
        assert "build has started" in first.prs[40]["comments"][0]["body"]
        assert CHECKS in first.prs[30]["comments"][-1]["body"]


@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
@pytest.mark.scenario("issue-intake/publication/ready-run-published")
def test_pause_during_failure_reporting_stops_all_work(pipelines, tmp_path, monkeypatch):
    first, second = pipelines
    first.ready(30, tmp_path)
    first.issue(1)
    first.issue(2)
    first.approved(40)
    second.issue(10)
    first.fail = "push"
    original = first.command

    def pause_on_push(args, **kwargs):
        result = original(args, **kwargs)
        if args[0] == "git" and "push" in args:
            daemon.PAUSE_FILE.touch()
        return result

    monkeypatch.setattr(first, "command", pause_on_push)
    health = daemon.RepoHealth()
    poll(pipelines, health)
    assert not health.failures
    assert not first.issues[1]["comments"]
    assert not first.issues[2]["comments"]
    assert not first.prs[40]["comments"]
    assert len(first.prs[30]["comments"]) == 1  # Admission only.
    assert not second.calls
