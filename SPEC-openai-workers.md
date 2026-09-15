# Native OpenAI workers

**Status: partially implemented; native execution disabled.** Written 2026-09-08
against the published Nightshift architecture. This document is the acceptance
contract, not a support claim. See [implementation status](docs/native-worker-progress.md)
for completed work and remaining gates.

## Decision

Add a native Codex adapter using its app-server stdio protocol. Keep Claude
Code as the existing driver for Anthropic and the validated Ollama route.
Select the driver through an endpoint assignment so implementation and review
can independently use either runtime.

The next live qualification route uses explicitly selected ChatGPT subscription
authentication. It must fail closed when included quota or billing policy cannot
be established; it must never fall back to an API key or purchased credits. The
separately configured Platform API route remains available as a future metered
option using a dedicated credential and the official OpenAI endpoint. It does not translate OpenAI
responses into fabricated Claude stream-json events. It also does not make
arbitrary “OpenAI-compatible” servers supported merely because they expose a
similarly named URL.

A direct Responses API driver is a later, separately qualified adapter. It
would require Nightshift to own the complete tool-execution loop. Native Codex
provides that coding runtime first; this is an engineering scope decision,
not a claim that a direct API integration is impossible.

## Why app-server

The official app-server protocol supplies thread/turn lifecycle events,
command completion, approval requests and cancellation over stdio. Those are
useful boundaries for a supervising daemon. Its schemas can be generated from
the installed CLI version. [Official app-server documentation](https://learn.chatgpt.com/docs/app-server).

`codex exec --json` is a simpler batch interface and remains useful for probes,
but the first adapter should use one structured control path for cancellation,
permissions and execution evidence. Do not implement both production transports.
[Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).

Local inspection found `codex-cli 0.153.4`; its generated schemas include
`ThreadStartParams`, `TurnStartParams`, command approval requests and terminal
turn notifications. This confirms a candidate interface, not runtime qualification.
Pin and record the version actually tested; reject unsupported schema versions
before claiming an issue. The local CLI labels app-server experimental.

## Scope and non-goals

Release scope:

- Codex implements → Claude reviews, Claude implements → Codex reviews, and
  Codex implements → a fresh Codex reviewer.
- Explicit model and reasoning-effort selection, authenticated preflight,
  bounded execution, typed outcomes, normal revision/escalation behavior.
- Existing worktree isolation, harness-owned pushes, human merges, durable
  outcomes and Paseo attention remain the delivery path.
- API usage is accounted separately from Anthropic subscription windows.

Not included: automatic model selection, a separate planning-model phase,
Chat Completions adapters, managed translating proxies, arbitrary third-party
OpenAI-compatible servers, remote public worker services, or a new UI.
ChatGPT-managed authentication and Platform API authentication are separate
explicit modes; neither is an automatic fallback for the other. Model choice is explicit; do not hardcode a
“latest” model or silently substitute another model when access fails.

## Current coupling that must be removed

| Current code | Required change |
| --- | --- |
| `worker._run` always executes `claude -p` | Dispatch through a worker-driver interface. Preserve the Claude invocation inside its adapter. |
| `trace.parse` understands only Claude events | Normalize each runtime's native events into the same typed result; retain private raw events separately. |
| `task.run` parses `Run.events` itself | Consume the adapter's typed result, not a provider-specific transcript. |
| `ran_verification` matches any verify-command stem as a substring | Require evidence that the complete configured chain finished successfully for the candidate revision. Requested command text and stdout claims are insufficient. |
| `after_implement` checks truncation but does not reject every unsuccessful result | Reject failed/interrupted/auth-error/protocol-error runs before verification, review or shipping. A remaining commit must not turn an errored run into success. |
| `Result.subscription_billed` infers Anthropic billing from Claude metadata | Track provider, auth mode and quota account explicitly. Never infer API spend or free execution from missing telemetry. |
| CLI progress reads Claude transcripts | Read normalized progress for new runs; preserve legacy readers for old records. |

Do not reproduce those assumptions inside the Codex adapter to make it fit.

## Proposed configuration

The API-key syntax below is validated by the implementation branch, but native
execution remains blocked. Subscription configuration and its preflight policy
are not implemented yet; an existing Codex login does not enable queued tasks. Driver and authentication fields extend Endpoint; omitted driver
means `claude-code`, so
existing configurations retain their meaning. Existing `protocol = "openai"`
continues to mean the legacy Claude-to-proxy route; never reinterpret it silently.

```toml
[[endpoints]]
name = "openai"
driver = "codex-app-server"
protocol = "responses"
base_url = "https://api.openai.com/v1"
auth = "api_key"
auth_env = "NIGHTSHIFT_OPENAI_API_KEY"
billing = "metered"
models = ["OPERATOR_SELECTED_MODEL_ID"]
reasoning_effort = "medium"
max_runtime_s = 1800
max_tool_calls = 100
max_output_tokens_total = 32000

[models]
implement = "openai:OPERATOR_SELECTED_MODEL_ID"
review = "opus"
```

The model placeholder must be replaced with an accessible model. Validate
reasoning effort against its advertised capabilities. Prefix parsing still
resolves a declared endpoint; per-repository `[repos.models]` overrides work
as they do today. Invalid driver/protocol/auth combinations fail configuration
validation. Native OpenAI v1 rejects custom base URLs and proxy_url; qualify a
custom-provider route separately rather than forwarding an OpenAI credential.

The existing `implement_max_turns` and `review_max_turns` remain Claude limits.
Do not map them to Codex turn counts: a Codex turn is a user request containing
multiple agent actions. Codex requires explicit elapsed-time, tool-call and
output-token budgets. Missing token telemetry makes the token budget unknown;
it does not disable the elapsed-time or action limits. These are supervised
limits, not promises of an exact dollar cap or zero in-flight overshoot.

## Adapter and event contract

Introduce `WorkerRequest`, `WorkerEvent`, `WorkerResult` and a driver registry.
Suggested files: `nightshift/workers/base.py`, `claude.py`, `codex.py`,
`codex_protocol.py`, plus a host-owned verification runner.

A request carries task/run/attempt identifiers, role, worktree, base SHA,
model assignment, permission profile, budgets and the private transcript sink.
A result carries:

- Runtime/version, requested model, observed model when available, endpoint ID,
  auth class and configuration fingerprint. Never include credential values.
- Terminal status: succeeded, failed, interrupted, budget_exhausted,
  auth_failed, rate_limited, needs_input, or protocol_error.
- Final assistant text, elapsed time, provider usage with explicit units,
  native thread/turn IDs and per-model usage when actually reported.
- Completed commands with cwd, start/end time, exit code and cancellation
  state; file-change and denied-action events; verification records.
- Structured reviewer verdict and blocking/non-blocking findings. A missing or
  malformed verdict remains a failure of the review gate.

Preserve unknown usage fields as unknown. Never report a requested model as
an observed model or collapse an aggregate usage count into invented per-model
measurements. Export only safe display fields through the plugin.

Write native and normalized events incrementally to private JSONL files with
bounded buffers. IDs and sequence numbers prevent duplicate completion events
from double-counting usage or verification. Unknown additive fields may be
ignored; unknown terminal statuses and authorization methods fail closed.

Protocol lifecycle: start a private stdio child, initialize, create a fresh
thread, start the role's turn, process item/usage/approval notifications, and
wait for its terminal status. On cancellation, request interruption, wait a
short grace period, then terminate the owned process group and reap descendants.
Do not borrow a desktop session or connect to the user's active app server.

Completion requires the matching terminal notification, a valid final result,
and no unresolved child operations. A final text message, EOF, process exit 0,
or a stream disconnect alone is not proof of success. Interrupted jobs preserve
work and diagnostics for recovery; do not automatically replay a mutating turn.

## Permissions and credential boundary

Authentication is an explicit setup step, not an agent capability. Preflight
must check the effective account/auth class against the selected mode. An API
key route requires a dedicated credential. Subscription qualification requires
ChatGPT login plus evidence that the run stays within included usage; login
alone is not a billing guarantee. If the runtime cannot enforce the requested
subscription-only policy, leave that route disabled and report the missing
control. Do not substitute the API-key route. [Official authentication documentation](https://learn.chatgpt.com/docs/auth).

Build the child environment from an allowlist. Do not source the complete
Nightshift credential file into tool subprocesses. Keep GH_TOKEN, Slack tokens,
Anthropic OAuth, other endpoint keys, SSH agent sockets and interactive auth
stores out of worker tools. Provide model authentication only to the runtime's
provider channel. Separate endpoint auth/config directories from worktrees;
preflight must demonstrate that model tools cannot read the credential store
or retrieve the provider credential through environment inspection.

**Release-blocking spike:** establish this boundary on each supported OS with
the pinned runtime. A distinct config directory is organization, not isolation.
If the native permission profile cannot enforce the necessary read/write and
network restrictions, use an externally isolated runtime with a host-side
credential broker, or reject that platform. Do not advertise a boundary that
rests only on the prompt, missing executable names, or environment scrubbing.

Disable inherited MCP/apps, hooks, plugins and user exec-policy rules. Honor
repository task instructions as content, but never let repository config widen
the host's role permissions. Do not enable blanket approvals, session-wide
permission grants, danger-full-access, or auto-approval by another model.

Implementation may edit only its worktree and dedicated scratch/build paths.
Deny tool network egress and writes to Git control data, credentials and other
checkouts. Provider transport access is distinct from tool network access.
Dependencies are prepared by the harness under an explicit policy; an agent
must not fetch arbitrary packages as a way around a denied tool operation.

Review sees a read-only candidate snapshot and bounded verification evidence,
in a fresh thread with no implementer reasoning history. It cannot edit source,
commit or change Git state. It may request a registered verify operation, whose
host-controlled execution is isolated from the reviewed snapshot. Hash/diff
checks detect mutation; they supplement permissions rather than replacing them.

App-server approval requests are an additional decision surface, not a promise
that every tool invocation asks permission. Deny network/permission expansion,
unknown requests and requests for user input; convert unresolved input to a
clear escalation. Do not implement shell security with string-prefix matching.
The sandbox must hold even for operations the runtime considers pre-approved.

## Verification, commits and review

A Codex implementation produces a candidate diff and summary. The harness
validates scope, stages explicit paths and creates the candidate commit; the
worker never receives general Git-control write access. This requires an
explicit adapter capability in the task state machine, not a fake commit event.
Claude's existing commit path remains supported during migration.

Run the complete configured verify chain in a disposable checkout of that exact
candidate, using a credential-free verification environment. Record every
clause's exit status, timeout and commit SHA. Only all-zero completion is green;
a started command, backgrounded process, stale successful run, weakened command,
or failed earlier clause cannot satisfy it. Retain existing verify policy checks.

The reviewer receives the issue, base/candidate diff, policy and verification
records, and produces a structured PASS/FAIL with separately classified
findings. A fresh reviewer thread is created on every attempt. Feedback sent to
the next implementer contains findings, not the reviewer's private reasoning.

On PASS, check candidate SHA and scope again before the harness pushes. If
anything changed after verification/review, invalidate both results. CI judges
the pushed SHA. A PR remains unmerged; no automatic merging is part of support.

## Accounting and failure behavior

Key quota state by provider/account/auth class, not one global Anthropic
five-hour window. Record API token usage separately from subscription quota and
local-model consumption. Estimated list-price cost must be labeled with its
pricing source/date; absent prices mean unknown, not zero. Do not reuse
Claude's `costBasis` heuristic for Codex. Charge failed attempts when usage is
reported and preserve unknown usage after crashes.

Use bounded backoff for retryable provider errors. Auth failures, unavailable
models and unsupported configuration fail preflight. A 429 must distinguish
transient throttling from exhausted quota when the provider supplies that
information. Never fall back to a different model, auth mode, provider or paid
route. Retries after tool execution require recovery decisions, not a blind
repeat of the entire model request.

Claims remain durable through interruption. Record runtime/version/config hash,
thread/turn IDs and last completed action in the run ledger. On restart inspect
Git state and verification evidence before resuming, revising or escalating.
No orphaned subprocess may keep writing after the task is marked finished.

## Delivery sequence and acceptance gates

1. **Normalize existing workers and strengthen verification.** Preserve Claude
   and Ollama behavior while adding typed events, explicit failure statuses,
   and complete verification evidence. Regression tests must cover the current
   successful pilot path, an errored result with a commit, fake verify output,
   and interrupted/backgrounded verification.
2. **Qualify the Codex boundary, then implement transport.** Generate schemas
   from a pinned version; run offline fixture tests for initialization, streamed
   items, duplicate events, failures and cancellation. Prove role permissions
   and credential isolation before enabling real repository tasks.
3. **Add endpoint/auth/budget integration.** Validate future configuration,
   maintain old config semantics, isolate provider quotas, expose accurate
   runtime/model/progress in the existing ledger and attention page.
4. **Run controlled live qualification.** Three task shapes (additive helper,
   bounded bug fix, small cross-file change), plus a seeded bad-diff review.
   Exercise Codex→Claude, Claude→Codex and Codex→fresh-Codex combinations.
   Record every attempt, including failures; report results without a reliability
   percentage from this small sample. Public results must distinguish tests of
   integration from measured coding/review quality.

Required negative tests: write outside worktree, symlink escape, read credential
store, inspect tool environment, push through Git/SSH/HTTP, weaken CI, alter
reviewed source, invoke an unapproved plugin, forge terminal success, drop the
stream, exhaust budgets, inject 429/auth failures, and crash during verification.
Use synthetic sentinel credentials and disposable repositories, never real
secrets, for adversarial fixtures. Seeded-bad-diff and known-good-diff reviews
must both produce the expected gate result; scope errors must escalate cleanly.

Normal public CI runs deterministic fixture/unit tests without provider keys.
Live model qualification is explicitly triggered, separately budgeted and uses
a dedicated sandbox. Publish sanitized results, exact runtime/model identifiers,
configuration shape, verification status and limitations; keep raw credentials,
private repository content and reasoning transcripts out of Git.

## Direct Responses API follow-up

A future `openai-responses` driver implements the same result/event contract.
It owns request state, streamed responses, tool-call IDs, retries, context
management and cancellation. Nightshift must execute and return tool results;
the API does not itself grant local filesystem access. [Function calling guide](https://developers.openai.com/api/docs/guides/function-calling).

Reuse the proven role sandbox and host verification service. Start with
explicit read/search/patch/verify tools and a narrow command runner. Disable
parallel mutations; deduplicate tool IDs and never replay a committed side
effect on an HTTP retry. Use API keys only, explicit retention settings and
independent provider preflight. Require the same live qualification before
adding “direct OpenAI API worker” to the support matrix.

Until the corresponding gates pass, the README should say **native OpenAI
worker support is planned**, while retaining the already demonstrated
Anthropic/Ollama support statement.
