## Purpose

Qualify managed draft workspace placement by format while preserving daemon evidence, existing OpenSpec paths and legacy state-migration guarantees.

## MODIFIED Requirements

### Requirement: Separate draft namespace

Build drafting SHALL use `<state_root>/drafts/<change>` for OpenSpec and `<state_root>/drafts/@speckit/<owner>/<repo>/<change>` for native spec-kit, selecting the format and repository from the trusted manifest as specified by Repository qualified native draft workspaces. Both locations SHALL remain separate from daemon `proposals/<repository>` receipts and jobs. Creating or replacing a draft SHALL preserve daemon evidence regardless of change-name collisions. Replacement SHALL affect only the selected leaf workspace and SHALL refuse symlink or non-directory managed ancestors before deletion or cloning.

This format exception replaces the former unconditional flat draft-path rule to isolate repository-local native names without allowing a valid flat change name to occupy the native namespace. OpenSpec paths, existing legacy-workspace refusals and completed compatibility links SHALL remain unchanged. Existing legacy relocation and journal recovery SHALL retain their recorded flat destinations; this change SHALL NOT migrate or adopt old flat native outputs, rewrite receipts or namespace runs. Subsequent native drafts SHALL use the new managed location and leave old outputs intact.

#### Scenario: Draft replacement preserves daemon evidence
- **WHEN** canonical and alias propose invocations create and re-draft the valid change owner__repo while daemon proposals/owner__repo contains receipts and job evidence
- **THEN** the production output resolver selects drafts/owner__repo and only that workspace is replaced; receipt and job bytes, paths, mtimes and offline inventory identities are unchanged
- **THEN** this holds before migration, after migration and after the completion marker makes later migration a no-op; existing proposal result schemas, branch refs and delivery behavior remain unchanged

#### Scenario: Generated names cannot collide with another repository
- **WHEN** intake generates owner__repo-issue-7-add-a-gate for owner/repo and daemon evidence exists for owner/repo-issue-7-add-a-gate
- **THEN** drafting and re-drafting use drafts/owner__repo-issue-7-add-a-gate, preserving that other repository's proposals/owner__repo-issue-7-add-a-gate receipts and jobs unchanged

#### Scenario: Unsafe draft replacement refuses
- **WHEN** an existing managed draft path or its drafts parent is a symlink or non-directory, or the same change still has a real legacy proposal workspace
- **THEN** propose refuses before deletion or cloning, retaining both draft and daemon evidence and directing an unmigrated workspace to nightshift migrate
- **THEN** canonical daemon proposals are never searched as draft fallback; a completed old-path compatibility link to the exact canonical draft remains supported
