## Purpose

Support native spec-kit identities and spec PRs through existing authorized, recoverable forge intake.

## ADDED Requirements

### Requirement: Native existing feature selection

For new eligible native work, intake SHALL reuse one explicitly named existing specs/<NNN>-<slug>/ matching the issue number, using the design's physical-line token rule and the exact pinned drafting base. It SHALL preserve committed identities and unrelated content. Ambiguous, unsafe, unavailable or Complete targets SHALL block rather than cause a silent new feature or overwrite.

#### Scenario: Existing feature directory is reused
- **WHEN** issue 42 names existing undelivered specs/042-add-a-gate/ in its body and its current title would generate a different slug
- **THEN** the effective change remains 042-add-a-gate, branch spec/042-add-a-gate and drafting revises that directory with existing identities retained
- **THEN** repeated identical tokens do not create ambiguity and uncommitted source edits do not establish a feature's existence

#### Scenario: Ambiguous or unavailable target refuses
- **WHEN** the body names several distinct same-number targets, or its sole target is missing, unsafe, unreadable or Complete at the pinned commit
- **THEN** intake uses existing questions/problems or operational-error handling as appropriate, with no agent writing an alternative directory and no successful PR delivery
- **THEN** identical token recognition applies inside fences, HTML and indentation, with no semantic parsing exception

#### Scenario: Untargeted native issue uses title
- **WHEN** no complete directory token for the issue's number appears, including when only other-number features or paths to files are mentioned
- **THEN** new native work uses the issue-derived name without sequential numbering or an implicit directory scan and never overwrites a Complete feature
- **THEN** blocker, authorization, health and pause gates precede new discovery/dispatch; uncertain Git reads and timeout do not imply absence

### Requirement: Native spec PR description

A successful native proposal SHALL retain the existing spec PR title, issue-closing line, feasibility risks and delivery effects and add one line naming the native feature directory. Existing OpenSpec PR bodies and recovered PRs SHALL remain unchanged.

#### Scenario: Spec PR body names feature directory
- **WHEN** an eligible native proposal for issue 7 successfully delivers on GitHub or configured Gitea
- **THEN** its draft PR is titled Spec: Add a gate, contains Closes #7, a line saying spec-kit feature followed by the backticked path specs/007-add-a-gate/, and existing feasibility risks
- **THEN** normal review labels, issue-link comments, exact-commit delivery and intake-label removal occur without new receipt fields or duplicate PRs on retry

## MODIFIED Requirements

### Requirement: Drafting from an issue

For a healthy specloop repository, an otherwise eligible issue labelled `spec:draft` (or `agent:ready`) SHALL be drafted with `specloop propose`, using its title, body and comments, only after Latest label actor authorization succeeds. New drafts SHALL use the manifest-selected naming rules in Owner qualified new identities, while recovery SHALL follow Recorded identities remain authoritative. Existing issue-author and comment trust checks SHALL remain in force. On success the daemon SHALL push `spec/<change>`, open a draft PR titled `Spec: <issue title>` that links the issue, label it `spec:review`, comment the PR link on the issue and remove the drafting label. Unhealthy enrollment SHALL defer new dispatch without consuming its trigger.

#### Scenario: Draft PR opened
- **WHEN** eligible issue 7 "Add a gate" in healthy OpenSpec repository owner/sample is labelled spec:draft by a trusted actor and propose drafts successfully
- **THEN** a draft PR from spec/owner__sample-issue-7-add-a-gate is opened, labelled spec:review, linked from a comment on issue 7, and spec:draft is removed

#### Scenario: Questions posted
- **WHEN** an authorized drafting request returns questions for the issue
- **THEN** the questions are posted as a comment on the issue, it is labelled needs-human, and no PR is opened

#### Scenario: Unfixable draft escalated
- **WHEN** an authorized drafting request reports contradictions or problems that survive its rounds
- **THEN** they are posted on the issue, it is labelled needs-human, and no PR is opened

### Requirement: Owner qualified new identities

