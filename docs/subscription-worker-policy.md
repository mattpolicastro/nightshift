# ChatGPT-managed native worker policy

Status: approved integration contract, not an enabled authentication route.
Native execution remains disabled. This route may use a ChatGPT plan's included
allowance and already-available ChatGPT credits. It must never use Platform API
key billing or a custom provider fallback. The standalone API-key smoke command
is a separate, explicitly metered operation.

## Provider process

Use a fresh provider home containing only harness-owned configuration and the
selected authentication material. Do not copy an interactive Codex profile,
skills, plugins, hooks or execution rules. Run a private pinned app-server with
ChatGPT authentication forced and the official built-in OpenAI provider. Supply
no API-key environment variables or custom provider/base-URL overrides. The
executor receives none of the provider home, credentials or host environment.

A fresh home alone does not exclude system or managed configuration. Before
thread creation, read effective configuration with all layers and origins, plus
configuration requirements. Reject nonempty system settings, managed/project or
unknown layers, inherited instructions/capabilities, provider overrides, and
unexpected credential-store or ChatGPT base-URL requirements. Official Codex
configuration supports forcing `chatgpt` login; it does not turn authentication
into a billing guarantee. [Codex authentication](https://learn.chatgpt.com/docs/auth).

Before a turn, check the effective account mode, current managed usage, the
explicitly requested model and its requested reasoning effort. Disable provider
model fallback. A mode change, model reroute, missing authentication or
authorization error fails the attempt; never switch accounts or providers
automatically. Authentication refresh and account identifiers remain private
controller concerns, not model tools.

## Managed usage admission

A ChatGPT login is not sufficient evidence of managed usage availability.
Official pricing documentation says available ChatGPT credits can continue usage
after included limits are reached. This policy permits that standard ChatGPT
route. [Codex pricing](https://learn.chatgpt.com/docs/pricing).

The pinned 0.153.4 generated protocol supplies account mode, quota windows,
optional credit/spend snapshots and rate-limit notifications. Its inspected
thread/turn start schemas do not provide an included-usage-only spending switch.
The documented rate-limit read is telemetry; it does not reserve quota for a
turn. [App-server account and rate-limit API](https://learn.chatgpt.com/docs/app-server).

Admission distinguishes three things:

- **Authentication:** effective ChatGPT mode, with no API-key fallback.
- **Headroom:** fresh, applicable quota buckets and windows, including secondary
  limits when supplied. Missing or ambiguous telemetry cannot mean unlimited.
- **Billing route:** generated configuration forces ChatGPT authentication and
  the built-in provider, uses no API-key environment variable, and rejects
  custom providers or base URLs. Existing ChatGPT credits are allowed.

Synthetic tests now enforce those conditions before thread creation and process
account, quota and reroute notifications synchronously when observed. Telemetry
is still a point-in-time signal rather than a reservation. Do not change account
billing settings, purchase credits, redeem resets or send credit-request
notifications as part of preflight. Do not use the protocol's unstable internal
ChatGPT-token injection variant as a credential-transfer mechanism.

Production activation remains blocked until the bound macOS keyring login can be
used through an owned stable-home lifecycle without exposing credentials to the
executor. A fresh `HOME` and `CODEX_HOME` do not isolate or inherit an enrolled
OS keyring identity. The public constructor therefore remains unavailable.

The metadata-only path now requires a private expected email and workspace ID,
compares both to live managed-account evidence, and returns no account or quota
details. A local qualification passed without creating a thread or turn. That
experiment also showed that the keyring login is scoped to the enrolled
`CODEX_HOME`: a new per-attempt home has no account. Production therefore needs
a stable, private and exclusively locked credential home whose generated policy
and startup assets are checked on every use. That lifecycle is not implemented.

## Accounting and failure handling

Keep OpenAI account/auth/bucket state separate from Anthropic subscription
windows and API usage. Record reported token counts with their actual units;
missing usage remains unknown. Quota telemetry is not a dollar receipt. Keep
account diagnostics and raw responses private; public qualification uses
synthetic data or separately approved sanitized evidence.

Monitor account and quota changes throughout a native run. Interrupt on a lost
admission condition, preserve the candidate and report the reason. Never replay
a mutating turn automatically after a transport, quota or authentication failure.
