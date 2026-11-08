## Purpose

Drive approved native spec-kit features through explicit admission, build,
revision, evidence, publication and completion without extending run state.

## ADDED Requirements

### Requirement: Native admission

Admission SHALL validate the committed native feature with its adapter, use
adapter-parsed task records for task/key/gate checks, preserve native `path:line`
diagnostics and refuse phase zero. Successful native JSON SHALL retain version 1
and outcome `admitted`, adding integer `sections` and `scenarios` counts.
OpenSpec JSON and existing admission cleanup/locking SHALL remain unchanged.

#### Scenario: Admit fixture counts
- **WHEN** `nightshift admit --json --skip-feasibility` admits a source copied from the parser fixture and deterministically grouped into three phases as specified in the design
- **THEN** it succeeds with `sections: 3` and `scenarios: 4`, retaining all seven task lines and recording the native manifest and admitted head
- **THEN** the untouched six-phase fixture copy also admits and reports six sections and four scenarios without modifying checked-in fixtures

#### Scenario: Broken fixture retains source diagnostic
- **WHEN** the last task citation for one scenario is removed from an otherwise valid fixture
- **THEN** admission refuses with the validator's repository-relative `spec.md:line: uncovered scenario` message in its reasons, one version-1 JSON document and no admitted state

#### Scenario: Invalid task structure refuses
- **WHEN** the native feature has no parsed tasks, duplicate task keys/labels or phase numbers, or a positive phase gate naming no populated phase
- **THEN** admission reports the task/phase problem using native terminology and preserves source refs and existing partial-admission cleanup
- **THEN** no agent is invoked and no admitted state is saved
- **THEN** refusal follows the selected format's recognized task structure, never detection of another heading style; a numeric OpenSpec heading inside a native file is not a phase

#### Scenario: Phase zero refuses
- **WHEN** native tasks declare any physical `## Phase 0:` heading, including an empty phase, or the manifest names gate zero
- **THEN** admission refuses with a clear phase-zero diagnostic before starting the build
- **THEN** standalone native parsing still recognizes phase zero, OpenSpec admission semantics remain unchanged and loop gate truthiness is not modified

### Requirement: Native build gates

The driver SHALL treat native phase numbers as sections, retain existing
scenario coverage/mutation behavior and group IDs by `rsplit('/', 1)` into
stories. It SHALL use adapter context and human checks, select reviewer notes
by exact parsed task labels and retain frozen-path enforcement. No
format-specific logic SHALL be added to the loop or evidence scorer.

#### Scenario: Evidence groups by story
- **WHEN** the completed US1 phase cites as1 and as2 but the fake coverage log names only `042-add-a-gate/us1/as1`
- **THEN** the gate reports only `042-add-a-gate/us1/as2` uncovered, with nothing from us2 or us3, and any strengthening task uses native labels in that phase
- **THEN** unknown runtime candidate IDs remain filtered and failed scenario lookup cannot pass evidence or append tasks

#### Scenario: Native notes reach the phase reviewer
- **WHEN** a task reviewer records a follow-up under T003 and the phase containing parsed task T003 reaches agent review
- **THEN** that note appears in the phase reviewer's context alongside native spec, plan and constitution files
- **THEN** notes for another phase or labels T003.1/T0030 do not match T003 by prefix, malformed labels are not selected, and numeric OpenSpec task labels still select their own notes

#### Scenario: Harness writes preserve frozen criteria
- **WHEN** a manifest freezes `specs/**` and `.specify/**`, an implementer attempts to edit those paths beside allowed code, and the harness later ticks tasks or completes the feature
- **THEN** the sealed candidate retains frozen base content and reports existing frozen-path violations while allowing ordinary code
- **THEN** only harness-authored task/Status updates land through trusted commits and no seal exemption is added for agents

#### Scenario: Real story build
<!-- nightshift:post-deploy -->
- **WHEN** the native build is exercised with fake agents offline and later with real implementer/reviewer profiles in a scratch repository
- **THEN** offline tests drive parsed native tasks through sealing, verification, review, ticking and an agent phase gate using native context
- **THEN** the human rollout check separately demonstrates a real story gate, publication and finish and records touchpoints on #184; offline results do not claim the live check occurred

### Requirement: Native publication lifecycle

Native publication SHALL use the adapter's title/kind, carry plan human checks
and preserve base freshness, workflow refusal, forge routing and PR reuse.
Finish SHALL check expected head and completed native tasks under the run lock,
commit Complete Status, compare-and-swap the branch, push and mark ready in that
order. Failed completion, ref advancement, push or readiness SHALL NOT mark done.

#### Scenario: Publish native feature
- **WHEN** a native run reaches ready_to_publish and passes existing publication guards
- **THEN** a newly created draft PR has title `{change}: spec-kit feature via specloop` and body kind `spec-kit feature`, completed task counts and committed plan human checks
- **THEN** Gitea adds its configured WIP prefix, adopted/existing PRs are reused and all three workflow-directory refusals still block before pushing or PR operations

#### Scenario: Finish guards and ordering
- **WHEN** native finish or act finish is requested at human_checks with matching or omitted expected head and all tasks ticked
- **THEN** forge/origin validation precedes completion, the Status commit advances the ref with its expected old head, external push precedes ready operations and done is saved only after readiness succeeds
- **THEN** expected-head mismatch, unfinished/ambiguous tasks or a ref race causes no push, ready operation or done transition, and the competing branch is preserved

#### Scenario: Status after native partial finish
- **WHEN** a completion commit survives a failed external push or readiness operation and status or finish is retried
- **THEN** status reads live tasks/counts and the actual completion head without mutation, while finish reuses that commit and performs only the remaining external work before saving done
- **THEN** no new run-state field, archive, duplicate completion commit or stale expected-head bypass is introduced

## MODIFIED Requirements

### Requirement: Revision round

`specloop revise <change> --feedback <file>` SHALL ask the selected adapter to
append one task per feedback block and commit it through the harness, leaving
a newly appended round running. OpenSpec SHALL retain `## N. Review round K`.
Spec-kit SHALL use `## Phase N: Review round K`, with K after the highest native
review-round title, N after the highest phase and globally fresh T labels.
The adapter SHALL own numbering and fresh/replay text reconstruction, keeping
`revise.py` format-agnostic. With `--round-id`, a matching already-landed round
SHALL follow Idempotent revision recovery, returning its original commit and
preserving later state. Unkeyed eligible invocations SHALL retain fresh-append
behavior. Empty feedback SHALL refuse even with a key. This qualifies the
previous OpenSpec-only heading requirement rather than changing its behavior.

#### Scenario: Feedback becomes tasks
- **WHEN** revise is given two feedback blocks for an admitted OpenSpec change
- **THEN** tasks.md gains the original review-round section and two unchecked tasks quoting the blocks, and status becomes running

#### Scenario: Empty feedback refused
- **WHEN** revise receives no feedback, with or without a round key
- **THEN** nothing is committed and the command exits nonzero

#### Scenario: Native rounds advance and replay
- **WHEN** two different keyed feedback submissions are applied to an eligible native run and the first key is replayed after the second commit
- **THEN** the first two commits append Review round 1 then Review round 2 with increasing phase numbers, globally fresh T labels and distinct task keys
- **THEN** replay returns the first commit without appending tasks or replacing later state, using native reconstruction for candidate validation
- **THEN** equal feedback under different keys still gets distinct rounds; conflicting feedback under the same key still refuses
