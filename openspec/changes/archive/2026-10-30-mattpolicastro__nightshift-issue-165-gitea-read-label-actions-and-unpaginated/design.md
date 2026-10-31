## Context

The pinned `gitea/gitea:28.2.0` contract run and a self-hosted instance expose two
incorrect assumptions: label timeline rows use `body`, and both comment-list
endpoints ignore pagination. `_pages` currently requires an empty terminal page,
so nonempty comments repeat and fail. `_read_issue_labels` already demonstrates
the single-GET pattern. No AGENTS.md is present in this checkout; README.md,
the affected implementations/tests and both capability specifications were read.

Landing delegates comment reads to `GiteaForge.comment_pages` through a subclass
that retains stricter body validation. Its independent `RecordingOpener` cannot
be repaired through FakeGitea. The approved scope amendment permits only comment
pagination fixture/assertion replacements in the two named landing test files.

There are two additional compatibility details inside the allowed scope:
`tests/contract/conftest.py:124–127` calls `_pages` directly for comments, and its
fake timeline fixture supplies `removed` booleans. Neither requires changing the
oracle: a narrow private-helper branch and fake wire serialization cover them.

## Goals / Non-Goals

Read the complete evidence returned by 28.2.0, refuse uncertain evidence, retain
all factual output shapes and trust rules, and keep the offline and live oracle
usable without modifying it. Full pytest and the existing type baseline must
pass after implementation.

Do not enable dispatch, change GitHub behavior, alter landing policy, relax
malformed-data checks, rewrite the general pagination loop, add a version probe,
or change retry policy. No Markdown/HTML parser or marker-rule change is needed.

## Decisions

### Drafted requirements and frozen implementation scope

The `specs/gitea-forge/spec.md` and `specs/gitea-landing/spec.md` files within
this change contain the complete proposed requirement deltas. They are written
during drafting and become read-only acceptance criteria for all three tasks.
The implementing agents may edit only the five code/test files allowed by the
proposal, with the stated restrictions on landing tests. They must not edit
`openspec/**`, including canonical specs, change artifacts or task checkboxes,
and must not run spec synchronization or archival commands. Applying these
deltas to canonical specifications is the separate finish/archive workflow's
responsibility, not an implementation task or a prerequisite for its tests.

The deltas explicitly replace the incompatible label-action and comment-list
requirements and narrowly permit the inventoried assertion replacements.
All other requirements in both capabilities remain in force. Verification
checks implementation behavior against these deltas without rewriting specs
or weakening the frozen contract oracle.

### Label action decoding and fake serialization

In `GiteaForge.label_event_pages`, require a string `body` exactly equal to `"1"`
or `""`; map these to `labeled` and `unlabeled`. Do not trim, coerce, default a
missing body, or consult `removed`, even if it contradicts body. Retain timeline
pagination, non-label filtering, positive IDs, actor/name/timestamp validation,
allowlist projection, conflict detection and increasing event-ID order.

New normal fixtures in `tests/test_forge_gitea.py` use body and omit removed.
FakeGitea's state-backed timeline serialization copies label rows, translating
legacy fixture input only when body is absent and removed is exactly boolean:
false becomes `"1"`, true becomes `""`. Omit removed from the emitted copy.
Never overwrite an explicitly supplied body or invent one from malformed legacy
input. Leave explicit `route` replies raw so malformed-wire tests stay effective.
This is a test-fixture compatibility conversion, never an adapter fallback.

### Complete comment reads and private-helper compatibility

Use a private complete-comment reader in `nightshift/forge/gitea.py` for
`/api/v1/repos/{o}/{r}/issues/{n}/comments` and
`/api/v1/repos/{o}/{r}/pulls/{n}/reviews/{id}/comments`. It calls `_request` once
with GET and no query. Successful reads issue exactly one HTTP request; ordinary
transport GET retries on transient failures remain possible and guarded.

Have `_pages` delegate only these exact endpoint shapes to that reader before
its pagination loop, including direct contract-setup calls. Match the full
repository API path with two nonempty encoded repository segments and positive
decimal issue/review numbers; a loose `/comments` suffix match is insufficient.
For these endpoints discard page/limit supplied to the private interface. Keep
all other `_pages` behavior, signature and caller options unchanged. The public
readers use this same route, with no parallel implementation or test-only branch.

