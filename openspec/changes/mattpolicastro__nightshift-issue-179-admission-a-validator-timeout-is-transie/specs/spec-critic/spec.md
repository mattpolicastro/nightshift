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
