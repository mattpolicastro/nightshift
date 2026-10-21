## Why

Four specloop CLI defects mislead callers and agents: successful proposals can report the wrong retained ref, path-shaped review text can generate impossible coverage tasks, prompts omit trusted preparation commands, and argument errors bypass JSON output. This change is blocked by issue #10; its isolated status reads are already represented in this checkout and must remain intact.

## What Changes

- Report the ref returned by local proposal publication in successful `propose --json` output.
- Resolve runtime task scenario references against the current change before using them in evidence gates or scenario context, retaining strict unknown-ID checks during drafting and admission.
- Show prepare commands before verify commands in the implementer recipe and feasibility prompt.
- Return one version-1 error document for argument parsing failures when `--json` is requested, preserving argparse's exit code 2.
- Add a regression test named for each item and require `uv run pytest` to pass.

Out of scope: daemon or plugin changes, GitHub delivery and authorization, new JSON versions, new success modes, task identity or feedback serialization changes, evidence scoring or concurrency, sandbox or preparation execution changes, and changes to trusted manifests.

## Capabilities

- `specloop-interface`: add requirements for accurate proposal refs, validated runtime scenario references, and JSON argument errors. Preserve existing command, status, template, admission, publication, and revision requirements, including issue #10's status failure isolation.
- `verify-preparation`: add a requirement for preparation visibility in agent prompts. Preserve execution order, receipts, failure behavior, and read-only source guarantees.

All deltas are additive; no existing requirement is modified or removed. The existing `run-actions`, `pr-revision`, `issue-intake`, and `parallel-evidence-mutants` contracts remain unchanged.
