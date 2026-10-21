## Purpose

Transport complete Nightshift drafting ideas through durable local files so issue and review text cannot exceed process argument limits or be parsed as CLI options.

## ADDED Requirements

### Requirement: File based drafting input

For each initial issue draft and spec-stage PR re-draft, Nightshift SHALL write the complete assembled idea as UTF-8 under the corresponding receipt's `<stem>.jobs` directory before dispatching `specloop propose`. It SHALL pass exactly one absolute `--idea-file` path and SHALL omit `--idea` and the idea payload from process arguments. It SHALL preserve existing text assembly, trust filtering, receipt content, branch selection and result delivery.

#### Scenario: Long issue thread
- **WHEN** an eligible trusted issue has at least 1 MiB of assembled title, body and trusted comments, and the drafting runner returns a successful draft
- **THEN** the runner reads the complete, untruncated idea from the file supplied by `--idea-file`, with no payload or `--idea` in argv
- **THEN** normal draft PR creation, issue linking and label updates complete

#### Scenario: Option like title
- **WHEN** an eligible issue's title begins with `--`
- **THEN** the file begins with that exact title and the title is not supplied as a command-line argument, allowing drafting and delivery to proceed normally

#### Scenario: Text and trust preserved
- **WHEN** an eligible issue contains Unicode, blank lines, trusted comments and comments from untrusted authors
- **THEN** the UTF-8 file contains exactly the title, body and trusted comment bodies in their existing order and blank-line format, without an extra trailing newline
- **THEN** untrusted comments remain excluded and the receipt retains the same assembled idea

#### Scenario: Spec revision feedback
- **WHEN** an unadmitted spec PR is revised with large new review feedback, using either an original proposal receipt or the PR title/body fallback
- **THEN** the file contains the complete existing revision idea and eligible feedback with their existing formatting
- **THEN** `specloop propose` receives `--idea-file` and the existing `--from-branch` ref, and successful revision updates the same PR normally

### Requirement: Durable drafting payload

Nightshift SHALL finish writing and close the idea file before dispatch. Each drafting invocation SHALL receive a distinct file that remains unchanged and available across polling and daemon restart. File preparation failure SHALL prevent dispatch and use the existing error path without falling back to argv or successful-draft delivery.

#### Scenario: Delayed background read
- **WHEN** a drafting job reads its idea after the initiating poll returns and daemon in-memory job tracking is reset, with a relative state directory containing spaces
- **THEN** its absolute file path still resolves to the complete original idea
- **THEN** intervening polls reuse the existing job and completed delivery occurs once

#### Scenario: Later round preserves earlier input
- **WHEN** an issue is re-armed with an answer or a spec PR begins another feedback round after its previous draft completes
- **THEN** the new drafting invocation receives a different file containing the new complete idea
- **THEN** the earlier invocation's file remains present and unchanged

#### Scenario: File preparation fails
- **WHEN** creating the payload directory or writing the idea raises a filesystem error
- **THEN** no drafting runner or background launcher is invoked for that attempt and no successful-draft delivery occurs
- **THEN** the error follows existing intake failure handling without retrying with `--idea`
