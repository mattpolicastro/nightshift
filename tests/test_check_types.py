import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "check_types", Path(__file__).resolve().parent.parent / "scripts" / "check_types.py")
check_types = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_types)


def test_normalize_drops_line_numbers_and_non_errors():
    out = ("a.py:12: error: Bad thing  [arg-type]\n"
           "a.py:3:7: error: Other  [index]\n"
           "a.py:4: note: See docs\n")
    assert check_types.normalize(out) == ["a.py: Bad thing  [arg-type]", "a.py: Other  [index]"]


def test_new_errors_fail_and_fixed_errors_are_reported():
    base = ["a.py: X  [c]", "a.py: X  [c]", "b.py: Y  [d]"]
    new, fixed = check_types.compare(["a.py: X  [c]", "c.py: Z  [e]"], base)
    assert new == ["c.py: Z  [e]"]
    assert fixed == ["a.py: X  [c]", "b.py: Y  [d]"]


def test_a_second_identical_error_counts_as_new():
    new, _ = check_types.compare(["a.py: X  [c]", "a.py: X  [c]"], ["a.py: X  [c]"])
    assert new == ["a.py: X  [c]"]
