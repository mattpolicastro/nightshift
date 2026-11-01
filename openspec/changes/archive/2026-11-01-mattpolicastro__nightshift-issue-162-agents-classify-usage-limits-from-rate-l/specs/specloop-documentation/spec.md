## Purpose

Keep quota guidance consistent with structured Claude classification and
recovery while preserving existing provider and documentation contracts.

## ADDED Requirements

### Requirement: Structured quota guidance

The guide SHALL document structured Claude classification, diagnostic-only
fallback, reset plus 60-second grace, the 30-minute fallback and the overage
human hold. It SHALL preserve existing fallback guidance, qualify its
all-blocked rule and cite issue #162. KNOWN-LIMITATIONS.md SHALL distinguish
after-session telemetry from advance detection and leave real-limit
validation pending. Other passages SHALL change only when made false.

#### Scenario: Operator distinguishes a quota wait from an overage hold
- **WHEN** an operator consults provider recovery and quota limitations
- **THEN** guidance identifies the evidence and deadline to inspect in run events, ordinary scheduled recovery, explicit human resume for overage, and the lack of live interruption or prevention of already-incurred charges
- **THEN** automated fixtures are not described as proof of a real limit, existing assertions, issue #72 references and unrelated guidance remain intact, and the new telemetry paragraph and issue link are validated with the design's physical-line rule that blocks missing or ambiguous boundaries
