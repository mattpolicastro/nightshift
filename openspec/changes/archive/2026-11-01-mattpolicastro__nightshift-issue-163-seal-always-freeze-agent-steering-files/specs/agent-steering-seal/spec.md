## Purpose

Keep agent and reviewer instructions under harness control by rejecting
candidate edits to reserved steering paths independently of manifest policy.

## ADDED Requirements

### Requirement: Unconditional steering freeze

The seal SHALL freeze every repository-relative path having a slash-delimited
component equal, ignoring case, to `AGENTS.md`, `CLAUDE.md`, `.claude`, `.codex`
or `.mcp.json`. This SHALL apply at every depth, including descendants of a
matching component. No manifest field SHALL override this built-in freeze.
Existing `.git` protection SHALL remain in force. Matching SHALL use whole
components, not substrings.

#### Scenario: Protected paths without manifest entries

- **WHEN** an implementer adds or changes a root or nested steering path and a valid manifest allows that path while omitting `paths.frozen`
- **THEN** the seal excludes the attempted change and reports a frozen-path violation for that path
- **THEN** protection covers all five reserved names, including files beneath `.claude` and `.codex`, at root and under `src/nested/`

#### Scenario: Manifest allowances cannot override protection

- **WHEN** a parsed manifest explicitly allows protected paths or their containing directory and has an empty or unrelated `frozen` list
- **THEN** attempted steering edits remain frozen and reported, even though the manifest's allowed-path matcher accepts them
- **THEN** unrelated allowed edits remain eligible for sealing

#### Scenario: Component matching is precise

- **WHEN** allowed edits target case variants of reserved names, matching directory components, and lookalikes such as `AGENTS.md.example`, `.claude-backup/settings.json` and `.mcp.json.example`
- **THEN** case variants and descendants of matching components are frozen at any depth
- **THEN** lookalikes are not frozen by the built-in steering policy and seal normally when otherwise admissible

### Requirement: Frozen edit results

The seal SHALL preserve protected paths' base content, mode or absence in
sealed candidates. Protected edits SHALL be excluded from `Sealed.changed`
and reported in `Sealed.violations` as `<path>: frozen path (change reverted)`.
Unchanged protected files SHALL NOT generate violations. Reversion SHALL NOT
require rewriting the agent's working copy.

#### Scenario: Existing steering edits are reverted and reported

- **WHEN** an implementer changes the content or executable bit of an existing protected file, or deletes it, alongside an admissible ordinary edit
- **THEN** the sealed commit retains the protected file's base blob and mode, includes the ordinary edit, and reports the protected path as frozen
- **THEN** `Sealed.changed` contains only the admissible edit, including when the protected file is beneath `.codex`

#### Scenario: Only protected changes produce no commit

- **WHEN** all attempted edits are additions, modifications or deletions of protected steering files
- **THEN** the seal returns `sha=None`, an empty `changed` list and path-specific frozen violations

#### Scenario: Unchanged steering files remain quiet

- **WHEN** protected files are present in the base and unchanged in the work tree while an ordinary allowed file changes
- **THEN** the seal preserves the protected entries, accepts the ordinary edit and produces no steering-path violations
