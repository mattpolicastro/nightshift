"""Publication through real git diffs and the offline Gitea transport."""

import subprocess

import pytest

from fake_gitea import FakeGitea, Reply
from nightshift.forge import http
from specloop import cli, seal
from test_specloop_act import act, admitted, publication_pr
from test_specloop_command_json import BRANCH, CHANGE, git, template
from test_specloop_forge_selection import configured

REQUEST = cli.GiteaForge._request
PATH = "/git/api/v1/repos/example/repo/pulls"


@pytest.fixture
def publication(admitted, configured, monkeypatch, tmp_path, request):
    run, source = admitted
    gitea = getattr(request, "param", "gitea") == "gitea"
    host = "forge.example:8443/git" if gitea else "github.com"
    if gitea:
        run.manifest_path.write_text(run.manifest_path.read_text().replace(
            '"example/repo"', '"forge.example/example/repo"'))
    origin = f"https://{host}/example/repo.git"
    remote = tmp_path / "remote.git"
    git(source, "clone", "-q", "--bare", str(source), str(remote))
    git(source, "remote", "add", "origin", origin)
    fake = FakeGitea("https://forge.example:8443/git")
    fake.repository("example/repo", labels=[])
    monkeypatch.setattr(cli.GiteaForge, "_request", REQUEST)
    monkeypatch.setattr(http, "build_opener", lambda: fake)
    monkeypatch.setattr(http, "sleep", lambda _: None)
    pushes, gh = [], []
    real_process, fetch = subprocess.run, cli.loop._fetch_publish_base

    def process(argv, **kw):
        if "push" in argv:
            assert argv == ["git", "-C", str(run.dir / "repo.git"), "push", "-q", origin,
                            f"refs/heads/{BRANCH}:refs/heads/{BRANCH}"]
            assert kw == {"check": True, "timeout": 300}
            pushes.append(argv)
            argv = [str(remote) if arg == origin else arg for arg in argv]
        return real_process(argv, **kw)

    def fresh(ctx, url, *, timeout):
        assert url == origin and timeout == 20
        return fetch(ctx, str(remote), timeout=timeout)

    def github(*args):
        assert not gitea
        gh.append(args)
        return "[]" if args[0] == "api" else "https://github.com/example/repo/pull/1"

    monkeypatch.setattr(cli.subprocess, "run", process)
    monkeypatch.setattr(cli.loop, "_fetch_publish_base", fresh)
    monkeypatch.setattr(cli, "_gh", github)
    run.pause("ready_to_publish")
    return run, remote, fake, pushes, gh


def pr(number=1, **changes):
    return dict(publication_pr(f"https://forge.example:8443/git/example/repo/pulls/{number}"),
                id=number, number=number, state="open", **changes)


@pytest.mark.scenario("specloop-forge/gitea-draft-publication/new-draft-opens-with-wip-title")
def test_publish_on_gitea_opens_wip_pr(publication, capfd):
    run, remote, fake, pushes, gh = publication
    body = cli._pr_body(cli._ctx(run))
    act(capfd, run, "publish")
    assert [c.method for c in fake.calls] == ["GET", "POST"] and not gh
    assert fake.calls[0].query == {"state": ["open"], "page": ["1"], "limit": ["50"]}
    assert fake.calls[1].body == {"title": f"Draft: {CHANGE}: OpenSpec change via specloop",
                                  "body": body, "head": BRANCH, "base": "main"}
    assert len(pushes) == 1 and git(remote, "rev-parse", BRANCH) == cli._ctx(run).head()
    assert run.state.pr == pr()["html_url"] and run.state.pause.reason == "human_checks"


