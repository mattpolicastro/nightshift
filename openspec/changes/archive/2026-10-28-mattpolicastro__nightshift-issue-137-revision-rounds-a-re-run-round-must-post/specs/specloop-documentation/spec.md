## Purpose

Keep the manual fresh-round recovery instructions accurate after revision
delivery gains distinct result-attempt comments.

## ADDED Requirements

### Requirement: Revision rerun workaround

`KNOWN-LIMITATIONS.md` SHALL explain that separate result-attempt comments do
not remove `agent:revise` after rejection or needs-input. It SHALL retain the
manual remove-and-reapply workaround, its normal eligibility gates and the
pending live verification boundary, citing issue #137 without inventing an
implementation PR. Unrelated limitations SHALL remain intact.

#### Scenario: Operator starts a fresh revision round
- **WHEN** an operator reads the revision recovery limitations after a rejected or needs-input result
- **THEN** the guidance says to resolve the questions or contradictions, remove `agent:revise`, clear `needs-human`, and have a trusted actor re-add `agent:revise` to obtain a new label event
- **THEN** it explains that applying an already-present label creates no fresh event, pause and stopped-job holds still apply, and the next live rejected revision's posted reason remains a human check rather than a claimed CI result
