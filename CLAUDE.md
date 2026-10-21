# nightshift — project instructions

Nightshift turns approved OpenSpec changes into reviewed, verified pull
requests. It has three parts:
- the daemon (`nightshift/`), the GitHub front end;
- the build engine (`specloop/`);
- the Paseo plugin (`plugins/nightshift`).

It runs on macOS under launchd. **Merge is always human.**

Read `docs/specloop/README.md` before engine work, and `KNOWN-LIMITATIONS.md`
for what is open. Each repository has an engine. `specloop` is the spec-driven
flow. `worker`, the default until it is retired, runs headless Claude Code
workers on `agent:ready` issues. Most flags in `worker.py` exist because
something failed without them; their comments say what.

## Engine rules

**Do not change an adapter flag in `specloop/agents/` without reading its module
docstring.** Each one is a sandbox property the harness probed itself. One lesson
from those probes applies everywhere: a model asked to run escape probes skipped
them and reported results anyway. So every capability claim is checked by the
harness, never taken from the agent.

- **Trusted manifests live outside the target repository.** Agents can read
  anything in the checkout. `examples/specloop/` holds a template; real manifests
  belong in a private location.
- **Only collaborators' GitHub text reaches a prompt.** That covers comments,
  reviews and inline comments from OWNER, MEMBER or COLLABORATOR
  (`queue.trusted`). An issue opened by anyone else is escalated, not drafted.
  Keep it that way: on a public repository anyone can comment.
- **State written by newer code must still load in older code.** Loaders ignore
  unknown fields, because a supervisor may still be running yesterday's code.

## Worker engine

The worker engine completed 79 tasks across two repositories by 2026-09-05: 66
shipped as merged PRs and 13 escalated. Recheck before treating those
historical counts as current. GitHub API outages are retried, a repository GitHub
cannot answer for is skipped, and anything unrecognised fails fast, so a 404 is
never retried into silence.

**The daemon keeps a heartbeat** at `~/.nightshift/heartbeat.json`, written
every poll and removed on a clean exit. `nightshift status` reads it, so the
first line tells you `running` / `WEDGED` / `DEAD` / `not running` rather than
reporting the queue and leaving liveness to `ps`. A file whose pid is gone is
the crash signal, and the next start announces it — that is the only way a
restart loop is visible without reading the log.

## The rule that matters

**Do not change a flag in `worker.py` without reading its comment.** Every
non-obvious entry in those allow/deny lists is there because a hand-run failed
without it:

- `--permission-mode acceptEdits` does not cover Bash mutations. Headless has
  no prompt to resolve, so an unlisted command is a hard deny — a worker did a
  full analysis and then could not `git commit` it.
- `Bash(git status:*)` does not match `git -C <path> status`.
- Denying `node` and `/tmp` writes made a worker relocate its scratch script
  into the worktree, where it then could not delete it.
- **`--allowedTools` and the filesystem guard are two different gates.** A path
  outside the session's directories is refused by the second no matter what the
  first says — "may only remove files from the allowed working directories" is
  that guard, and "This command requires approval" is the allow-list. `/tmp`
  therefore needs `--add-dir`, and without it `Bash(rm /tmp/*)` was a
  permission that had never worked. Tell the two messages apart before you edit
  a list.
- `Bash(git rm:*)` / `Bash(git mv:*)` — without them a task whose last step is
  a deletion cannot be finished at all. Three sessions on sample #9 tried eight
  strategies between them and every one was refused; the last escalated, and a
  human ran one command. Only the git forms: they touch tracked files, so every
  removal is recoverable from the index. A bare `rm` outside `/tmp` stays
  denied, and the reviewer gets neither.
- `Bash(git clean:*)` — the untracked half, and the one that actually cost a
  task: sample #23 finished and verified its work, wrote a scratch test inside
  the worktree, and burned its remaining turns failing to remove it. Blast
  radius is the worktree's untracked non-ignored files, which are scratch by
  definition once the real work is committed.
- A dedicated `CLAUDE_CONFIG_DIR` makes every worktree untrusted, and untrusted
  workspaces ignore project `settings.json` entirely. Permissions must be CLI
  flags. This is the daemon's steady state, not an edge case.

## Credentials

`~/.config/nightshift/env` (chmod 600), sourced by `worker._env()`. launchd
reads no shell config, so the daemon sources it explicitly — `.zprofile` covers
your terminal, not the service.

Never set `ANTHROPIC_API_KEY` in a worker environment: it silently outranks the
OAuth token and moves workers onto metered billing. An interactive login in
`~/.claude` does the same thing, which is why workers get their own
`CLAUDE_CONFIG_DIR`.

**Any auth test run on a logged-in machine is a false positive.** Verify with an
isolated `CLAUDE_CONFIG_DIR` and a deliberately bogus token as a control — valid
should succeed, bogus should 401.

## Cost accounting

`total_cost_usd` is **not money**. These runs are on a Max subscription and
consume quota (`rateLimitType: five_hour`). The field is list-price arithmetic
on the token counts, useful only as a consumption proxy — and it prices at
standard rates, ignoring any introductory pricing in effect.

Parse `modelUsage`, not the requested model: a `--model sonnet` run also bills
small auxiliary `claude-haiku-4-5` calls.

`rate_limit_info` arrives on **successful** runs, not just on exhaustion. Read
window state off each run rather than modelling consumption.

## Guardrails that are not negotiable

- Workers never push and never merge. The harness pushes; a human merges.
- The reviewer is a separate session with no shared context, and is read-only
  at the tool level rather than by instruction.
- A missing `VERDICT:` line is not a pass — a reviewer that ran out of turns
  rendered no verdict.
- A run that hit `--max-turns` is a failed task, not a partial success.
- A commit without a green verify run is a failed task. "I could not verify" is
  a valid outcome; committing and hoping is not.
