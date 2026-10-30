"""Provision a fresh, disposable Gitea service; write live inputs to GITHUB_ENV.

Only the initial administrator uses docker exec. All fixture data, including
commits, users and tokens, is created through the HTTP API. Never target an
existing instance: this intentionally fails if its fixture names already exist.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


OWNER = "contract-owner"
BOT = "contract-bot"
OUTSIDER = "contract-outsider"
LABELS = (
    "agent:ready", "agent:working", "agent:done", "needs-human", "agent:revise",
    "spec:draft", "spec:review", "spec:approved", "merge:ready", "merge:blocked",
)


def mask(value):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{value}", flush=True)
    return value


class API:
    def __init__(self, url, authorization=""):
        self.url = url.rstrip("/")
        self.authorization = authorization

    def request(self, method, path, data=None):
        headers = {"Content-Type": "application/json"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        request = Request(self.url + path, method=method, headers=headers,
                          data=None if data is None else json.dumps(data).encode())
        try:
            with urlopen(request, timeout=15) as response:
                body = response.read()
                return json.loads(body) if body else None
        except HTTPError as exc:
            # Response bodies can include credentials; report only the operation.
            raise RuntimeError(f"{method} {path}: HTTP {exc.code}") from None

    def token(self, login, password, scopes):
        basic = base64.b64encode(f"{login}:{password}".encode()).decode()
        row = API(self.url, "Basic " + basic).request(
            "POST", f"/api/v1/users/{login}/tokens",
            {"name": "contract-suite", "scopes": scopes})
        token = mask(row["sha1"])
        return API(self.url, "token " + token), token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:3000")
    args = parser.parse_args()
    env_file = Path(os.environ["GITHUB_ENV"])
    api = API(args.url)
    for attempt in range(60):
        try:
            api.request("GET", "/api/healthz")
            break
        except (URLError, RuntimeError):
            if attempt == 59:
                raise RuntimeError("Gitea did not become healthy within 120 seconds") from None
            time.sleep(2)

    password = mask(secrets.token_urlsafe(32) + "aA1!")
    result = subprocess.run([
        "docker", "exec", "--user", "git", args.container, "gitea",
        "--config", "/data/gitea/conf/app.ini", "admin", "user", "create",
        "--username", OWNER, "--email", OWNER + "@example.invalid",
        "--password", password, "--admin", "--must-change-password=false",
    ], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError("Could not bootstrap the disposable Gitea administrator")
    owner, _ = api.token(OWNER, password, ["all"])
    clients = [owner]
    bot_token = ""
    for login in (BOT, OUTSIDER):
        password = mask(secrets.token_urlsafe(32) + "aA1!")
        owner.request("POST", "/api/v1/admin/users", {
            "username": login, "email": login + "@example.invalid", "password": password,
            "must_change_password": False, "restricted": login == BOT,
            "send_notify": False,
        })
        client, token = api.token(login, password, ["read:user", "write:repository", "write:issue"])
        clients.append(client)
        if login == BOT:
            bot_token = token
            user = client.request("GET", "/api/v1/user")
            if user["is_admin"] or not user["restricted"]:
                raise RuntimeError("Bot must be restricted and non-admin")

    repo = OWNER + "/contract"
    root = "/api/v1/repos/" + repo
    owner.request("POST", "/api/v1/user/repos", {
        "name": "contract", "auto_init": True, "default_branch": "main", "private": False,
    })
    owner.request("PUT", root + "/collaborators/" + BOT, {"permission": "write"})
    labels = {}
    for name in LABELS:
        labels[name] = owner.request("POST", root + "/labels", {"name": name, "color": "4287f5"})["id"]
    label = "agent:ready"
    label_id = labels[label]
    issue = owner.request("POST", root + "/issues", {"title": "Contract issue", "body": "Fixture"})["number"]
    issue_labels = root + f"/issues/{issue}/labels"
    owner.request("POST", issue_labels, {"labels": [label_id]})
    owner.request("DELETE", issue_labels + f"/{label_id}")
    owner.request("POST", issue_labels, {"labels": [label_id]})
    commit = owner.request("POST", root + "/contents/contract.txt", {
        "branch": "main", "new_branch": "contract-head", "message": "Contract fixture",
        "content": base64.b64encode(b"Change contract\n").decode(),
    })
    sha = commit["commit"]["sha"]
    pr = owner.request("POST", root + "/pulls", {
        "title": "WIP: contract", "body": "Fixture", "head": "contract-head", "base": "main",
    })["number"]
    owner.request("POST", root + f"/issues/{pr}/labels", {"labels": [label_id]})
    owner.request("POST", root + f"/statuses/{sha}", {"state": "success", "context": "contract"})
    for client in clients:
        client.request("POST", root + f"/issues/{pr}/comments", {"body": "Change contract"})
        client.request("POST", root + f"/pulls/{pr}/reviews", {
            "body": "Change contract", "event": "COMMENT", "commit_id": sha,
            "comments": [{"path": "contract.txt", "new_position": 1, "body": "Change contract"}],
        })
    # Gitea accepts whitespace while the contract deliberately checks body.strip().
    owner.request("POST", root + f"/issues/{pr}/comments", {"body": " "})
    fixture = dict(issue=issue, pr=pr, label=label, untrusted_login=OUTSIDER, sha=sha)
    values = dict(URL=args.url.rstrip("/"), TOKEN=bot_token, REPO=repo, BOT_LOGIN=BOT,
                  TRUSTED_LOGIN=OWNER, FIXTURE=json.dumps(fixture))
    with env_file.open("a") as output:
        for name, value in values.items():
            if "\n" in value or "\r" in value:
                raise ValueError("GITHUB_ENV values must be single-line")
            output.write(f"GITEA_CONTRACT_{name}={value}\n")
    print("Provisioned live Gitea contract fixture")


if __name__ == "__main__":
    main()
