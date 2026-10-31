## Purpose

Align legacy revision-marker recovery with the canonical final-line marker rule, keeping every legacy marker form.

## ADDED Requirements

## MODIFIED Requirements

### Requirement: Legacy result marker compatibility

Receipts without `delivery_attempt` SHALL retain their original unqualified or
round-qualified revision markers during recovery. Matching SHALL use the
complete literal marker including its closing delimiter, as the comment's
canonical final line, never a round prefix, a fallback alias or a marker
elsewhere in the body. Attempt-qualified delivery SHALL leave earlier markers
and comments intact. Unrelated receipt fields and other delivery namespaces
SHALL retain their existing behavior.

#### Scenario: Old round markers still recognised
- **WHEN** a pending legacy result with an unqualified, event-derived or UUID-round marker is recovered after restart without attempt metadata
- **THEN** the existing comment whose final line is that exact old marker is reconciled without a second create or retroactive attempt field
- **THEN** the saved result, consumption history and unknown receipt fields remain available under existing recovery rules

#### Scenario: Initial markers and build replay remain compatible
- **WHEN** a new logical round is selected or an ordinary keyed build retry retains its saved snapshot
- **THEN** the first result keeps the exact round-only marker and the keyed retry keeps its original round and delivery identity
- **THEN** no attempt suffix changes the `--round-id` argument, keyed result validation or existing successful replay wording
