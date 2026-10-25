## Why

The original prerequisite, one change through the front door, was met on 2026-10-21 (#11, then #24 and #25). Maintaining a second execution engine duplicates scheduling, model configuration, prompts and operational instructions. Retire the worker engine after its prerequisites land, leaving specloop as the only engine.

Blocked by: #58, #47, #49, #50, #51, #52, #46, #5, #6, #7, #9, #10, #12, #13, #14, #15, #21, #31, #33, #34, #4, #8, #39, #103. This is one atomic change and one implementation PR; internal tasks are not separate releases.

## What Changes

- Default omitted repository engines to `specloop`; reject `engine = "worker"` with an actionable configuration error before dispatch.
- Remove worker orchestration, execution, prompts, endpoint probes and active worker configuration. Preserve manifest health isolation, front-door authorization and independent specloop supervision.
- Remove worker session status output, including the off-subscription model report. Preserve all other status output and behavior.
- Preserve historical outcomes, transcript readers, attention JSON, legacy claim evidence and durable safe recovery. Do not resume or destroy interrupted worker work.
- Remove worker-only service prerequisites and update current setup instructions, examples and package description. Keep the daemon and supervisor jobs distinct.
- Check Paseo command adapters and message commands against the retained CLI boundaries; no rename is necessary.
- Use bounded caller-first deletion tasks, including affected test callers. Deletions count toward the approximately 300-line ceiling. More than eight tasks is intentional and authorized for this retirement.

Out of scope: #40's package/CLI rename and merge, moving `~/.nightshift` or `~/.specloop`, changing plugin RPC schemas, deleting historical data, rewriting shared queue/git/trace utilities, changing provider profiles or specloop quotas, and automatically editing or installing live host configuration/plists. Mutation/test workers are unrelated to the retired engine.

## Capabilities

### Added

- `worker-retirement`: sole execution engine, inert legacy configuration, preserved history and service/client boundaries.

### Modified

- `issue-intake`: specloop default and explicit worker rejection; scheduling guarantees without worker claims or harvest.
- `config-resilience`: manifest holds and durable leftover-claim recovery apply to every enrolled repository.
- `background-job-control`: explicitly remove worker status output while retaining all other status guarantees.
- `host-paths`: retain worktree-root parsing for compatibility, with specloop-only enrollment isolation.
- `specloop-documentation`: current instructions describe one engine and preserve historical recovery and #40 boundaries.

All other requirements remain in force, including `attention`, `pr-revision`'s feedback compatibility, provider isolation, job cancellation, publication and Paseo actions.
