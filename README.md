# Nightshift

[![ci](https://github.com/mattpolicastro/nightshift/actions/workflows/ci.yml/badge.svg)](https://github.com/mattpolicastro/nightshift/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A quota-aware daemon that runs headless Claude Code workers against a
GitHub-Issues queue on a machine you own. Each task gets an isolated Git
worktree, implementation, verification, independent review, and a pull request.
**Humans decide what enters the queue and what gets merged.**

Nightshift is a personal automation system, published as a starting point for
others. Read [known limitations](BACKLOG.md) before running unattended.

## Design

- Label an approved issue `agent:ready` to admit it to the queue.
- Workers implement in worktrees and run a repository-specific verify command.
- A separate reviewer session examines the result with a read-only tool contract.
- The harness pushes the branch and opens a PR; workers never merge.
- Failed verification, missing verdicts and ambiguous tasks escalate to a human.
- A narrowly scoped GitHub credential without Workflows permission keeps agents
  from pushing changes to the CI workflow that judges their work.
- Durable claims, heartbeats and outcomes support recovery and attention tracking.

These controls reduce risk; they are not a security sandbox. Enroll trusted
repositories, review queued issue content and keep human review at merge time.

## Worker and model support

Implementation and review are separate Claude Code sessions and can use
separate models or endpoints. Anthropic is the production default. Ollama
routing is implemented: a sandbox task completed GLM-4.7 Flash implementation,
independent Opus review, PR creation and green GitHub CI on 2026-09-08. OpenAI-compatible endpoints
require an operator-supplied translating proxy and remain unvalidated end to
end. Native Codex workers and automatic model selection are not implemented.

See [provider setup and support status](docs/providers.md) and
[validation evidence](docs/validation.md). Planning is task preparation, not a
separate model-backed execution phase in the current daemon.

## Setup

Requires Python 3.12+, uv, Git, GitHub CLI, Claude Code, and the tools used by
your repositories' verify commands. The included service launcher targets macOS.

```sh
git clone https://github.com/mattpolicastro/nightshift.git
cd nightshift
uv sync
cp config.example.toml config.toml
```

Edit `config.toml` with your repository names and verification commands. Start
with a sandbox repository. Set `forbidden_probe` to a private repository your
worker credential must not access; a public repository cannot prove isolation.

Create `~/.config/nightshift/env` with mode 600. Keep credentials out of this
checkout. Configure `CLAUDE_CODE_OAUTH_TOKEN` and `GH_TOKEN`; optionally set
`NIGHTSHIFT_SLACK_WEBHOOK` for alerts. Never set `ANTHROPIC_API_KEY` in the worker
environment when you intend to use subscription authentication.

Use a fine-grained GitHub token limited to enrolled repositories. Allow the
repository operations needed for contents, issues and pull requests, but do
**not** grant Workflows permission. Use preflight to verify the effective scope.

```sh
uv run nightshift --help
uv run nightshift preflight
uv run nightshift status
uv run nightshift run
```

For unattended macOS operation, edit every `YOUR_USERNAME` path in
`launchd/com.mattpolicastro.nightshift.plist`, verify the launcher's PATH and
required binaries, and install the plist in `~/Library/LaunchAgents`. The
launcher exits instead of starting when required credentials or tools are absent.
Do not start a second daemon alongside an existing service.

## Operations

Use GitHub labels `agent:ready`, `agent:working`, `agent:done` and `needs-human`
to follow queue state. Review escalations before rearming them. Nightshift keeps
its local runtime state under `~/.nightshift`; do not publish that directory.

```sh
uv run nightshift status
uv run nightshift outcomes
uv run nightshift attention
uv run nightshift attention --json
```

[Endpoint routing](SPEC-endpoints.md) supports explicitly configured model
hosts. [Task outcomes](SPEC-paseo-outcomes.md) feed the optional
[Paseo attention plugin](plugins/nightshift/README.md), which displays outstanding
work and links to GitHub without adding merge or arbitrary-command operations.

## Development

```sh
uv sync
uv run pytest -q
cd plugins/nightshift
npm ci --ignore-scripts
npm run typecheck
npm test
```

CI runs Python tests and plugin typechecking/tests. Agent contributors should
read [CLAUDE.md](CLAUDE.md). See [WORKLOG.md](WORKLOG.md) for the public changelog.

This repository starts from a sanitized source snapshot; private development
history, operational notes and original worker transcripts are not included.
The retained transcript fixtures are redacted test data.

MIT licensed. Copyright Matt Policastro.
