# Pinned Codex executor qualification image

This ARM64 Linux image contains the official Codex 0.153.4 executable and its
packaged runtime resources. It contains no credentials, repository source or
provider configuration. Its default command is `codex exec-server --listen stdio`.

The image alone is not an isolation policy. The qualification fixture supplies
network-none, a read-only root, dropped capabilities, no-new-privileges, resource
limits and an executor-owned source tmpfs. The provider app-server runs outside
that container with an isolated configuration directory. No Docker socket or
host credential directory is mounted into the executor.

Build from the repository root with Docker, npm and Python available:

```sh
build_root="$(mktemp -d)"
npm pack @openai/codex@0.153.4-linux-arm64 --ignore-scripts --pack-destination "$build_root"
python3 - "$build_root/openai-codex-0.153.4-linux-arm64.tgz" <<'PY'
import base64
import hashlib
import pathlib
import sys
expected = "QKdjYLYV4hXIuUQDP3P6F4NXuWFoKo9WUoV4nAREIx55kiUyi8UsYdsVobkeXir5n/maEQgYMCKLHVma4rNPiw=="
actual = base64.b64encode(hashlib.sha512(pathlib.Path(sys.argv[1]).read_bytes()).digest()).decode()
if actual != expected:
    raise SystemExit("Pinned package integrity mismatch")
PY
tar -xzf "$build_root/openai-codex-0.153.4-linux-arm64.tgz" -C "$build_root"
cp tools/qualification/codex-executor/Dockerfile "$build_root/Dockerfile"
cp tools/qualification/codex-executor/.dockerignore "$build_root/.dockerignore"
docker pull alpine@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b
docker build --network none --pull=false -t nightshift-codex-executor:0.153.4 "$build_root"
executor_image="$(docker image inspect nightshift-codex-executor:0.153.4 --format '{{.Id}}')"
NIGHTSHIFT_TEST_CODEX_EXEC_IMAGE="$executor_image" uv run pytest -q tests/test_remote_environment.py
```

The download is `@openai/codex@0.153.4-linux-arm64`; the apparent
`@openai/codex-linux-arm64` package in the parent package's optional dependencies
is an npm alias. SHA-512 integrity above comes from the pinned official package
metadata. No vendor binaries are committed here. Rebuilding requires reviewing
both the package and base-image pins when changing versions.

The fixture uses a deterministic loopback Responses API server with a synthetic
key. It does not contact OpenAI, use a subscription login, or spend model tokens.
The `gpt-5.4` label selects the pinned runtime's model-family tool inventory; the
fixture supplies every response itself. It exercises remote `exec_command`,
`write_stdin` and `apply_patch`, plus a missing-remote negative case. The source
write is inspected inside the running container; this does not qualify source
export from Docker tmpfs mounts.

The advertised inventory is checked exactly on every fake-provider request:
`exec_command`, `write_stdin`, `apply_patch`, and the `skills.list`/`skills.read`
namespace. Unexpected tools fail the fixture. Image, browser, computer, app,
plugin, hook and multi-agent features are disabled. User-input tools are disabled
through `tools.experimental_request_user_input.enabled = false`.

**The skills namespace remains a separate qualification gate.** An inventory
entry is not permission to use it. These tests do not establish live provider
accounting, subscription authentication, complete tool coverage, source transfer,
or task/reviewer integration. Native repository workers remain disabled until
those gates are qualified and enforced in the production adapter.
