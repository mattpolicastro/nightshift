import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { FactoryActInput, FactoryRun, FactorySnapshot } from '../shared/factory';
import { colors, styleView } from './factory-style-harness';

test('status chip colour follows the run status', async t => {
  await t.test('paseo-runs-page/status-chip-presentation/chip-follows-all-run-statuses', async t => {
    const runs: FactoryRun[] = (['done', 'running', 'aborted', 'paused'] as const).map(status => ({
      change: `chip-${status}`, status, tasks: { done: 1, total: 3 }, pr: '',
    }));
    const page = styleView(runs);
    t.after(page.dispose);
    await page.settle();

    function assertRows(expanded: boolean, tokens = colors) {
      const visible = [runs[3], runs[1], ...(expanded ? [runs[0], runs[2]] : [])];
      const controls = page.nodes().filter(node => node.type === 'Pressable' &&
        (node.props.accessibilityLabel?.startsWith('Details for ') || node.props.accessibilityLabel === 'Finished (2)'));
      assert.deepEqual(controls.map(node => node.props.accessibilityLabel), [
        'Details for chip-paused', 'Details for chip-running', 'Finished (2)',
        ...(expanded ? ['Details for chip-done', 'Details for chip-aborted'] : []),
      ], 'paused rows precede running rows; finished rows preserve snapshot order after the disclosure');
      const toggle = page.find('Finished (2)')!;
      assert.equal(toggle.props.accessibilityRole, 'button');
      assert.equal(toggle.props.accessibilityState.expanded, expanded);
      assert.equal(page.text(toggle.children), 'Finished (2)');
      for (const run of [runs[0], runs[2]]) {
        assert.equal(page.text().includes(run.change), expanded, 'finished titles are absent while folded');
        assert.equal(page.text().includes(`${run.status} · 1/3 tasks done`), expanded);
      }
      for (const run of visible) {
        const title = page.nodes().filter(node => node.type === 'Text' && page.text(node.children) === run.change);
        assert.equal(title.length, 1, 'each change title appears once');
        const card = page.nodes().find(node => node.type === 'View' && node.children.includes(title[0]));
        assert.ok(card, 'the change title remains a direct card child');
        assert.equal(card.children[0], title[0]);
        const sentence = card.children[1];
        assert.ok(sentence && typeof sentence !== 'string');
        assert.equal(sentence.type, 'Text', 'the status sentence immediately follows the title');
        const expected = `${run.status} · 1/3 tasks done`;
        assert.equal(page.text(sentence.children), expected);
        assert.deepEqual({ ...sentence.props.style }, { color: tokens.foregroundMuted });
        const chip = sentence.children[0];
        assert.ok(chip && typeof chip !== 'string');
        assert.equal(chip.type, 'Text');
        assert.deepEqual(chip.children, [run.status]);
        assert.ok(sentence.children.slice(1).every(child => typeof child === 'string'));
        assert.equal(page.text(sentence.children.slice(1)), ' · 1/3 tasks done', 'adjacent progress inherits the muted parent color');
        const statusColors = {
          paused: tokens.statusWarning, running: tokens.accent,
          done: tokens.statusSuccess, aborted: tokens.foregroundMuted,
        };
        assert.deepEqual({ ...chip.props.style }, {
          color: statusColors[run.status], backgroundColor: tokens.surface2,
          borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2, fontWeight: '600',
        });
        assert.equal(page.text(card.children), `${run.change}${expected}Details ▾`, 'no duplicate or hidden substitute text');
        assert.equal(page.nodes().filter(node => node.type === 'Text' && page.text(node.children) === expected).length, 1);
      }
    }

    assertRows(false);
    await page.press('Finished (2)');
    assertRows(true);
    const nextColors = Object.fromEntries(Object.entries(colors).map(([key, value]) => [key, `next-${value}`])) as typeof colors;
    page.setColors(nextColors);
    assertRows(true, nextColors);
    await page.press('Finished (2)');
    assertRows(false, nextColors);
    assert.deepEqual(page.actions, []);
  });
});

type Verb = FactoryActInput['verb'];
type Role = 'primary' | 'destructive' | 'outlined';
const labels = { resume: 'Resume', accept: 'Accept', publish: 'Publish', finish: 'Finish', abort: 'Abort' };
function runWith(actions: Verb[]): FactoryRun {
  Object.freeze(actions);
  return {
    change: 'styled-run', status: 'paused', tasks: { done: 1, total: 3 }, pr: '',
    action: { kind: 'pause', headline: 'Review needed', why: 'Paused', command: '', actions, pause_id: 'stuck:1' },
  };
}
const actionButtons = (page: ReturnType<typeof styleView>) => page.nodes().filter(node =>
  node.type === 'Pressable' && node.props.accessibilityLabel !== 'Refresh Factory' &&
  node.props.accessibilityLabel !== 'Details for styled-run');

