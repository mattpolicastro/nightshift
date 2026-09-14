# Native OpenAI worker implementation status

The first implementation milestone adds an offline transport and stronger task
verification. **Native OpenAI execution is disabled.** It cannot claim queue
items, fall back to Claude, or be enabled with an operator bypass.

## Implemented

- Typed worker requests, budgets, completed commands, results and review verdicts.
- An app-server stdio transport tested with synthetic processes: initialization,
  thread/turn identity, terminal completion, command exit status, structured
  review output, cumulative usage, cancellation and bounded private journals.
- Unknown server requests fail closed. Runtime, action, output and stream limits
  terminate the fixture session; stderr cannot supply protocol events.
- Explicit native endpoint configuration validation. Invalid routes, duplicate
  names, missing endpoints and ignored authentication settings are rejected.
- Host verification of the committed candidate in a fresh detached checkout,
  followed by a check that the candidate and shipping branch still match.
  Worker claims of running tests are no longer sufficient shipping evidence.
- Failed worker processes cannot succeed through a success-shaped final event.
- Evidence-persistence and shipping failures escalate while retaining the
  unpushed candidate; successful review alone cannot activate teardown.
- A generated ChatGPT-managed provider policy forces the built-in OpenAI route,
  keyring credentials and ChatGPT login while excluding API-key environment,
  custom providers and inherited configuration. Synthetic admission validates
  account mode, managed usage or existing credits, the model catalog and the
  requested reasoning effort before thread creation. Its production constructor
  remains blocked pending an owned stable keyring-home lifecycle.
- A private metadata-only seam now binds an expected principal and workspace,
  validates all of that evidence, and exits before thread creation. A local
  no-turn qualification passed with the authorized managed account and an
  advertised model. It also confirmed that keyring enrollment is scoped to a
  stable `CODEX_HOME`; the current one-attempt provider home cannot reuse it.
- A stable credential-home lease now supplies exclusive cross-process locking,
  durable attempt evidence, descriptor-based exact startup writes, shutdown-bound
  cleanup, and retained recovery state. Its metadata adapter cannot start a model
  thread and uses an inert remote-only executor descriptor. The final namespace
  is enrolled and has passed a live metadata-only qualification with confirmed
  provider shutdown. The version probe runs in a separate disposable home so its
  runtime files cannot alter the authenticated namespace.
- A dormant native claim marker can be made durable before a future provider
  launch. Queue reconciliation and startup recovery retain any marked claim and
  skip destructive Git/PR recovery. The launch guard requires a complete claim
  at its canonical queue path and tests that restart loading preserves it. No
  production caller prepares it, so this does not activate native dispatch.
- A private implementation runner now composes that marker, the enrolled stable
  credential lease, generated ChatGPT policy, managed-account admission, owned
  container session and isolated executor. Candidate bytes remain provisional
  until provider, executor, session and lease cleanup all succeed. Synthetic
  JSON-RPC coverage proves admission precedes the single turn and that the lease
  remains held until transport shutdown. A minimal live subscription canary
  produced the exact requested synthetic file and confirmed all cleanup gates.
- Dormant native accounting snapshots exact reported token fields by prepared
  run and phase. Missing usage remains unknown; native evidence does not alter
  legacy Anthropic cost, quota, turns or unbilled scheduling capacity. The live
  protocol's optional cache-write token count is preserved explicitly.
- Unattributed live quota notifications revoke eligibility until fresh account
  and quota reads rebind the private identity and confirm available managed
  usage. Exhaustion or failed rebinding stops the attempt.
- A private coordinator now joins stable implementation to a host-owned exact
  candidate commit and pinned isolated verification. It binds the complete Claim,
  worktree, symbolic branch and base commit across every handoff; preserves
  implementation files and accounting on failure; and stops at
  `verified_pending_review`, which cannot authorize shipping.
- A private stable reviewer independently reloads the exact baseline and
  candidate from the Claim's Git worktree, derives and validates the diff, checks
  typed isolated-verification clauses, and starts a fresh managed ChatGPT turn
  only after an immutable source mount is confirmed. PASS requires a distinct
  thread, structured verdict, unchanged source/Claim/Git state and complete
  provider, executor, session and credential cleanup. A live review canary over
  a real verified synthetic commit returned a bound structured PASS with every
  isolation, identity, accounting and cleanup check satisfied.
- A private controller now holds a cross-process Claim lock across implementation,
  commit, isolated verification and independent review. It writes a durable
  `implement` intent before the first model call and persists exact returned usage
  before any host Git mutation. It then durably records the verified candidate,
  advances the unchanged Claim to `reviewing`, and writes a separate review intent
  before starting the fresh reviewer. Returned review provenance and accounting
  are persisted before qualification. Existing, incomplete or ambiguous journals
  block replay and require operator inspection. Even a complete PASS stops at
  `reviewed_pending_human` and cannot authorize shipping.

The complete private controller has also passed a live two-turn canary over a
synthetic local Git repository. A managed implementation turn produced the exact
single-file candidate, the pinned verifier accepted its committed SHA, and a fresh
managed review thread returned structured PASS over the immutable candidate.
Both phase receipts were durable, the Claim remained in `reviewing`, implementation
and review threads differed, every cleanup gate succeeded, and the final result
still denied shipping. The canary had no Git remote and did not use the daemon or
GitHub queue.

