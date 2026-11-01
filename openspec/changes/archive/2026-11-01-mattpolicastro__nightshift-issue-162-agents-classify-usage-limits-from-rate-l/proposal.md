## Why

Claude's adapter searches serialized final output for usage-limit words. A failed
agent that merely discusses “quota” or “rate limit” can therefore park its
profile. Claude Code already emits structured `rate_limit_event` telemetry,
parsed by `nightshift/trace.py`, but specloop discards it and waits a flat
30 minutes. It also lacks a run-level stop for reported overage consumption.

## What Changes

- Reuse the trace rate-limit parser in Claude's adapter and carry its normalized
  status, window, reset time and overage flag through `agents.Result`.
- Prefer structured classification. Restrict Claude's usage regex fallback to
  stderr and explicit provider `errors`, excluding final prose and metadata.
- Schedule Claude quota recovery from a usable reset timestamp plus the existing
  60-second grace; retain the 30-minute fallback when the reset is unknown or stale.
- Pause admitted build work on reported overage, even with another usable profile,
  until a human resumes it. Record the structured evidence and chosen deadline.
- Update the provider guide and quota limitation to describe these boundaries.

Implementation scope is `specloop/agents/claude.py`, `specloop/quota.py`, tests,
specs, and passages made false in `README.md`, `KNOWN-LIMITATIONS.md` or
`docs/specloop/README.md`. The user approved minimal additional changes to
`specloop/agents/__init__.py`, `specloop/loop.py` and `nightshift/trace.py` solely
for this structured classification and quota-wait plumbing.

Explicitly out of scope: `_fetch_publish_base` and all other publication/base
logic (#174 owns overlapping work there); Codex classification or quota policy;
provider authentication matching; new polling, preflight or billing controls;
live interruption of an active CLI; supervisor, state-schema or UI changes;
broader drafting/critic/arbiter recovery; unrelated documentation rewrites.

## Capabilities

- **Modified `provider-fallback`:** add structured Claude telemetry, reset-based
  waits and an overage hold. Explicitly modify `Unavailable failure
  classification` to narrow Claude's usage sources and `Compatible all blocked
  pauses` to permit the overage exception. Other requirements remain intact.
- **Extended `specloop-documentation`:** document structured quota recovery and
  pending live validation while retaining existing fallback guidance and
  documentation-integrity requirements.
