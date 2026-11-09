## Purpose

Verify native front-door revisions use the existing adapter-owned append and keyed recovery contracts.

## ADDED Requirements

### Requirement: Native front door review rounds

Native front-door build revisions SHALL preserve selected feedback and the existing keyed result contract while appending through the selected adapter. Numbering SHALL use maximum native phase plus one, maximum exact review-round title plus one, and globally fresh numeric T roots including dotted children, formatted with at least three digits. Fresh append and replay validation SHALL reconstruct identical text through revise._append; no new state or receipts SHALL be added.

#### Scenario: Native round appends phase and next task IDs
- **WHEN** a native run with a three-phase test fixture whose highest T root is 9 receives its first keyed review round
- **THEN** tasks append beneath ## Phase 4: Review round 1 starting at T010, preserving earlier task/scenario IDs and quoting one feedback block per task
- **THEN** dotted roots and values beyond 999 participate without truncation, and duplicate/ambiguous parsed identities refuse

#### Scenario: Native keyed replay returns same commit
- **WHEN** the same feedback and round key are replayed after a successful native append, including after a later commit or control-state change
- **THEN** replay returns the original commit without appending another phase or replacing later state, and conflicting feedback under that key refuses
- **THEN** native spec-stage revision still uses the reviewed branch and existing head binding; every OpenSpec revision, feedback-consumption and crash-recovery assertion remains intact
