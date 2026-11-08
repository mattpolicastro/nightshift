## Why

#189 supplies native drafting, and the build adapter already supports native tasks and revision replay. The daemon front door still generates OpenSpec identities and reads OpenSpec paths during readiness. Native names also need attribution in arming and touchpoint recovery. This change completes that integration for #190, part of #184, against main `5a8eaf69` with #189 included.

## What Changes

- Select native naming from the trusted manifest: issue 7 becomes `007-add-a-gate`, issue 1234 becomes `1234-add-a-gate`, and the branch remains `spec/<change>`. Preserve recorded identities and reuse an explicitly named matching existing feature.
- Resolve the drafting base once using the existing upstream-first rule, discover existing features at that commit, and pass the exact commit to drafting. Qualify the existing **Draft base** requirement with a MODIFIED delta; unpinned calls retain their behavior.
- Put native draft workspaces at `<state_root>/drafts/@speckit/<owner>__<repo>/<change>`, using the trusted manifest's repository. Assert that `SAFE_CHANGE` rejects `@speckit`; keep OpenSpec workspaces flat and test cross-format replacement isolation.
- Resolve native human-check citations through `speckit.parse_spec` at the run head, obtain tasks and sections through the selected adapter, and identify the feature directory in the spec PR body.
- Extend native identity attribution in `nightshift/arm.py` and receipt-name fallback in `nightshift/touchpoints.py`. Verify native revision numbering and keyed replay already implemented by the adapter.
- Update enrollment/front-door documentation and add native tests without changing existing OpenSpec assertions or #182's protected fixtures.

Out of scope: Paseo and `plugins/nightshift/`; build-loop, admission, publication and state-storage redesign; new receipt/state kinds or configuration fields; format auto-detection; unifying the two OpenSpec slug functions; template or parser rewrites. The specifically authorized engine exceptions are draft-path selection and pinned-base plumbing in `propose.py`/`cli.py`, and revision numbering/reconstruction only if the existing adapter delegation needs repair. No draft-workspace lock, wrapper handoff or native run namespacing migration is included. Existing one-job-per-item receipts and stopped-job holds govern same-issue daemon concurrency; direct CLI callers sharing a repository/change still require operator coordination.

## Capabilities

Modified capabilities:

- `issue-intake`: format-selected identities, existing-feature discovery, recovery and native spec PRs.
- `spec-format`: qualify optional pinned-base compatibility while retaining ordinary OpenSpec behavior.
- `spec-drafting`: repository-qualified native draft workspaces and the explicit pinned-base exception to Draft base.
- `issue-arming`: native source-issue attribution for PRs, runs and overlap exemptions.
- `touchpoint-report`: native producer-name recovery without proposal history.
- `merge-readiness`: committed native scenarios and adapter-parsed task sections, preserving #182's evidence policy.
- `pr-revision`: native front-door rounds and replay regression coverage.
- `speckit-format`: permit the requested front-door integration while retaining pure parser contracts.
- `specloop-documentation`: native enrollment, lifecycle, arming identities and scope roots, collision/recovery boundaries and live verification.
- `speckit-preset`: correct the README's pending-front-door claim without changing authoring assets.

No new capability or durable state kind is introduced.
