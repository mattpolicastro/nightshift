"""Fail on new mypy errors; tolerate the committed baseline.

    uv run python scripts/check_types.py           # check
    uv run python scripts/check_types.py --update  # rewrite mypy-baseline.txt

Errors are compared without line numbers, so unrelated edits don't churn the
baseline, and as a multiset, so a second identical error in one file is new.
Shrink the baseline by fixing errors and running --update.
"""
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "mypy-baseline.txt"
LINE = re.compile(r"^(?P<path>[^:]+):\d+(?::\d+)?: error: (?P<rest>.*)$")


def normalize(output: str) -> list[str]:
    return sorted(f"{m['path']}: {m['rest']}" for m in map(LINE.match, output.splitlines()) if m)


def compare(current: list[str], baseline: list[str]) -> tuple[list[str], list[str]]:
    now, before = Counter(current), Counter(baseline)
    return sorted((now - before).elements()), sorted((before - now).elements())


def main(argv: list[str]) -> int:
    result = subprocess.run([sys.executable, "-m", "mypy", "--no-error-summary"], cwd=ROOT,
                            capture_output=True, text=True)
    if result.returncode not in (0, 1) or (result.returncode == 1 and not result.stdout.strip()):
        print(result.stdout + result.stderr, file=sys.stderr)
        return 2
    current = normalize(result.stdout)
    if "--update" in argv:
        BASELINE.write_text("".join(f"{line}\n" for line in current))
        print(f"baseline: {len(current)} errors")
        return 0
    baseline = BASELINE.read_text().splitlines() if BASELINE.exists() else []
    new, fixed = compare(current, baseline)
    for line in new:
        print(f"new: {line}")
    if fixed:
        print(f"{len(fixed)} baseline error(s) fixed; run with --update to shrink the baseline")
    print(f"mypy: {len(current)} errors, {len(new)} new, baseline {len(baseline)}")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
