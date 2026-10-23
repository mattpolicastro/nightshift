## Context

The front door starts drafting from an intake label and already understands
`blocked-by:`. It does not lint issue wording or prevent concurrent file work
before the label is applied. This change adds an operator command preceding
that flow, using only new `nightshift/arm.py`, `tests/test_arm.py`, and
`docs/arming.md`.

`queue.blockers(body)` parses explicit dependency markers, including bullets,
quotes, mixed case, and multiple issue numbers. `queue.open_blockers` checks
only closed state; it cannot detect closed-as-not-planned dependencies.
`propose.paths(text, root=...)` recognizes backticked paths, normalizes function
suffixes, and recognizes existing root files. `propose.in_flight(source, change)`
returns path-to-change lists for running and paused runs in the same resolved
checkout, skipping unreadable runs. `critic.open_pr_files(github, branch,
runner=...)` returns a path-to-PR-label map plus limitation notes for all open
PRs, including renamed paths, pagination, and partial results. None needs an edit.

The existing `issue-intake`, `in-flight-file-map`, and `spec-critic` requirements
remain unchanged. The new lint of completion reason is deliberately local to
arming; it does not redefine when the daemon considers a dependency satisfied.
The README's existing direct-label workflow remains supported.

## Goals / Non-Goals

Goals are to report all actionable issue-writing findings in one pass, expose
unchained file overlap, and make label decisions predictable and testable without
GitHub, a daemon, an agent, or a Paseo host.

Non-goals are semantic understanding of prose, proving test existence, discovering
unmentioned files, expanding filesystem globs, scheduling work, repairing
dependency graphs, changing shared helpers, or implementing the later single CLI.
Implementation must preserve every existing file, especially
`nightshift/queue.py`, `specloop/propose.py`, `specloop/critic.py`,
`nightshift/cli.py`, `specloop/cli.py`, `README.md`, `KNOWN-LIMITATIONS.md`,
`docs/specloop/README.md`, all existing tests including
`tests/test_status_pause_since.py`, and everything under `plugins/nightshift/`.
Only the three new files above are implementation outputs.

## Decisions

### Entry point and boundaries

Expose `main(argv: list[str] | None = None) -> int` and a module guard in
`nightshift/arm.py`. Accept one or more positive issue numbers, optionally
prefixed by `#`, deduplicated in argument order. Resolve the checkout root using
`git rev-parse --show-toplevel` and its repository using `gh repo view --json
nameWithOwner,url`; retain the URL hostname for non-default GitHub hosts. This
version operates on the current checkout only and has no cross-repository issue
arguments. Invalid arguments exit 2 before collection.

Keep pure section/lint, graph reachability, and overlap functions separate from
collection and label writes. Use an injectable subprocess-compatible runner,
argv arrays, JSON responses, and a 30-second timeout for new GitHub calls; pass
the same runner to `critic.open_pr_files`, retaining its own overall budget.
Tests patch the boundary and local run root, never contact live services.

### Issue lint

Fetch each target using `gh issue view NUMBER --repo REPO --json
number,body,state,stateReason,labels`. An empty body is valid input to lint.
Read the body only, not title or comments. Recognize the four bold section
markers at the start of a line after optional whitespace, case-insensitively:
`**Scope:**`, `**What to do:**`, `**Definition of done:**`, and
`**What CI cannot prove:**`. Include inline text after a marker; end a section
at the next bold section marker or Markdown heading. Skip fenced code when
recognizing markers. Missing markers each produce a separate finding.

A present definition of done needs a backticked identifier beginning `test_`
(also accept a qualified pytest node ending in that identifier), a single- or
double-quoted `test_` name, or a named test introduced by `test "title"` /
`test 'title'`. A generic quoted sentence or `uv run pytest` alone is insufficient.
No repository test lookup is performed because the tests may be proposed work.
A missing definition of done produces the missing-section finding without an
additional missing-test finding; a present empty section fails the named-test rule.

