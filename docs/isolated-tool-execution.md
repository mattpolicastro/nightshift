# Fallback isolation design

Status: proposed; no runtime enabled by this document.

The installed legacy Codex sandbox schema cannot express restricted reads.
Named profiles remain a candidate, but their actual model-tool enforcement
has not been qualified. A container around the entire Codex runtime would
still put provider authentication in the same environment as model tools;
that alone does not satisfy Nightshift's credential boundary.

If named-profile qualification cannot establish that boundary, separate the
provider client from the tool executor:

- A host controller owns provider authentication, request budgets, model calls,
  transcripts, Git operations and task/review state. It never runs model-selected
  code on the host.
- A disposable executor receives only a source snapshot and explicit tool
  arguments. It receives no provider or GitHub key, host home, Git control data,
  container socket or host process namespace.
- Tool execution has network disabled, a non-root identity, dropped capabilities,
  read-only base filesystem, bounded scratch space and CPU/memory/process limits.
  A reviewer receives a read-only source snapshot.
- The host controls dependency preparation. Workers cannot opt into network,
  extra mounts, alternate images, broader resource limits or package downloads.
- The host imports permitted candidate changes after rejecting special files,
  path/symlink escapes and policy violations. It owns commit and exact-SHA
  verification. Cancellation destroys the executor, including detached children.

This requires a qualified external-tool interface for Codex; none has been
established by the current spike. Do not expose a general credential proxy to
worker commands to make a whole-runtime container work.

A direct Responses adapter is an alternative already identified in the native
worker spec. Its application-owned function-call loop allows the provider
client and code executor to be separate components. The API returns tool calls;
the application executes them and returns outputs. [Official function-calling
documentation](https://developers.openai.com/api/docs/guides/function-calling).
That route adds responsibility for context management, tool schemas, patch
application, completion, retries and accounting; it is a separate driver, not
an alias for `codex-app-server`.

## Next acceptance experiment

Use an existing local container runtime and an explicitly pinned executor image,
with synthetic data only. No provider request is needed to test this boundary.
Prove normal source edits, denied outside/Git/credential access, denied network,
reviewer immutability, bounded output, and cleanup of detached child processes.
Run both successful and failing controls; record image digest, effective mounts,
user, capabilities, network mode and resource limits. Mere flag acceptance does
not pass qualification. A working container service does not qualify the image
or executor policy.

Only after those checks pass should implementation select the qualified Codex
external-tool path or the separate Responses driver. Existing Claude/Ollama
routing remains unchanged throughout this work.
