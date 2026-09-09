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

## Registered skills routing and provider-home policy

The synthetic fixture still advertises `skills.list` and `skills.read`. Explicit
`thread/start.selectedCapabilityRoots` entries with an environment location
register executor-owned packages; merely writing a SKILL.md does not establish
namespace registration. Listing then returns an actual package identifier, and
reading that identifier returns the executor's synthetic skill marker. A private
synthetic wire trace observed the corresponding remote filesystem calls.

Two reproducible fixture modes compare a fresh provider home with a deliberately
seeded `CODEX_HOME/skills` directory. The seeded skill is advertised in the initial
provider prompt even with `skip_host_skill_discovery=true`; the fresh home omits
it. Production must construct an allowlisted provider home and explicit remote
capability roots rather than rely on that flag or copy an interactive profile.
The orchestrator namespace registry is empty in these fixtures.

Direct host paths, file URLs, traversal, a package symlink to an unmounted host
sentinel, and resource URIs outside the registered root are rejected. Extra
authority/environment arguments on a valid package read are ignored by the
pinned runtime: the read still returns the remote marker, rather than retargeting
to local or orchestrator data. A package symlink can read a synthetic file in executor
scratch. This does not establish confinement to the individual package, and the
launcher must not use package membership as a narrower filesystem boundary.
Executor scratch is already permitted by the container policy; this observation
is not evidence of host credential access.

These are behavior-characterization tests with a genuine registered-package
positive control. They resolve the earlier registration mystery, but do not
qualify a production launcher that has not yet been implemented. Native
execution remains disabled. All fixtures use synthetic data and a fake provider;
no account diagnostics or live credentials are published.


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

These nine integration checks passed locally with a fake provider and synthetic
source only. Normal CI skips them unless explicitly opted in. They are separate
from offline unit tests and do not establish live authentication or billing.

`workers/candidate.py` adds a host-owned commit primitive for an exclusively
owned clean worktree at an exact base SHA. It writes validated regular files and
stages their exact blobs using Git plumbing. It does not invoke repository clean
filters, hooks, signing, external diff helpers or porcelain commit refresh.
An atomic expected-base ref update prevents overwriting a moved branch; failures
preserve the staged candidate for investigation. There is no push or merge path
in this helper, and it is not yet wired into daemon dispatch.

## Joined candidate lifecycle control

The synthetic transfer suite now joins the existing primitives in one test:
export an exact Git baseline, edit its isolated source, pause and validate the
returned archive, create a host-owned candidate commit, and materialize that
exact commit for isolated verification and read-only review. The unchanged
verification script fails on a deliberately bad baseline and passes on the
candidate. The reviewer cannot overwrite the source and sees no Git control
directory. Final committed bytes and the host worktree remain unchanged.

This passes for the fixed synthetic fixture; it does not implement production
lifecycle ownership, dependency preparation or daemon dispatch. In particular,
the fixture's small `capture_output` transfers are not the bounded streaming
transport required for production source export.

## Remaining production integration

- Reproduce the characterized skills routing with an allowlisted fresh provider
  home and explicit remote capability roots in the production launcher. Keep
  filesystem authority at the executor boundary; do not assume package-local
  symlink confinement or rely solely on discovery-disable flags.
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


## Owned session manager and isolated verification

`workers/container_session.py` now joins source transfer and repeated foreground
commands in one owned session. It creates a labeled, size-limited tmpfs-backed
volume and a fixed non-root, network-disabled container, checks their effective
policy before startup, and confirms the imported snapshot before returning the
session. Unexpected image environment or mounts are rejected. Use a compatible
immutable local image containing `/bin/sleep` and `/bin/tar`; the current Codex
exec-server image has additional environment entries and is not yet a qualified
image for this manager.

Binary stdin, stdout and stderr are streamed with byte and time limits. Command
output consumes a cumulative session budget; ordinary completed nonzero exits
allow repair, while timeouts, excess output and unconfirmed completion invalidate
the session. Daemon-owned process inventories reject surviving background work.
Snapshot checkpoints pause the container and inspect the frozen process set
before reading a bounded archive, then resume it. A checkpoint is provisional;
`finish()` accepts source only after owned resources are removed successfully.

A private, fsynced ownership record precedes Docker creation. The recovery
directory must be owned and private under a trusted host-controlled parent.
Cleanup has one separate 15-second deadline and verifies container and volume
absence before removing the record. An ambiguous creation result keeps the
record even if a current listing shows nothing: the daemon request may still
be in flight. Cleanup or record-persistence failure cannot produce an accepted
candidate. There is no automatic recovery deletion based on a supplied JSON file.

`workers/isolated_verification.py` reads an exact Git commit and runs every
configured argv clause through the session. It checks source fingerprints after
each successful clause and again at finish, so a later clause cannot hide an
earlier mutation. Failed/backgrounded commands, missing completion, changed
source and unconfirmed cleanup prevent success. Source export and execution
share one deadline, including the Git snapshot reader. Repository scripts never
execute in the host verification process.

Dependencies must already be available in the compatible immutable image.
There is no package installation or network bootstrap. The verification copy is
mutable and checked after each clause; immutable reviewer mounting is still a
separate integration step. This command-session manager is not yet a native
Codex exec-server launcher or a daemon dispatch path.

Run the additional real-engine qualification with the local Alpine fixture:

```sh
export NIGHTSHIFT_TEST_CONTAINER_SESSION_IMAGE="sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b"
export NIGHTSHIFT_TEST_DOCKER_HOST="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"
uv run pytest -q tests/test_container_session_integration.py
```

The eight synthetic integration cases cover persistent edits, failed-test repair,
host/environment/network denial, detached work, output and time budgets,
malformed exports and cleanup, and exact-commit verification with per-clause
mutation detection. Offline tests additionally exercise ambiguous creation,
wrong ownership, checkpoint failure and recovery-record persistence errors.

Next, adapt the owned lifecycle to the pinned native tool server, assemble
host-owned commits and immutable review in the task coordinator, and integrate
explicit authentication/accounting and recovery. The
[subscription-only policy](subscription-worker-policy.md) separates login and
quota telemetry from the still-unresolved billing enforcement requirement.
Native task execution remains disabled.
