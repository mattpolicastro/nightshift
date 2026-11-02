## Context

`specloop/seal.py::seal(repo, work, base, m, message, secrets)` builds a candidate
from the trusted base tree and files on disk, independently of agent commits.
Manifest `frozen` checks precede `allowed` checks. `_unsafe` separately rejects
`.git` and `.codex` components regardless of case or depth, but its caller
silently skips paths already present in the base. Extending that early-return
list alone would preserve existing steering files without reporting edits or
deletions, failing the requested violation behavior.

`specloop/manifest.py::matches` supports exact paths and `dir/**`, not arbitrary
globs or a root `**` catch-all. Tests must use allowances that really match.
`tests/test_specloop_seal_verify.py` already supplies temporary Git repositories,
tree inspection and frozen-path assertions. There is no `AGENTS.md` in this
checkout. Existing forge-secret requirements and documentation requirements
remain applicable and unchanged.

## Goals / Non-Goals

The goal is unconditional freezing of the named steering paths with observable
rejection of attempted edits, while preserving unrelated admissible changes.
One implementation session should cover the small policy change and its tests,
within roughly 300 changed lines.

Non-goals are runtime write prevention, repairing the agent's working copy,
cleaning up existing trusted instructions, extending protection to other file
names, changing manifest syntax, and changing credentials, publication or
retry policy. The trusted base may already contain steering files; they stay
available to readers with their original content and mode.

## Decisions

1. **Use a harness-owned component predicate in `specloop/seal.py`.** Split a
   repository-relative path on `/`, lowercase each component, and compare by
   exact equality with `agents.md`, `claude.md`, `.claude`, `.codex`, `.mcp.json`.
   Any matching component freezes the path, including descendants and a leaf
   named `.claude` or `.codex`. Conservatively, a directory named `AGENTS.md`
   also freezes its descendants. Substrings such as `AGENTS.md.example`,
   `.claude-backup` and `.mcp.json.example` do not match.
2. **Keep syntactic rejection separate from frozen edit detection.** Preserve
   `_unsafe`'s path-syntax checks and unconditional `.git` exclusion. Route
   `.codex` through the steering predicate with the other protected names,
   rather than silently skipping its base entries. Integrate the predicate
   into `seal`'s frozen-path decision before manifest allowances. A built-in
   match or `matches(path, m.frozen)` is sufficient to freeze an edit; there
   is no new manifest option. Preserve safe file handling, size caps and
   rejection of nonregular candidate files; never follow a candidate symlink
   to determine whether a protected edit can be accepted.
3. **Use the existing result contract.** Additions, content edits, deletions
   and executable-bit changes to steering files produce
   `<path>: frozen path (change reverted)` in `Sealed.violations` and never
   enter `Sealed.changed`. Unchanged protected entries produce no violation.
   Preserve base blobs and modes, or absence for new paths, in any returned
   commit. Here “reverted” means excluded from the sealed tree, as for existing
   manifest freezes; `seal` need not rewrite the agent's checkout. Admissible
   companion edits may still yield a commit; protected-only edits yield
   `sha=None`. Keep the existing repeat-violation handling in callers.
4. **Exercise the public seal boundary with real local Git fixtures.** Add
   `test_agent_steering_files_frozen_without_manifest_entry`, parameterized over
   root and nested examples for all five names: `AGENTS.md`, `CLAUDE.md`,
   `.claude/settings.json`, `.codex/config.toml`, `.mcp.json`, and their
   `src/nested/` equivalents. Cover addition, content modification, deletion
   and mode-only changes, with unchanged-base controls and case variants.
   Use exact root allowances plus `src/**`, omit `paths.frozen`, and assert
   that `manifest.matches` considers each attempted path allowed. Inspect the
   sealed tree, blobs, modes, `changed` and path-specific frozen diagnostics;
   include an ordinary allowed companion edit and a protected-only case.
   Add `test_manifest_cannot_unfreeze_steering_files` using parsed manifests
   with explicit protected-path allowances and both empty and unrelated
   `frozen` lists. Cover the exact-component negative controls in small
   additional parametrized cases. Attach the task's scenario markers to tests.
5. **Keep acceptance local and preserve existing behavior.** Run the focused
   seal tests, then `uv run pytest`, preserving every existing assertion.
   These tests need Git and temporary files, not an agent application, forge,
   live credentials or service. No human-only validation is required for this
   change. Existing `.git`, manifest freeze/allow, secret, size, symlink and
   submodule behavior outside the named steering policy remains intact.
6. **Keep documentation corrections narrow.** The current descriptions of
   harness-owned commits and frozen `CLAUDE.md` remain true. Audit the three
   allowed documents for claims made false by this change and correct only
   such claims, citing issue #163 if a correction is needed. Preserve all
   unrelated guidance and existing documentation assertions. If a correction
   needs a scoped heading check, select one exact column-zero heading through
   the next physical `^#{1,k} +\S.*$` heading (where `k` is its level), or EOF;
   missing or duplicate starts block the check. Do not interpret fences,
   HTML or indentation to hide recognized heading lines.

## Risks / Trade-offs

- Runs previously permitted to edit these files will now report violations;
  deliberate changes to repository instructions must be made outside this
  implementation seal path. No manifest escape hatch is provided.
- Case-insensitive component matching reserves case variants even on
  case-sensitive filesystems. This follows the current metadata policy and
  prevents platform-dependent protection; similarly named components remain
  editable when allowed.
- Merely extending `_unsafe` would miss reporting existing-file changes.
  Tests must include trusted base entries and assert diagnostics as well as
  retained tree content, especially for `.codex`.
- This preserves the existing trust in the base and the existing file-listing
  boundary. It does not claim to prevent an agent from reading its own changed
  instructions before sealing or to discover every possible steering file.
