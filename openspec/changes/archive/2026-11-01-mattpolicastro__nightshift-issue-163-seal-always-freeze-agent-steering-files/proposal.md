## Why

An implementer can currently change `AGENTS.md`, `CLAUDE.md`, `.claude/**` or
`.mcp.json` when the manifest allows those paths without explicitly freezing
them. Sealing those changes lets the implementer influence the instructions
used by subsequent agents or the independent reviewer. Instruction integrity
must be a harness invariant, not an optional manifest convention (issue #163).

## What Changes

- Always freeze `AGENTS.md`, `CLAUDE.md`, `.claude`, `.codex` and `.mcp.json`
  path components at every depth, using case-insensitive exact component
  matching consistent with the existing metadata guard.
- Preserve the base version of protected paths in sealed candidates and report
  attempted changes through the existing frozen-path violation interface.
  Manifest allowances cannot override this policy.
- Add the two requested regression tests, including nested paths, existing-file
  edits and deletions, and controls proving ordinary allowed edits still seal.
- Preserve all existing assertions and requirements. Correct only passages in
  `README.md`, `KNOWN-LIMITATIONS.md` or `docs/specloop/README.md` made false by
  this behavior; no broad documentation refresh is planned.

Out of scope: manifest schema or glob changes, opt-outs, changes to agent
sandboxes or review prompts, content-based instruction detection, additional
reserved names, credential discovery changes, and retry/escalation redesign.
This is a seal boundary, not a filesystem write prohibition during a session.

## Capabilities

### New Capabilities

- `agent-steering-seal`: unconditional protection and violation reporting for
  agent-steering paths during candidate sealing.

### Modified Capabilities

None. Existing `forge-seal` credential discovery/scanning requirements and
`specloop-documentation` requirements remain intact; no existing requirement
needs a MODIFIED or REMOVED delta.
