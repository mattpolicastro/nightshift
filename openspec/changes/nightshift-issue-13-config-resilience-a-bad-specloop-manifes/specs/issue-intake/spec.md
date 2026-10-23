## Purpose

Preserve the GitHub front door while isolating unhealthy enrollment and qualifying new identities. Implementation stays in the scoped Nightshift modules and tests; publication requirements remain unchanged.

## ADDED Requirements

### Requirement: Owner qualified new identities

New issue drafts SHALL use `<owner>__<repository>-issue-<number>-<title-slug>` and branch `spec/<change>`, preserving owner/repository spelling and the existing title slug algorithm. The generated component SHALL pass the existing admission/revision branch predicate. Unsupported repository spellings SHALL report repository health and prevent new drafting and admission, without encoding or relaxing safety assertions.

#### Scenario: Different owners have distinct changes
- **WHEN** repositories a/x and b/x each draft issue 7 titled Add a gate
- **THEN** their new changes are a__x-issue-7-add-a-gate and b__x-issue-7-add-a-gate, with matching distinct branches, receipts, CLI arguments and PR lookups

#### Scenario: Safe punctuation is preserved
- **WHEN** owner/widget.js_tools-x drafts an eligible issue
- **THEN** the new name preserves widget.js_tools-x and uses the existing bounded title slug or issue fallback, passing the unchanged branch predicate

#### Scenario: Unsafe spelling holds new work
- **WHEN** owner/widget..js would generate spec/owner__widget..js-issue-7-add-a-gate
- **THEN** repository health reports the unsupported name before dispatch, no new draft or admission runs, and existing unsafe-branch no-command assertions remain valid
- **THEN** a safe recorded branch of an admitted run remains eligible for publication and build revision

### Requirement: Recorded identities remain authoritative

Existing receipts, PR branches and admitted runs SHALL retain their recorded identity. Without a receipt, intake SHALL look up exact qualified and legacy basename candidates within the repository before creating a new draft, and SHALL treat lookup failures as errors rather than absence. This bounded compatibility SHALL NOT rename runs or claim recovery after both receipt loss and title change.

#### Scenario: Legacy receipt survives title edits
- **WHEN** an existing active proposal receipt records an ownerless identity and the issue title changes before restart or delivery
- **THEN** intake reuses its saved change, branch, job and result without redrafting or renaming, and legacy PR admission/revision/publication use recorded identities

#### Scenario: Legacy PR is recovered
- **WHEN** no local proposal receipt exists but exact lookup finds the open legacy branch for the current issue/title
- **THEN** authorized intake recovers delivery to that PR without creating another draft or PR

#### Scenario: Lookup failure does not create duplicate work
- **WHEN** either required exact PR lookup fails
- **THEN** intake preserves existing state, reports the failure and dispatches no new proposal

#### Scenario: Closed legacy PR starts a qualified round
- **WHEN** normal lifecycle rules permit a new round after the legacy PR was closed and the issue is rearmed
- **THEN** new work uses the owner-qualified identity, retains replacement bookkeeping and preserves merged-PR suppression and existing recovery authorization

## MODIFIED Requirements

### Requirement: Engine per repository

Each enrolled repository SHALL have an engine, `worker` (the default, unchanged) or `specloop` with a manifest path required for new work. An omitted or unusable manifest SHALL be a repository health hold under Repository manifest health rather than preventing all configuration loading. The daemon SHALL never run worker claims for a specloop repository, nor specloop intake for a worker repository; reconciling leftover worker claims on a switch SHALL not launch workers. Specloop triggers SHALL satisfy Latest label actor authorization before acting.

#### Scenario: Worker repository unchanged
- **WHEN** a repository has no engine set and an issue is labelled agent:ready
- **THEN** the daemon claims it for the worker exactly as before

#### Scenario: Specloop repository skips worker claims
- **WHEN** a healthy repository has engine specloop and an eligible issue is labelled agent:ready by a trusted actor
- **THEN** no worker claim is made and the issue is drafted as a spec instead

### Requirement: Drafting from an issue

For a healthy specloop repository, an otherwise eligible issue labelled `spec:draft` (or `agent:ready`) SHALL be drafted with `specloop propose`, using its title, body and comments, only after Latest label actor authorization succeeds. New drafts SHALL use Owner qualified new identities, while recovery SHALL follow Recorded identities remain authoritative. Existing issue-author and comment trust checks SHALL remain in force. On success the daemon SHALL push `spec/<change>`, open a draft PR titled `Spec: <issue title>` that links the issue, label it `spec:review`, comment the PR link on the issue and remove the drafting label. Unhealthy enrollment SHALL defer new dispatch without consuming its trigger.

#### Scenario: Draft PR opened
- **WHEN** eligible issue 7 "Add a gate" in healthy repository owner/sample is labelled spec:draft by a trusted actor and propose drafts successfully
- **THEN** a draft PR from spec/owner__sample-issue-7-add-a-gate is opened, labelled spec:review, linked from a comment on issue 7, and spec:draft is removed

#### Scenario: Questions posted
- **WHEN** an authorized drafting request returns questions for the issue
- **THEN** the questions are posted as a comment on the issue, it is labelled needs-human, and no PR is opened

#### Scenario: Unfixable draft escalated
- **WHEN** an authorized drafting request reports contradictions or problems that survive its rounds
- **THEN** they are posted on the issue, it is labelled needs-human, and no PR is opened

### Requirement: Approval admits

`spec:approved` on an otherwise eligible spec PR SHALL admit its change with `specloop admit --pr` only after Latest label actor authorization succeeds and repository readiness permits new admission. Unhealthy enrollment SHALL retain the request without a new admission, approval-head write or refusal escalation; already-committed admissions SHALL retain completion recovery. A refused admission SHALL be explained in a PR comment with needs-human added. Unauthorized label applications SHALL use the separate rejection behavior instead of admission refusal.

#### Scenario: Approved spec admitted
- **WHEN** a spec PR in a healthy repository is labelled spec:approved by a trusted actor and admission succeeds
- **THEN** the change is admitted with that PR recorded and the PR gets a comment saying the build has started

#### Scenario: Refused admission explained
- **WHEN** an authorized admission is refused for a contradiction
- **THEN** the PR gets a comment with the contradiction and evidence and is labelled needs-human

#### Scenario: Unhealthy enrollment retains approval
- **WHEN** a trusted approval requests new admission while enrollment is unhealthy
- **THEN** no admission starts, the approval label and existing receipts remain, and no needs-human refusal is posted for the configuration problem
