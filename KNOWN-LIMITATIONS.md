# Known limitations

Open gaps and queued follow-ups for Nightshift and its specloop engine. Fixes land
through Nightshift's own front door: each item is meant to become an issue.

## Queued follow-ups

- **Sync at task boundaries:** pick up fast-forward human commits on the source
  branch without pausing.
- **Retire the worker engine,** once one real issue has gone through the
  specloop front door end to end. Then rename specloop's CLI and state into
  Nightshift's, so one name remains.
- **Parallel mutants:** the evidence gate runs its mutants one after another,
  each a full verify. At about a minute per mutant this is a modest win.

## Open gaps

- **Who applied a label is not checked.** On GitHub only collaborators can
  label, so this matters only if collaborators are untrusted.
- **Revision loop:**
  - Feedback is anything newer than the branch tip's commit time, so comments
    posted during a long revision session are skipped, and re-labelling before
    the next publish re-queues the last round's comments.
  - A build-stage revise while the supervisor holds the lock escalates
    (`refused`) instead of retrying.
  - A revise job that crashes after committing appends a duplicate round.
- **Kill switch:** jobs already running finish, including an admission, while
  paused. Jobs are their own sessions, so unloading the daemon does not stop
  them, and there is no command to stop one.
- **Background jobs:**
  - No deadline and no status line for a hung job.
  - Job directories are never cleaned, and their `request.json` keeps the full
    idea text.
  - Daemon git timeouts kill only the direct child, not its process group.
- **Front end:**
  - One unreadable run makes `specloop status --json` fail for every repo. For
    example, `finish` archives before pushing, so a failed push leaves
    `human_checks` with an archived `tasks.md`.
  - `HUMAN_REASONS` omits an infra pause whose automatic resumes ran out, and
    `requested`.
  - `--idea` travels in argv. `--idea-file` exists.
- **Config:**
  - An invalid specloop manifest stops `config.load`, and the worker repos
    with it.
  - Switching a repo from worker to specloop leaves `agent:working` issues
    unrecovered.
  - Change names drop the repo owner.
- **specloop CLI:**
  - `publish` takes no run lock.
  - The `propose` JSON `branch` field ignores its fallback ref.
  - A backticked `a/b/c` in review feedback parses as a scenario ID.
  - The implementer recipe and feasibility review don't show prepare steps.
  - argparse errors print no JSON.

## Other known gaps

- Agents can read credential files; diffs containing their literal values are
  refused.
- Quota is handled by detecting a usage-limit error, not read ahead of time.

## Platform assumptions

- **macOS only.** Verification runs under `sandbox-exec`, and the services are
  launchd agents.
- **Homebrew paths.** The verify sandbox's `PATH` starts at `/opt/homebrew/bin`,
  and the Paseo plugin runs `/opt/homebrew/bin/uv`.
- **The Node toolchain is expected under `~/n/bin`.** The `n` version manager
  puts it there. It's used for the OpenSpec CLI through `npx`, and by the
  plugin's `PATH`.
- **Fixed locations.** The Paseo plugin expects the Nightshift checkout at
  `~/Projects/nightshift`, and worker worktrees default to
  `~/Projects/nightshift-wt`.
- **The attention page and Factory section need a Paseo host.**
