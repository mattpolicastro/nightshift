## Why

The first real landing of PR #83 refused twice immediately after `specloop finish` pushed its archive commit. GitHub had not registered checks for the new head, so `gh pr checks` returned exit 1, empty stdout, and `no checks reported` on stderr. Landing classified this transient state as malformed JSON. It refused safely, but required unnecessary operator retries during normal post-push CI registration.

Dependency: `blocked-by: #78`. Its landing implementation and `reviewed-pr-landing` requirements are present in this checkout.

## What Changes

- Recognize only exit 1 with exactly empty stdout and stderr containing the literal `no checks reported` as pending CI registration.
- Poll within the existing shared CI deadline, preserving the head reads around every check query and every existing merge gate.
- If this state persists until the deadline, refuse with `no CI checks were reported for head <sha> within <timeout>s`, retaining operator recovery guidance.
- Preserve all other malformed-response refusals and existing test assertions.
- Implement in `nightshift/land.py`, centered on `ci_is_green` and `wait_for_ci`, with only minimal timeout-argument plumbing in `continue_landing` if needed. This file overlaps issue #72, but these CI functions must change to fix the bug; provider-routing work is excluded.
- Add the two required regression tests and boundary cases in a new `tests/test_land_no_checks.py`. Do not edit `tests/test_land.py`, which overlaps issue #72. This is the sole adjustment to the idea's original test-file scope, required by the work-in-flight constraint.

Out of scope: changes to any other implementation, test, configuration, or documentation file; provider fallback; finish/push behavior; CI policy, polling interval or timeout defaults; base updates; human checks; automatic merging; GitHub workflows. In particular, leave `README.md`, `KNOWN-LIMITATIONS.md`, `docs/landing.md`, `pyproject.toml`, and `tests/test_land.py` untouched.

## Capabilities

- Modified: `reviewed-pr-landing` — clarify bounded CI waiting with a narrowly recognized pre-registration response and a specific deadline diagnostic. All other existing capability requirements remain in force. The delta modifies `Bounded CI wait` because this response is pending rather than invalid, and adds the precise response contract.
