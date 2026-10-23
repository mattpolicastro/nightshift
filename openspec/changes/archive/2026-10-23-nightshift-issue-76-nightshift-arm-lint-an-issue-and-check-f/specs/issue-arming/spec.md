## Purpose

Help operators write executable issues and avoid unchained concurrent file work
before applying the existing Nightshift intake label.

## ADDED Requirements

### Requirement: Issue body lint

The command SHALL read each target body through `gh` and report every applicable
finding using the section, named-test, and finite phrase rules in the design.
It SHALL identify each missing required section separately, require a named test
in a present definition of done, suggest `existing assertions are preserved`
for discouraged test-edit prohibitions, and direct manual acceptance work to
What CI cannot prove.

#### Scenario: Missing sections accumulate
- **WHEN** an issue omits two or more of Scope, What to do, Definition of done, and What CI cannot prove
- **THEN** the report identifies every missing marker separately and continues the other applicable checks

#### Scenario: Named tests are required
- **WHEN** a present definition of done contains only `uv run pytest`, an empty body, or an unrelated quoted sentence, while comparison fixtures name a backticked test identifier, a quoted test identifier, or `test "descriptive title"`
- **THEN** the first fixtures receive a named-test finding and the comparison fixtures satisfy that rule
- **THEN** a test named only outside the definition of done cannot satisfy the rule

#### Scenario: Discouraged phrases have replacements
- **WHEN** a body includes variants of the design's discouraged test-edit prohibitions, including mixed case and plural forms
- **THEN** every occurrence is reported with its line and `existing assertions are preserved` as the suggested wording
- **THEN** the suggested wording itself produces no discouraged-phrase finding

#### Scenario: Human checks stay outside done
- **WHEN** the definition of done contains manually, by hand, or visually, and the same words occur under What CI cannot prove
- **THEN** only occurrences in the definition of done receive manual-check findings with a suggestion to move them
- **THEN** inline section text is checked and section markers inside fenced examples do not satisfy missing sections

### Requirement: Dependency completion lint

The command SHALL parse explicit dependencies with `queue.blockers` and report
direct blockers closed as NOT_PLANNED. Open blockers SHALL remain eligible for
arming and closed COMPLETED blockers SHALL satisfy completion lint. Unknown
closed reasons or failed lookups SHALL be reported as incomplete context.

#### Scenario: Closed incomplete blocker is reported
- **WHEN** an issue names open, closed COMPLETED, and closed NOT_PLANNED blockers using supported marker syntax
- **THEN** only the NOT_PLANNED blocker receives a completion finding naming its issue number
- **THEN** the open blocker does not by itself prevent arming

#### Scenario: Unknown blocker state stays visible
- **WHEN** a blocker lookup fails or a closed blocker has no recognized completion reason
- **THEN** the target receives an incomplete-context finding and other available lint findings remain visible

### Requirement: Concurrent path collection

The command SHALL use `propose.paths` on Scope text with the checkout root and
compare its normalized paths against all open `spec:draft` issues, every other
requested candidate, the `propose.in_flight` map, and front-door PR paths supplied
by `critic.open_pr_files`. It SHALL preserve shared-helper behavior, use exact
path-token matching, paginate GitHub listings, and attribute PRs to source issues
using the design's repository and naming checks.

#### Scenario: All three sources contribute
- **WHEN** an issue's Scope overlaps a queued issue, a running run, a paused run, and an open front-door PR
- **THEN** all sources contribute evidence, including production, documentation, and test paths, and renamed PR paths returned by the helper
- **THEN** repeated evidence for the same path and source issue is combined without dropping its run or PR attribution

#### Scenario: Scope and path rules are preserved
- **WHEN** Scope names existing `README.md`, a new nested file, a function-qualified path, and a wildcard token, with other paths only outside Scope
- **THEN** collection uses the normalized Scope paths returned by `propose.paths`, excludes the other sections, and compares wildcard tokens literally without filesystem expansion

#### Scenario: Pagination identity and self exclusion
- **WHEN** queued issues or PRs span pages and include the target's own evidence, ordinary PRs, fork PRs, and a same-repository front-door PR whose source issue number differs from its PR number
- **THEN** all pages participate, self evidence is excluded, ordinary and fork PRs are excluded, and the front-door PR is attributed to its source issue rather than its PR number

