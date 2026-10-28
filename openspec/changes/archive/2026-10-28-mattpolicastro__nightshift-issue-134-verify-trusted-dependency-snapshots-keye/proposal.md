## Why

Dependency preparation repeats for every verification build and mutant. On the reported Dart workload it costs about 25 seconds and copies or fetches about 565 MB each time. Reusing dependencies from a trusted base can remove that repeated work without allowing candidate code to populate a shared cache.

blocked-by: #133

Implementation builds on #133's native mutation support and configurable invalid-step classifications. Its existing behavior and assertions remain required.

## What Changes

- Add optional trusted-manifest `[[snapshot]]` entries with `id`, repository-relative `lockfiles`, and argv-list `warm`. Hash the declared lockfile contents to identify reusable dependencies.
- Warm only a pinned, freshly fetched trusted base in a network-enabled prepare sandbox. Use driver base synchronization, including an initial synchronization before task execution; do not add warming to admission or publication commands.
- Publish successful dependency payloads read-only beneath the run state directory. Give each fresh verification build, mutation baseline, mutant and confirmation build a private writable APFS `clonefile` copy, available as `{snapshot:<id>}`.
- Keep existing prepare commands and receipts. Missing, stale or unusable snapshots select existing preparation behavior and produce diagnostic events; they never trigger candidate warming.
- Retain the last three completed snapshots across the run, with no age, size or cross-run eviction.
- Document configuration, fallback, platform limits and the pending scheming-vizier timing comparison.

Implementation scope is `specloop/manifest.py`, `specloop/verify.py`, `specloop/loop.py`, `specloop/evidence.py`, `docs/specloop/prepare.md`, tests and specs. Out of scope: shared caches across runs, candidate-produced snapshots, changed network permissions, automatic skipping of prepare steps, cached source or test results, dependency resolution policy, new CLI/configuration commands, changes to mutation selection/scoring/budgets, changes to timing-report schemas, private deployment manifests and performance guarantees.

## Capabilities

- `verify-preparation`: add manifest, trusted warming, lockfile identity, private clone, fallback and retention requirements. Preserve every existing preparation and credential-isolation requirement.
- `parallel-evidence-mutants`: add snapshot reuse across independent baseline and mutant builds, preserving all existing ordering, isolation, confirmation, classification and receipt contracts.
- `specloop-documentation`: add snapshot configuration and operational guidance in the preparation guide.

All deltas are additive. In particular, existing `[[prepare]]` steps still run against each extracted candidate, and publish still only schedules driver preparation when needed. No existing requirement is removed or relaxed.
