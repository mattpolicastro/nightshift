## Context

The checkout contains #165's body-based timeline actions and unpaginated comment
readers. `GiteaForge.label_event_pages` still calls `_pages`, which requires a
list and interprets `X-Total-Count` as a stable listing total. Gitea 28.2.0 instead
returns counts for each timeline page and JSON null with count zero past the end.

`checks_for_sha` validates one `(state, total_count)` envelope snapshot across
all pages and reads each row's `state`. The real row field is `status`. Its
terminal envelope has `state: pending`, `total_count: 0`, and `statuses: null`,
even after a success page. The caller in `nightshift/land.py` consumes the
normalized combined check; neither that interface nor landing policy needs to
change. FakeGitea currently slices status rows into an empty list while retaining
the initial envelope, hiding both differences. The unchanged contract fixture
also supplies legacy row `state` values to FakeGitea.

The root README, affected adapter and fixtures, and the `gitea-forge`,
`gitea-landing` and `forge-interface` requirements inform this design. No
AGENTS.md was present in the checkout or its ancestor directories. PR #167's
setup script is not present in this checkout; the human live check uses that
PR's setup, without adding provisioning work to this change.

## Goals / Non-Goals

Accept the two observed wire shapes with complete, conservative reads, retain
normalized label/check outputs, and prove behavior through socket-free tests.
Keep all assertions except explicitly inventoried obsolete shape fixtures;
finish with `uv run pytest` green and no new type-baseline errors.

Do not alter any other endpoint's pagination, GitHub behavior, landing gates,
daemon eligibility, trust, retries, guards, or contract sources. Do not add a
generic permissive-null mode, a server-version probe, or rich-text recognition.
No task parses Markdown, HTML, fences, headings or checklists; their existing
rules and fixtures are outside this change.

## Decisions

### Two tasks and requirement ownership

Implement timeline handling first, then combined-status handling, each with its
fake changes and tests in the same session, targeting about 200 changed lines.
The change's specification deltas are acceptance criteria, not implementation
files to rewrite. Canonical `openspec/specs/gitea-forge/spec.md` is updated by
the normal finish/archive application of these deltas, not a third task.

**Complete endpoint pagination** is modified to state the two endpoint
exceptions while retaining every general safeguard and #165's unpaginated
readers. **Exact SHA combined status** is modified because valid terminal
envelopes need not share the nonempty snapshot and rows use another wire key.
**Offline fake and optional live contracts** is modified only to model these
shapes and enumerate the new fixture exceptions alongside #165's established
ones. **Unpaginated comment listings** is modified only to qualify its other
endpoint preservation clause with the timeline and combined-status exceptions
defined by **Complete endpoint pagination** and **Exact SHA combined status**.
Its remaining text and #165's unpaginated readers and scenarios stay word for
word unchanged. All other requirements remain unchanged, including label actions,
allowlist projection, exact-head landing policy and GitHub transcripts.

### Timeline specialization stays inside the existing loop

In `nightshift/forge/gitea.py:_pages`, identify only the full API path
`/api/v1/repos/{o}/{r}/issues/{n}/timeline`: two nonempty encoded repository
segments and a positive decimal issue number, with no extra suffix, query or
fragment in the path. Use an exact full match, not a `/timeline` suffix check.
This also covers direct private-helper callers. Keep the signature and the
existing comment dispatch unchanged.

For that path alone, normalize a JSON-null payload to an empty list before list
validation. Do not coerce objects, scalars, malformed rows or other endpoints'
null responses. If `X-Total-Count` is present, retain decimal/integer syntax
validation, but do not assign it as the listing total, compare it across pages,
compare it with accumulated unique IDs, or stop when it is reached. Do not add
a new requirement that this optional header equal the page length. A short page
still requires another request; production page numbering starts at 1 with
limit 50. Fake page_size 1 can exercise per-page counts without changing that
request limit.

