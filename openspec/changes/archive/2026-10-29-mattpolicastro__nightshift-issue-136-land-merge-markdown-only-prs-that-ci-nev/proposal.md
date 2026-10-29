## Why

A Markdown-only PR such as #114 can be intentionally excluded by CI's `paths-ignore: ['**.md']`. No checks register, but #89 correctly treats that absence as pending, so `land` waits until its deadline. A proven workflow exclusion should satisfy the CI gate without pretending that checks passed.

Depends on #130, whose landing forge boundary is present in this checkout.

## What Changes

- On the first validated empty-check response for a head, read the complete PR file list and CI workflow from the recorded base revision before sleeping for CI. Require every changed path to match a supported `paths-ignore` glob.
- Allow a proven excluded PR through the CI gate only after a second empty observation at least one existing 10-second poll interval later. There is no separate grace timer in current code; this change defines that narrow grace using the existing cadence, within the unchanged shared deadline.
- Any observed registered check disables the exception for that head. Existing CI classification, head validation, base updates, three-round limit, human checks and protected merge remain authoritative.
- Print `CI not required: docs-only` and record a populated CI attestation for the exact head and base, including when no human checklist exists.
- Document supported workflow syntax, conservative fallback, attestation and the pending real Markdown-only landing check.

Implementation scope is `nightshift/land.py`, `docs/landing.md`, landing tests and these spec deltas. No workflow edits, new dependencies or flags, generic CI bypass, daemon/Factory eligibility changes, deployment, or changes to human checklist parsing are included. General YAML/Actions interpretation, multiple-workflow exemptions and Forgejo workflow discovery are out of scope; unsupported configurations retain normal CI waiting. Concurrently owned files are explicitly excluded in the design and tasks.

## Capabilities

### Modified Capabilities

- `reviewed-pr-landing`: add base-derived exclusion evidence and a bounded, attested no-CI outcome; narrow the existing unconditional passing-check and missing-registration requirements only for proven exclusions.
- `gitea-landing`: use the same exclusion policy through landing-specific factual reads and the human credential; preserve exact-head status validation and qualify the no-additional-calls compatibility promise only for the new empty-check path.

All other requirements remain in force. No capability is removed.
