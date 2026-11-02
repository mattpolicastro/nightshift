## Why

Runs fetch and push from an isolated `repo.git`, so they miss credential helpers configured only in the enrolled source clone. Two self-hosted Gitea runs on 2026-10-31 paused at `base_fetch` until helpers were copied into the run config. Global GitHub helpers mask this defect. This addresses issue #174, part of #122, without persisting source credentials in runs.

## What Changes

- Read source-local `credential.helper` values as NUL-delimited bytes before each base fetch, publish push and finish push. Forward them in order, including empty resets, through the child Git config environment only.
- Catch credential-bearing transport failures and timeouts where subprocesses run. Redact raw helper bytes before decoding diagnostics; suppress unsafe exception chaining and never stream unfiltered child output.
- Preserve existing no-helper transport behavior, base preparation, target validation, PR reconciliation and finish recovery. Keep fetch safety flags and noninteractive behavior; apply them to the new helper-bearing push path.
- Add regression coverage for propagation, nonpersistence, non-UTF-8 redaction, CLI output, retry and no-helper compatibility. Prepare keyword-compatible push test doubles separately so four tasks remain roughly 250 changed lines each, at most 300 per session.
- Update only the explicit-publication passage in `docs/specloop/README.md` to explain all three operations and cite #174. This overlap with #163 is necessary because the existing publication instructions describe the affected credentials and publish/finish sequence.

Out of scope: `seal.git`, `specloop/seal.py`, manifests, daemon behavior, land, forge API authentication, new capabilities, helper rewriting or credential provisioning. Do not edit root `README.md`, `KNOWN-LIMITATIONS.md` or `tests/test_specloop_seal_verify.py`; preserve concurrent steering-file guidance throughout the engine guide.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `publish-base-sync`: add source-local helper forwarding, environment-only transport, byte redaction, failure boundaries and legacy compatibility requirements; retain every existing base-sync requirement.
- `specloop-forge`: narrowly qualify existing git-argv guarantees for helper-bearing pushes while retaining byte-identical `gh` argv and all target, workflow, publication and finish contracts.
- `specloop-documentation`: add source-local credential guidance to the existing publication instructions, retaining every existing documentation requirement.