Validate the complete response before returning: it must be a list of dicts,
each ID an exact positive int (not bool), unique within that response. Refuse
identical as well as conflicting duplicate IDs. Preserve `_record` dispatch,
including landing's override, body normalization, user projection, feedback
state/path/location validation and source-qualified IDs. Return `[]` for an
empty listing and `[records]` otherwise from the page-shaped interface. Cross-
review identical inline identities may still deduplicate at feedback aggregation;
conflicting identities still refuse. Do not conflate review-list pagination with
review-comment completeness.

Retain refusal of contradictory optional metadata without additional reads:
an X-Total-Count, if supplied, must be decimal and equal the complete row count;
X-Next-Page may only be absent, empty or `0`; any next relation in any Link header
refuses. Never follow response URLs. This preserves existing landing uncertainty
assertions for totals and next-page claims with one complete response.

FakeGitea returns entire issue-comment and per-review comment arrays regardless
of page/limit or configured page_size. Verify this by direct fake transport reads
with and without pagination parameters. Timeline, review lists, statuses,
repository labels, issues and pulls retain independent empty-terminated loops;
issue labels retain their existing single GET.

### Assertion replacement inventory

These are the only existing shape expectations to replace. Test names identify
the assertions even if line numbers move. No error, ownership, trust, payload,
formatting, secret-redaction or no-extra-write assertion is relaxed.

| Existing fixture/assertion | Replacement and governing requirement |
| --- | --- |
| `test_timeline_actions_sorted_and_projected` and `label_event` use removed; `test_invalid_timeline_evidence_refused` rejects nonboolean removed and creates an action conflict with removed | Body-based fixtures, invalid/missing body cases and body-based conflict; retain normalized event/order assertions. Governed by **Label timeline evidence**. |
| `test_complete_feedback_records_and_existing_format` requires limit 50 on every GET and later pages for conversation/inline endpoints | GET remains required; page/limit and later pages remain required only for reviews; each comment endpoint is queried once per top-level read with no query. Governed by **Unpaginated comment listings** and **Issue and feedback records**. |
| `test_feedback_partial_reads_refused`, `test_drafting_requires_complete_comment_pages`, `test_contract_later_page_failure_refuses_feedback` inject comment failures on page 2 | Inject comment failure on the sole unpaginated request; put duplicate/conflicting records together in that response. Retain page-2 failure coverage for reviews and every refusal/transience/redaction assertion. Governed by **Unpaginated comment listings**. |
| `test_comment_marker_rules_preserve_original_body` expects two GETs on an unchanged marked upsert | Expect one complete-list GET; preserve body bytes and no write. Governed by **Unpaginated comment listings**, retaining **Bot owned marked comments**. |
| `test_comment_upsert_ignores_humans_and_reads_all_pages` expects pages 1 through bots+2 | Expect one complete-list GET without query; retain human preservation, duplicate-marker refusal and current-ownership read before PATCH. Same requirements. |
| `test_gitea_comment_create_reconciles_marker` expects pages 1,2 then 1,2,3; `test_comment_reconciliation_failure_never_posts_again` injects a page-2 read failure | One unpaginated list before POST and one on reconciliation; inject the failure on that reconciliation read. Preserve one POST and all result/PATCH assertions. Governed by **Unpaginated comment listings**, retaining **Ambiguous comment reconciliation**. |
| `test_comment_guard_identity_at_every_boundary` has terminal-page stage 4, ownership stage 5, PATCH stage 6 and page-1 retry injection | Remove only the nonexistent terminal-page stage; ownership becomes 4 and PATCH 5; retry injection targets the unpaginated GET. Keep all exception-identity, stop-before-dispatch, context-reset and POST-count assertions. Governed by **Unpaginated comment listings**, retaining **Failure and guard boundaries**. |
| `test_unmarked_and_distinct_markers_create_without_touching_existing` expects GET,GET,POST for a distinct marker | Expect GET,POST; unmarked POST-only behavior and stored bodies stay exact. Governed by **Unpaginated comment listings**. |
| `tests/test_land_gitea.py:pages` and its callers, including `test_listed_bot_publication_and_human_attestation` | Flatten supplied record groups into one response at COMMENTS_PATH, including one GET for empty evidence; omit terminal empty pages. Keep RecordingOpener's exact URL/method/body/timeout checks. Governed by **Existing human check semantics**. |
| `test_lost_post_reconciles_complete_history_once` has explicit page-1/page-2 unreadable fixtures | One unpaginated failed read; retain complete-history, duplicate recovery and one-POST assertions. Governed by **Existing human check semantics**. |
| `test_unreadable_comment_evidence_refuses_before_mutations` spreads failure/duplicate/metadata fixtures across pages | One response: transport failure, both duplicate rows, contradictory total, or next header as appropriate. Keep all malformed body/ID/date and no-mutation assertions. Governed by **Existing human check semantics** and **Unpaginated comment listings**. |
| `test_comment_reads_cannot_outlive_current_budget` encodes page/limit in its request | Remove query only; retain response/retry/body-expiry cases, timeout and refusal assertions. Governed by **Existing human check semantics**; deadline policy is unchanged. |
| `test_gitea_pages_and_retries_bind_replaced_operations` expires on a terminal empty page and expects timeouts `[30,3,2,1]` | Remove the terminal-page fixture; make the successful retry consume the remaining two seconds, retaining deadline refusal. Expect `[30,3,2]` for the initial read and failed/successful retry attempts. This is solely the removed comment-pagination boundary, governed by **Existing human check semantics**. |
| `tests/test_land_docs_only.py:LandingScript.comments`, Gitea branch | Flatten supplied groups into one unpaginated GET. Preserve the GitHub branch and every other assertion byte-for-byte. Governed by **Existing human check semantics**. |