A dormant daemon-integration foundation now validates an exact ChatGPT-managed
profile without reading credentials or offering an activation switch. It binds
both phase models, reasoning effort and budgets; distinct immutable implementation,
review and verification images; the pinned host runtime; an owned local Docker
socket; private credential/recovery roots; and an opaque identity reference.
Mixed drivers, API-key/custom providers and fallback fields are rejected.

Native Claim ownership can now begin before marker creation and remain continuous
through controller cleanup. The preparation lease checks the exact clean Git base,
branch, worktree and persisted fresh Claim, creates a private recovery directory,
and durably installs the `implementing` marker before opening the attempt journal
under the same cross-process lock. Unreadable claims, unsafe claim directories and
orphan native lock tombstones block destructive reconciliation and re-admission.
Native recovery remains attention-visible regardless of remote issue/PR state
until an explicit local recovery action clears it. These paths remain unwired from
normal daemon dispatch.

The transport's private fixture runner accepts an explicit test process and
synthetic environment. It is not a production credential or launch path.
A transitional Claude adapter now emits typed results while preserving legacy
quota and verdict data separately. Missing telemetry stays unknown; requested
commands do not become completed-command evidence. Its invocation accepts the
legacy max-turn limit explicitly, not unenforced Codex budgets. Production
dispatch still uses the existing interface; integrating the adapters remains
implementation work.

An offline schema inventory records input fingerprints and available permission
fields separately from untested enforcement. It always returns a blocked
qualification result; schema presence cannot enable execution.

## Verification configuration change

Repository `verify` commands must be direct argv commands, optionally joined
with `&&`, such as `pnpm typecheck && pnpm test`. Shell expansion, pipelines,
redirections, background operators and compound shell syntax are unsupported.
Move complex verification into a trusted repository script and invoke it
explicitly. Preflight validates this syntax.

The host runs every clause with a clean environment and scratch home, within a
shared ten-minute deadline. Each command has a 16 MiB combined stdout/stderr
limit; exceeding it fails verification and terminates its process group. Only
the final 16 KiB is retained in memory. A pnpm lockfile triggers a frozen-lockfile install;
other repositories must supply self-contained verification commands. Failed
bootstrap, incomplete commands, background work, candidate edits or branch
movement prevent shipping. A private `.verification.json` artifact records the
candidate SHA and command outcomes alongside worker transcripts.

This executes trusted repository code on the host. Environment filtering and
process-group cleanup are not filesystem/network isolation and cannot contain
processes that escape their group. Native worker isolation is a separate gate.

## Remaining before native support can be claimed

1. Extend the immutable reviewer and executor qualification across every
   supported platform/runtime. The local pinned-image reviewer probe now checks
   configured and effective read-only mounts, denied source mutation, scratch,
   network and host isolation, exact source preservation and owned cleanup. See
   [security qualification](codex-security-qualification.md).
2. Integrate the private controller, durable accounting persistence, exclusive
   claim/worktree ownership and retained recovery outcomes with daemon dispatch.
3. Run an end-to-end sandbox task with independent review, isolated verification,
   host-owned PR creation and GitHub CI before enabling production dispatch.

The approved route allows a ChatGPT plan's included allowance and existing
ChatGPT credits. Platform API-key and custom-provider fallback remain forbidden.

The [specification](../SPEC-openai-workers.md) is the acceptance contract.
The private canary used the approved managed subscription route. No production
daemon dispatch or repository task was activated.

See the [fallback isolation experiment](isolated-tool-execution.md) for the
provider/tool separation required if native permission qualification fails.

A standalone [OpenAI smoke test](openai-smoke-test.md) can check API-key/model
access before executor qualification. It sends a fixed prompt with no tools or
repository content. This command does not enable native worker dispatch, and
offline tests are not evidence of a successful live provider call.

Isolated-executor prototypes now have synthetic evidence for remote command,
patch and interactive input routing, no host fallback, bounded container cleanup,
validated source transfer and immutable reviewer source. See [isolation progress](isolated-tool-execution.md)
for reproduction, registered skills-routing characterization, and the remaining
production-launcher and lifecycle gates.


An owned foreground-command session manager now handles bounded binary source
transfer, exact import checks, paused export, cumulative output limits, process
quiescence and ownership-checked cleanup with durable recovery records. An
isolated verifier runs complete command chains against exact committed snapshots
and checks source after each clause. A native tool-server attachment and private
fixture coordinator now join source export, host-owned commits and exact-commit
verification. Four real-engine tests
exercise native edits, missing-executor rejection, cancellation, and the joined
coordinator with an explicitly synthetic reviewer. A separate bound reviewer
adapter now requires an inspected immutable source mount, fresh execution context,
exact evidence identities and cleanup before PASS can qualify. Production daemon
wiring remains incomplete. Synthetic native
runs now use a generated one-attempt provider home and validate the pinned host
runtime plus effective configuration layers, origins and requirements before
thread creation. The ChatGPT-managed variant also validates account, usage,
model and reasoning capability evidence. Principal/workspace binding and a local
metadata-only qualification now pass. The private stable runner composes this
policy with the enrolled lease and isolated executor, while its production
constructor and daemon route remain blocked. See the
[ChatGPT-managed integration policy](subscription-worker-policy.md).