function assertActions(page: ReturnType<typeof styleView>, verbs: Verb[], roles: Role[], tokens = colors, disabled = false) {
  const buttons = actionButtons(page);
  assert.deepEqual(buttons.map(button => page.text(button.children)), verbs.map(verb => labels[verb]));
  const palettes = {
    primary: [tokens.accent, tokens.accentForeground, tokens.accent],
    destructive: [tokens.surface1, tokens.statusDanger, tokens.statusDanger],
    outlined: [tokens.surface1, tokens.foreground, tokens.border],
  };
  buttons.forEach((button, index) => {
    assert.equal(button.props.accessibilityRole, 'button');
    assert.equal(button.props.accessibilityLabel, `${labels[verbs[index]]} styled-run`);
    assert.equal(button.props.disabled, disabled);
    const [backgroundColor, color, borderColor] = palettes[roles[index]];
    assert.deepEqual({ ...button.props.style }, {
      minHeight: 44, minWidth: 44, paddingHorizontal: 14, paddingVertical: 10,
      borderRadius: 8, borderWidth: 1, alignItems: 'center', justifyContent: 'center',
      backgroundColor, borderColor, opacity: disabled ? 0.5 : 1,
    });
    const label = page.nodes(button.children).find(node => node.type === 'Text');
    assert.ok(label);
    assert.deepEqual({ ...label.props.style }, { color, fontWeight: '600' });
  });
  if (buttons.length) {
    const group = page.nodes().find(node => node.type === 'View' && node.children.includes(buttons[0]));
    assert.ok(group);
    assert.deepEqual(group.children, buttons, 'the group contains only the supplied ordered actions');
    assert.deepEqual({ ...group.props.style }, { flexDirection: 'row', flexWrap: 'wrap', gap: 8 });
  }
}

test('action buttons are styled by role', async t => {
  await t.test('paseo-runs-page/action-button-presentation/primary-destructive-and-outlined-actions', async t => {
    const cases: [Verb[], Role[]][] = [
      [['resume', 'accept', 'abort'], ['primary', 'outlined', 'destructive']],
      [['accept', 'resume', 'abort'], ['primary', 'outlined', 'destructive']],
      [['publish', 'abort'], ['primary', 'destructive']],
      [['finish', 'abort'], ['primary', 'destructive']],
      [['resume', 'publish', 'finish', 'abort'], ['primary', 'outlined', 'outlined', 'destructive']],
    ];
    for (const [verbs, roles] of cases) await t.test(verbs.join(', '), async t => {
      const original = [...verbs];
      const run = runWith(verbs);
      const page = styleView([run]);
      t.after(page.dispose);
      await page.settle();
      assertActions(page, original, roles);
      const nextColors = Object.fromEntries(Object.entries(colors).map(([key, value]) => [key, `next-${value}`])) as typeof colors;
      page.setColors(nextColors);
      assertActions(page, original, roles, nextColors);
      assert.deepEqual(run.action!.actions, original, 'the supplied list is unchanged');
      assert.equal(run.action!.actions, verbs);
      await page.press(`${labels[verbs[0]]} styled-run`);
      if (verbs[0] === 'publish' || verbs[0] === 'finish') {
        assert.equal(page.actions.length, 0, 'confirmation still gates dispatch');
        for (const verb of verbs) {
          assert.equal(page.find(`${labels[verb]} styled-run`)?.props.disabled, true);
          assert.equal(page.find(`${labels[verb]} styled-run`)?.props.style.opacity, 0.5);
        }
        await page.press(`Confirm ${verbs[0]} styled-run`);
      }
      assert.deepEqual(page.actions.map(input => ({ ...input })), [{ change: run.change, verb: verbs[0], expect: 'stuck:1' }]);
    });
    await t.test('pending actions retain disabled styles and dispatch guards', async t => {
      let complete!: (value: unknown) => void;
      const pending = new Promise(resolve => { complete = resolve; });
      const verbs: Verb[] = ['resume', 'accept', 'abort'];
      const page = styleView([runWith(verbs)], { act: () => pending });
      t.after(page.dispose);
      await page.settle();
      page.find('Resume note for styled-run')!.props.onChangeText('Try again');
      await page.settle();
      await page.press('Resume styled-run');
      assertActions(page, verbs, ['primary', 'outlined', 'destructive'], colors, true);
      page.find('Accept styled-run')!.props.onPress(); // Even a direct stale/native invocation is guarded.
      assert.deepEqual(page.actions.map(input => ({ ...input })), [
        { change: 'styled-run', verb: 'resume', expect: 'stuck:1', note: 'Try again' },
      ]);
      complete({ outcome: 'done', detail: 'Resumed' });
      await page.settle();
      assertActions(page, verbs, ['primary', 'outlined', 'destructive']);
    });
  });
  await t.test('paseo-runs-page/action-button-presentation/abort-takes-precedence-over-primary', async t => {
    const cases: [Verb[], Role[]][] = [
      [['abort', 'resume', 'accept'], ['destructive', 'outlined', 'outlined']],
      [['abort'], ['destructive']],
      [[], []],
    ];
    for (const [verbs, roles] of cases) await t.test(verbs.join(', ') || 'empty list', async t => {
      const original = [...verbs];
      const run = runWith(verbs);
      const page = styleView([run]);
      t.after(page.dispose);
      await page.settle();
      assertActions(page, original, roles);
      assert.deepEqual(run.action!.actions, original);
      if (verbs.length) {
        await page.press('Abort styled-run');
        assert.equal(page.actions.length, 0);
        assert.ok(page.find('Confirm abort styled-run'));
      }
    });
    const page = styleView([{ ...runWith([]), action: undefined }]);
    t.after(page.dispose);
    await page.settle();
    assertActions(page, [], []);
  });
});