The approved landing expansion covers these issue-comment pagination fixtures
and assertions, not general RecordingOpener weakening or unrelated edits. The
additional sites beyond the issue's example line numbers encode the same
`/issues/{n}/comments` assumption. Preserve all remaining assertions, and keep
`tests/contract/` byte-for-byte unchanged. The **Offline fake and optional live
contracts** delta explicitly permits only the replacements above.

### Verification and preserved text rules

Add the four issue-named regression tests exactly as requested, with scenario
markers. Cover absent/empty/many comments (more than limit 50), empty per-review
lists, multiple review pages, raw malformed lists/rows/IDs, duplicate IDs,
non-string/unknown/missing action bodies and a contradictory removed field.
Existing tests retain trust, bot exclusion, formatting, inline conflicts,
reconciliation, guard identity and paginated-endpoint coverage. Run `uv run
pytest` and `uv run python scripts/check_types.py`; do not edit the type baseline.

Marker/checklist behavior is unchanged. When updating its fixtures, keep the
existing deterministic physical-line rules: adapter markers normalize CRLF and
one trailing newline, require exactly one line beginning `<!-- nightshift:` and
a valid exact final marker line; ambiguity refuses. Landing counts lines matching
`^[ \t>]*[-*+] \[( |x|X)\] ` and uses literal publication/attestation tokens;
duplicate/untrusted marker evidence blocks. No semantic fence, HTML or indentation
parsing may hide evidence. This work does not implement a new text recognizer.

## Risks / Trade-offs

Complete comment arrays use memory proportional to thread size, as the server's
API requires. There is no fallback pagination or guessed truncation threshold.
An exact endpoint branch in `_pages` is necessary because the unchanged oracle
uses that private helper; regression tests must ensure it cannot affect any
other listing. Fake legacy-fixture conversion must never conceal malformed body
evidence or be copied into production decoding.

CI can prove parsing, request transcripts and refusal behavior with injected
transport, but cannot establish actual server response shapes. Human checks
after merge, before treating this prerequisite for #150 as live-verified:

- [ ] Run `uv run pytest tests/contract --gitea-live` against pinned
  `gitea/gitea:28.2.0`, using the gitea-contract CI job's provisioning and required
  GITEA_CONTRACT settings. Confirm live label-event and comment cases pass rather
  than skip; retain redacted command/result evidence.
- [ ] Re-read timeline and comments for a real issue on an enrolled Gitea repo
  with label history and at least one comment. Confirm body-based actions and
  complete unpaginated comment retrieval agree with the real issue. These reads
  do not lift the daemon hold or require a merge/label mutation on that issue.
