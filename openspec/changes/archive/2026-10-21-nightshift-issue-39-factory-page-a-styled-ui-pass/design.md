## Context

`FactorySection` and its private `FactoryRow` in
`plugins/nightshift/client/factory.tsx` use React Native primitives and
`PluginSurfaceProps['theme']['colors']`. Cards already have a surface and border;
their controls mostly have only colored text and vertical padding.
`factory-model.ts` supplies `factoryButtons`, `decideFactoryAction`,
`factoryDetails`, `groupFactoryRuns`, and workspace matching. Their behavior
already meets the run and finished-fold specifications.

The existing `factory-view.test.ts` evaluates the real TSX with a small hook
runner and native/SDK stubs. It supplies an empty theme and asserts exact text
and some tree/style details. It cannot prove token selection as written, and
must not be refactored to accommodate this change. No AGENTS.md was found in
the checkout or its ancestor directories; README.md points to CLAUDE.md, which
was read along with the plugin README and relevant capability specifications.

## Goals / Non-Goals

Give actions, navigation, status, and disclosure distinct visual treatments
using only `accent`, `accentForeground`, `surface0`, `surface1`, `surface2`,
`border`, `foreground`, `foregroundMuted`, `statusSuccess`, `statusWarning`,
and `statusDanger`. Make action targets usable on a phone and allow rows to wrap.

Preserve all existing behavior and requirements in `paseo-runs-page`,
`factory-finished-runs`, `run-context`, and `run-actions`, including run order,
exact allowed verbs, confirmations, notes, stale retention, visible results,
workspace lookup, and finished disclosure state across refreshes. No requirement
is replaced or removed. All other repository capabilities are unaffected.

Do not edit server/shared code, attention, specloop, the daemon, dependencies,
or any work-in-flight file. Do not change existing tests, their fixtures,
helpers, assertions, or names. New test files are preferred over append-only
additions to `factory-view.test.ts`. Deployment and visual acceptance are human
checks, not implementing-agent tasks.

## Decisions

1. **Keep presentation local and theme-driven.** Use the existing `Colors`
   alias, React Native `Pressable`, `Text`, and `View`, and plain resolved style
   objects. No new library, icon package, stylesheet, color literal, derived
   alpha color, or global theme change is needed. A small local style factory
   may accept `colors` and a visual role. No `factory-model.ts` change is
   expected; if essential, only add a pure presentation helper without changing
   any current function's signature or return shape. Colors must resolve from
   current props on render, so changing the host theme updates the surface.

2. **Assign action roles from the existing ordered button list.** Keep the
   output and order of `factoryButtons(run)` unchanged. Abort takes destructive
   precedence even at index zero or when it is the only verb; otherwise index
   zero is primary and later entries are outlined. Do not promote another verb
   when Abort occupies index zero. Use these defaults:

   | Role | Background | Text | Border |
   | --- | --- | --- | --- |
   | Primary | `accent` | `accentForeground` | `accent` |
   | Destructive | `surface1` | `statusDanger` | `statusDanger` |
   | Outlined | `surface1` | `foreground` | `border` |

   All run actions and Confirm/Cancel use `minHeight: 44`, `minWidth: 44`,
   `paddingHorizontal: 14`, `paddingVertical: 10`, `borderRadius: 8`,
   `borderWidth: 1`, centered content, and semibold text. Action and confirmation
   containers use horizontal flow, wrapping, and an 8-point gap. Confirm is
   primary for Publish/Finish and destructive for Abort; Cancel is outlined.
   Preserve all handlers, guards, disabled conditions and existing action-row
   opacity behavior. Keep literal confirmation messages and button text.

3. **Give status a compact chip without changing the status sentence.** Keep
   the change title a direct child of the card. Immediately below it, retain
   a parent `Text` whose descendant text is exactly
   `<status> · <done>/<total> tasks done`. A nested status `Text` receives the
   chip treatment: `surface2` background, rounded corners (radius 6), horizontal
   padding 6, vertical padding 2, and semibold text. Its text color is
   `statusWarning` for paused, `accent` for running, `statusSuccess` for done,
   and `foregroundMuted` for aborted. The separator and task progress stay
   `foregroundMuted`. This preserves the exact Text-node assertion
   `done · 1/3 tasks done` without hidden or duplicated text. Native rendering
   of inline chip padding and rounding must be checked by Matt.