Continue through the existing empty-page branch so a null page with next-page
evidence still refuses. Preserve all Link fields, X-Next-Page validation and
contradictions, declared-end checks, positive exact-integer IDs, identical
overlap deduplication, conflicting duplicate refusal, repeated-page signatures,
and the 1,000-request bound. An identical overlap is not a new event; an entirely
repeated nonempty page still refuses. Keep label body/action validation, actor
projection and chronological ID order exactly as #165 established them.

### Combined-status decoding and terminal envelopes

Keep the change local to `checks_for_sha` and its `unpack` closure. Require a
dictionary with explicit `sha`, `state`, `total_count` and `statuses` keys;
missing `statuses` must not be confused with explicit null. Validate the exact
requested SHA, supported envelope state and nonnegative exact-integer count on
every envelope, including terminal ones.

For explicit `statuses: null`, require `total_count == 0`, return an empty row
list, and neither compare nor assign the nonempty snapshot. This accepts the
reported pending/zero/null terminal shape while preserving existing refusals of
null paired with a positive count. It does not relax `_pages` HTTP header total
checks for status reads. Contradictory next-page/Link evidence still refuses.

For nonempty list pages, validate each row's `status` as one of success, pending,
failure, error, warning or skipped, and require agreement of the envelope
`(state, total_count)` with every other nonempty page. A row's `state` is never
a fallback, even if `status` is missing, invalid or contradicts it. Use `status`
also for the final success/contradiction check. Retain positive IDs, overlap
deduplication, conflict/repeat checks and the complete unique-row count check.

Continue accepting explicit empty-list envelopes with the existing consistency
rules: when a nonempty snapshot exists they must match it; when none exists
they must have zero count. They do not establish a nonempty snapshot. Only null
receives the changed-envelope exception. This preserves existing empty-list
refusal assertions and avoids an unrelated compatibility change.

Replace the unconditional snapshot assertion with explicit empty handling:
after validated terminal evidence, no row-bearing page means JSON `[]` in
ResponseText. Otherwise compare collected unique rows with the saved count and
derive the single combined check from the saved state. Preserve mappings,
name/bucket/state output order, nontransient malformed-evidence errors, and the
rule that success requires at least one successful row and no rows outside
success/skipped. Pending or failed nonempty snapshots cannot be turned green by
any terminal envelope; skipped-only evidence stays nonpassing.

### Fake wire behavior and fixture replacement inventory

`FakeGitea._state_reply` emits each timeline slice with `X-Total-Count` equal to
that slice's length, and null with count zero when the slice is empty, including
an initially empty timeline. Keep #165's body-action serialization intact.

For state-backed combined statuses, copy rows at serialization time. When a
legacy row lacks `status` and has string `state`, move that value to `status`
in the emitted copy. Never overwrite an explicit `status`, synthesize a valid
value for missing/malformed input, or mutate stored fixture data. Emit no legacy
row `state` on normal wire responses. Nonempty pages retain the supplied
envelope; empty slices emit pending/zero/null while retaining SHA and other
unrelated fields. Explicit `route` replies remain raw, so malformed-wire and
missing-status tests cannot be repaired by the fake. Production decoding never
performs this legacy conversion.

Only the following fixture sites change; no existing assertion is deleted or
weakened. New assertions inspect actual fake response bodies and headers.