@pytest.mark.scenario("specloop-forge/gitea-draft-publication/existing-pr-is-reused")
@pytest.mark.parametrize("existing", ["saved", "foreign", "listed", "absent", "ambiguous"])
def test_gitea_publication_matches_exact_pr(publication, capfd, existing):
    run, remote, fake, pushes, _ = publication
    rows = [pr(10), pr(11), pr(12)]
    rows[0]["head"]["repo"]["full_name"] = "fork/repo"
    rows[1]["base"]["ref"] = "other"
    rows[2]["head"]["ref"] = "other"
    if existing in ("listed", "ambiguous"):
        rows += [pr()] + ([pr(2)] if existing == "ambiguous" else [])
    fake.repositories["example/repo"]["pulls"] = list(rows)
    if existing in ("saved", "foreign"):
        run.state.pr = pr()["html_url"].replace("example/repo", "other/repo") if existing == "foreign" else pr()["html_url"]
        run.save()
    before = (run.dir / "state.json").read_bytes()
    act(capfd, run, "publish", code=int(existing in ("ambiguous", "foreign")))
    assert len(pushes) == int(existing != "foreign")
    assert git(remote, "rev-parse", BRANCH) == cli._ctx(run).head()
    assert [c.method for c in fake.calls] == ([] if existing in ("saved", "foreign") else
        ["GET"] * (len(rows) + 1) + (["POST"] if existing == "absent" else []))
    if existing in ("ambiguous", "foreign"):
        assert (run.dir / "state.json").read_bytes() == before
    else:
        assert run.state.pr == (pr(13) if existing == "absent" else pr())["html_url"]
        assert run.state.pause.reason == "human_checks"


@pytest.mark.scenario("specloop-forge/gitea-draft-publication/interrupted-creation-is-reconciled")
@pytest.mark.parametrize("interruption", ["response_lost", "before_save", "foreign_url"])
def test_gitea_publish_recovers_create(publication, monkeypatch, capfd, interruption):
    run, remote, fake, pushes, _ = publication
    before = (run.dir / "state.json").read_bytes()

    def create(call):
        fake.repositories["example/repo"]["pulls"].append(pr())
        return (OSError("lost response") if interruption == "response_lost" else
                Reply({"html_url": "https://foreign/repo/pulls/1"}) if interruption == "foreign_url" else Reply(pr()))

    def crash(*args, **kw):
        raise SystemExit("before save")

    fake.route("POST", PATH, create)
    with monkeypatch.context() as patch:
        if interruption == "before_save":
            patch.setattr(cli.loop.Ctx, "pause", crash)
            with pytest.raises(SystemExit, match="before save"):
                act(capfd, run, "publish")
            capfd.readouterr()
        else:
            act(capfd, run, "publish", code=1)
    assert (run.dir / "state.json").read_bytes() == before
    act(capfd, run, "publish")
    assert [c.method for c in fake.calls] == ["GET", "POST", "GET", "GET"]
    assert run.state.pr == pr()["html_url"] and run.state.pause.reason == "human_checks"
    assert len(pushes) == 2 and git(remote, "rev-parse", BRANCH) == cli._ctx(run).head()


@pytest.mark.scenario("specloop-forge/gitea-draft-publication/interrupted-creation-is-reconciled")
@pytest.mark.parametrize("failure", ["failed", "malformed", "incomplete", "identity"])
def test_gitea_lookup_failure_prevents_create(publication, capfd, failure):
    run, _, fake, _, _ = publication
    fake.repositories["example/repo"]["pulls"] = [pr()]
    if failure == "identity":
        fake.repositories["example/repo"]["pulls"][0]["head"] = {}
    else:
        reply = {"failed": Reply(status=403), "malformed": Reply({}),
                 "incomplete": Reply([], headers={"X-Total-Count": "2"})}[failure]
        fake.route("GET", PATH, reply, page=2)
    before = (run.dir / "state.json").read_bytes()
    act(capfd, run, "publish", code=1)
    assert (run.dir / "state.json").read_bytes() == before
    assert [c.method for c in fake.calls] == ["GET", "GET"]


