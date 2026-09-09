# OpenAI connection smoke test

This is a standalone provider check, not a native worker or executor test.
A successful result establishes that the supplied API credential can obtain a
response from the explicitly selected model. It does not qualify Codex tool
permissions, repository execution, independent review or the task pipeline.

## Setup

Select an accessible Platform model explicitly. Configure a dedicated
`NIGHTSHIFT_OPENAI_API_KEY` in the environment, or in the private Nightshift
credential file at `~/.config/nightshift/env` using the existing `export NAME=...`
format. Keep that file readable only by its owner. Never place the key in the
repository, command-line arguments, chat, or a checked-in test fixture.

The test does not borrow ChatGPT/Codex login credentials or another provider's
key. Missing configuration stops before any request; there is no fallback model.

From the implementation checkout, run:

```sh
uv run python -m nightshift.workers.openai_smoke --model YOUR_ACCESSIBLE_MODEL_ID
# If the key is in the private Nightshift credential file:
uv run python -m nightshift.workers.openai_smoke --model YOUR_ACCESSIBLE_MODEL_ID --use-nightshift-env
```

## Request and evidence

The command sends one HTTPS request to the fixed official Responses API URL.
Its input is a literal request to reply `NIGHTSHIFT_OPENAI_OK`; no repository
content, local files, user task instructions or tools are sent. The request uses
`tools: []`, `tool_choice: "none"`, `store: false`, and a maximum of 1,024 output
tokens. This can incur API usage. The cap includes the model's output budget;
a reasoning model may exhaust it without completing the marker response.

Redirects, inherited proxy settings and automatic retries are disabled. A
30-second socket timeout and a capped response read limit network resource use;
the socket timeout is not a guaranteed total wall-clock deadline. An ambiguous
network failure is not automatically retried because the first request may have
been processed.

A completed response with the exact marker passes. Incomplete output, unexpected
tool output, malformed data, authentication failures and unavailable models do
not pass. The safe JSON report contains the requested/observed model and reported
numeric usage; missing usage remains unknown. It excludes credentials, raw API
error bodies and generated response text.

A live result should be recorded separately from offline tests. Passing fixtures
or preparing this command does not establish a working provider connection.

The request follows the [official text-generation guide](https://developers.openai.com/api/docs/guides/text).
