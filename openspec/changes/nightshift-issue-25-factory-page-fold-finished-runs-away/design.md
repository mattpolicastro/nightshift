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
function's contract would fail that test. The issue requires existing tests to
remain unchanged, so the grouped interface must be opt-in. No existing Factory
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

1. **Keep compatibility explicit in the model.** Add overloads in
   `plugins/nightshift/client/factory-model.ts`:

   ```ts
   orderFactoryRuns(runs: readonly FactoryRun[]): FactoryRun[];
   orderFactoryRuns(
     runs: readonly FactoryRun[], options: { groupFinished: true },
   ): { active: FactoryRun[]; finished: FactoryRun[] };
   ```

   The one-argument overload keeps its exact current semantics. The grouped
   overload returns paused entries followed by running entries in `active`,
   preserving source order within each status, and done/aborted entries in
   `finished`, preserving their combined source order. Both return new arrays,
   retain the original run objects, and never mutate the input. Empty input
   returns two empty groups in grouped mode. Factory uses the grouped overload.

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

4. **Preserve row and snapshot behavior.** Leave action dispatch,
   confirmations, Details, lazy workspace lookup, and refresh logic intact.
   Retain the section-level notice keyed by change. A notice remains attached
   to its row even if that run becomes finished; expanding reveals it. A
   collapsed row is still present in the snapshot, so it must not trigger the
   existing above-list fallback, which is reserved for a run absent from the
   entire snapshot. Do not clear the notice when toggling. A failed refresh
   retains both groups and the current disclosure choice, alongside the
   existing stale indicator and error. Polling, foreground refresh, manual
   refresh, action serialization, and post-action refresh stay unchanged.

5. **Add tests beside each implementation step.** Append the exact top-level
   test names `orderFactoryRuns separates active from finished` in
   `factory-client.test.ts` and `finished runs are folded under a collapsed
   toggle` in `factory-view.test.ts`. Use scenario IDs as nested subtest names
   or in additional test names for evidence mapping. Test mixed statuses,
   stable order, immutability, empty groups, disclosure transitions, counts,
   and finished-row Details/links with the existing Node harness. Add focused
   regression cases for snapshot updates, stale failures, and action notices.
   Optional harness inputs may be added for controlled read responses and
   failures, retaining existing defaults and leaving every existing test body
   and assertion unchanged. No count assertion currently needs replacement.

   Required implementation checks: run `npm test` and `npm run typecheck`
   from `plugins/nightshift`, and `uv run pytest` from the repository root.
   Inspect the diff to confirm only allowed implementation paths changed and
   all existing test cases are untouched. These are implementation acceptance
   checks, not claims that the documentation draft implements the feature.

## Risks / Trade-offs

- The overload retains a legacy ordering mode to honor the explicit
  no-existing-test-edits constraint. Tests must exercise grouped mode directly;
  the view must not accidentally keep calling the flat overload.
- Finished rows and their action notices are hidden while collapsed. Keeping
  the notice attached and revealing it on expansion preserves existing notice
  ownership without inventing another notification channel.
- Conditional rendering can reset a finished row's local Details state when
  it is collapsed and reopened. The existing Details behavior still starts
  collapsed on row mount; persistent per-row UI state is outside this change.
- Node tests can prove ordering, visibility, handlers, and accessibility props,
  but cannot prove that the fold reads well in native layouts. After deploy,
  Matt checks the Paseo desktop and phone layouts with mixed and finished-only
  lists: count legibility, toggle discoverability and touch target, spacing,
  and expanded row/Details readability. Compact browser checks do not replace
  that phone check. Deployment and this human check are not automated tasks.