#### Scenario: Unattributed run overlap is visible
- **WHEN** an overlapping running or paused run has no recognized front-door issue identity
- **THEN** the report identifies the path and run, grants no dependency exemption, and requests identification of the source issue without inventing a blocker number

#### Scenario: Partial context retains evidence
- **WHEN** a context request fails, returned PR metadata is missing, or the PR helper returns paths plus limitation notes
- **THEN** the report retains known overlap evidence and all distinct incomplete-context findings, and default mode does not label the affected targets

### Requirement: Dependency aware overlap decisions

The command SHALL exempt an overlap exactly when either distinct source issue
transitively depends on the other through `queue.blockers` edges. It SHALL fetch
unarmed intermediate issues as needed, cache lookups, terminate on cycles, and
report every remaining attributed overlap with its path, evidence, and suggested
`blocked-by: #N` naming the other source issue.

#### Scenario: Unchained overlap suggests a blocker
- **WHEN** two issues share a Scope path without a directed dependency path between them, including when they merely share a prerequisite
- **THEN** the candidate receives an overlap finding naming the file and the other issue with the corresponding `blocked-by: #N` suggestion

#### Scenario: Transitive chains work in both directions
- **WHEN** either overlapping issue reaches the other through one or more blockers, including an unarmed intermediate issue
- **THEN** the overlap produces no finding in either direction of the comparison
- **THEN** unrelated wording or completion findings remain present

#### Scenario: Cycles terminate and failures do not exempt
- **WHEN** traversal encounters a cycle unrelated to the counterpart or an unreadable intermediate issue
- **THEN** traversal terminates, the unproved overlap remains reported, and a lookup failure also produces an incomplete-context finding

#### Scenario: Batch candidates are compared together
- **WHEN** two initially unlabelled requested issues overlap without a chain
- **THEN** both receive overlap findings before any label write and neither is labelled in default mode, regardless of argument order

### Requirement: Arming modes and outcomes

`python -m nightshift.arm` SHALL accept one or more issue numbers for the current
checkout, report all findings deterministically, and apply only `spec:draft` to
eligible targets. It SHALL implement the design's argument, mode, retry,
idempotency, and aggregate exit rules, with no local state writes or unrelated
GitHub mutations.

#### Scenario: Clean issue is armed
- **WHEN** default mode checks an open unlabelled issue with no findings
- **THEN** it adds only `spec:draft`, reports the armed disposition, and exits 0

#### Scenario: Findings prevent default arming
- **WHEN** default mode finds multiple wording or overlap problems on an open issue
- **THEN** it prints every finding, makes no label write for that issue, and exits 1

#### Scenario: Check mode never labels
- **WHEN** check mode receives clean and failing fixtures, including invocations also supplying force
- **THEN** it makes no GitHub mutation, prints all findings, and exits 0 only for an all-clean invocation and 1 otherwise

#### Scenario: Force labels and reports
- **WHEN** force mode checks an open readable issue with lint, overlap, or incomplete-context findings and its label write succeeds
- **THEN** it prints every finding, adds `spec:draft`, reports forced arming, and exits 0

#### Scenario: Existing labels are idempotent
- **WHEN** a target already carries `spec:draft` and is clean or force is selected
- **THEN** it is reported as already armed with no label mutation and contributes success
- **THEN** findings on an already-labelled issue in default or check mode still contribute exit 1 without removing or reapplying the label

#### Scenario: Target and label failures remain failures
- **WHEN** a target is unreadable or closed, or a label write fails and a subsequent label read cannot confirm `spec:draft`, even with force
- **THEN** it is reported as unarmed or unconfirmed, contributes exit 1, and does not stop later targets from being processed
- **THEN** a lost write response followed by confirmed label presence counts as success without a blind repeat write

#### Scenario: Batch results retain failures
- **WHEN** one target is successfully armed while another has findings and is not armed, with duplicate target arguments also supplied
- **THEN** each distinct target is processed once in argument order, the successful label remains, all findings are printed in stable order, and the invocation exits 1

#### Scenario: Invalid arguments fail before reads
- **WHEN** the command receives no issue numbers or an invalid issue argument
- **THEN** it exits 2 with usage guidance before GitHub collection or mutation
