# Subscription-only native worker policy

Status: integration contract, not an enabled authentication route. Native
execution remains disabled. The standalone API-key smoke command is a separate,
explicitly metered operation and must never be a fallback for this route.

## Provider process

Use a fresh provider home containing only harness-owned configuration and the
selected authentication material. Do not copy an interactive Codex profile,
skills, plugins, hooks or execution rules. Run a private pinned app-server with
ChatGPT authentication forced and the official built-in OpenAI provider. Supply
no API-key environment variables or custom provider/base-URL overrides. The
executor receives none of the provider home, credentials or host environment.

Before a turn, check the effective account mode, explicitly requested model and
its capabilities. Disable provider model fallback. A mode change, model reroute,
missing authentication or authorization error fails the attempt; never switch
accounts, providers or paid routes automatically. Authentication refresh and
account identifiers remain private controller concerns, not model tools.

## Included usage is a separate decision

A ChatGPT login is not sufficient evidence of subscription-only execution.
Official pricing documentation says available credits can continue usage after
included limits are reached. [Codex pricing](https://learn.chatgpt.com/docs/pricing).

The pinned 0.153.4 generated protocol supplies account mode, quota windows,
optional credit/spend snapshots and rate-limit notifications. Its inspected
thread/turn start schemas do not provide an included-usage-only spending switch.
The documented rate-limit read is telemetry; it does not reserve quota for a
turn. [App-server account and rate-limit API](https://learn.chatgpt.com/docs/app-server).

Admission must therefore distinguish three things:

- **Authentication:** effective ChatGPT mode, with no API-key fallback.
- **Headroom:** fresh, applicable quota buckets and windows, including secondary
  limits when supplied. Missing or ambiguous telemetry cannot mean unlimited.
- **Billing enforcement:** evidence that this deployment cannot fall through to
  purchased credits or another chargeable route during the task. A point-in-time
  `hasCredits=false` value or remaining percentage alone does not establish this.

Do not enable unattended subscription dispatch until the third condition has a
qualified enforcement mechanism. A runtime/provider spending control or a
verified non-chargeable deployment policy may supply it; do not invent a boolean
configuration override that merely asserts it. The current schema inventory
leaves this control unresolved. Do not change account billing settings, purchase
credits, redeem resets or send credit-request notifications as part of preflight.

## Accounting and failure handling

Keep OpenAI account/auth/bucket state separate from Anthropic subscription
windows and API usage. Record reported token counts with their actual units;
missing usage remains unknown. Quota telemetry is not a dollar receipt. Keep
account diagnostics and raw responses private; public qualification uses
synthetic data or separately approved sanitized evidence.

Monitor account and quota changes throughout a native run. Interrupt on a lost
admission condition, preserve the candidate and report the reason. Interruption
cannot retroactively guarantee zero in-flight credit use, so it supplements the
billing enforcement condition rather than replacing it. Never replay a mutating
turn automatically after a transport, quota or authentication failure.
