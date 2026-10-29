"""Gitea critic pagination through the real adapter and HTTP transport."""

import socket

import pytest

from fake_gitea import FakeGitea, Reply
from nightshift.forge import http
from nightshift.forge.gitea import GiteaForge
from specloop import critic


SHORT = "spec-critic/gitea-context-pagination/short-pages-retain-renamed-paths"
GAPS = "spec-critic/gitea-context-pagination/pagination-evidence-cannot-hide-gaps"
ROOT = "/gitea/api/v1/repos/org/repo/"
PRIVATE = "private-body-patch-or-error"


def pr(number, ref="other", repo="org/repo", **extra):
    return {"number": number, "html_url": f"https://forge.example/gitea/org/repo/pulls/{number}",
            "head": {"ref": ref, "repo": {"full_name": repo}}, "body": PRIVATE, **extra}


def label(number):
    return f"#{number} {pr(number)['html_url']}"


@pytest.fixture
def collect(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Collector must use confined requests through the in-process opener")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(GiteaForge, "_pages", forbidden)
    monkeypatch.setattr(http, "sleep", lambda _: None)

    def run(pages):
        fake = FakeGitea()
        for (endpoint, page), result in pages.items():
            reply = result if isinstance(result, (Reply, Exception)) else Reply(result)
            fake.route("GET", ROOT + endpoint, reply, reply, reply, page=page)
        forge = GiteaForge(fake.base_url, "test-token", trusted_logins=[], bot_login="Robot", opener=fake)
        files, notes = critic._gitea_open_pr_files(forge, "org/repo", "proposal")
        for call in fake.calls:
            assert call.method == "GET" and call.body is None
            assert call.path.startswith(ROOT)
            assert call.query == {"page": call.query["page"], "limit": ["50"],
                                  **({"state": ["open"]} if call.path == ROOT + "pulls" else {})}
        assert all(response.closed for response in fake.responses)
        assert PRIVATE not in repr((files, notes))
        assert list(files) == sorted(files)
        return files, notes, [(c.path.removeprefix(ROOT), int(c.query["page"][0])) for c in fake.calls]

    return run


@pytest.mark.scenario(SHORT)
@pytest.mark.parametrize("metadata", [False, True])
@pytest.mark.parametrize("head_repo", [None, "missing", {}, {"full_name": 7}])
def test_critic_reads_gitea_pr_files(collect, head_repo, metadata):
    head = {"ref": "proposal", **({} if head_repo == "missing" else {"repo": head_repo})}
    pages = {("pulls", 1): [pr(1, "proposal", "ORG/REPO")],
             ("pulls", 2): [pr(2, "proposal", "fork/repo", title="WIP: change", base={"ref": "release"})],
             ("pulls", 3): [pr(3, head=head)], ("pulls", 4): [],
             ("pulls/2/files", 1): [{"filename": "src/new.py", "previous_filename": "old.py", "patch": PRIVATE}],
             ("pulls/2/files", 2): [{"filename": "README.md"}], ("pulls/2/files", 3): [],
             ("pulls/3/files", 1): [{"filename": "README.md"}], ("pulls/3/files", 2): []}
    if metadata:
        pages = {key: Reply(rows, headers={
            "X-Total-Count": str(sum(len(r) for (endpoint, _), r in pages.items() if endpoint == key[0])),
            "X-Next-Page": str(key[1] + 1) if pages.get((key[0], key[1] + 1)) else "0",
            **({"Link": '<https://foreign/ignored?page=999>; rel="next"'} if key == ("pulls", 1) else {}),
        }) for key, rows in pages.items()}
    files, notes, calls = collect(pages)
    assert files == {"README.md": [label(2), label(3)], "old.py": [label(2)], "src/new.py": [label(2)]}
    assert len(notes) == (0 if head_repo is None else 1)
    if notes:
        assert "#3" in notes[0] and "malformed head repository" in notes[0]
    assert set(calls) == set(pages) and len(calls) == len(pages)


@pytest.mark.scenario(SHORT)
def test_no_github_file_ceiling(collect):
    pages = {("pulls", 1): [pr(1)], ("pulls", 2): [], ("pulls/1/files", 62): []}
    pages.update({("pulls/1/files", page): [{"filename": f"src/{page}-{i}.py"} for i in range(50)]
                  for page in range(1, 62)})
    files, notes, calls = collect(pages)
    assert len(files) == 3050 and all(labels == [label(1)] for labels in files.values())
    assert notes == [] and len(calls) == len(pages)


@pytest.mark.scenario(GAPS)
@pytest.mark.parametrize("endpoint", ["pulls", "pulls/1/files"])
@pytest.mark.parametrize("fault", ["repeat", "conflict", "http", "connection", "json", "shape",
                                  "total-change", "total-gap", "total-small", "bad-total", "huge-total",
                                  "bad-next", "next-empty", "link-empty", "end-next", "early-end"])
def test_pagination_gaps_keep_evidence(collect, endpoint, fault):
    pages = {("pulls", 1): [pr(1), pr(2)], ("pulls", 2): [],
             ("pulls/1/files", 1): [{"filename": "kept.py"}], ("pulls/1/files", 2): [],
             ("pulls/2/files", 1): [{"filename": "later.py"}], ("pulls/2/files", 2): []}
    first = pages[endpoint, 1]
    second = Reply([])
    if fault == "repeat":
        second = Reply(first)
    elif fault == "conflict":
        second = Reply([pr(1, ref="changed")] if endpoint == "pulls" else
                       [{"filename": "kept.py", "previous_filename": "untrusted.py"}])
    elif fault == "http":
        second = Reply(status=403, raw=PRIVATE.encode())
    elif fault == "connection":
        second = OSError(PRIVATE)
    elif fault in ("json", "shape"):
        second = Reply(raw=PRIVATE.encode()) if fault == "json" else Reply({"message": PRIVATE})
    elif fault.startswith("total-"):
        count = len(first)
        pages[endpoint, 1] = Reply(first, headers={"X-Total-Count": str(count)})
        second = Reply([], headers={"X-Total-Count": str(count + 1)})
        if fault == "total-gap":
            pages[endpoint, 1] = Reply(first, headers={"X-Total-Count": str(count + 1)})
        if fault == "total-small":
            pages[endpoint, 1] = Reply(first, headers={"X-Total-Count": "0"})
    elif fault in ("bad-total", "huge-total"):
        second = Reply([], headers={"X-Total-Count": "invalid" if fault == "bad-total" else "9" * 5000})
    elif fault in ("bad-next", "next-empty"):
        second = Reply([], headers={"X-Next-Page": "99" if fault == "bad-next" else "3"})
    elif fault in ("link-empty", "end-next"):
        second = Reply([], headers=[("Link", '<https://evil/prev>; rel="prev"'),
                                     ("Link", '<https://evil/next>; rel="alternate next"'),
                                     *([("X-Next-Page", "0")] if fault == "end-next" else [])])
    elif fault == "early-end":
        pages[endpoint, 1] = Reply(first, headers={"X-Next-Page": "0"})
        second = Reply([pr(3)] if endpoint == "pulls" else [{"filename": "extra.py"}])
        pages["pulls/3/files", 1] = [{"filename": "extra.py"}]
        pages["pulls/3/files", 2] = []
    pages[endpoint, 2] = second
    files, notes, calls = collect(pages)
    assert files["kept.py"] == [label(1)] and files["later.py"] == [label(2)]
    assert "untrusted.py" not in files
    if fault == "early-end":
        assert files["extra.py"] == [label(3 if endpoint == "pulls" else 1)]
    assert len(notes) == 1 and "incomplete" in notes[0]
    assert ("Open PR list" if endpoint == "pulls" else "Open PR #1 files") in notes[0]
    assert (endpoint, 3) not in calls


@pytest.mark.scenario(GAPS)
def test_overlap_uses_relevant_fields_and_keeps_partly_malformed_paths(collect):
    pages = {("pulls", 1): [pr(1)], ("pulls", 2): [pr(1, id=99, body="changed"), None, pr(True), pr(2)],
             ("pulls", 3): [], ("pulls/2/files", 1): [],
             ("pulls/1/files", 1): [{"filename": "a"}],
             ("pulls/1/files", 2): [{"filename": "a", "id": 7, "patch": PRIVATE}, {"filename": "b"}],
             ("pulls/1/files", 3): [None, {"filename": "c", "previous_filename": 1},
                                     {"filename": None, "previous_filename": "old"}],
             ("pulls/1/files", 4): []}
    files, notes, calls = collect(pages)
    assert files == {path: [label(1)] for path in ("a", "b", "c", "old")}
    assert len(notes) == 2 and all("malformed" in note for note in notes)
    assert len(calls) == len(pages)


@pytest.mark.scenario(GAPS)
@pytest.mark.parametrize("endpoint", ["pulls", "pulls/1/files"])
def test_pagination_exhaustion_retains_evidence(collect, endpoint):
    pages = {("pulls", 1): [pr(1)], ("pulls", 2): [],
             ("pulls/1/files", 1): [{"filename": "kept.py"}], ("pulls/1/files", 2): []}
    pages.update({(endpoint, page): [pr(page, "proposal")] if endpoint == "pulls" else
                  [{"filename": f"file-{page}"}] for page in range(2, 1001)})
    files, notes, calls = collect(pages)
    assert files["kept.py"] == [label(1)]
    assert len(files) == (1 if endpoint == "pulls" else 1000)
    assert len(notes) == 1 and "1000 pages" in notes[0]
    assert (endpoint, 1000) in calls and (endpoint, 1001) not in calls
