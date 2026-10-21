# Validation and scope

## Current public snapshot

On 2026-09-08, the sanitized public snapshot passed 358 Python tests with one
skipped, plus seven Paseo plugin tests and plugin typechecking. Both GitHub CI
jobs passed. CI is reproducible from the public repository; old private task
transcripts and repository contents are intentionally not published.

The daemon creates worktrees, invokes an implementation session, checks evidence
of verification, invokes a fresh reviewer, and opens a PR only after the gate
passes. A failed review can trigger a revision with the reviewer's feedback.
Merge is a human action. Recovery decisions are modeled separately from their
filesystem and GitHub side effects; see recovery.py and tests/test_recovery.py.

There are **two model-backed execution phases: implementation and review**.
Task preparation/planning is not a separately dispatched planning-model phase.
Different models can serve the two phases; different providers are optional.

## Historical reviewer experiment — 2026-08-03

The private development record describes a controlled, deliberately seeded
bad diff: a behavior-preserving claim was paired with an assertion weakened
from identity equality to structural equality. The target repository's 824
tests, typecheck, build and formatting checks passed; the independent reviewer
rejected the diff and identified the weakened test as blocking.

Those 824 tests belonged to the target sandbox, not Nightshift's own suite.
The diff was deliberately planted, not an organically discovered agent failure.
The record also reports a successful review of a valid diff. These are examples
of the gate working in both directions, not a measured detection rate or proof
that reviewers catch every defect. The original private fixture and transcripts
are not included, so this historical result is reported evidence, not a public
reproduction package.

The lesson is narrow: passing the same suite whose assertions were weakened
is not enough to establish that behavior was preserved. Independent review adds
a check outside that suite; it does not make verification infallible.

## Local-provider pilot — 2026-09-08

**Result: passed on 2026-09-08.** A fresh GLM-4.7 Flash preflight completed a
Read → Write loop in three turns and passed endpoint/model and credential-
isolation checks. The normal one-task daemon pipeline then completed an
additive, non-mutating numeric-helper task in a private sandbox:

| Stage | Recorded result |
| --- | --- |
| Implementation | Ollama GLM-4.7 Flash, 16 turns, approximately 11m 55s |
| Independent review | Anthropic Opus (`claude-opus-5` in telemetry), 11 turns, approximately 1m 35s; `VERDICT: PASS` |
| Verification | Reviewer reran the complete typecheck, tests, build and formatting chain successfully |
| Revision | None required; first attempt passed |
| Delivery | Harness opened an unmerged PR; GitHub CI passed |
| Attention state | Outcomes ledger recorded `awaiting_merge` with no refresh error |

The review recorded two non-blocking consistency/documentation observations.
Neither required changing the task's scope. Human merge review remains pending.
Total pipeline time was approximately 13m 41s. Phase durations come from the
worker result events, not a throughput estimate. A small auxiliary Haiku call
also appears in review telemetry; Opus produced the review verdict.

The pilot ran the published worker code with a private configuration and a
dedicated admission label. No unrelated ready issues were consumed. Logs,
transcripts, endpoint addresses and sandbox repository details remain private;
this is a sanitized result summary, not a public reproduction archive.

This establishes one successful local-implementation/cloud-review integration.
It does not establish local reviewer quality, OpenAI proxy compatibility,
automatic routing, native Codex support, or reliability across larger tasks.
