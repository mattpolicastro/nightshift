## Why

The original prerequisite, one change through the front door, was met on 2026-10-21 (#11, then #24 and #25). Maintaining a second execution engine duplicates scheduling, model configuration, prompts and operational instructions. Retire the worker engine after its prerequisites land, leaving specloop as the only engine.

Blocked by: #58, #47, #49, #50, #51, #52, #46, #5, #6, #7, #9, #10, #12, #13, #14, #15, #21, #31, #33, #34, #4, #8, #39, #103. This is one atomic change and one implementation PR; internal tasks are not separate releases.

## What Changes

- Default omitted repository engines to `specloop`; reject `engine = "worker"` with an actionable configuration error before dispatch.
- Remove worker orchestration, execution, prompts, endpoint probes and active worker configuration. Preserve manifest health isolation, front-door authorization and independent specloop supervision.
- Remove worker session status output, including the off-subscription model report. Preserve all other status output and behavior.
- Preserve historical outcomes, transcript readers, attention JSON, legacy claim evidence and durable safe recovery. Decouple the retained mirror utility from worker imports while preserving its historical config-directory default and assertions. Do not resume or destroy interrupted worker work.
- Remove worker-only prerequisites from Python preflight and update allowed setup instructions and examples. Keep the daemon and supervisor jobs distinct. Frozen wrappers retain their existing startup checks; document the separate host rollout requirement.
- Check Paseo command adapters and message commands against the retained CLI boundaries; no rename is necessary.
- Use 13 caller-first tasks in one atomic change, including affected test callers. The approximately 300-line ceiling applies to added or modified lines, including tests; whole-file and contiguous retired-block deletions stay together regardless of removed line count. Every task leaves the suite green.

Out of scope: #40's package/CLI rename and merge, moving `~/.nightshift` or `~/.specloop`, changing plugin RPC schemas, deleting historical data, rewriting shared queue/git/trace utilities, changing provider profiles or specloop quotas, and automatically editing or installing live host configuration/plists. Mutation/test workers are unrelated to the retired engine.

Implementation edits are limited to `nightshift/**`, `specloop/**`, `tests/**`, `README.md`, `KNOWN-LIMITATIONS.md`, `config.example.toml`, `docs/specloop/**` and `docs/landing.md`. The landing guide is an explicit scope addition for the reviewed enrollment correction. Do not edit `mypy-baseline.txt`, `bin/**`, `launchd/**`, `plugins/**`, `CLAUDE.md`, `pyproject.toml` or other paths outside that allowance. Resolved type diagnostics may remain in the baseline; wrapper cleanup, frozen instructions/metadata and historical-document corrections are deferred. This draft itself changes only this OpenSpec change directory.

## Capabilities

### Added

- `worker-retirement`: sole execution engine, inert legacy configuration, preserved history and service/client boundaries.

### Modified

- `issue-intake`: specloop default and explicit worker rejection; scheduling guarantees without worker claims or harvest.
- `config-resilience`: manifest holds and durable leftover-claim recovery apply to every enrolled repository.
- `background-job-control`: explicitly remove worker status output while retaining all other status guarantees.
- `host-paths`: retain worktree-root parsing for compatibility, with specloop-only enrollment isolation.
- `specloop-documentation`: current instructions describe one engine and preserve historical recovery and #40 boundaries.
- `reviewed-pr-landing`: landing prerequisites describe sole-engine enrollment and worker rejection while retaining interpreter, authentication and canonical run-discovery guarantees.

All other requirements remain in force, including `attention`, `pr-revision`'s feedback compatibility, provider isolation, job cancellation, publication and Paseo actions.
