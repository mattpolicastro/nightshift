## Purpose

Explain session scratch lifetime alongside retained attempt evidence and the
limits of automated cleanup validation.

## ADDED Requirements

### Requirement: Session scratch retention guidance

`docs/specloop/README.md` and `KNOWN-LIMITATIONS.md` SHALL document Codex session
scratch lifetime, non-fatal failure events, retained evidence and cleanup
limits. They SHALL cite issue #116 or its verified implementation PR, preserve
unrelated guidance and existing assertions, and identify the real-host check
as pending until performed.

#### Scenario: Operator distinguishes scratch from evidence
- **WHEN** an operator reads the run-state and storage-limit guidance
- **THEN** the docs identify disposable `<stem>.tmp` and the sibling `session-cleanup.<stem>.jsonl` failure event, distinguish retained transcripts, outputs, seals, receipts, reviews and verify builds, and explain why old or interrupted sessions may still leave scratch
- **THEN** they explain removal after confirmed process-group exit, unchanged attempt outcomes on cleanup failure, and remaining limits from historical leftovers, harness/host termination or unconfirmed cleanup

#### Scenario: Host validation remains explicit
- **WHEN** automated cleanup tests pass
- **THEN** the documentation still identifies inspection of one finished real Codex attempt after the first updated build as a pending human check, without claiming that fixtures proved live-host cleanup
