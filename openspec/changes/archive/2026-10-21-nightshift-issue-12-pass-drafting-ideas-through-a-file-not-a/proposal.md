## Why

Nightshift sends issue titles, bodies and trusted comments to `specloop propose` as a single `--idea` argument. Large threads can exceed operating-system argument limits, and text beginning with `-` can be interpreted as an option. Spec-stage PR revisions use the same fragile transport. Specloop already supports reading the idea through `--idea-file`.

## What Changes

- Write each drafting invocation's complete idea to a durable UTF-8 file under its intake job directory and pass an absolute `--idea-file` path.
- Apply this to initial issue drafting and spec-stage PR revision in `nightshift/specloop_intake.py`, for both synchronous test runners and background jobs.
- Preserve idea formatting, trusted-author filtering, receipts, branch arguments, delivery and retry behavior.
- Add `test_long_issue_thread_drafts_via_idea_file` and regressions for option-like text, file lifetime, revision feedback and write failures; update existing argv assertions to inspect file content.
- Explicitly out of scope: changes to the specloop CLI or job launcher, worker intake, admission, build-stage revision, receipt migrations, thread truncation, prompt policy, job cleanup and documentation outside this change. Implementation scope is `nightshift/specloop_intake.py` and tests.

## Capabilities

### Modified Capabilities

- `issue-intake`: add a file-based drafting-input contract covering both issue proposals and spec-stage re-drafting through the intake module.

### New Capabilities

None.
