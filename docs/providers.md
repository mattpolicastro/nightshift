# Worker providers and model routing

Nightshift separates the worker process from the endpoint serving its model.
Every worker currently runs through **Claude Code (`claude -p`)**. Implementation
and independent review can use different models and endpoints. This is static
routing, not automatic model selection or a native adapter for every coding CLI.

## Status — 2026-09-08

| Route | Implemented | Evidence and limits |
| --- | --- | --- |
| Anthropic through Claude Code | Yes | Used for unattended tasks; the default configuration uses Sonnet implementation and Opus review. |
| Ollama's Anthropic-compatible API | Yes | Live Read → Write probes passed for GLM-4.7 Flash and Qwen3 Coder Next during development. A fresh GLM probe passed in three turns on September 8. A full GLM implementation → Opus review sandbox pilot is underway; this is not yet a completed-task claim. |
| OpenAI-compatible API via a translating proxy | Configuration and routing implemented | An operator-supplied proxy must accept Anthropic messages and translate them for the upstream provider. No proxy integration has been validated end to end here. |
| Native Codex or another worker CLI | No | No native worker adapter exists. |
| Automatic routing by task difficulty or cost | No | Explicit phase and per-repository assignments only. |

A tool-loop probe establishes basic compatibility. It does not establish
coding quality, reviewer quality, throughput, or reliability over real tasks.
Model versions and server behavior change; run preflight against your endpoint.

## Ollama example

Start from config.example.toml. Retain your daemon, labels, notification and
repository configuration. Add an endpoint and replace the existing models table:

```toml
[[endpoints]]
name = "local"
protocol = "anthropic"
billing = "none"
base_url = "http://YOUR_OLLAMA_HOST:11434"
auth_env = "NIGHTSHIFT_OLLAMA_TOKEN"
models = ["glm-4.7-flash"]
# Confirm this against your server's model metadata; do not guess a window.
context_tokens = { "glm-4.7-flash" = 202752 }
max_turns_multiplier = 1.0
warmup_timeout_s = 180

[models]
implement = "local:glm-4.7-flash"
review = "opus"
```

On a trusted Ollama installation that does not authenticate requests, Claude
Code still needs a nonempty token value. Put a placeholder in the local
credential file, not in Git:

```sh
export NIGHTSHIFT_OLLAMA_TOKEN='ollama-local-placeholder'
```

For an authenticated endpoint, supply its actual credential instead. Never
reuse the subscription OAuth token as the local endpoint token. Nightshift
removes subscription credentials when directing a worker to another endpoint,
uses a separate Claude configuration directory for it, and checks isolation in
preflight. The default reviewer continues to use subscription authentication.

```sh
uv run nightshift preflight --deep --config /absolute/path/to/pilot.toml
uv run nightshift run --once --config /absolute/path/to/pilot.toml
```

Use a dedicated admission label and one enrolled sandbox repository for a
pilot. `--once` claims the next eligible issue, not an issue number you specify;
ensure only the intended task is eligible. A separate config does not isolate
all state: claims and the outcomes ledger are shared on the same account.
Never run competing consumers for the same repository/task. Keep concurrency
at one and do not enroll unrelated ready issues in an experiment.

To route just one repository locally, keep the global models unchanged and
place this immediately after that repository's `[[repos]]` block:

```toml
[repos.models]
implement = "local:glm-4.7-flash"
review = "opus"
```

## OpenAI-compatible endpoints

`protocol = "openai"` requires both the upstream `base_url` and a `proxy_url`.
Claude Code is pointed at the proxy. Nightshift does not start the proxy,
translate messages itself, or verify a particular proxy product's mapping of
tools, models and credentials. Treat this route as experimental until it passes
the same preflight and full-task checks as the local route. There is no direct
Responses API integration or native Codex worker in this release.

## Evidence in the code

- [config.py](../nightshift/config.py): endpoint declarations and phase/repository routing.
- [worker.py](../nightshift/worker.py): actual process invocation, environment isolation and probes.
- [preflight.py](../nightshift/preflight.py): endpoint, model and credential checks.
- [test_endpoints.py](../tests/test_endpoints.py): routing and isolation regression tests.
- [test_reviewer_readonly.py](../tests/test_reviewer_readonly.py): reviewer probe contract.
- [validation.md](validation.md): current test counts and historical evaluation scope.
