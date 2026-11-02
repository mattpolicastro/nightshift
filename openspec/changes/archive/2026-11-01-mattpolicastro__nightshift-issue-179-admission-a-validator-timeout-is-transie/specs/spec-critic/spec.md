## Purpose

Keep proposal review gates ordered while distinguishing validator unavailability from draft defects that existing correction rounds can repair.

## MODIFIED Requirements

### Requirement: Ordered proposal gates

`propose` SHALL invoke the critic only after lint and feasibility pass for the candidate. It SHALL preserve existing pre-critic retries, question handling, manifest overrides, and branch behavior, except that `ValidatorUnavailable` from OpenSpec validation or scenario discovery during lint SHALL propagate immediately through the Command JSON exception contract without becoming lint problems or drafting correction feedback. This exception SHALL apply to initial drafts and spec-stage revisions, including lint during a critic correction. Ordinary lint problems, plain `SpecError` scenario-discovery failures and feasibility findings SHALL retain their existing correction behavior and round limits. Admission and the build loop SHALL retain their existing reviews without acquiring the proposal critic.

#### Scenario: Clean draft receives the extra review
- **WHEN** an initial draft or a spec-stage revision passes lint and feasibility without contradictions
- **THEN** the next review is the critic against that candidate, before the proposal reports success
- **THEN** an empty critic result permits the usual successful result on the effective branch, retaining existing feasibility risks

#### Scenario: Earlier gate prevents critic
- **WHEN** drafting requests human input, ordinary lint fails without `ValidatorUnavailable`, or feasibility fails or reports contradictions
- **THEN** the current attempt makes no critic call or PR-file lookup and follows its existing questions, problems, or feasibility-feedback behavior

#### Scenario: Validator unavailability bypasses correction
- **WHEN** OpenSpec validation or scenario discovery raises `ValidatorUnavailable` during proposal lint, even with drafting rounds remaining
- **THEN** the exception exits the attempt without becoming correction feedback or starting another drafting round, feasibility review, PR-file lookup, critic review or proposal publication for that candidate
- **THEN** `propose --json` exits 1 with exactly one version-1 document containing a nonempty `error` and no `outcome`, as specified by Command JSON

#### Scenario: Ordinary scenario lookup failure retains correction
- **WHEN** scenario discovery raises plain `SpecError` during pre-critic lint and another ordinary drafting attempt remains
- **THEN** the failure remains a lint problem passed as correction feedback to the next drafting attempt under the existing round limit
- **THEN** the failed candidate receives no feasibility review, PR-file lookup or critic review, and subsequent candidates must pass the existing ordered gates

### Requirement: Single critic correction

Blocking critic findings SHALL occupy `Draft.contradictions` and feed all task/problem/evidence fields to at most one subsequent drafting attempt within the existing `rounds` limit. That correction SHALL pass lint, feasibility and a second critic review before success. A failed or unchanged correction SHALL never clear unresolved findings or trigger another critic correction. As an explicit exception to returning a failed correction's problems, contradictions or questions, `ValidatorUnavailable` from OpenSpec validation or scenario discovery during correction lint SHALL propagate immediately through the Command JSON exception contract: `propose --json` SHALL exit 1 with exactly `{"version": 1, "error": "..."}`, a nonempty diagnostic and no `outcome` or draft-result fields. This exception SHALL NOT count as resolving the critic findings, publish a proposal, or start another correction in the same invocation. Ordinary lint failures, plain `SpecError` scenario-discovery failures, feasibility failures, questions and drafting-session failures SHALL retain their existing result and correction limits.

#### Scenario: Blocking finding triggers one redraft
- **WHEN** the first critic returns blocking findings, another drafting attempt remains, and the corrected candidate passes lint, feasibility and the critic
- **THEN** every finding reaches the next drafting prompt, exactly one correction write occurs, and the critic reviews the new commit once
- **THEN** the draft succeeds with no contradictions, the corrected commit, and only that candidate's review notes

#### Scenario: Persistent findings reject
- **WHEN** the critic still returns contradictions after the one correction, even with general drafting attempts remaining
- **THEN** those contradictions remain in the result, no third draft or critic correction occurs, and the existing CLI reports rejection rather than publishing a successful proposal

#### Scenario: No remaining attempt rejects
- **WHEN** the first critic finds a blocker on the last allowed drafting attempt
- **THEN** its contradictions remain in the rejected result and the total number of writes does not exceed `rounds`

#### Scenario: Unchanged correction keeps findings
- **WHEN** the drafter makes no change in response to critic feedback
- **THEN** the previous commit and blocking findings remain, no duplicate feasibility or critic review runs, and the proposal is rejected

#### Scenario: Correction fails an earlier gate
- **WHEN** the one critic correction fails lint or feasibility without `ValidatorUnavailable`, asks questions, or its drafting session fails
- **THEN** the corresponding existing problems, contradictions, or questions are returned and no further automatic correction is attempted
- **THEN** no critic call occurs for that unsuccessful correction
- **THEN** a plain `SpecError` from scenario discovery remains a lint problem with the prior unresolved critic findings in the rejected result

#### Scenario: Validator unavailable during critic correction
- **WHEN** a first candidate receives blocking critic findings and OpenSpec validation or scenario discovery raises `ValidatorUnavailable` during lint of its one correction, even with general drafting attempts remaining
- **THEN** `propose --json` exits 1 with exactly one version-1 document containing only `version` and a nonempty `error`, instead of returning the correction's problems or prior critic findings as a domain outcome
- **THEN** no further drafting attempt, feasibility review, PR-file lookup, critic review or proposal publication occurs in that invocation
- **THEN** the outage does not resolve the findings; existing critic artifacts remain available for diagnosis, without adding persistence or cross-poll correction recovery
