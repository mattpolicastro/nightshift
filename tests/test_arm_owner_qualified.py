"""arm attributes owner-qualified run names (since #13) to their front-door issue."""

import pytest

from nightshift.arm import front_door_issue


@pytest.mark.parametrize("change,issue", [
    ("mattpolicastro__nightshift-issue-7-make-revise-rounds-idempotent", 7),
    ("nightshift-issue-46-before-publishing-merge-the-current-base", 46),
])
def test_front_door_issue_accepts_owner_qualified_and_legacy_names(change, issue):
    assert front_door_issue("mattpolicastro/nightshift", change) == issue


@pytest.mark.parametrize("change", [
    "someoneelse__nightshift-issue-7-make-revise-rounds-idempotent",
    "mattpolicastro__other-issue-7-x",
    "basic-playable-mixer",
])
def test_front_door_issue_rejects_other_owners_repos_and_non_front_door_runs(change):
    assert front_door_issue("mattpolicastro/nightshift", change) is None
