"""Other runs' hands-off clauses are not planned edits in the in-flight map."""

import pytest

from specloop.propose import in_flight
from test_specloop_in_flight_paths import local_runs  # noqa: F401 -- shared run fixture


@pytest.mark.scenario("in-flight-file-map/hands-off-mentions/hands-off-paths-are-not-planned-edits")
def test_in_flight_ignores_hands_off_mentions(local_runs):  # noqa: F811
    source, add_run = local_runs
    (source / "README.md").touch()
    add_run("other-change", "Edit `specloop/loop.py`. Do not edit `nightshift/cli.py`, `README.md` or `specloop/cli.py`.")
    assert in_flight(source, "current-change") == {"specloop/loop.py": ["other-change"]}


@pytest.mark.scenario("in-flight-file-map/hands-off-mentions/an-edited-path-still-counts")
def test_in_flight_counts_a_path_also_named_for_editing(local_runs):  # noqa: F811
    source, add_run = local_runs
    add_run("other-change", "Edit `specloop/loop.py` and `specloop/cli.py`. "
                            "Leave `specloop/cli.py` unchanged apart from the flag; do not edit `nightshift/cli.py`.")
    assert in_flight(source, "current-change") == {
        "specloop/loop.py": ["other-change"], "specloop/cli.py": ["other-change"]}
