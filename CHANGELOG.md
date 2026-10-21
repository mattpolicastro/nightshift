# Changelog

## 2026-10-21 — specloop, the GitHub front door and the Paseo Factory section

Nightshift now turns approved OpenSpec changes into reviewed, verified pull
requests. Earlier development history and operational records remain private.

- **specloop, the build engine.** It works through a change's tasks one at a
  time: a fresh agent implements each task, the harness seals the commit,
  trusted commands verify it under `sandbox-exec` with no network, and an
  independent reviewer applies limited blocking rules. Failures retry with the
  findings. Providers are pluggable: Codex, Claude Code, or a local model
  through an Anthropic-compatible endpoint.
- **Evidence gates.** At section boundaries specloop checks scenario coverage
  and runs language-agnostic mutation testing. A shortfall adds
  strengthening tasks.
- **Fewer human stops.**
  - An agent can review a whole section at a phase gate.
  - A stuck task gets one arbiter ruling: accept, retry with a note, or ask a
    human.
  - `specloop pause` stops at a task boundary.
- **The GitHub front door.** Per-repository `engine = "specloop"`. Issues
  labelled `spec:draft` are drafted into spec PRs, `spec:approved` admits
  them, the build publishes to the same PR, and `agent:revise` adds a review
  round. Only collaborators' comments reach a prompt, and an approval covers
  only the head it was applied to.
- **The Paseo Factory section.** The Nightshift page lists specloop runs with
  one button per action that fits each pause, through a narrow
  `specloop act`.
- **Hardening.**
  - Prepare steps read `$HOME` through an allowlist and leave no background
    processes.
  - Unsafe change names are refused.
  - State loaders tolerate newer fields.
  - The supervisor polls its drivers.
- The `worker` engine is unchanged and remains the default.

## Initial public release — 2026-09-08

Published from a sanitized source snapshot. It includes worktree-based task
execution, independent review, verification and push guards, recovery,
endpoint routing, durable outcomes, and a Paseo attention plugin with bounded
refreshes. Merge remains a human decision.
