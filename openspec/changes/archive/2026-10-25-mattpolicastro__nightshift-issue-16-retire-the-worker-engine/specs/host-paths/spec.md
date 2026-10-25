## Purpose

Preserve host paths and legacy root validation without a worker execution engine.

## MODIFIED Requirements

### Requirement: Configured worker worktree root

`nightshift.config.load()` SHALL retain `[daemon].worktree_root` and its default `~/Projects/nightshift-wt`. It SHALL expand leading `~/`, preserve absolute locations and resolve relative values beside the selected config file independent of process cwd. It SHALL reject invalid types, blank values, unsupported tilde forms and NUL/line breaks with a `ValueError` naming `daemon.worktree_root`. Loading SHALL NOT create or migrate worktrees or eagerly validate repository manifests. This setting SHALL remain a compatibility value after retirement and SHALL NOT create worker execution capacity or relocate existing evidence.

#### Scenario: Worker default and absolute roots remain compatible
- **WHEN** worktree_root is omitted or names an absolute path
- **THEN** Config exposes the existing home-based default or that absolute location respectively, without moving existing worktrees or creating directories during loading

#### Scenario: Worker root expansion is independent of cwd
- **WHEN** configuration supplies a leading-home or relative worktree_root and loads from a different working directory
- **THEN** the root expands under the host home or resolves beside the selected config file respectively, preserving spaces as literal path characters

#### Scenario: Worker root validation preserves enrollment isolation
- **WHEN** worktree_root has an invalid type or path value
- **THEN** config loading rejects it with a ValueError naming daemon.worktree_root
- **WHEN** the root is valid and a default or explicit specloop enrollment manifest is absent or unusable alongside a healthy specloop repository
- **THEN** configuration still loads and existing repository-local manifest-health behavior remains in force