test('confirmation buttons preserve the action gate', async t => {
  await t.test('paseo-runs-page/confirmation-button-presentation/confirmation-styles-preserve-the-action-gate', async t => {
    const cases = [
      { verb: 'publish', pause: 'ready_to_publish:7', message: 'Publish styled-run? This pushes to GitHub.' },
      { verb: 'finish', pause: 'human_checks:7', message: 'Finish styled-run? This pushes to GitHub.' },
      { verb: 'abort', pause: 'stuck:7', message: 'Abort styled-run? This cannot be undone.' },
    ] as const;
    for (const { verb, pause, message } of cases) await t.test(verb, async t => {
      // Confirm's role is independent of the initiating action's list position.
      const run = runWith(['resume', verb]);
      run.action!.pause_id = pause;
      const other = { ...run, change: 'other-run' };
      const runs = [run, other];
      const snapshot: FactorySnapshot = { runs, stale: false, fetchedAt: '', error: null };
      let completeAction!: (value: unknown) => void;
      const pendingAction = new Promise(resolve => { completeAction = resolve; });
      let completeRefresh!: (value: FactorySnapshot) => void;
      const pendingRefresh = new Promise<FactorySnapshot>(resolve => { completeRefresh = resolve; });
      let reads = 0;
      const page = styleView(runs, {
        act: () => pendingAction,
        read: async () => ++reads === 1 ? snapshot : pendingRefresh,
      });
      t.after(page.dispose);
      await page.settle();
      page.find('Resume note for styled-run')!.props.onChangeText('Only for resuming');
      await page.settle();

      function assertConfirmation(tokens = colors, change = run.change, disabled = false) {
        const confirm = page.find(`Confirm ${verb} ${change}`);
        assert.ok(confirm);
        assert.equal(confirm.props.accessibilityRole, 'button');
        assert.equal(confirm.props.accessibilityLabel, `Confirm ${verb} ${change}`);
        assert.equal(confirm.props.disabled, disabled);
        assert.equal(page.text(confirm.children), `Confirm ${verb}`);
        const group = page.nodes().find(node => node.type === 'View' && node.children.includes(confirm));
        assert.ok(group);
        assert.deepEqual({ ...group.props.style }, { flexDirection: 'row', flexWrap: 'wrap', gap: 8 });
        const cancel = page.nodes(group.children).find(node => node.type === 'Pressable' && page.text(node.children) === 'Cancel');
        assert.ok(cancel);
        assert.deepEqual(group.children, [confirm, cancel]);
        assert.equal(cancel.props.accessibilityRole, 'button');
        assert.equal(cancel.props.accessibilityLabel, undefined);
        assert.equal(cancel.props.disabled, undefined, 'Cancel remains available even during another pending action');
        for (const [button, backgroundColor, color, borderColor] of [
          [confirm, verb === 'abort' ? tokens.surface1 : tokens.accent,
            verb === 'abort' ? tokens.statusDanger : tokens.accentForeground,
            verb === 'abort' ? tokens.statusDanger : tokens.accent],
          [cancel, tokens.surface1, tokens.foreground, tokens.border],
        ] as const) {
          assert.deepEqual({ ...button.props.style }, {
            minHeight: 44, minWidth: 44, paddingHorizontal: 14, paddingVertical: 10,
            borderRadius: 8, borderWidth: 1, alignItems: 'center', justifyContent: 'center',
            backgroundColor, borderColor,
          });
          const label = page.nodes(button.children).find(node => node.type === 'Text');
          assert.ok(label);
          assert.deepEqual({ ...label.props.style }, { color, fontWeight: '600' });
        }
        return { confirm, cancel };
      }
      const confirmationAlerts = () => page.nodes().filter(node =>
        node.type === 'Text' && node.props.accessibilityRole === 'alert' && page.text(node.children) === message);
      function assertActionsDisabled(change: string, disabled: boolean) {
        for (const action of ['resume', verb] as const) {
          const button = page.find(`${labels[action]} ${change}`);
          assert.ok(button);
          assert.equal(button.props.disabled, disabled);
          assert.equal(button.props.style.opacity, disabled ? 0.5 : 1);
        }
      }

      await page.press(`${labels[verb]} styled-run`);
      assert.equal(page.actions.length, 0, 'opening confirmation sends nothing');
      assert.equal(confirmationAlerts().length, 1, 'the exact existing alert appears once');
      assertActionsDisabled(run.change, true);
      assertConfirmation();
      const nextColors = Object.fromEntries(Object.entries(colors).map(([key, value]) => [key, `next-${value}`])) as typeof colors;
      page.setColors(nextColors);
      const { cancel } = assertConfirmation(nextColors);
      assert.equal(confirmationAlerts().length, 1, 'theme changes preserve the message');
      await page.press(cancel);
      assert.equal(page.actions.length, 0, 'Cancel sends nothing');
      assert.equal(page.find(`Confirm ${verb} styled-run`), undefined);
      assert.equal(confirmationAlerts().length, 0);
      assertActionsDisabled(run.change, false);

      await page.press(`${labels[verb]} styled-run`);
      assert.equal(confirmationAlerts().length, 1);
      const { confirm } = assertConfirmation(nextColors);
      await page.press(`${labels[verb]} other-run`);
      assertConfirmation(nextColors, other.change);
      assert.equal(page.actions.length, 0, 'reopening either confirmation sends nothing');
      assert.equal(reads, 1, 'opening and cancelling confirmations does not refresh');

      confirm.props.onPress();
      confirm.props.onPress(); // Duplicate native callbacks before React renders must be coalesced.
      await page.settle();
      const expected = [{ change: 'styled-run', verb, expect: pause }];
      assert.deepEqual(page.actions.map(input => ({ ...input })), expected, 'only the authorized request is sent, without the resume note');
      assert.equal(page.find(`Confirm ${verb} styled-run`), undefined);
      assert.equal(confirmationAlerts().length, 0);
      assertActionsDisabled(run.change, true);
      assertActionsDisabled(other.change, true);
      assert.equal(page.find('Refresh Factory')!.props.disabled, true);
      const pending = assertConfirmation(nextColors, other.change, true);
      await page.press(`Confirm ${verb} other-run`);
      pending.confirm.props.onPress(); // The handler also guards a disabled native invocation.
      await page.settle();
      assertConfirmation(nextColors, other.change, true);
      await page.press(pending.cancel);
      assert.equal(page.find(`Confirm ${verb} other-run`), undefined);
      page.find(`${labels[verb]} other-run`)!.props.onPress();
      await page.settle();
      assert.equal(page.find(`Confirm ${verb} other-run`), undefined, 'pending actions cannot reopen confirmation');
      assert.deepEqual(page.actions.map(input => ({ ...input })), expected);

      completeAction({ outcome: 'done', detail: 'Completed' });
      await page.settle();
      assert.equal(reads, 2, 'confirmation triggers the existing post-action refresh');
      assertActionsDisabled(run.change, true);
      assertActionsDisabled(other.change, true);
      confirm.props.onPress();
      await page.settle();
      assert.deepEqual(page.actions.map(input => ({ ...input })), expected, 'the guard lasts through the post-action refresh');
      completeRefresh(snapshot);
      await page.settle();
      assertActionsDisabled(run.change, false);
      assertActionsDisabled(other.change, false);
      assert.equal(page.find('Refresh Factory')!.props.disabled, false);
      assert.deepEqual(page.actions.map(input => ({ ...input })), expected);
    });
  });
});
