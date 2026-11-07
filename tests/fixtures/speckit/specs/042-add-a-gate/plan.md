# Plan: Add a gate

## Approach

Run the configured check before permitting a change. Share its result with the
refusal reporter and rerun it when a repaired change is retried.

## Human checks

- [ ] Try a failing check and confirm the refusal names the check clearly.
- [ ] Repair the change and confirm a retry displays the new result.
