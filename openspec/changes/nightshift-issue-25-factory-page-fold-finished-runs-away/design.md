## Context

`FactorySection` in `plugins/nightshift/client/factory.tsx` currently counts
every snapshot entry and maps `orderFactoryRuns` into `FactoryRow` components.
The model in `client/factory-model.ts` returns a flat array: paused runs first,
then all other runs in source order. The existing shared `FactoryRun` type
already restricts status to `paused`, `running`, `done`, and `aborted`; no
server or schema change is necessary.

The model test named `row ordering preserves source order within waiting and
other runs, including empty lists` explicitly expects a done run before a
running run when that was their input order. Changing the single-argument
function's contract would fail that test. Add a separate grouping helper and
leave the existing function and tests untouched. No existing Factory
view test asserts the count text.

The existing view tests execute the actual TSX with a small React hook runner
and mocked native/SDK interfaces. They support testing disclosure state,
rendered text, handlers, and RPC effects without Paseo. No `AGENTS.md` was found
in the checkout or its ancestor directories; the root and plugin READMEs and
the root README's linked `CLAUDE.md` guidance were consulted.

## Goals / Non-Goals

Goals: keep current work visible, retain access to every finished run, make
counts and disclosure state accurate, and leave existing workflows intact.
Preserve source snapshots and all existing test cases.

Non-goals: altering status production, action permissions, confirmation rules,
RPCs, refresh timing, caching, cleanup, or run lifecycle. No changes outside
the two named client files and `plugins/nightshift/tests/*` during
implementation. This draft itself changes only its OpenSpec change directory.

## Decisions

1. **Add a separate grouping helper.** Export this interface from
   `plugins/nightshift/client/factory-model.ts`:

   ```ts
   groupFactoryRuns(
     runs: readonly FactoryRun[],
   ): { active: FactoryRun[]; finished: FactoryRun[] };
   ```

   Leave `orderFactoryRuns` unchanged; add no overload or options parameter.
   `groupFactoryRuns` returns paused entries followed by running entries in `active`,
   preserving source order within each status, and done/aborted entries in
   `finished`, preserving their combined source order. The helper returns new
   arrays, retains the original run objects, and never mutates the input. It
   can reuse `orderFactoryRuns` on the filtered active list. Empty input returns
   `{ active: [], finished: [] }`. Factory uses `groupFactoryRuns`.

2. **Use one section-local disclosure.** In `FactorySection`, initialize
   `finishedOpen` with `useState(false)`. Render `active` using the existing
   `FactoryRow`, followed by a `Pressable` only when `finished.length > 0`.
   Its visible text and accessibility label are `Finished (N)`, its role is
   `button`, and `accessibilityState.expanded` equals `finishedOpen`. Pressing
   toggles that boolean. Render finished rows beneath it only while expanded,
   using the same row component, keys (`run.change`), and props as active rows.
   Use existing theme colors and disclosure spacing conventions. Opening the
   fold itself sends no action, refresh, or workspace lookup.

   Keep the boolean across snapshot updates, including an interval with no
   finished runs. A newly mounted section starts collapsed again. No storage
   or host preference is added. Do not automatically expand after an action.

3. **Count groups, not visible rows.** Show `N runs` for the active count,
   retaining existing `1 run` grammar. Append ` · M finished` only for a
   nonempty finished group, then append the existing ` · Stale` when warranted.
   Examples: `2 runs · 3 finished`, `1 run`, `0 runs · 2 finished`, and
   `1 run · 2 finished · Stale`. Expansion never changes counts. Keep
   `Loading Factory…` before a snapshot, and show `No Factory runs.` only
   when the entire loaded snapshot is empty, not when only active is empty.

4. **Keep action results visible while preserving snapshot behavior.** Leave action dispatch,
   confirmations, Details, lazy workspace lookup, and refresh logic intact.
   Retain the section-level notice keyed by change. In `FactorySection`, derive
   whether its row is rendered from membership in `active`, or membership in
   `finished` with `finishedOpen === true`. Only a rendered matching row receives
   the notice. Otherwise render it in the existing above-list alert slot as
   `${notice.change}: ${notice.text}`, including when its run remains in the
   snapshot but is folded. Use this visibility predicate instead of the current
   full-snapshot membership check. A Finish or Abort result therefore stays
   visible when the refreshed run moves to done or aborted. Expanding moves the
   notice into its row; collapsing moves it above the list; removing the run
   keeps it above the list. Render exactly one copy in every state. This routing
   applies to all existing notices, including refusals and transport errors,
   without changing their text or lifetime. Do not clear the notice or expand
   the fold automatically. A failed refresh
   retains both groups and the current disclosure choice, alongside the
   existing stale indicator and error. Polling, foreground refresh, manual
   refresh, action serialization, and post-action refresh stay unchanged.

5. **Add tests beside each implementation step.** Append the exact top-level
   test names `orderFactoryRuns separates active from finished` in
   `factory-client.test.ts` and `finished runs are folded under a collapsed
   toggle` in `factory-view.test.ts`. The requested model test name is retained,
   but its grouping assertions call `groupFactoryRuns`; existing
   `orderFactoryRuns` tests remain untouched. Use scenario IDs as nested subtest names
   or in additional test names for evidence mapping. Test mixed statuses,
   stable order, immutability, empty groups, disclosure transitions, counts,
   and finished-row Details/links with the existing Node harness. Add focused
   regression cases for snapshot updates and stale failures. Test both Finish
   and Abort through confirmation and post-action refresh, with finished rows
   initially folded and expanded, checking exactly one visible result after
   each expansion, collapse, and removal. Also retain coverage for a result in
   an active row and for refusals/errors using the same notice routing.
   Optional harness inputs may be added for controlled read responses and
   failures, retaining existing defaults and leaving every existing test body
   and assertion unchanged. No count assertion currently needs replacement.

   Required implementation checks: run `npm test` and `npm run typecheck`
   from `plugins/nightshift`, and `uv run pytest` from the repository root.
   Inspect the diff to confirm only allowed implementation paths changed and
   all existing test cases are untouched. These are implementation acceptance
   checks, not claims that the documentation draft implements the feature.

## Risks / Trade-offs

- Two small model helpers serve different callers. New grouping tests exercise
  `groupFactoryRuns` directly; the view must use that helper while existing
  `orderFactoryRuns` callers keep their behavior.
- Notice placement depends on rendered rows, not full-snapshot membership.
  Testing only whether the run exists would silently hide Finish and Abort
  results; test both disclosure states and transitions to prevent regression.
- Conditional rendering can reset a finished row's local Details state when
  it is collapsed and reopened. The existing Details behavior still starts
  collapsed on row mount; persistent per-row UI state is outside this change.
- Node tests can prove ordering, visibility, handlers, and accessibility props,
  but cannot prove that the fold reads well in native layouts. After deploy,
  Matt checks the Paseo desktop and phone layouts with mixed and finished-only
  lists: count legibility, toggle discoverability and touch target, spacing,
  expanded row/Details readability, and notice legibility above the list and
  within an expanded finished row. Compact browser checks do not replace
  that phone check. Deployment and this human check are not automated tasks.
