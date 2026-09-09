# Changelog

## Stable credential lease and native recovery marker — 2026-09-09

Added a private stable `CODEX_HOME` lease for macOS keyring-backed metadata
qualification. It validates a pre-existing private root without following
symlinks, acquires a cross-process lock before inspection, writes a durable
attempt marker before namespace mutation, creates exact bounded startup assets
through held directory descriptors, and requires confirmed provider shutdown
before bounded cleanup. Clean attempts preserve the stable namespace inode;
stale assets, concurrent use, control-state changes, and uncertain cleanup retain
recovery evidence and fail closed.

A metadata-only adapter holds that lease across version and managed-account
admission and uses an inert remote-only executor descriptor. It cannot inherit
the model runner or create a thread. A separate dormant native claim marker now
persists before any future provider launch and makes queue/startup recovery retain
the claim, worktree, branch, and cleanup evidence before Git or PR probes. No
production caller can prepare the marker, and native dispatch remains disabled.

## Bound ChatGPT metadata qualification — 2026-09-09

Added private principal/workspace binding and a metadata-only qualification path
that validates generated configuration, managed account mode, quota buckets,
the explicit model and reasoning capability without creating a thread or turn.
The public result contains no account, quota, stderr or transcript data. Opaque
quota bucket identifiers are bounded and identity-preserving; every returned
bucket must pass admission.

A local private qualification confirmed the authorized ChatGPT account, managed
usage and an advertised model through this no-turn sequence. It also established
that macOS keyring credentials are scoped to the enrolled `CODEX_HOME`: a fresh
per-attempt home does not inherit the login. Production remains blocked until a
stable private credential-home lifecycle is implemented and joined to daemon
recovery. No prompt or model turn was sent.

## ChatGPT-managed admission policy — 2026-09-08

Added a generated provider policy for the standard ChatGPT subscription route:
included allowance and already-available ChatGPT credits are permitted, while
Platform API keys, custom providers and endpoint overrides are rejected. Before
thread creation, synthetic admission checks account mode, managed usage, a
bounded unambiguous model catalog and the requested reasoning effort. Observed
account, quota and model changes fail the attempt, and private account/config
responses are redacted from native journals.

Production construction remains blocked until the intended macOS keyring account
can be bound without exposing credentials to the isolated executor. No live model
call, daemon dispatch change or public activation was made.

## Generated provider policy and bounded review context — 2026-09-08

Native qualification now uses a one-attempt generated provider home instead of
caller-supplied TOML, environment or flags. Before thread creation it confirms
the pinned host runtime and validates effective configuration layers, origins and
requirements. Nonempty system policy, managed/project layers, provider or feature
expansion, startup-file tampering and reuse fail closed. Subscription construction
is explicitly unsupported while included-usage-only billing remains unenforceable.

The isolated reviewer now receives bounded operator-approved task and review
policy data as untrusted JSON. Only declared fields enter its prompt; implementation
history and raw verifier output remain excluded. Public native dispatch remains
disabled, and no live model calls or production daemon changes were made.

## Immutable native reviewer boundary — 2026-09-08

Added a qualification-only native reviewer adapter backed by a fresh owned
container whose exact candidate source volume is inspected as read-only before
the provider starts. A separately journaled writable loader is removed and its
absence confirmed first. Review acceptance is bound to the review ID, candidate
SHA and verification fingerprint, and requires a fresh provider home/thread,
structured PASS, unchanged source, clean tool-server quiescence and confirmed
container cleanup.

Native dispatch remains disabled. Production provider authentication and billing
policy, issue/policy prompt assembly, daemon coordination and recovery wiring are
still incomplete. No live model calls or production daemon changes were made.

## Native executor attachment and coordinator fixture — 2026-09-08

Added a bounded, host-owned bridge to the pinned native tool server inside an
owned container Session. Export requires clean tool-server shutdown and confirmed
session cleanup. The private transport distinguishes provider cwd from container
workspace and disables provider model fallback.

A fixture coordinator now joins native implementation, host-owned candidate
commit, exact-commit container verification and explicitly synthetic review.
Real-engine tests also cover missing-executor rejection and cancellation. Public
execution remains disabled; live immutable review, production authentication,
billing enforcement, accounting and daemon integration are still incomplete.
No live model calls or production daemon changes were made for this milestone.

## Owned executor sessions — 2026-09-08

Added bounded persistent command sessions with validated source import/export,
paused checkpoints, background-process rejection and durable ownership records.
Ambiguous Docker creation and cleanup failures retain recovery evidence rather
than accepting a candidate. Exact-commit verification now has an isolated runner
with per-clause source checks and a shared source/execution deadline.

Synthetic real-engine tests cover the joined lifecycle and its failure paths.
Native tool-server launch, immutable review, authentication/accounting and daemon
integration remain incomplete. No production daemon changes or live model calls.

## Native-worker draft review — 2026-09-08

Native execution remains disabled. Independent review found and fixed candidate
loss on evidence/shipping failures, ignored model reroutes, incomplete file-change
completion, and HTTP error classification. Failure-reporting errors cannot
reactivate destructive cleanup in the retained-candidate paths.

A synthetic integration test now joins isolated edits, host-owned commits,
exact-candidate verification and immutable reviewer source, with a failing
baseline control. Registered skills now have a reproducible remote read control
and host-path/symlink negatives. Characterization also records provider-home
discovery and executor-scratch symlink behavior; it does not claim package-local
confinement. Production launcher/lifecycle ownership, authentication/accounting
and daemon dispatch remain unfinished.

## Initial public release — 2026-09-08

Published from a sanitized source snapshot. Earlier development history and
operational records remain private.

Includes worktree-based task execution, independent review, verification and
push guards, recovery, endpoint routing, durable outcomes, and a Paseo attention
plugin with bounded refreshes. Merge remains a human decision.
