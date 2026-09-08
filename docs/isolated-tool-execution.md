# Fallback isolation design

Status: isolated-execution prototypes and synthetic qualification tests implemented;
native task execution remains disabled.

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

The pinned runtime supports an experimental external execution service.
`environments.toml` selects a program launched over stdio with `include_local=false`;
that program can be a network-disabled Docker executor running `codex exec-server`.
Synthetic model responses have exercised remote commands, patches and interactive
input without host fallback. Complete tool coverage remains a release gate.
Do not expose a general credential proxy to worker commands.

A direct Responses adapter is an alternative already identified in the native
worker spec. Its application-owned function-call loop allows the provider
client and code executor to be separate components. The API returns tool calls;
the application executes them and returns outputs. [Official function-calling
documentation](https://developers.openai.com/api/docs/guides/function-calling).
That route adds responsibility for context management, tool schemas, patch
application, completion, retries and accounting; it is a separate driver, not
an alias for `codex-app-server`.

## Whole-process macOS sandbox alternative

A synthetic macOS outer-sandbox probe denied targeted outside/symlink reads,
Git-control writes, and a parent-process inspection attempt while permitting a
normal worktree write. It did not qualify a complete filesystem allowlist or
all process-inspection paths. Applying Codex's nested read-only sandbox beneath
that outer sandbox failed before command execution. This construction therefore
cannot yet separate provider network access from tool network access, and a
shared readable authentication store would remain exposed to tool descendants.
The whole-process sandbox alternative remains unqualified; prefer independently
isolated tool execution rather than treating those partial denials as a release
gate pass.

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

## Synthetic reviewer and network evidence

A separate offline probe used local image
`sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b`
as UID/GID 1000, with network disabled, a read-only root filesystem, a dedicated
synthetic source directory mounted read-only, and separate writable scratch.
All 12 checks passed: source reads and scratch writes succeeded; overwrite,
unlink, rename, chmod, and source hardlink attempts failed with a read-only
filesystem error. A hardlink into scratch failed across filesystems, and a
symlink to an unmounted synthetic host sentinel could not be read. Source and
host-sentinel hashes matched before and after the probe.

Only loopback interfaces were present, the route table was empty, and an
outbound connection to a numeric test address failed with `Network unreachable`.
A loopback listener succeeded as a positive control. The container was removed
afterward. No provider request or real repository content was used.

This qualifies these synthetic reviewer/network primitives for the tested image
and policy only. Safe snapshot transfer, complete executor lifecycle and output
bounds, model-tool integration, and exact-candidate review/verification remain
separate acceptance gates. The test's read-only bind mount is evidence for
reviewer immutability, not authorization to mount host source or credentials in
a production executor.

## Remaining native skills-reader gate

The synthetic remote-executor fixture still advertises `skills.list` and
`skills.read` after optional integrations, skill-search/discovery flags, and
candidate per-tool disable settings are switched off. Configuration acceptance
is not proof of removal; the captured provider inventory remains authoritative.
Actual namespace calls returned empty registries and rejected forged absolute,
file-URL, and traversal package identifiers targeting synthetic host data.
Those negative results did not establish a successful registered-package read,
so they do not qualify the complete reader boundary. A corrected remote-seeding probe wrote both workspace and executor-home skill
fixtures through the container executor and verified their contents before
thread creation. Both skill registries still returned empty results. Filesystem
presence therefore did not establish namespace registration, and those empty
results are not credited as a successful registered-package control. Before native execution is enabled, a reproducible test
must register and read an executor-owned synthetic skill, reject resources
outside that package and host-data access, and establish authority routing;
alternatively, demonstrate that the reader tools are actually absent. The
strict tool-inventory gate remains in force. No real account diagnostics,
credentials, or provider calls were used for these tests.


## Implemented transfer and lifecycle primitives

`workers/container_exec.py` runs an absolute argv in a uniquely owned disposable
container. It requires an immutable local image ID, rejects unexpected effective
mounts/environment/policy before start, bounds output and elapsed execution,
and removes the owned container on success, failure, timeout or cancellation.
Real synthetic checks covered successful output, a timeout, an output flood and
a detached child. This module runs an empty per-command workspace; it is not yet
the persistent native worker lifecycle.

`workers/snapshot.py` exports exact committed regular Git blobs and validates
bounded source archives entirely in memory. Links, submodules, special files,
control directories, traversal, Unicode/case aliases and conflicting entries
are rejected. Materialization creates a new private staging parent and ordinary
source files; it never extracts an untrusted archive over an existing host tree.
A caller must select a staging parent shared with its local Docker VM before
using the read-only reviewer mount. Only the fresh snapshot child is mounted.

The source-transfer fixture uses an owned, size-limited **tmpfs-backed Docker
volume**. It seeds only validated files, pauses the container before export,
decodes the returned archive as data, and compares the complete expected tree.
A plain container `--tmpfs /workspace` did not work for this export strategy:
Docker's archive endpoint returned an empty directory even though files were
visible to commands inside the container. An empty export cannot count as a
successful roundtrip. The volume and container are removed after each fixture.

Run the opt-in transfer and native-tool checks with the pinned image built from
[the executor instructions](../tools/qualification/codex-executor/README.md):

```sh
export NIGHTSHIFT_TEST_CODEX_EXEC_IMAGE="$(docker image inspect nightshift-codex-executor:0.153.4 --format '{{.Id}}')"
export NIGHTSHIFT_TEST_DOCKER_HOST="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"
uv run pytest -q tests/test_remote_environment.py tests/test_container_snapshot.py
```

These six integration checks passed locally with a fake provider and synthetic
source only. Normal CI skips them unless explicitly opted in. They are separate
from offline unit tests and do not establish live authentication or billing.

`workers/candidate.py` adds a host-owned commit primitive for an exclusively
owned clean worktree at an exact base SHA. It writes validated regular files and
stages their exact blobs using Git plumbing. It does not invoke repository clean
filters, hooks, signing, external diff helpers or porcelain commit refresh.
An atomic expected-base ref update prevents overwriting a moved branch; failures
preserve the staged candidate for investigation. There is no push or merge path
in this helper, and it is not yet wired into daemon dispatch.

## Remaining production integration

- Resolve the skills reader's package registration and resource authority, or
  prove that the capability is absent. An exact advertised inventory alone is
  insufficient permission evidence.
- Assemble the persistent executor, validated source transfer, host-owned
  candidate commit, and immutable reviewer snapshot into one owned lifecycle.
- Run native candidates' verification scripts inside the qualified executor.
  The legacy host verifier is not filesystem/network isolation; a modified test
  or install script must not regain host access through that path.
- Wire typed driver results, explicit authentication/quota policy and accounting
  into daemon implementation/review dispatch.
- Run an isolated sandbox task through verification, independent review and PR
  creation. Keep human merge review and exact-candidate checks.

The transport's `externalSandbox` declaration means that Docker owns containment;
it does not grant a worker extra Docker privileges. Qualification must inspect
and test the actual external policy before using that declaration.
