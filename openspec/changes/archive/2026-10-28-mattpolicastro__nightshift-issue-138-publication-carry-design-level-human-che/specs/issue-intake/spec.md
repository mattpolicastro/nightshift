## Purpose

Carry design-level human checks into publication alongside existing task/scenario checks, preserving readiness, receipt, and delivery contracts.

## ADDED Requirements

### Requirement: Design checklist collection

Publication check collection SHALL append unchecked physical lines from the run head's committed `design.md` under exact `## Human checks` or `### Verification and human rollout checklist` headings. It SHALL preserve existing sources, order and rendering, append design items in document order, and omit only exact-text duplicates of earlier collected rows.

#### Scenario: Both design headings contribute
- **WHEN** the committed design contains unchecked items beneath both supported headings, including repeated selected sections and deeper subsections
- **THEN** collection uses `splitlines()` and removes only outer ASCII spaces/tabs from each line; the two exact, case-sensitive headings open selected sections
- **THEN** a line matching `^(#{1,6})[ \t]+` at the same or shallower depth closes the active section and may open another selected section, while deeper headings retain it
- **THEN** collection appends all those items in physical document order through each section's next same-or-shallower heading or EOF
- **THEN** only a literal `- [ ]` followed by a space or tab and nonblank text contributes an item; its complete normalized line, including the checkbox, is emitted
- **THEN** checked `[x]`/`[X]` items, other bullet styles, unrelated sections, prose, continuation lines, and unsupported heading spellings (including different case or closing hashes) contribute no items

#### Scenario: Task checks precede design checks
- **WHEN** existing task/scenario checks and design items include exact duplicates and near duplicates differing in case, punctuation, or internal spacing
- **THEN** existing checks retain their original source order and rendering before design additions, and only design rows exactly equal to an earlier normalized checklist row are omitted
- **THEN** comparison uses the complete line with only outer ASCII spaces/tabs removed, preserving case, punctuation, backticks and internal whitespace; existing duplicate rows remain unchanged, and ordinary implementation checkboxes in `tasks.md` do not become human checks
- **THEN** both readiness detail and generated PR bodies preserve their existing task/scenario punctuation and retain distinct near duplicates

#### Scenario: Literal examples remain pending
- **WHEN** matching selected headings or unchecked item lines appear inside fences, HTML blocks, or indentation
- **THEN** the same physical-line rules apply, including section boundaries, without parsing those containers
- **THEN** a collected unchecked example remains an outstanding or unknown check under existing readiness rules unless its referenced scenario already explicitly permits post-deploy checking

#### Scenario: Committed head supplies checks
- **WHEN** the source working tree's design differs from the run's committed design, or base preparation advances the run head
- **THEN** newly generated readiness detail and publication body collect from the applicable committed run head, including the integrated head after preparation, and never from uncommitted source text

#### Scenario: Absent or empty design preserves checks
- **WHEN** the committed tree has no design file or its readable design has no unchecked items under a supported heading
- **THEN** existing checks and their output formatting remain unchanged, including existing empty-check behavior

#### Scenario: Read failure cannot erase checks
- **WHEN** Git cannot establish design absence or cannot read or decode the committed design during readiness collection
- **THEN** collection fails with a diagnostic and does not generate a ready-to-publish pause with an assumed empty design checklist

### Requirement: Design checks survive publication

Collected design checks SHALL flow through readiness detail into the publication receipt's `checks`, the delivered human-check comment and newly generated PR bodies. Recovery SHALL preserve these checks under existing publication contracts, without ticking items or rewriting historical receipts.

#### Scenario: Design only checks reach the receipt
- **WHEN** a change with no task/scenario human checks and an unchecked committed design check completes readiness and publishes to its adopted spec PR
- **THEN** the ready detail, matching saved publication receipt's `checks`, and delivered human-check comment all contain the design item
- **THEN** the ready detail contains the item before receipt capture, retaining its existing header and `Next:` line, and publication preserves base-sync gates and JSON contracts
- **THEN** the existing adopted PR is reused and its body is not rewritten

#### Scenario: New PR body lists design checks
- **WHEN** publication creates a PR for a change with collected design items
- **THEN** its `## Checks a human must make` section lists existing task/scenario checks first and the deduplicated design additions afterward

#### Scenario: Recovery retains design checks
- **WHEN** publication succeeds but its response is lost, or comment delivery is interrupted before acknowledgement
- **THEN** existing recovery retains the matching receipt's complete checklist and delivers or reconciles exactly one publication comment without republishing solely for comment delivery
- **THEN** receipt identity `(change, head, pr)`, round markers and retry behavior remain unchanged, the comment retains the hosted-CI reminder, and unrelated historical receipts are not rewritten

#### Scenario: Unannotated design check blocks readiness
- **WHEN** a delivered front-door publication contains an unannotated or unmapped design check and the adopted PR body lacks an explicit human-check section
- **THEN** existing merge readiness reads the receipt fallback and blocks for outstanding or unknown human checks even when other readiness conditions pass
- **THEN** collection does not tick the item, infer a post-deploy exemption, or change PR-body precedence
