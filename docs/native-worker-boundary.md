# Native worker boundary decision

Status: accepted direction, 2026-09-15. Native daemon activation remains disabled.

## Decision

Stop expanding Nightshift into a general agent runtime. Use Codex app-server as
the ChatGPT-authenticated agent and protocol boundary, and delegate command
confinement to Codex's supported permission/environment interface plus a thin,
qualified container adapter where credential separation requires it.

Nightshift should own the overnight workflow:

- exact issue, repository, base and candidate identity;
- exclusive task ownership and durable interrupted-attempt classification;
- subscription-only provider admission with no API-key/custom fallback;
- verification and independent review bound to the same candidate;
- explicit authority to publish, merge or remove retained work; and
- privacy-safe outcomes and observed accounting.

It should not independently implement a reusable shell runtime, agent conversation
framework, model catalog, container platform or general configuration language.

## Why

The current native branch proved the hard boundaries, but it also accumulated
parallel prototypes for container execution, candidate coordination, review,
dispatch and configuration. Its 1,489 collected tests include valuable Nightshift
policy regressions and many tests required only because the branch owns generic
runtime machinery.

Codex app-server already provides the thread/turn protocol, typed lifecycle events,
auth endpoints, approvals, permission profiles, command time/output limits and
explicit execution environments. Codex exec-server owns subprocess spawning and
control for remote environments. Nightshift should test its use of those contracts,
not reproduce their implementation. See the official
[app-server protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md),
[exec-server interface](https://github.com/openai/codex/blob/main/codex-rs/exec-server/README.md),
and [Codex security guidance](https://learn.chatgpt.com/docs/agent-approvals-security).

Docker remains the narrow containment mechanism when host authentication must stay
outside the worker. Nightshift must still select and inspect mounts, environment,
networking, limits and cleanup, but should rely on Docker for namespaces, cgroups,
read-only mounts and disabled external networking. Docker documents that the
[`none` network driver](https://docs.docker.com/engine/network/drivers/none/) leaves
only loopback and that bind-mount policy belongs to the container creator.

[SWE-ReX](https://github.com/SWE-agent/SWE-ReX) is the preferred alternative if a
maintained deployment/runtime API becomes necessary. It is narrower than OpenHands,
but its standard Docker deployment uses an authenticated HTTP runtime and its
cleanup result still needs independent qualification for Nightshift's retention
policy. [OpenHands](https://github.com/OpenHands/software-agent-sdk) supplies agents,
conversations, tools and local/remote workspaces; adopting it would replace much
more of the product and does not by itself establish ChatGPT subscription behavior.

## Consolidation before activation

1. Freeze new native infrastructure and preserve the current branch as working
   evidence.
2. Choose one canonical lifecycle from `native_task_lane`, `stable_controller` and
   `stable_pipeline`; remove `candidate_pipeline` and other superseded coordinator
   paths once their unique behavior is accounted for.
3. Keep one reviewer adapter. Fold or remove the unused `reviewer`/`stable_reviewer`
   variant while preserving immutable exact-candidate review.
4. Keep one container boundary. Remove superseded `container_exec`, snapshot and
   session prototypes after the canonical Codex environment path passes the same
   isolation contract.
5. Consolidate loader/profile/dispatch validation around one private configuration
   boundary. Replace exhaustive repeated field matrices with representative parser
   tests and end-to-end contract tests.
6. Reduce native accounting to observed usage, unknown/incomplete evidence,
   identity binding, conflict atomicity and separation from legacy scheduling.
7. Run one approved sandbox issue through implement, verify, independent review and
   PR creation. Humans retain merge authority.

The target validation pyramid is product-rule unit tests, roughly 10–15 real-Git
lifecycle tests, two opt-in runtime checks per backend, and the single sandbox
issue-to-PR demonstration. Isolation and retention tests are removed only with the
code they protect, never merely to lower the count.