New OpenSpec issue drafts SHALL use `<owner>__<repository>-issue-<number>-<title-slug>` and branch `spec/<change>`, preserving owner/repository spelling and the existing title slug algorithm. The generated component SHALL pass the existing admission/revision branch predicate. Unsupported repository spellings SHALL report repository health and prevent new drafting and admission, without encoding or relaxing safety assertions.

For a healthy trusted manifest selecting speckit, new issue drafts SHALL instead use <number:03d>-<title-slug> and branch spec/<change>. Padding SHALL be a minimum of three digits without truncation, with positive repository-local issue numbers on both forges and the unchanged bounded ASCII title slug. Existing safe-component and enrollment checks SHALL remain; no repository format setting or filesystem format inference SHALL be introduced.

#### Scenario: Different owners have distinct changes
- **WHEN** OpenSpec repositories a/x and b/x each draft issue 7 titled Add a gate
- **THEN** their new changes are a__x-issue-7-add-a-gate and b__x-issue-7-add-a-gate, with matching distinct branches, receipts, CLI arguments and PR lookups

#### Scenario: Safe punctuation is preserved
- **WHEN** OpenSpec repository owner/widget.js_tools-x drafts an eligible issue
- **THEN** the new name preserves widget.js_tools-x and uses the existing bounded title slug or issue fallback, passing the unchanged branch predicate

#### Scenario: Unsafe spelling holds new work
- **WHEN** an OpenSpec enrollment for owner/widget..js would generate spec/owner__widget..js-issue-7-add-a-gate
- **THEN** repository health reports the unsupported name before dispatch, no new draft or admission runs, and existing unsafe-branch no-command assertions remain valid
- **THEN** a safe recorded branch of an admitted run remains eligible for publication and build revision

#### Scenario: Native issue numbers pad and grow
- **WHEN** native repositories draft issue 7 and issue 1234 titled Add a gate
- **THEN** the changes are 007-add-a-gate and 1234-add-a-gate, with matching spec/ branches, CLI arguments and repository-scoped PR identities
- **THEN** repositories may independently use the same issue number and native name; this does not bypass existing run-collision refusal

#### Scenario: Manifest selects native naming
- **WHEN** the trusted manifest selects speckit, omits format or explicitly selects openspec, independently of the files present in the repository
- **THEN** only speckit selects native names; omitted/explicit openspec retain exact existing naming and slug assertions, and no repository configuration field is added


### Requirement: Recorded identities remain authoritative

Existing receipts, PR branches and admitted runs SHALL retain their recorded identity. Without a receipt, OpenSpec intake SHALL look up exact qualified and legacy basename candidates within the repository before creating a new draft, and SHALL treat lookup failures as errors rather than absence. This bounded compatibility SHALL NOT rename runs or claim recovery after both receipt loss and title change.

Native recovery SHALL also consider the exact current-title native branch and literal same-number issue-body directory candidates alongside current-title qualified/legacy branches. It SHALL recover existing open PRs before dependency-gated discovery, refuse conflicting open identities and treat every failed required lookup as an error. Recorded identities SHALL override regenerated names and discovery, even after title edits. This extends bounded discovery without changing OpenSpec lookup order or lifecycle gates.

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
- **WHEN** normal lifecycle rules permit a new OpenSpec round after the legacy PR was closed and the issue is rearmed
- **THEN** new work uses the owner-qualified identity, retains replacement bookkeeping and preserves merged-PR suppression and existing recovery authorization

#### Scenario: Native and legacy recovery keep identity
- **WHEN** a native enrollment has a saved proposal under an earlier title or an exact recoverable native or legacy open PR without a receipt
- **THEN** authorized recovery reuses its identity, saved work and PR without redrafting, renaming or consulting blockers merely for delivery
- **THEN** new native rounds after closed PRs retain replacement leases and use native naming or the explicitly selected existing feature; merged PRs still suppress drafting

#### Scenario: Recovery ambiguity blocks new work
- **WHEN** native exact-branch lookup fails or finds incompatible open identities among the bounded candidates
- **THEN** intake retains history and triggers, reports the error and starts no proposal or duplicate PR