Search the whole body case-insensitively for the finite discouraged families
`no existing test(s) [is/are/to be] modified/changed/edited`,
`do not/don't modify/change/edit existing tests`, and
`existing tests must remain unchanged`. Report each occurrence with a line and
suggest `existing assertions are preserved`. This is lexical lint, including
quoted examples; documentation must explain that limitation. Search only the
definition of done for whole-word `manually`, `by hand`, and `visually`, and
suggest moving each occurrence under What CI cannot prove.

Use `queue.blockers` directly on each target body. Fetch direct blockers' state
and `stateReason`; CLOSED plus NOT_PLANNED produces a finding naming the blocker.
CLOSED plus COMPLETED is acceptable; an open blocker is also acceptable for
arming, because the existing queue defers drafting. A closed blocker with an
absent or unknown reason is an incomplete-context finding, not assumed completed.

### Snapshot and identity

Collect all target bodies and shared overlap context before label writes. Fetch
all open `spec:draft` issues with paginated GitHub API reads, excluding PR entries.
Add all requested targets to the comparison set, including those not labelled
yet, so two unchained colliding arguments cannot both slip through. Ignore the
target's own issue, run, and PR evidence by resolved issue identity.

Extract paths only from each issue's Scope section using
`propose.paths(scope, root=checkout_root)`. Do not alter its lexical rules or
use the test-only `propose.overlaps` filter. Compare exact normalized tokens;
wildcards remain literal tokens, and directory/glob expansion is not promised.
An existing root file such as `README.md` and a new nested file both participate.
Paths elsewhere in the body do not contribute to issue Scope.

Call `propose.in_flight(checkout_root, "")` once, then filter self evidence by
identity. Recognize front-door change names with the producer convention
`<repo-basename>-issue-<positive-number>-<slug>`, requiring a full match and a
1–40 character lowercase alphanumeric/hyphen slug. The helper has already
restricted runs to the same checkout. Do not recompute identity from current
issue titles, which may have changed. Preserve unrecognized run attribution;
an overlap with such a run cannot be exempted by a guessed issue number.

Call `critic.open_pr_files(repository, "", runner=runner)` once to avoid
excluding a branch merely because it is currently checked out. Read paginated
open-PR metadata separately, retaining only same-repository heads matching
`spec/<front-door-change-name>` with the same identity convention. Join helper
attribution by PR number, retaining its URLs and old/new rename paths. A PR
number is never an issue number. Ordinary and fork PRs are outside this command's
front-door comparison set; this filtering does not affect critic behavior.
Missing metadata for a returned PR is incomplete context, rather than silently
classified as unrelated. Issue and PR pages and dependencies are cached for this
invocation; PR metadata and file reads are a best-effort snapshot, not atomic.

### Dependencies and findings

For each attributed issue, obtain its body and lazily follow `queue.blockers`
through unarmed intermediate issues as needed. Cache by repository and issue
number, and use visited sets so cycles terminate. An overlap is exempt exactly
when the candidate reaches the other issue or the other issue reaches the
candidate by one or more directed edges. Sharing a prerequisite is insufficient.
Cycles are not repaired or newly linted by this command; reachability retains
the stated rule, and docs warn that a cycle may leave drafting blocked forever.

Report one overlap finding per target, normalized path, and other issue,
combining all issue/run/PR evidence. Include `blocked-by: #N` where N is the
other source issue. For an unattributed run, report path and run and ask the
operator to identify its source issue before adding a dependency; never invent
a number. Dependency lookup failures cannot establish an exemption.

Findings have stable codes and text, with issue, line or path where applicable,
and a suggested remedy. Preserve argument order; sort findings within each issue
by code, location, and related identity. Continue collecting independent findings
after lint or lookup failures. Surface PR helper notes and failed issue/context
reads as incomplete-context findings while preserving available overlap evidence.
The existing in-flight helper silently skips unreadable runs; document this
blind spot rather than claim complete local-run coverage or modify the helper.

