"""Shared contracts: pytest tests/contract [--gitea-live].

Live selection adds cases alongside the always-offline fake cases. Operators
provide GITEA_CONTRACT_{URL,TOKEN,REPO,BOT_LOGIN,TRUSTED_LOGIN} and
GITEA_CONTRACT_FIXTURE, a JSON object with positive issue/pr numbers, label,
untrusted_login and a full sha. The token must authenticate as BOT_LOGIN.

Provision an open trusted-authored issue carrying label, and a same-repository
open PR carrying label, titled
"WIP: contract" on branch "contract-head", at sha with a successful status and
commit metadata. On the PR, each of trusted, untrusted and bot authors supplies
a conversation, COMMENT review and inline comment with body "Change contract";
inline context is contract.txt:1. Also supply one blank trusted conversation.
The issue timeline must contain trusted apply/remove/reapply events for label.
These fixtures are read-only. Mutations use a new UUID-named issue, closed in
teardown (retaining its comments as evidence), even when an assertion fails.
No server provisioning or live execution is part of the offline suite.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
import re
import socket
from uuid import uuid4

import pytest

from fake_gitea import FakeGitea
from nightshift.forge.gitea import GiteaForge


# Pytest's prepend mode exposes the last non-package conftest under this name.
# Preserve the root tests' legacy import of the already-installed guard roots.
LIVE_STATE_ROOTS = getattr(os, "_nightshift_test_guard_roots")


def pytest_addoption(parser):
    parser.addoption("--gitea-live", action="store_true", help="Add live Gitea contracts")


def pytest_generate_tests(metafunc):
    if "contract" in metafunc.fixturenames:
        modes = ["fake", "live"] if metafunc.config.getoption("--gitea-live") else ["fake"]
        metafunc.parametrize("contract", modes, indirect=True)


@dataclass
class Contract:
    forge: GiteaForge
    repo: str
    evidence: dict
    trusted: str
    bot: str
    fake: FakeGitea | None = None


def live_contract(environ):
    # This must precede credential/fixture validation and transport construction.
    if not environ.get("GITEA_CONTRACT_URL"):
        pytest.skip("GITEA_CONTRACT_URL is absent")
    required = ("TOKEN", "REPO", "BOT_LOGIN", "TRUSTED_LOGIN", "FIXTURE")
    for key in required:
        if not environ.get("GITEA_CONTRACT_" + key, "").strip():
            pytest.fail("Missing GITEA_CONTRACT_" + key, pytrace=False)
    try:
        evidence = json.loads(environ["GITEA_CONTRACT_FIXTURE"])
        assert isinstance(evidence, dict)
        assert all(type(evidence.get(k)) is int and evidence[k] > 0 for k in ("issue", "pr"))
        assert evidence["issue"] != evidence["pr"]
        assert all(isinstance(evidence.get(k), str) and evidence[k].strip()
                   for k in ("label", "untrusted_login", "sha"))
        assert re.fullmatch(r"[0-9a-fA-F]{40}", evidence["sha"])
        repo = environ["GITEA_CONTRACT_REPO"]
        GiteaForge._repo_path(repo)
        trusted, bot = (environ["GITEA_CONTRACT_" + k] for k in ("TRUSTED_LOGIN", "BOT_LOGIN"))
        assert len({trusted.casefold(), bot.casefold(), evidence["untrusted_login"].casefold()}) == 3
    except (ValueError, AssertionError, TypeError):
        pytest.fail("Invalid Gitea contract fixture configuration", pytrace=False)
    forge = GiteaForge(environ["GITEA_CONTRACT_URL"], environ["GITEA_CONTRACT_TOKEN"],
                       trusted_logins=[trusted, bot], bot_login=bot)
    user = forge._detail("/api/v1/user")
    if str(user.get("login", "")).casefold() != bot.casefold():
        pytest.fail("Gitea contract token must authenticate as the bot", pytrace=False)
    return Contract(forge, repo, evidence, trusted, bot)


def fake_contract():
    fake = FakeGitea()
    stamp = "2026-10-20T12:00:00Z"
    evidence = dict(issue=1, pr=2, label="contract-ready", untrusted_login="Mallory", sha="a" * 40)
    label = {"id": 1, "name": evidence["label"]}
    rows = [dict(id=i, user={"login": login}, body="Change contract", created_at=stamp,
                 updated_at=stamp, submitted_at=stamp, state="COMMENT", path="contract.txt", position=1,
                 author_association="OWNER")
            for i, login in enumerate(("Alice", "Mallory", "Robot"), 1)]
    issue = dict(id=1, number=1, state="open", title="Contract issue", body="Fixture",
                 user={"login": "Alice"}, labels=[label], timeline=[
                     dict(id=i, type="label", label=label, user={"login": "Alice"},
                          removed=removed, created_at=stamp)
                     for i, removed in enumerate((False, True, False), 1)])
    pr = dict(id=2, number=2, state="open", merged=False, title="WIP: contract", body="Fixture",
              labels=[label], head={"ref": "contract-head", "sha": evidence["sha"], "repo": {"id": 1}},
              base={"ref": "main", "repo": {"id": 1}}, html_url=fake.base_url + "/owner/repo/pulls/2",
              comments=[*rows, dict(rows[0], id=4, body="")],
              reviews=[dict(row, comments=[dict(row)]) for row in rows])
    fake.repository("owner/repo", labels=[label], issues=[issue], pulls=[pr], page_size=1,
                    commits=[dict(sha=evidence["sha"], parents=[{"sha": "b" * 40}],
                                  commit={role: {"date": stamp} for role in ("author", "committer")})],
                    combined_statuses={evidence["sha"]: dict(sha=evidence["sha"], state="success",
                        total_count=2, statuses=[{"id": i, "state": "success"} for i in (1, 2)])})
    return Contract(GiteaForge(fake.base_url, "offline-token", trusted_logins=["Alice", "Robot"],
                               bot_login="Robot", opener=fake), "owner/repo", evidence, "Alice", "Robot", fake)


def require_evidence(contract):
    """Check provisioning on the wire, before shared adapter assertions run."""
    f, repo, e = contract.forge, contract.repo, contract.evidence
    root = f._repo_path(repo)
    issue = f._detail(root + f'/issues/{e["issue"]}')
    pr = f._detail(root + f'/pulls/{e["pr"]}')
    roles = {login.casefold() for login in (contract.trusted, contract.bot, e["untrusted_login"])}
    comments = [r for p in f._pages(root + f'/issues/{e["pr"]}/comments') for r in p]
    reviews = [r for p in f._pages(root + f'/pulls/{e["pr"]}/reviews') for r in p]
    inline = [r for review in reviews for p in f._pages(
        root + f'/pulls/{e["pr"]}/reviews/{review["id"]}/comments') for r in p]
    try:
        assert issue["state"] == pr["state"] == "open"
        assert issue["user"]["login"].casefold() == contract.trusted.casefold()
        assert all(e["label"] in {r["name"] for r in item["labels"]} for item in (issue, pr))
        assert pr["head"]["sha"].lower() == e["sha"].lower()
        assert pr["head"]["ref"] == "contract-head" and pr["title"] == "WIP: contract"
        assert pr["head"]["repo"]["id"] == pr["base"]["repo"]["id"] and pr["merged"] is False
        for rows in (comments, reviews, inline):
            assert roles <= {r["user"]["login"].casefold() for r in rows if r["body"] == "Change contract"}
        for rows, timestamp in ((comments, "created_at"), (reviews, "submitted_at"), (inline, "created_at")):
            for row in rows:
                if row["user"]["login"].casefold() == contract.trusted.casefold():
                    assert row[timestamp] and row["updated_at"]
        assert all(r["state"] == "COMMENT" for r in reviews)
        assert all(r["path"] == "contract.txt" and r.get("line", r.get("position")) == 1 for r in inline)
        assert any(not r["body"].strip() and r["user"]["login"].casefold() == contract.trusted.casefold()
                   for r in comments)
        events = [r for p in f.label_event_pages(repo, e["issue"]) for r in p]
        assert [r["event"] for r in events if r["label"]["name"] == e["label"]] == [
            "labeled", "unlabeled", "labeled"]
        assert all(r["actor"]["login"].casefold() == contract.trusted.casefold() for r in events)
        assert json.loads(f.checks_for_sha(repo, e["sha"]).stdout)[0]["state"] == "success"
        commit = f.commit(repo, e["sha"])
        assert all(commit["commit"][role]["date"] for role in ("author", "committer"))
    except (AssertionError, KeyError, TypeError, IndexError, AttributeError):
        pytest.fail("Missing required Gitea contract fixture evidence", pytrace=False)


@contextmanager
def owned_issue(contract):
    """Only a confirmed create can enter cleanup; never adopt fixture IDs."""
    f, repo = contract.forge, contract.repo
    root = f._repo_path(repo, "issues")
    title = "nightshift-contract-" + uuid4().hex
    _, _, row = f._request("POST", root, json_body={"title": title, "body": title})
    assert type(row.get("number")) is int and row["number"] > 0
    assert row["number"] not in (contract.evidence["issue"], contract.evidence["pr"])
    assert row.get("title") == row.get("body") == title
    try:
        yield row["number"], title
    finally:
        # Close, rather than delete, the run's issue and retain its comment evidence.
        f._request("PATCH", root + f'/{row["number"]}', json_body={"state": "closed"})


@pytest.fixture
def contract(request, monkeypatch):
    if request.param == "fake":
        def forbidden(*args, **kwargs):
            pytest.fail("Offline contracts must not open sockets")
        monkeypatch.setattr(socket, "socket", forbidden)
        monkeypatch.setattr(socket, "create_connection", forbidden)
        result = fake_contract()
    else:
        result = live_contract(os.environ)
    require_evidence(result)
    return result
