## Purpose

Preserve Gitea landing's human-check and refusal semantics while consuming the
complete, unpaginated issue-comment response returned by Gitea 28.2.0.

## ADDED Requirements

## MODIFIED Requirements

### Requirement: Existing human check semantics

Gitea landing SHALL preserve the existing checklist line rule, publication token
matching, source selection, in-place edits, attestation format, one-create
reconciliation, source revalidation and duplicate recovery. Each complete issue-
comment listing SHALL use one GET without page/limit or a terminal-page request,
because Gitea 28.2.0 ignores pagination on that endpoint. Landing SHALL refuse
malformed or duplicate-ID comment responses without authorizing subsequent
mutations. Transport retries, deadline guards and stricter landing record
validation SHALL remain unchanged. Landing SHALL NOT infer Markdown or HTML
structure to hide candidate evidence.

#### Scenario: Physical lines and literal markers
- **WHEN** selected sources contain checks or marker lookalikes inside fences, HTML, blockquotes or indented text
- **THEN** every line matching `^[ \t>]*[-*+] \[( |x|X)\] ` counts, and only the matched checkbox state changes during completion
- **THEN** publication uses the existing body-prefix and literal change/optional-round token rules; attestation uses exact marker substring/occurrence counting, with duplicate or untrusted matches refusing
- **THEN** prose, line endings, existing ticks and unrelated comments remain intact, and copied checklist text is HTML-escaped in attestation

#### Scenario: Partial and uncertain writes recover
- **WHEN** a checklist edit fails, a single attestation POST loses its response, or a later invocation sees a late duplicate
- **THEN** completed source edits persist, within-invocation reconciliation rereads the complete comment list with one GET without repeating POST, and an unresolved result blocks finish/merge
- **THEN** a later invocation rereads actual state, edits only unfinished sources, updates a unique trusted marker or refuses duplicates with existing recovery guidance; no cross-attempt create guard is added

#### Scenario: Complete comment evidence remains mandatory
- **WHEN** landing discovers or revalidates publication or attestation evidence, including docs-only CI attestations, through the Gitea issue-comments endpoint
- **THEN** each successful listing uses one unpaginated GET and considers every returned comment with the existing human credential and trust policy
- **THEN** malformed records, duplicate IDs, contradictory completeness metadata, unreadable responses or deadline expiry refuse before subsequent mutations
- **THEN** the exact request fixtures reflect this endpoint behavior while all other landing and GitHub assertions remain unchanged