### Label and exit semantics

Print every finding and each target's final disposition. For an open readable
target, default mode adds only `spec:draft` when there are no findings. `--force`
allows labelling despite lint, overlap, or incomplete-context findings, retaining
all diagnostics. `--check` wins even when combined with `--force` and never
mutates GitHub. Use `gh issue edit NUMBER --repo REPO --add-label spec:draft`;
never create the label, remove another label, edit a body, or post a comment.

An existing `spec:draft` is a no-op for a clean issue (or with `--force`), reported
as already armed. Findings on an already-labelled issue still fail default/check
mode; existing labels are not proof that this check passed. Never remove and
reapply a label to change its actor. Unreadable or closed targets cannot be armed,
even with `--force`. Label-write errors are reported and do not stop later targets.
Do not retry uncertain writes blindly: re-read labels once after failure; treat
confirmed presence as success, otherwise report the write as unconfirmed.

Exit 0 when every target is clean in check mode, or every target is successfully
armed/already armed under the selected default/force policy. Exit 1 when any
target has findings without a permitted successful arm, cannot be read/armed,
or has an unconfirmed label write. Successful labels are not rolled back when
another target fails. This avoids hiding a rejected target behind another
target's successful write. No local receipts or state are written.

### Validation and operator documentation

Keep all new tests in `tests/test_arm.py`. Use fixture bodies, temporary checkout
files and run records, and a fake runner asserting read/write argv. Cover the
actual main boundary and imported helper integration, as well as pure functions.
Required tests are `test_arm_flags_missing_sections`,
`test_arm_flags_discouraged_phrasing`, `test_arm_requires_a_named_test`,
`test_arm_reports_unchained_overlap`,
`test_arm_accepts_transitively_chained_overlap`,
`test_arm_check_mode_never_labels`, `test_arm_labels_when_clean`, and
`test_arm_force_labels_and_reports`; add focused cases for the remaining scenarios.
Run `uv run pytest` and `uv run python scripts/check_types.py`, preserving the
existing assertions and baseline. Implementation tasks are small increments,
with their tests in the same session, limited to about 300 changed lines each.

`docs/arming.md` must provide the four-section issue template, test naming and
wording examples, direct/transitive dependencies, modes, exit statuses, batch
behavior, prerequisites (`gh` authentication and an existing `spec:draft` label),
and all coverage limits above. Explain that the authenticated label actor still
passes the existing daemon authorization checks and that arming is not a promise
of acceptance by the drafter or critic.

Human checklist for the implementation PR: on the next few issues armed with
the command, compare its findings with subsequent drafter/critic objections and
record missed objections and false positives. CI can establish the deterministic
contract with fixtures, but cannot establish whether the heuristics predict those
future reviews. This comparison is not an implementing-agent task.

## Risks / Trade-offs

- Lexical lint can flag explanatory examples and miss paraphrases. Finite rules
  and `--force` make that trade-off visible; no model call is introduced.
- Exact Scope tokens miss undeclared edits, glob-to-file intersections, new bare
  root filenames, and unreadable runs skipped by the existing helper. Document
  these limitations and recommend explicit backticked file paths.
- GitHub snapshots race with other operators and daemon transitions. Batch
  candidates are compared together, but no cross-process lock or reservation
  system is added. Later drafting/critic checks remain necessary.
- Additional graph and PR metadata reads cost API calls; pagination, per-call
  bounds, caching, and the existing PR-file budget keep failures diagnosable.
- `--force` intentionally permits unsafe overlap and incomplete context. It does
  not bypass daemon trust, dependency, admission, or review rules.
- Full-match branch/run naming is narrower than arbitrary custom proposals.
  Unidentified overlapping runs stay visible; non-front-door PRs are outside scope.