4. **Use one navigation group with existing visibility rules.** Move links
   into a single `View` per card below the action/confirmation controls and
   above Details. Use `flexDirection: 'row'`, `flexWrap: 'wrap'`, a 12-point gap,
   and a top border in `border` with 8 points of top padding. Omit the container
   if it has no visible items. Item order is Open PR, Open change on GitHub,
   Open in Paseo. Use accent-colored, underlined text without action-button
   backgrounds or enclosing button borders; retain vertical touch padding.
   Open PR remains available whenever `run.pr` exists. The change link and
   workspace control remain conditional on `detailsOpen`, plus their current
   URL/workspace/navigation checks. Closing Details hides them even after a
   workspace lookup succeeds. The lookup still begins only on the first
   Details opening and is shared as before. Preserve literal labels and link
   text, `Linking.openURL` handlers, navigation arguments, and errors. Open in
   Paseo remains `accessibilityRole="button"` despite its link styling.
   Details content stays adjacent below its toggle, with every existing field,
   event, and command. Repositioning is visual only; no control becomes newly
   available or causes a different request.

5. **Style disclosures and refresh as secondary controls.** Details and
   Finished use horizontal alignment, an 8-point gap, muted semibold labels,
   and a chevron. Retain `paddingVertical: 10`, `alignSelf: 'flex-start'`, and
   the plain-object `style` on Finished, because current tests assert them;
   retain Details' matching vertical padding. Add `minHeight: 44` without
   changing their state or handlers. Keep Details' existing `▴`/`▾` text.
   Finished must keep its complete descendant text exactly `Finished (N)`:
   add a decorative, non-accessible chevron made from a small `View` with two
   `foregroundMuted` borders, rotated for open/closed state. Do not append a
   text glyph or duplicate its label. Keep the Details pressable a direct card
   child and the Finished pressable a direct section child, preserving existing
   row discovery and notice assertions. Refresh stays in its current header
   slot, using `surface2`, `foreground`, a 1-point `border`, radius 8,
   horizontal padding 12, vertical padding 8, and minimum 44-by-44 target;
   its compact appearance comes from content width and a 13-point label.
   Preserve `Refresh Factory`, `Checking…`, role, label, and disabled logic.

6. **Verify the rendered component without Paseo.** Add a test-only harness
   `plugins/nightshift/tests/factory-style-harness.ts`, following the existing
   VM/hook-runner approach without editing or importing the existing test
   module. Render the actual `FactorySection` and stub only React Native, SDK,
   RPC, navigation and timers. Supply distinct sentinel values for every allowed
   theme token and support a second theme, delayed RPC results, tree traversal,
   control presses, and cleanup. Helpers may be extended alongside each test
   that needs them; do not build an unrelated general renderer.

   Put tests in new `plugins/nightshift/tests/factory-style.test.ts`. Include
   the exact test names `action buttons are styled by role`,
   `links are grouped apart from actions`, and
   `status chip colour follows the run status`. Use scenario IDs as subtest
   names so evidence maps to this delta. Assert actual rendered Pressable/Text
   `style` properties, not source strings or only helper return values.
   Cover different first verbs, Abort first/only, Confirm/Cancel, all four
   statuses (expand finished rows), link ancestry and gates, disclosure
   decoration/state, refresh disabled state, and a second theme. Assert
   existing accessibility props and literal text alongside style checks.

   Run `npm test` and `npm run typecheck` from `plugins/nightshift` after each
   relevant task. At completion also run `uv run pytest` from the repository
   root. Existing view/client/server tests supply regression coverage for run
   ordering, confirmation dispatch, results in the row or above the list,
   stale/error behavior, and the finished fold; do not weaken or replace them.

## Risks / Trade-offs

- Inline status styling must preserve the existing sentence; React Native
  platforms may render nested Text padding and corners differently. Automated
  style checks prove token use, not actual layout or contrast.
- Links that depend on Details appear in the shared group directly above the
  toggle while it is open. This keeps one navigation row without changing
  availability or eager-loading host data.
- An outlined destructive treatment avoids inventing a danger-foreground
  token. Theme contrast remains a host responsibility and a visual check.
- A separate test harness duplicates a small amount of existing infrastructure
  to honor the prohibition on changing existing tests. Keep it narrow and use
  the same real component and model rather than reproducing their decisions.

Human checklist for the implementation PR (Matt, after deployment):

- Inspect the deployed Factory page in Paseo on desktop and a real phone,
  using available light/dark themes. Confirm it reads as a designed surface,
  with obvious primary/destructive actions and secondary navigation/disclosures.
- Check real button touch targets, wrapping on narrow screens, long change
  names, status-chip padding/corners, contrast, and the finished chevron in both
  states. Confirm labels and task progress remain readable without clipping.
- Inspect expanded Details, the grouped links, confirmations, and expanded
  finished cards. Check native focus/accessibility presentation and that the
  styled controls remain easy to distinguish. Node tests cannot substitute for
  host typography, native layout, device interaction, or Matt's visual judgment.
