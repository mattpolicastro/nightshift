## Why

Landing PRs #81, #84, #87 and #92 on 2026-10-23 exposed two avoidable refusals. Running `.venv/bin/python -m nightshift.land` failed discovery when the `specloop` console script was absent from PATH. On #81, GitHub accepted a branch update, but `gh api` exited nonzero with `unexpected end of JSON input`; landing refused instead of inspecting the resulting head. Both failures were safe but required reruns.

Dependency: `blocked-by: #89`. Its CI-registration handling and requirements are already present in this checkout and must remain intact.

## What Changes

- Invoke discovery and finish through `[sys.executable, "-m", "specloop.cli", ...]`, preserving arguments, runner injection, timeouts and eligibility gates.
- Recognize the specific nonzero parser-error response and a silent empty response as uncertain branch updates. Preserve existing transport-loss reconciliation. JSON error documents, HTTP-status diagnostics, authentication/permission failures and other unrecognized errors refuse immediately.
- Reuse head observation and exact ordered two-parent validation within the existing deadline. Never repeat the uncertain PUT; fresh CI and all final gates still apply.
- Correct only the prerequisites passage of `docs/landing.md`: the interpreter running landing must import `specloop.cli`; its console script need not be on PATH.
- Add regressions in `tests/test_land_followups.py`, including all four requested test names. Preserve existing assertions; only adjust obsolete command expectations in `tests/test_land.py` that this invocation change makes wrong.

Implementation scope is `nightshift/land.py`, new `tests/test_land_followups.py`, the prerequisites passage of `docs/landing.md`, and those strictly necessary command-expectation adjustments in `tests/test_land.py`. Every other file is out of scope. In particular, do not edit `tests/test_land_no_checks.py`, `README.md`, `KNOWN-LIMITATIONS.md`, `docs/specloop/README.md`, configuration, daemon/intake code, provider code, or their tests. No changes to specloop itself, dependency installation, CI policy, deadlines, round limits, merge policy or background invocation are planned.

## Capabilities

- Extended: `reviewed-pr-landing` — separate requirements for discovery invocation, finish invocation, interpreter prerequisites, narrow response classification and bounded reconciliation. All existing requirements remain in force; no requirement is removed or weakened. The existing logical `specloop status` and `specloop finish` operations retain their semantics while their process argv changes. The added response rules make existing ambiguous-update reconciliation precise.
