"""Must fail: proves the verify step's test runner can fail at all."""


def test_negative_control():
    assert False, "deliberate failure"
