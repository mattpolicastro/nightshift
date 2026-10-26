## Purpose

Explain Codex scratch lifetime, retained attempt evidence and the limits of
automated cleanup validation.

## ADDED Requirements

### Requirement: Session scratch retention guidance

The run-state passage in `docs/specloop/README.md` and one focused entry in
`KNOWN-LIMITATIONS.md` SHALL explain Codex scratch cleanup and its remaining
limits, cite issue #116 or its verified implementation PR and preserve
unrelated guidance. Documentation checks SHALL validate structure, not prose
phrases. The live-host check SHALL remain pending until performed.

#### Scenario: Operator distinguishes scratch from evidence
- **WHEN** an operator reads the run-state and limitations passages
- **THEN** the docs distinguish disposable `<stem>.tmp` from retained transcripts, outputs, seals, receipts, reviews and verify builds, and locate non-fatal cleanup events in session stderr
- **THEN** they explain finalization after the existing runner returns or setup raises, accepted descendant races, historical leftovers and harness/host crash limits without promising confirmed group exit or changing Claude behavior

#### Scenario: Host validation remains explicit
- **WHEN** automated cleanup tests pass and documentation is checked
- **THEN** structural checks use the design's physical-line rules, block ambiguous boundaries or link syntax, and validate links without exact-prose assertions
- **THEN** the docs still identify inspection of one finished real Codex attempt after the first updated build as a pending human check, without claiming fixtures prove live-host cleanup
