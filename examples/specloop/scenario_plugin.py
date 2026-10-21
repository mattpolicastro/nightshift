"""pytest plugin for specloop coverage: print the scenario IDs of tests that passed.

Trusted: lives with the manifests, outside the paths agents may change. Load it
with `-p scenario_plugin` (its directory on PYTHONPATH). A test declares the
OpenSpec scenario it covers with `@pytest.mark.scenario("cap/req/scenario")`;
a scenario counts only when a test carrying it passed in this run.
"""

import pytest

_passed: set[str] = set()


def pytest_configure(config):
    config.addinivalue_line("markers", "scenario(id): the OpenSpec scenario this test covers")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.passed:
        for mark in item.iter_markers("scenario"):
            _passed.update(str(a) for a in mark.args)


def pytest_terminal_summary(terminalreporter):
    for sid in sorted(_passed):
        terminalreporter.write_line(f"SCENARIO {sid}")
