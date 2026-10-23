## Purpose

Document routed mutation evidence and the trusted selector example while retaining the existing guide's operational and evidence guarantees.

## ADDED Requirements

### Requirement: Routed evidence guidance

Section evidence SHALL explain trusted route configuration, glob and fallback semantics, mutation-only steps, `{mutant_path}`, full baselines, confirmation, result classifications and decision receipts. It SHALL preserve existing guidance and citations and explain selector fallback. Private-manifest rollout and timing comparison SHALL remain human checks, without claims of measured speed-up before rollout.

#### Scenario: Reader configures routed evidence
- **WHEN** a reader follows Section evidence and `examples/specloop/self-hosting.toml`
- **THEN** the Python route and mutation-only command use the selector from the trusted checks directory, the existing verification and coverage commands remain intact, and documentation shows how a second plugin route can refer to a separately defined suite
- **THEN** the reader can identify the first matching route, the unmatched fallback, the full baseline per used sequence and the receipt deciding a mutant's outcome

#### Scenario: Example fallback runs the whole suite
- **WHEN** the example mutation command is exercised against a fixture root with a selected importing test or with an empty/unresolvable selector input
- **THEN** its argv runs the selected test paths or the full `tests/` directory respectively, preserves paths containing spaces and the existing pytest environment/options, and propagates the test process exit status
- **THEN** the guide explains full confirmation, selector limits, unchanged scoring and the human timing comparison against the approximately 14-minute baseline

## MODIFIED Requirements

### Requirement: Documentation integrity

The three-document-only implementation scope applies to #67's documentation reconciliation, not to subsequent features with explicit code scope and requirement deltas. Subsequent guide updates SHALL stay within their declared sections, use short, concrete, present-tense prose, preserve unrelated runtime requirements and all existing assertions, and cite verified issue/PR identities without inventing references. This change SHALL limit guide edits to Section evidence and leave the root README and known-limitations list unchanged. Implementation acceptance SHALL include green `uv run pytest`; human rollout checks SHALL remain in the design and implementation PR, not implementing-agent tasks.

#### Scenario: Scope and regression checks remain intact
- **WHEN** the implementation diff and local validation are reviewed
- **THEN** changes stay within this proposal's named files, guide edits stay within Section evidence, unrelated documentation and existing test assertions remain unchanged, relative documentation links resolve, and `uv run pytest` passes without weakening checks

#### Scenario: References and reader checks are reviewable
- **WHEN** the documentation is prepared for merge
- **THEN** existing implementation citations remain and new references distinguish issue #73 from any verified implementation PR, without inventing a PR number or describing pending rollout as shipped
- **THEN** the PR carries the design's human checklist for private-manifest rollout and real-gate timing comparison; #67's historical live-reference/ledger and readability checks are not reassigned as implementing-agent tasks
