"""Minimal saved runs with real Git boundaries, reusable by readiness tests."""

import json
import subprocess

import pytest

from specloop import state
from test_specloop_approval import ApprovalGh


class LocalRun:
    def __init__(self, root):
        self.run = state.Run.for_change("c")
        self.run.dir.mkdir(parents=True)
        self.work = root / "checkout"
        self.work.mkdir()
        self.git("init", "-q", "--separate-git-dir", str(self.run.dir / "repo.git"))
        self.row = {"change": "c", "pr": "https://github.com/owner/repo/pull/1"}
        self.tasks_path = "openspec/changes/c/tasks.md"
        self.document = "## 1. Build\n- [x] 1.1 Implement\n\n## 2. Revision\n- [x] 2.1 Revise\n"
        self.base = self.commit({self.tasks_path: self.document, "source.py": "original\n"})
        self.middle = self.commit({"tests/one.py": "first\n"})
        self.commit({"tests/two.py": "second\n"})
        self.run.state.pr = self.row["pr"]
        self.run.state.section_base = {"1": self.base, "2": self.middle}
        self.run.state.evidenced = [1, 2]
        self.run.save()
        self.events = [dict(event="evidence", section=n, passed=True, score=0.9,
                            timeouts=0, uncovered=[]) for n in (1, 2)]
        self.write_events()
        self.run.manifest_path.write_text('''run = {repo = ".", change = "c", branch = "spec/c"}
roles = {implement = "p", review = "p"}
profiles = {p = {adapter = "codex", model = "m"}}
paths = {allowed = ["tests/**"]}
verify = [{id = "ok", run = ["true"]}]''')

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.work), "-c", "user.name=Test", "-c",
                               "user.email=test@example.invalid", *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, files):
        for name, content in files.items():
            path = self.work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.unlink() if content is None else path.write_text(content)
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.row["head"] = self.git("rev-parse", "HEAD")
        return self.row["head"]

    def write_events(self):
        (self.run.dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in self.events))


@pytest.fixture
def local_run(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path)
    return LocalRun(tmp_path)


class PollingGitHub:
    """Strict logical-operation log with separate publication/readiness comments."""

    def __init__(self, row):
        self.pr = dict(state="OPEN", isCrossRepository=False, url=row["pr"],
                       headRefName=f"spec/{row['change']}", headRefOid=row["head"],
                       body="## Checks a human must make\n(none)")
        self.labels = {"unrelated", "merge:blocked"}
        self.comments = []
        self.operations = []
        self.checks = [{"name": "CI", "bucket": "pass", "state": "COMPLETED"}]
        self.fail = None
        self.view_fields = "state,labels,headRefName,headRefOid,isCrossRepository,url,body"

    def __call__(self, args):
        if args[:2] == ["pr", "view"]:
            op = "view"
            assert args == ["pr", "view", "1", "--repo", "owner/repo", "--json", self.view_fields]
        elif args[:2] == ["pr", "checks"]:
            op = "checks"
            assert args == ["pr", "checks", "1", "--repo", "owner/repo", "--json", "name,bucket,state"]
        elif args[:2] == ["pr", "edit"]:
            assert args[2:5] == ["1", "--repo", "owner/repo"] and len(args) == 7
            assert args[5] in ("--remove-label", "--add-label")
            op = f"{args[5][2:]}:{args[6]}"
        elif args[:2] == ["pr", "comment"]:
            op = "post"
            assert args[2:6] == ["1", "--repo", "owner/repo", "--body"] and len(args) == 7
        elif args[:2] == ["api", "repos/owner/repo/issues/1/comments"]:
            op = "lookup"
            assert args[2:] == ["--paginate", "--slurp"]
        else:
            op = "patch"
            assert args[0] == "api" and args[1].startswith("repos/owner/repo/issues/comments/")
            assert args[2:5] == ["--method", "PATCH", "-f"] and len(args) == 6
        self.operations.append(op)
        if self.fail == op:
            raise OSError(f"failed {op}")
        if op == "view":
            return json.dumps(dict(self.pr, labels=[{"name": n} for n in self.labels]))
        if op == "checks":
            return json.dumps(self.checks)
        if op == "lookup":
            return json.dumps([self.comments])
        if op.startswith("remove-label:"):
            self.labels.discard(args[-1])
        elif op.startswith("add-label:"):
            self.labels.add(args[-1])
        elif op == "post":
            self.comments.append({"id": len(self.comments) + 1, "body": args[-1]})
        elif op == "patch":
            comment = next(c for c in self.comments if str(c["id"]) == args[1].split("/")[-1])
            comment["body"] = args[-1].removeprefix("body=")
        return ""

    @property
    def publication_comments(self):
        return [c for c in self.comments if "<!-- nightshift:publish:" in c["body"]]

    @property
    def readiness_comments(self):
        return [c for c in self.comments if "<!-- nightshift:approval:c:merge-readiness -->" in c["body"]]


class ReadinessTransport:
    """Extend older publication fakes without mixing their effect collections.

    The original call log remains unfiltered; API reads expose both collections.
    """

    def __init__(self, runner, calls):
        self.runner, self.calls = runner, calls
        self.labels, self.comments = {}, {}

    def __call__(self, args):
        if args[0] == "api":
            parts = args[1].split("/")
            if "PATCH" in args:
                for comments in self.comments.values():
                    for comment in comments:
                        if str(comment["id"]) == parts[-1]:
                            self.calls.append(args)
                            comment["body"] = args[-1].removeprefix("body=")
                            return "{}"
            result = self.runner(args)
            if parts[-1] == "comments" and parts[-3] == "issues":
                key = ("/".join(parts[1:3]), parts[-2])
                return json.dumps([*json.loads(result), self.comments.get(key, [])])
            return result
        if args[0] != "pr" or args[1] in ("list", "create"):
            return self.runner(args)
        repo = args[args.index("--repo") + 1]
        key = (repo, args[2])
        labels = self.labels.setdefault(key, set())
        if args[1] == "checks":
            self.calls.append(args)
            assert args[-2:] == ["--json", "name,bucket,state"]
            return '[{"name":"CI","bucket":"pass","state":"COMPLETED"}]'
        if args[1] == "edit" and args[-1] in ("merge:ready", "merge:blocked"):
            self.calls.append(args)
            assert args[-2] in ("--add-label", "--remove-label")
            (labels.add if args[-2] == "--add-label" else labels.discard)(args[-1])
            return ""
        if args[1] == "comment" and ":merge-readiness -->" in args[-1]:
            self.calls.append(args)
            comments = self.comments.setdefault(key, [])
            comments.append({"id": 1000000 + sum(map(len, self.comments.values())), "body": args[-1]})
            return ""
        result = self.runner(args)
        if args[1] == "view":
            pr = json.loads(result)
            pr["labels"] += [{"name": label} for label in labels]
            pr.setdefault("body", "## Checks a human must make\n(none)")
            return json.dumps(pr)
        return result


class PublicationOnlyGh(ApprovalGh):
    """Keep legacy admission/publication assertions on their original collections."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.readiness = ReadinessTransport(super().__call__, self.calls)

    def __call__(self, args):
        return self.readiness(args)
