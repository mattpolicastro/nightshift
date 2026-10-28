"""Only collaborators' text reaches an agent prompt (queue.TRUSTED_ASSOCIATIONS).

On a public repository anyone can comment, and an issue's author can edit it
after a collaborator labels it. These tests use the production filter
(`real_trust`), not the conftest allowance for legacy fakes.
"""

import json

import pytest

from test_engine_routing import DraftGh
from test_specloop_intake import Commands, idea_file, poll, repo  # noqa: F401 (fixture)

pytestmark = pytest.mark.real_trust

def comment(assoc, key="authorAssociation", **extra):
    item = {"author": {"login": f"user-{assoc}"}, "user": {"login": f"user-{assoc}"},
            "body": f"from {assoc}", "createdAt": "2026-10-21T00:00:00Z",
            "created_at": "2026-10-21T00:00:00Z", **extra}
    if assoc is not None:
        item[key] = assoc
    return item


class AuthoredGh(DraftGh):
    def __init__(self, author_association, comments):
        super().__init__(("spec:draft",))
        self.author_association = author_association
        self.comments = comments

    def __call__(self, args):
        if args[0] == "api" and args[1] == "repos/owner/sample/issues/7":
            self.calls.append(args)
            return json.dumps({"number": 7, "author_association": self.author_association})
        return super().__call__(args)


def test_an_outsiders_comment_is_left_out_of_the_draft(repo, tmp_path):  # noqa: F811
    gh = AuthoredGh("OWNER", [comment("OWNER", id=1), comment("NONE", id=2)])
    commands = Commands()
    poll(repo, tmp_path, gh, commands)
    propose = commands.calls[0]
    idea = idea_file(propose).read_text(encoding="utf-8")
    assert "from OWNER" in idea and "from NONE" not in idea


@pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR", None])
def test_an_outsiders_issue_is_not_drafted_even_when_labelled(repo, tmp_path, association):  # noqa: F811
    gh = AuthoredGh(association, [comment("OWNER", id=1)])
    commands = Commands()
    receipt, = poll(repo, tmp_path, gh, commands)
    assert commands.calls == []  # no propose was started
    assert receipt["result"]["outcome"] == "rejected"
    assert "outside the repository's collaborators" in gh.comments[-1]["body"]
    assert "needs-human" in gh.labels
