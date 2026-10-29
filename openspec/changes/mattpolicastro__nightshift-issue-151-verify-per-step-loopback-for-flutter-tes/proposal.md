## Why

Flutter tests start a TCP server on an ephemeral loopback port and connect to it from `flutter_tester`. The current verify sandbox denies both operations. The 2026-10-28 macOS spike with Flutter 3.47.8 / Dart 3.13.7 demonstrated that two localhost TCP allowances suffice, but unrestricted localhost access would also expose existing model servers, the forge and the agent daemon to candidate test code.

This is issue #151, blocked by #135, part of #122 (Gitea plan E1, Phase 3). The per-step environment support from #135 is present in this checkout. Flutter analysis already works with the current profile through the tool snapshot and `FLUTTER_ALREADY_LOCKED=true`; it needs no engine exception.

## What Changes

- Add optional boolean `Step.loopback`, default false, preserving older manifests, positional construction, env handling and ignored unknown keys.
- For opted-in verification-mode steps, retain the default network denial, add the measured inbound/outbound localhost TCP allowances, then deny outbound TCP to every loopback-reachable port found by a fresh IPv4/IPv6 listener snapshot immediately before that step launches.
- Fail before launching the affected command if listener discovery or application of the new profile cannot be trusted. Never fall back to unrestricted localhost or unsandboxed execution.
- Keep ordinary verify profiles byte-for-byte unchanged and preserve preparation, credentials, source protection, environment isolation and existing test assertions. Add unit and real Seatbelt integration coverage.
- Correct affected passages of `README.md`, `KNOWN-LIMITATIONS.md` and `docs/specloop/README.md`, including the snapshot's timing limits and pending human Flutter validation.

Explicitly out of scope: `nightshift/`, the plugin, prepare-mode network rules, changing any existing step's configuration/profile, Flutter installation or dependency setup, new `network-bind` allowances, external-network access, continuous listener monitoring, and proving IPv6 Flutter operation. Implementation is capped at three tasks.

## Capabilities

- **verify-preparation**: add typed per-step loopback opt-in, deterministic profile construction, launch-time listener exclusion and fail-closed execution. Existing preparation, snapshot and env requirements remain in force; the exception is selected only by the new flag.
- **parallel-evidence-mutants**: add fresh per-execution listener policies and narrowly modify **Independent mutant workspaces** to permit the explicitly configured exception. Its unconditional preservation of the old verify network restriction otherwise prevents loopback-enabled evidence steps. Preserve all existing isolation scenarios and all other evidence requirements.
- **specloop-documentation**: add configuration and evidence-limit guidance without replacing existing preparation or step-environment guidance.
