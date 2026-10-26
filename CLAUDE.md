# nightshift — project instructions

Nightshift turns approved OpenSpec changes into reviewed, verified pull
requests. It has three parts:
- the daemon (`nightshift/`), the GitHub front end;
- the build engine (`specloop/`);
- the Paseo plugin (`plugins/nightshift`).

It runs on macOS under launchd. **Merge is always human.**

Read `docs/specloop/README.md` before engine work, and `KNOWN-LIMITATIONS.md`
for what is open. Every enrolled repository runs the `specloop` engine.
Since #40 one CLI drives both halves: `nightshift <verb> <change>` for builds
and `nightshift daemon status|pause|resume` for the front door; bare
`nightshift run` still starts the daemon for the frozen wrapper, and
`specloop` is a one-release alias. Build state lives in `~/.nightshift`
(`runs/`, `drafts/`, `homes/`); `~/.specloop` keeps compatibility links. The
original `worker` engine, which ran headless Claude Code sessions on
`agent:ready` issues, was retired in #16 (2026-10-25); its history lives in the
vault, not here.

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
- **Behaviour other work depends on belongs in `openspec/specs/`.** A direct PR
  (one not built through the front door) that adds or changes such behaviour
  updates the matching requirement in the same PR. The drafter and the
  feasibility review read those requirements. A rule that lives only in code
  gets broken by the next spec: spec PR #26 hid the action results that PR #23
  had just made visible.
- **New type errors fail CI.** `uv run python scripts/check_types.py` runs mypy
  against `mypy-baseline.txt`; fix an existing error and run it with `--update`
  to shrink the baseline, but never grow it by hand.
- **State written by newer code must still load in older code.** Loaders ignore
  unknown fields, because a supervisor may still be running yesterday's code.
- **Claude Code permissions have two gates.** `--allowedTools` is the allow-list
  ("This command requires approval"). The filesystem guard refuses paths outside
  the session's directories whatever the list says ("may only remove files from
  the allowed working directories"). A dedicated `CLAUDE_CONFIG_DIR` makes every
  workspace untrusted, and untrusted workspaces ignore project `settings.json`,
  so permissions must be CLI flags. Tell the two refusals apart before editing
  `specloop/agents/claude.py`.

## Daemon

GitHub API outages are retried, a repository GitHub cannot answer for is
skipped, and anything unrecognised fails fast, so a 404 is never retried into
silence.

**The daemon keeps a heartbeat** at `~/.nightshift/heartbeat.json`, written
every poll and removed on a clean exit. `nightshift daemon status` reads it, so the
first line tells you `running` / `WEDGED` / `DEAD` / `not running` rather than
reporting the queue and leaving liveness to `ps`. A file whose pid is gone is
the crash signal, and the next start announces it — that is the only way a
restart loop is visible without reading the log.

## Credentials

`~/.config/nightshift/env` (chmod 600), sourced by `bin/nightshift-daemon`.
launchd reads no shell config, so the wrapper sources it explicitly —
`.zprofile` covers your terminal, not the service.

Never set `ANTHROPIC_API_KEY` in an agent environment: it silently outranks the
OAuth token and moves sessions onto metered billing. An interactive login in
`~/.claude` does the same thing, which is why agent sessions get their own
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

- Agents never push and never merge. The harness pushes; a human merges.
- The reviewer is a separate session with no shared context, and is read-only
  at the tool level rather than by instruction.
- A review with no verdict is not a pass — a reviewer that ran out of turns
  rendered none.
- A session that hit its turn or time limit is a failed attempt, not a partial
  success.
- A commit without a green verify run is a failed task. "I could not verify" is
  a valid outcome; committing and hoping is not.