@pytest.mark.scenario("specloop-forge/workflow-publication-refusal/protected-paths-block-every-forge")
@pytest.mark.parametrize("publication", ["github", "gitea"], indirect=True)
@pytest.mark.parametrize("prefix", [".github/workflows/", ".gitea/workflows/", ".forgejo/"])
@pytest.mark.parametrize("operation", ["add", "modify", "delete", "rename_in", "rename_out"])
def test_publish_refuses_workflow_paths(publication, monkeypatch, capfd, prefix, operation):
    run, _, fake, pushes, gh = publication
    ctx = cli._ctx(run)
    path, safe = prefix + "nested/odd\n\t\x1b'\\é.yml", "ordinary.txt"
    base = seal.commit_files(ctx.repo, ctx.head(),
                             {safe if operation == "rename_in" else path: "old"}, "base")
    env = {"GIT_INDEX_FILE": str(run.dir / "path-index")}
    seal.git(ctx.repo, "read-tree", base, env=env)
    if operation in ("delete", "rename_out", "rename_in", "add"):
        removed = safe if operation == "rename_in" else path
        seal.git(ctx.repo, "update-index", "-z", "--index-info", env=env,
                 input=f"0 {'0' * 40}\t{removed}\0".encode())
    tree = seal.git(ctx.repo, "write-tree", env=env).decode().strip()
    head = seal.git(ctx.repo, "commit-tree", tree, "-p", base, "-m", "remove", env=seal.IDENTITY).decode().strip()
    if operation != "delete":
        head = seal.commit_files(ctx.repo, head, {safe if operation == "rename_out" else path:
                                                "old" if operation.startswith("rename") else "new"}, "head")
    if operation == "add":
        base = git(ctx.repo, "rev-parse", head + "^")
    git(ctx.repo, "update-ref", ctx.ref, head)
    monkeypatch.setattr(cli.loop, "_fetch_publish_base", lambda *a, **kw: base)
    # The guard also applies to a recorded PR.
    run.state.pr = pr()["html_url"] if fake.base_url in cli._remote(ctx) else "https://github.com/example/repo/pull/1"
    run.save()
    before = (run.dir / "state.json").read_bytes()
    assert ascii(path) in act(capfd, run, "publish", code=1)["detail"]
    assert not pushes and not fake.calls and not gh
    assert (run.dir / "state.json").read_bytes() == before


@pytest.mark.scenario("specloop-forge/workflow-publication-refusal/safe-paths-and-failed-inspection")
@pytest.mark.parametrize("publication", ["github", "gitea"], indirect=True)
@pytest.mark.parametrize("inspection", ["safe", "failed", "malformed"])
def test_publish_path_inspection(publication, monkeypatch, capfd, inspection):
    run, _, fake, pushes, gh = publication
    ctx = cli._ctx(run)
    head = seal.commit_files(ctx.repo, ctx.head(), {p: "safe" for p in (
        "ordinary.txt", ".github/workflows-old/x", ".gitea/workflows-old/x", ".forgejo-old/x")}, "safe files")
    git(ctx.repo, "update-ref", ctx.ref, head)
    real_git = cli.git

    def inspect(repo, *args, **kw):
        if args[0] == "diff" and inspection != "safe":
            if inspection == "failed":
                raise RuntimeError("inspection unavailable")
            return b"ordinary.txt\0truncated"
        return real_git(repo, *args, **kw)

    monkeypatch.setattr(cli, "git", inspect)
    before = (run.dir / "state.json").read_bytes()
    act(capfd, run, "publish", code=int(inspection != "safe"))
    if inspection == "safe":
        assert len(pushes) == 1 and run.state.pause.reason == "human_checks"
    else:
        assert not pushes and not fake.calls and not gh
        assert (run.dir / "state.json").read_bytes() == before


@pytest.mark.scenario("specloop-forge/manifest-forge-selection/github-stays-compatible")
@pytest.mark.parametrize("publication", ["github"], indirect=True)
@pytest.mark.parametrize("admitted", ["example/repo", "github.com/example/repo"], indirect=True)
def test_github_publish_argv_unchanged(publication, capfd, monkeypatch):
    run, _, fake, pushes, gh = publication
    monkeypatch.setenv("NIGHTSHIFT_FORGE_CONFIG", "invalid-relative-path")
    ctx = cli._ctx(run)
    body = cli._pr_body(ctx)
    act(capfd, run, "publish")
    host = ("--hostname", "github.com") if ctx.m.github.startswith("github.com/") else ()
    assert gh == [("api", "repos/example/repo/pulls", *host, "--method", "GET", "-f", "state=open",
                   "-f", "base=main", "-f", "per_page=30", "-f", "page=1"),
                  ("pr", "create", "--draft", "-R", ctx.m.github, "--head", BRANCH, "--base", "main",
                   "--title", f"{CHANGE}: OpenSpec change via specloop", "--body", body)]
    assert len(pushes) == 1 and not fake.calls
