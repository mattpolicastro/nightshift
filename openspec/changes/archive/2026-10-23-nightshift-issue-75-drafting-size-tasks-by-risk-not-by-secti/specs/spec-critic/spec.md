## Purpose

Keep independent spec review proportionate to task overhead and operational
blast radius while preserving its evidence, correction and delivery contracts.

## ADDED Requirements

### Requirement: Advisory task count findings

The critic's over-engineering item SHALL flag plans with more than eight tasks,
or documentation-only plans with more than two tasks, as non-blocking risks.
It SHALL name task labels that could merge and their shared concern, or explain
why necessary boundaries prevent a safe merge. Task count alone SHALL never
be a contradiction or require a correction. Independent concrete defects
SHALL retain their existing blocking criteria.

#### Scenario: Excessive plans receive merge advice
- **WHEN** the critic reviews a plan with nine or more tasks, or a documentation-only plan with three or more tasks
- **THEN** its checklist instructs it to report a non-blocking risk identifying tasks that could merge and why, without inventing a merge when boundaries are necessary

#### Scenario: Threshold boundaries are advisory
- **WHEN** a general plan has eight tasks or a documentation-only plan has two tasks
- **THEN** the checklist does not flag it solely for task count, and the drafter's usual six-task target does not become a critic rejection threshold

#### Scenario: Count risk preserves successful drafting
- **WHEN** lint and feasibility pass and the critic's only finding is a task-count risk naming possible merges
- **THEN** the draft retains no contradictions, carries the finding through existing risks with one `Critic: ` prefix, and spends no correction round

### Requirement: Proportionate retry remedies

The critic's retry and idempotency item SHALL scale remedies to blast radius.
Safe re-runs that re-check real state SHALL satisfy the item for an
operator-attended local command. Durable journals, transaction records and
reconciliation of interrupted runs SHALL be treated as over-engineering for
that case. Unattended daemon operations and GitHub writes SHALL retain strict
review of lost responses, retries and duplicate or dropped effects, citing
the write and retry path.

#### Scenario: Safe local reruns suffice
- **WHEN** an operator-attended local command can be safely re-run by re-checking actual current state
- **THEN** the checklist accepts that remedy and does not demand durable journals, transaction records or interrupted-run reconciliation
- **THEN** operator attendance alone does not excuse a re-run that can cause unsafe effects

#### Scenario: Unattended and GitHub effects retain scrutiny
- **WHEN** a change adds or alters an unattended daemon operation or a GitHub comment, label, push or PR edit, even when invoked from a local CLI
- **THEN** the checklist retains strict scrutiny of lost-response and retry paths that can duplicate or drop effects and requires evidence naming the write and retry path

## MODIFIED Requirements

### Requirement: Independent checklist review

The critic SHALL use a separate read-only agent call on the exact candidate and effective change, with `feasibility.SCHEMA`. Its checklist SHALL cover over-engineering, file collisions and hidden instructions, invalidated protected assertions, conflicts with unowned behavior, tests broken by pending changes, unplanned migrations, retry and idempotency gaps, and stale operator docs and manual steps. Contradictions SHALL carry concrete evidence; uncertain and non-blocking concerns SHALL be risks.

#### Scenario: Complete checklist and read contract
- **WHEN** the critic reviews a candidate whose effective change differs from the manifest template
- **THEN** its call uses access `read`, the shared feasibility schema, a fresh extraction of that commit, and a prompt naming the effective change with all eight checklist items and the allowed/frozen paths
- **THEN** its profile selection and time limits match the feasibility review defaults, and its transcript and result are retained separately from the drafting and feasibility artifacts

#### Scenario: Review failure is not a pass
- **WHEN** the critic agent fails, omits its verdict, or returns a malformed result
- **THEN** the review returns an error, retains a diagnostic result, and removes its temporary extraction
- **THEN** proposal handling reports a critic review problem and does not deliver a successful draft or spend another drafting attempt
