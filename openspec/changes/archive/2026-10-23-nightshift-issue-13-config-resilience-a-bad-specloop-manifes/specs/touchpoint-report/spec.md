## Purpose

Preserve Front door attribution when intake starts producing owner-qualified change names. The existing touchpoint-report requirements remain unchanged; this compatibility requirement scopes the necessary fallback matcher update in `nightshift/touchpoints.py` and tests in new `tests/test_touchpoint_identity_compatibility.py`.

## ADDED Requirements

### Requirement: Producer naming compatibility

When proposal records are missing, the report SHALL infer issue identity from exact owner-qualified or legacy producer names in admission/revision receipts and retained jobs, matching the validated receipt repository and existing issue/slug grammar. It SHALL preserve Front door attribution guarantees, offline read-only operation, accounting, schema, diagnostics and date filtering. Only the fallback matcher and its tests SHALL change.

#### Scenario: Qualified fallback recovers without a proposal
- **WHEN** proposal records are absent and admission or revision evidence for owner/sample names owner__sample-issue-7-add-a-gate, including evidence retained only in a job request
- **THEN** the report retains a row for owner/sample, source issue 7 and that exact change, independently of the receipt's PR number, with the matching run event path
- **THEN** the draft date remains unknown with a warning, and a date-filtered report excludes the row with the existing unknown-date error

#### Scenario: Both conventions retain isolated attribution
- **WHEN** local history without proposals includes a__x-issue-7-add-a-gate for a/x, b__x-issue-7-add-a-gate for b/x, and legacy x-issue-8-add-a-gate receipts for both repositories
- **THEN** the qualified rows and their run events remain separate, the legacy rows infer issue 8 while their shared run events remain unread with an ambiguity diagnostic, and no records are renamed or rewritten
- **THEN** reporting works offline even if either repository's enrollment manifest is unusable, preserving existing counts and output fields

#### Scenario: Fallback requires exact repository and suffix
- **WHEN** fallback evidence includes a correctly qualified name for owner/widget.js_tools-x alongside another owner's prefix, regex-lookalike punctuation, a zero or leading-zero issue number, an invalid or overlong slug, a path-like change, a conflicting issue field or an ambiguous receipt directory without an explicit repository
- **THEN** the valid row is recovered with its exact repository spelling, invalid evidence retains diagnostics without speculative attribution or unsafe event reads, and healthy rows remain available
- **THEN** matching proposal/job identities continue to take precedence over name inference under the existing attribution rules
