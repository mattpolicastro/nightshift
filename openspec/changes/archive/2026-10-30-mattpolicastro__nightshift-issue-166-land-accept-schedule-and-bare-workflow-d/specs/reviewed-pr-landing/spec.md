## Purpose

Allow base workflow exclusion proof to recognize narrowly supported scheduled and manual triggers while preserving path-event ignore checks and conservative fallback.

## ADDED Requirements

## MODIFIED Requirements

### Requirement: Base workflow exclusion proof

Landing SHALL establish exclusion only from a complete nonempty PR changed-path set and supported CI `paths-ignore` patterns read at the recorded base SHA. It SHALL include deletions and both names of renames and SHALL require every changed path to be ignored for every declared `push` and `pull_request` event. It SHALL require `pull_request` and a supported nonempty ignore list for each declared path event. It SHALL additionally accept at most one block-form `schedule` containing one or more single- or double-quoted cron items, blank lines and comment lines, and at most one bare `workflow_dispatch` containing no nested lines. These two non-path events SHALL contribute no ignore list and SHALL NOT be checked against changed paths. Landing SHALL reject empty or malformed schedules, non-bare dispatches, duplicate events and all other event names. It SHALL use the single-workflow selection and deterministic physical-line/glob rules in the design, and SHALL retain ordinary CI waiting whenever proof is unavailable or ambiguous.

#### Scenario: Base content controls exclusion
- **WHEN** the PR changes root and nested Markdown files and the sole base workflow is `ci.yml` with `paths-ignore: ['**.md']` for its pull_request and optional push events
- **THEN** landing establishes exclusion using that base SHA, never the local checkout or PR-head workflow content
- **THEN** a different ignore list on the head cannot authorize the exemption

#### Scenario: Mixed and renamed paths need CI
- **WHEN** any changed path is not ignored, including a deletion or the old name of a code-to-Markdown rename
- **THEN** landing cannot establish exclusion and retains ordinary CI rules
- **THEN** matching is case-sensitive against full paths, so `*.md` does not ignore nested Markdown while `**.md` does

#### Scenario: Incomplete or ambiguous evidence needs CI
- **WHEN** paths are empty, missing, malformed, duplicated or incomplete, pagination/counts cannot establish completeness, a provider cap is reached, or workflow selection/content/syntax/globs are unsupported or ambiguous
- **THEN** landing cannot use the exemption, including for multiple workflows, missing ignore lists on path events, negation, duplicate trigger fields or unreadable base content
- **THEN** a deadline or head/draft failure still refuses through the existing gate rather than becoming permission to skip CI

#### Scenario: Schedule and dispatch events do not block exclusion
- **WHEN** an otherwise supported trigger block contains a valid `schedule`, a bare `workflow_dispatch`, or both, in any event order
- **THEN** `ignored_paths` returns exactly the declared push/pull_request ignore lists in their existing order; schedule and workflow_dispatch contribute no ignore list and are never matched against changed paths
- **THEN** root and nested Markdown paths ignored by all declared path events remain eligible, while a missing pull_request or a nonmatching path-event ignore list still prevents exclusion

#### Scenario: Schedule accepts only cron items
- **WHEN** a schedule header has only optional spaces after its colon and its block contains one or more exact four-space `- cron: ` lines with single- or double-quoted scalars under the existing scalar grammar, optional trailing spaces, blank lines and whole-line comments
- **THEN** the parser accepts schedule without contributing an ignore list, including multiple cron items and CRLF input
- **WHEN** schedule is repeated, empty or comment-only at an event/block/end boundary, uses a flow form, or contains an unquoted value, inline comment, wrong indentation, other key or any unsupported nested line
- **THEN** `ignored_paths` returns `None` and exclusion cannot be established

#### Scenario: Dispatch with nested lines needs CI
- **WHEN** workflow_dispatch has an inline value such as `{}`, is repeated, or has any nested physical line, including inputs, branches, paths-ignore, list items, indented comments or space-only lines
- **THEN** `ignored_paths` returns `None` and exclusion cannot be established
- **THEN** replacing `  push:` in the existing `TRIGGER` fixture with `  workflow_dispatch:` still returns `None` because its nested branches/paths-ignore lines are refused
- **THEN** every pre-existing ambiguous-trigger case remains refused and no other event name becomes supported
