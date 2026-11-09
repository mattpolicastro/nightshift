## Purpose

Recover native source issues from retained receipts without losing offline accounting or repository isolation.

## MODIFIED Requirements

### Requirement: Producer naming compatibility

When proposal records are missing, the report SHALL infer issue identity from exact native, owner-qualified or legacy producer names in admission/revision receipts and retained jobs, matching the validated receipt repository and existing legacy issue/slug grammar. Native names SHALL use the design's safe basename with a positive numeric prefix of at least three digits (including supported exact repository qualification), infer the issue by integer conversion and retain bounded slug validation. Native padding is accepted; legacy leading-zero issue spellings remain invalid. It SHALL preserve Front door attribution guarantees, offline read-only operation, accounting, schema, diagnostics and date filtering. Only the fallback matcher and its tests SHALL change.

#### Scenario: Qualified fallback recovers without a proposal
- **WHEN** proposal records are absent and admission or revision evidence for owner/sample names owner__sample-issue-7-add-a-gate, including evidence retained only in a job request
- **THEN** the report retains a row for owner/sample, source issue 7 and that exact change, independently of the receipt's PR number, with the matching run event path
- **THEN** the draft date remains unknown with a warning, and a date-filtered report excludes the row with the existing unknown-date error

#### Scenario: Both conventions retain isolated attribution
- **WHEN** local history without proposals includes a__x-issue-7-add-a-gate for a/x, b__x-issue-7-add-a-gate for b/x, and legacy x-issue-8-add-a-gate receipts for both repositories
- **THEN** the qualified rows and their run events remain separate, the legacy rows infer issue 8 while their shared run events remain unread with an ambiguity diagnostic, and no records are renamed or rewritten
- **THEN** reporting works offline even if either repository's enrollment manifest is unusable, preserving existing counts and output fields

#### Scenario: Fallback requires exact repository and suffix
- **WHEN** fallback evidence includes a correctly qualified name for owner/widget.js_tools-x alongside another owner's prefix, regex-lookalike punctuation, a legacy zero or leading-zero issue number, an invalid or overlong slug, a path-like change, a conflicting issue field or an ambiguous receipt directory without an explicit repository
- **THEN** the valid row is recovered with its exact repository spelling, invalid evidence retains diagnostics without speculative attribution or unsafe event reads, and healthy rows remain available
- **THEN** matching proposal/job identities continue to take precedence over name inference under the existing attribution rules

#### Scenario: Native admission recovers without proposal
- **WHEN** collect_history reads an admission for 007-add-a-gate and no proposal history exists
- **THEN** it returns one issue-attributed row for issue 7 in the receipt's repository, with the exact change, unknown draft date and existing warning
- **THEN** the same fallback works for revision receipts and retained admission/revision requests, preserving accounting and date-filter diagnostics without network or state writes

#### Scenario: Native repositories remain separate
- **WHEN** two repositories have proposal-free receipts for 007-add-a-gate, or native evidence has a conflicting explicit issue or foreign repository qualification
- **THEN** valid receipts retain separate repository/issue rows, ambiguous shared run events remain unread with the existing diagnostic, and conflicting evidence cannot be speculatively joined
- **THEN** existing proposals remain authoritative and no records or output schema are rewritten
