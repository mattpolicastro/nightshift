# verify-output Specification

## Purpose
Make bounded verification output traceable to its full retained log and preserve
that output when a failed verification supplies feedback for another attempt.

## Requirements

### Requirement: Truncated tails identify retained logs

For logs over 2,000 bytes, the verifier SHALL prefix the final 2,000 bytes,
decoded with UTF-8 replacement, with `[showing the last N of M bytes; full log: <path>]`
and one newline. `N` SHALL be 2000 and `M` the full raw byte count. The path SHALL
be relative to the owning run directory supplied by the loop, defaulting to the
output directory for direct calls. The verifier SHALL preserve untruncated
output, other receipt fields, and full logs.

#### Scenario: Long output names the full log
- **WHEN** a prepare or check step emits more than 2,000 bytes during task, base-sync, coverage, or acceptance verification
- **THEN** its receipt tail and serialized tail contain the exact marker with `N = 2000`, the full byte count, and the actual log path relative to the owning run directory, followed by one newline and the decoded final 2,000 bytes, with the marker additional to the payload limit
- **THEN** passing and failing receipts use the same formatting rule, and the full log retains all original bytes after cleanup

#### Scenario: Short output stays unchanged
- **WHEN** a step emits at most 2,000 bytes, including empty output and exactly 2,000 bytes
- **THEN** its receipt tail and serialized tail equal the original bytes decoded with UTF-8 replacement, without a marker, added newline, or trimmed whitespace

#### Scenario: Byte boundaries preserve decoding
- **WHEN** a log is 2,001 bytes long or its final 2,000-byte boundary splits a multibyte UTF-8 character or includes invalid UTF-8
- **THEN** truncation is determined by raw byte length, the marker reports raw byte counts, and the payload equals the final 2,000 bytes decoded with replacement without raising a decoding error

#### Scenario: Direct invocation uses its output directory
- **WHEN** a direct `run_steps()` or `run_extracted()` call omits `run_dir` and produces a truncated step log
- **THEN** the marker names `<step>.log` relative to that call's output directory, and the receipt schema and existing positional call interface remain compatible

### Requirement: Retry feedback preserves receipt output

Verification summaries SHALL include the first failing receipt's complete tail
verbatim, without another truncation or marker transformation. The feedback
file referenced by the next implementer prompt SHALL preserve that same tail.
Existing per-step summary status lines and first-failure selection SHALL remain
unchanged. The system SHALL treat receipt tails as opaque text.

#### Scenario: Retry retains marker and payload
- **WHEN** verification fails with a marked receipt tail and the loop supplies feedback to the next implementation attempt
- **THEN** the summary and referenced feedback file contain the marker exactly once followed by the complete receipt payload, with its byte counts and run-relative log path intact

#### Scenario: Unmarked tails pass through
- **WHEN** a failing receipt has an unmarked tail, including empty output or an untruncated tail between 1,501 and 2,000 characters
- **THEN** the summary and referenced feedback file include that tail verbatim without adding a marker or applying the former 1,500-character slice
