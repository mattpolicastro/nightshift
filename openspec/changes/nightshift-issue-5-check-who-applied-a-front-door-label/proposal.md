## Why

The specloop front door currently accepts trigger labels regardless of who applied them. Issue-author and feedback-author filtering does not authorize the separate decision to draft, admit, or revise. An older trusted application must not authorize a newer application by somebody outside the repository's trusted actors.

This change is blocked by #12, whose file-based drafting input is already present in this checkout and must remain intact.

## What Changes

- Authorize the actor of the latest current application of `spec:draft`, its `agent:ready` alias, `spec:approved`, and `agent:revise`, including configured label names.
- Use the existing OWNER/MEMBER/COLLABORATOR policy. Resolve the event actor's repository relationship through GitHub when the label-event payload provides identity without association; never substitute the issue author, PR author, or feedback author.
- Explain an unauthorized application once per label event, leave the labels and workflow receipts intact, and skip its action. Reapplication by a trusted actor permits normal processing.
- Preserve pagination, event IDs in receipts, dependency gating, approval-to-head binding, trusted-text filtering, file-based drafting, and delivery recovery for authorized requests.
- Implement in `nightshift/specloop_intake.py` and tests. Add the required `test_label_from_non_collaborator_is_ignored` and `test_label_from_collaborator_is_acted_on` in a new authorization test file.
- Explicitly out of scope: worker-engine authorization, a new configurable actor allowlist, changing prompt trust, cancelling running jobs, changing publication policy, token provisioning, CLI/config schemas, and edits to files listed as work in flight. The association-based option is selected; no new allowlist setting is introduced.

## Capabilities

### Modified Capabilities

- `issue-intake`: add label-event authorization and rejection deduplication; qualify drafting and admission requirements with the authorization prerequisite.
- `pr-revision`: apply the same prerequisite before both revision stages and before empty-feedback handling.

### New Capabilities

None.
