## Context

The daemon (`nightshift/daemon.py`, `queue.py`, `task.py`, `worker.py`) and
specloop (`specloop/`) live in this repository, with separate state
(`~/.nightshift`, `~/.specloop`) and separate services. The daemon is paused by
`~/.nightshift/pause` (the September 30 cutover) and must stay paused until a
human removes that file. Read WORKLOG.md (2026-10-19 and 2026-10-20) and
FACTORY-STATUS.md for how specloop behaves, and the existing daemon tests for
how GitHub is faked (`runner=` injection).

## Goals / Non-Goals

**Goals:** a specloop repository is driven entirely from GitHub: an issue
starts a spec, the PR is where the spec and then the code are reviewed, and
labels move it forward. The daemon never executes tasks for such a repository;
specloop never needs to know about issues.

**Non-Goals:** removing the worker engine; quota pacing; a Paseo page;
multi-machine execution.

## Decisions

### The daemon drives specloop through its CLI, with JSON

The daemon never imports `specloop` internals. A new
`nightshift/specloop_client.py` runs `specloop <command> ... --json` as a
subprocess, through an injectable runner, the same pattern as `queue._gh`, so
tests fake it. Each command prints exactly one JSON document with
`"version": 1`:
- `status --json`: `{"version":1, "supervisor":{"state":...}, "runs":[{"change","status","pause":{"reason","task","detail"}|null,"tasks":{"done","total"},"head","pr"}]}`.
  An unknown change prints `{"version":1,"error":"..."}` and exits 1.
- `propose --json`: `{"version":1,"outcome":"drafted"|"needs_input"|"rejected","branch","commit","questions":[],"contradictions":[],"risks":[],"problems":[]}`.
- `admit --json`: `{"version":1,"outcome":"admitted"|"refused","head","reasons":[],"risks":[]}`.
- `revise --json` and `publish --json`: `{"version":1,"outcome":...,"pr"?}`.

Human-readable output is unchanged without `--json`.

### One repository manifest, used as a template

`admit` and `propose` accept `--change` and `--branch`, overriding the
manifest's `[run]` values. The run directory keeps the overridden copy as its
trusted manifest. Change names for issues are `<repo>-issue-<number>-<slug>`.
`<repo>` is the repository's name (without the owner), so that issue 7 in two
enrolled repositories gets two runs. The slug is the title lowercased, with
runs of other characters replaced by `-`, cut to 40 characters. The branch is
`spec/<change>`.

### One branch and one PR per change

- **Drafting.** The daemon opens the spec as a **draft PR** from `spec/<change>`
  against the repository's base, titled `Spec: <issue title>`. The body links
  the issue (`Closes #<n>`) and lists the feasibility review's risks. The PR is
  labelled `spec:review`.
- **Admission.** `admit --pr <url>` records that PR in the run state, so
  `specloop publish` pushes the branch and keeps using it rather than opening
  another.
- **Merging.** When the code is reviewed, the human merges; `complete()` then
  closes the issue as today.

### Labels

`queue.Labels` gains:
- `spec_draft = "spec:draft"`, on an issue: draft a change from it.
- `spec_review = "spec:review"`, on a PR: set by the daemon when it opens it.
- `spec_approved = "spec:approved"`, on a PR: set by the human to admit.

`agent:ready` on an issue in a specloop repository means the same as
`spec:draft`. `agent:revise` and `needs-human` keep their meanings. On failure
the daemon comments its reason and adds `needs-human`, never silently dropping
an item. This reuses `queue.escalate` where its shape fits.

### Revision has two stages

`agent:revise` on a PR whose change is **not yet admitted** re-runs `propose`
with the review feedback appended to the idea (the spec stage). On an
**admitted** change it runs `specloop revise <change> --feedback <file>` (the
build stage).

`specloop revise` appends a section `## N. Review round K` to tasks.md, with
one task per feedback block (each a review comment, carrying its file and line
when it has them). The appended tasks are committed by the harness, and the run
is resumed. Feedback comes from `queue.pr_feedback()` since the branch tip, and
`queue.has_instructions()` keeps refusing an empty revise.

### Prepare steps

A manifest may declare `[[prepare]]` steps, with the same shape as verify steps.
They run after the candidate is extracted and before the verify steps:
- **under their own sandbox profile,** which is the verify profile without its
  network denial: writes are confined to the build directory and credential
  directories stay unreadable. A file mode alone is no protection against a
  process owned by the same user, which could simply restore write permission;
- **with the build directory as the working directory;**
- **recorded** as receipts alongside the verify steps.

A prepare step that does not meet its expected exit fails verification, like
any verify step. Prepare commands are trusted manifest content. What they
fetch is governed by the candidate's lockfile, which a manifest may freeze.

### Idempotence and recovery

Every daemon action for a specloop repository checks GitHub and specloop state
before acting, so a crash or a repeated poll never duplicates an action:
- **Drafting** looks for an existing PR for `spec/<change>`
  (`queue._latest_pr_number`) before opening one.
- **Approval** checks `specloop status --json` for an existing run before
  admitting.
- **Revision** removes `agent:revise` only after `specloop revise` reports that
  its commit landed.
- **Comments** carry a hidden marker (`<!-- nightshift:<action>:<change> -->`)
  so a repeated action edits rather than duplicates.
- **Failures are not absences.** `queue._latest_pr_number` and `queue.pr_feedback`
  swallow errors today, which would turn a GitHub failure into "no PR" or "no
  feedback" and so into a duplicate PR or a refused revise. The specloop path
  must tell "GitHub could not answer" (skip this pass; `RepoHealth`) from
  "nothing there", without changing how the worker path behaves.

### Spec-stage re-drafts continue the branch

`propose` gains `--from-branch`: the drafting clone starts at the existing spec
branch rather than at the base, and the revised draft is a new commit on top.
Pushes to the PR are then always fast-forward.

### Attention

`nightshift attention` reads `specloop status --json` and adds every run whose
pause needs a human: `stuck`, `blocked`, `needs_human`, `gate_evidence`,
`phase_gate`, `ready_to_publish`, `human_checks`, `coverage`, `acceptance`,
`violation_repeat`, `review_invalid` and `secret`. Each entry carries the
reason, the change and the PR link. If specloop's status cannot be read, the
last snapshot is shown as STALE, as for GitHub failures today. The JSON
contract gains a `specloop` list beside `tasks`.

### The pause file

While `~/.nightshift/pause` exists, the daemon performs no intake, approval,
revision or publication for specloop repositories. The specloop supervisor
keeps running runs that were already admitted, since it has its own off
switch: unloading its launchd job.

### Testing

All GitHub and specloop interaction is through injected runners.
`tests/conftest.py` registers the `scenario` marker, and every test that covers
a scenario carries `@pytest.mark.scenario("<id>")`, using the IDs in tasks.md.
The verify sandbox cannot nest, so a test that exercises sandbox policy is
skipped when `specloop.verify.already_sandboxed()`. The coverage plugin counts
only tests that passed, so every scenario must also have a test that does not
depend on sandbox policy and therefore runs inside verification. For example,
"write to source refused" is a test of read-only permissions, which hold with
or without a sandbox.

## Risks / Trade-offs

- **GitHub's API has bad days.** The daemon's existing retry and `RepoHealth`
  handling covers the new calls, but only if they go through `queue._gh`.
- **The spec PR collects review comments from both stages.** Revision reads
  only feedback newer than the branch tip, so spec-stage comments are not
  re-fed to the build stage.
- **The worker and specloop engines coexist until a real issue has gone
  through the new path.** The engine field keeps them from claiming each
  other's work.
