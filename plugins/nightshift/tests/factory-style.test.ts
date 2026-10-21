import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { FactoryActInput, FactoryRun } from '../shared/factory';
import { colors, styleView } from './factory-style-harness';

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
