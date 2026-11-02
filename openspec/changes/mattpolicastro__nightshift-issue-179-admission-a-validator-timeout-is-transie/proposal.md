## Why

On 2026-11-01, admission refused a valid one-task change because the OpenSpec command timed out after 120 seconds. The same command completed in two seconds minutes later. Treating that outage as a spec refusal removed `spec:approved` and required human reapproval without any spec change.

Blocked by: #174. This change builds on its source-clone behavior without changing it.

## What Changes

- Add `ValidatorUnavailable(SpecError)` in `specloop/openspec.py` for subprocess timeouts and no-JSON npm network/registry failures carrying `ENOTFOUND`, `ETIMEDOUT`, `ECONNRESET`, `EAI_AGAIN`, or `ECONNREFUSED`.
- Route this exception from admission and drafting validation to the existing version-1 `error` envelope, exit 1, with no `outcome`. Catch it before `SpecError` or broader refusal handlers. Real validation problems and other no-JSON failures retain their current outcomes.
- Extend the exception paragraph in `docs/specloop/command-json.md` and the matching `specloop-interface` requirements; amend `spec-critic`'s `Ordered proposal gates` to exempt validator unavailability from pre-critic correction retries, including scenario-discovery failures. Preserve ordinary lint and feasibility retries and add focused regression tests. One implementation task targets about 150 changed lines.
- Preserve the existing daemon item-failure path: retain the approval/arming trigger for later eligible polls, with `needs-human` only at the existing failure threshold.

Out of scope: in-process retries or backoff, changing validation rules or the 120-second timeout, edits to `specloop/loop.py`, `specloop/evidence.py`, or anything under `nightshift/`, new daemon recovery logic, and changes to unrelated command behavior. Only the validator calls and their exception routing in `specloop/cli.py` and `specloop/propose.py` are in scope.

## Capabilities

### Modified Capabilities

- `specloop-interface`: classify transient validator failures and define their admission/drafting error contract. Explicitly amend `Command JSON` and the general non-success promise in `Proposal source ref JSON`; retain their existing scenarios and all unrelated requirements.
- `spec-critic`: explicitly narrow `Ordered proposal gates`' pre-critic retry guarantee for `ValidatorUnavailable` during validation or scenario discovery. Infrastructure failures cannot be repaired by rewriting the draft; ordinary gate ordering, correction retries, questions, overrides, branches and reviews remain unchanged.

### New Capabilities

None.