| Existing site | Replacement and governing requirement |
| --- | --- |
| `tests/fake_gitea.py:_state_reply`, timeline's shared slice response | Per-page count headers and null empty slices; **Timeline wire pages**. |
| `tests/fake_gitea.py:_state_reply`, combined-status slice response | Copy legacy input into row `status`; pending/zero/null empty slices; **Combined status wire pages**. This preserves `tests/contract/conftest.py` verbatim. |
| `tests/test_forge_gitea.py:combined` and row literals in `test_success_refuses_contradictory_status_rows`, `test_success_with_skipped_rows_requires_successful_evidence`, `test_combined_status_malformed_evidence_refused`, `test_combined_status_inconsistent_later_pages_refused` | Express intended wire results and malformed values in `status`, retaining every result/refusal/request assertion; **Combined status wire pages** and **Exact SHA combined status**. The existing null/positive-count cases remain invalid. |
| `tests/test_land_gitea.py:combined` | Default row uses `status`; envelope still uses `state`; **Combined status wire pages**. |
| `tests/test_land_gitea.py:expect_status` | Successful terminal response becomes pending/zero/null, including first-page empty reads; preserve exact request URLs, counts, timeouts and output assertions; **Combined status wire pages**. |
| `test_gitea_malformed_status_refuses` | Unknown-result and missing-ID row fixtures use `status`, preserving those independent failure causes; null with positive count still refuses; **Exact SHA combined status**. |
| `test_gitea_inconsistent_status_pages_refuse` | Repeat/conflict rows and success-with-* row mutations use `status`; retain deliberately inconsistent empty-list envelopes and every refusal/call-count assertion; **Exact SHA combined status**. |
| `test_gitea_success_requires_complete_pages` | Second row uses `status: skipped`; last response uses pending/zero/null; preserve complete-page and head-bracketing transcript; **Combined status wire pages**. |
| `test_gitea_ci_requests_share_deadline` and `test_gitea_ci_retry_and_pages_consume_original_budget` | Replace only valid terminal response payloads with pending/zero/null, preserving timeout, retry, remaining-budget and refusal assertions; **Combined status wire pages**, with unchanged landing deadline requirements. |

All unlisted fixtures/assertions stay intact, including timeline action tests,
generic null/total refusals, guard identities, transport failures, non-status
landing fixtures, and all GitHub and contract files. Updating raw status-row
fixtures preserves their existing assertion meaning; accepting old row keys in
the adapter to avoid fixture edits is prohibited.

### Offline verification and human live checklist

Add exactly named regressions `test_gitea_timeline_null_terminal_page`,
`test_gitea_timeline_per_page_total_is_not_a_listing_total`,
`test_gitea_status_rows_read_status_field`, and
`test_gitea_status_null_terminal_envelope`, with scenario markers. Parameterize
or add small adjacent cases for unchanged refusals and boundary behavior. Use
raw routes to distinguish wire validation from fake compatibility serialization.
Test both first-page emptiness and populated-then-terminal reads, and differing
nonempty status envelopes. Run focused tests with each task; after both run
`uv run pytest` and `uv run python scripts/check_types.py`. Do not update the
type baseline or edit the contract suite to obtain a pass.

CI establishes parsing and integration through injected transport and clocks.
It cannot establish the actual deployment's responses. After merge, the PR's
human checklist is:

- [ ] Use PR #167's setup script to provision the pinned `gitea/gitea:28.2.0`
  image and its required contract environment, then run
  `uv run pytest tests/contract --gitea-live`. Confirm all eight cases pass
  (four fake, four live), with no live skips; retain redacted command/results.
- [ ] On one real PR in an enrolled Gitea repository, read label history through
  `label_event_pages` and exact-head CI through `checks_for_sha` (or `pr_checks`).
  Compare event IDs/actions/actors and the normalized CI result with that PR's
  actual history and status. Record the PR, SHA and redacted observations before
  #150 considers lifting the hold. No merge, label mutation or daemon enablement
  is needed for these reads.

## Risks / Trade-offs

Timeline per-page counts cannot prove listing completeness; termination hints,
empty-page evidence, record checks and the page cap remain the available checks.
An exact endpoint match prevents this unavoidable limitation from leaking into
other listings. Null status envelopes must be validated before being excluded
from snapshot comparison, or missing evidence could become false absence.

Fake compatibility serialization is needed to leave the oracle unchanged, but
can mask fixture mistakes; raw-route regressions and direct wire assertions
separate those responsibilities. The fix does not provide a transactionally
stable snapshot if the server changes rows during pagination: inconsistent
nonempty evidence continues to refuse. Live server validation remains pending
until the human checklist is completed; offline success does not lift #150's hold.
