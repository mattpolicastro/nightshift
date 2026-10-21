import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { FactoryActInput, FactoryRun, FactorySnapshot } from '../shared/factory';
import { colors, styleView, type Node } from './factory-style-harness';

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

const navigationRun: FactoryRun = {
  change: 'linked-run', status: 'paused', tasks: { done: 1, total: 3 },
  pr: 'https://github.com/owner/repo/pull/42',
  action: { kind: 'pause', headline: 'Review needed', why: 'Paused', command: '', actions: ['resume', 'abort'], pause_id: 'stuck:1' },
  context: {
    summary: 'Group the navigation.', repo: '~/Projects/nightshift', branch: 'spec/linked-run',
    task: { label: '2.2', section: 'Paseo plugin', text: 'Group links.' },
    detail: 'Keep the diagnostic.\nAnd its second line.',
    recent: [
      { at: '2026-10-21T12:00:00Z', text: 'task 2.2 attempt 1 failed' },
      { at: '2026-10-21T11:00:00Z', text: 'task 1.1 landed' },
    ],
    github: 'https://github.com/owner/repo/tree/spec/linked-run/openspec/changes/linked-run',
  },
};
const linkedWorkspace = {
  id: 'workspace-42', workspaceDirectory: '/Users/studio/Projects/nightshift',
  archivingAt: null, activityAt: '2026-10-21T12:00:00Z',
};
type StylePage = ReturnType<typeof styleView>;
function parentOf(page: StylePage, child: Node) {
  const parent = page.nodes().find(node => node.children.includes(child));
  assert.ok(parent);
  return parent;
}
function assertNavigation(page: StylePage, run: FactoryRun, visible: number[], tokens = colors) {
  const descriptors = [
    [`Open PR for ${run.change}`, 'link', 'Open PR ↗'],
    [`Open change ${run.change} on GitHub`, 'link', 'Open change on GitHub ↗'],
    [`Open ${run.change} in Paseo`, 'button', 'Open in Paseo'],
  ];
  const toggle = page.find(`Details for ${run.change}`)!;
  const card = parentOf(page, toggle);
  const controls = visible.map(index => {
    const [label, role, text] = descriptors[index];
    const matches = page.nodes(card.children).filter(node => node.props.accessibilityLabel === label);
    assert.equal(matches.length, 1, `${label} appears exactly once`);
    const control = matches[0];
    assert.equal(control.type, 'Pressable');
    assert.equal(control.props.accessibilityRole, role);
    assert.equal(control.props.accessibilityLabel, label);
    assert.equal(control.props.disabled, undefined);
    assert.equal(page.text(control.children), text);
    assert.deepEqual({ ...control.props.style }, { paddingVertical: 10, alignSelf: 'flex-start' });
    assert.equal(control.children.length, 1);
    const labelNode = control.children[0] as Node;
    assert.equal(labelNode.type, 'Text');
    assert.deepEqual({ ...labelNode.props.style }, {
      color: tokens.accent, fontWeight: '600', textDecorationLine: 'underline',
    });
    return control;
  });
  descriptors.forEach(([label], index) => {
    if (!visible.includes(index)) assert.equal(page.find(label), undefined, `${label} stays unavailable`);
  });
  const groups = page.nodes(card.children).filter(node => node.type === 'View' &&
    (node.props.style?.borderTopWidth === 1 || controls.some(control => node.children.includes(control))));
  assert.equal(groups.length, visible.length ? 1 : 0, 'omit the secondary row when it has no items');
  if (!visible.length) return;
  const group = groups[0];
  assert.deepEqual(group.children, controls, 'all navigation items share one immediate parent in order');
  assert.deepEqual({ ...group.props.style }, {
    flexDirection: 'row', flexWrap: 'wrap', gap: 12,
    borderTopWidth: 1, borderColor: tokens.border, paddingTop: 8,
  });
  assert.equal(parentOf(page, group), card, 'the navigation row is a direct card child');
  assert.equal(card.children[card.children.indexOf(toggle) - 1], group, 'navigation sits directly above Details');
  for (const button of page.nodes(card.children).filter(node => node.type === 'Pressable' &&
    (node.props.accessibilityLabel === `Resume ${run.change}` || node.props.accessibilityLabel === `Abort ${run.change}` ||
      node.props.accessibilityLabel === `Confirm abort ${run.change}` || page.text(node.children) === 'Cancel'))) {
    const actionGroup = parentOf(page, button);
    assert.notEqual(actionGroup, group);
    assert.ok(!page.nodes(actionGroup.children).includes(group), 'navigation is outside action and confirmation groups');
    const branch = card.children.find(child => typeof child !== 'string' && page.nodes([child]).includes(button))!;
    assert.ok(card.children.indexOf(branch) < card.children.indexOf(group), 'actions and confirmations precede navigation');
  }
}
function assertDetails(page: StylePage, run: FactoryRun, expanded: boolean) {
  const toggle = page.find(`Details for ${run.change}`)!;
  assert.equal(toggle.props.accessibilityRole, 'button');
  assert.equal(toggle.props.accessibilityState.expanded, expanded);
  assert.equal(page.text(toggle.children), `Details ${expanded ? '▴' : '▾'}`);
  const card = parentOf(page, toggle);
  const command = `specloop status ${run.change} --events 20`;
  if (!expanded) {
    assert.equal(page.text(card.children).includes(command), false);
    return;
  }
  const content = card.children[card.children.indexOf(toggle) + 1] as Node;
  assert.equal(content.type, 'View', 'Details content stays adjacent to the toggle');
  const texts = page.nodes(content.children).filter(node => node.type === 'Text');
  const context = run.context;
  const fields = [
    ['Summary', context?.summary], ['Repository', context?.repo], ['Branch', context?.branch],
    ['Task', context?.task ? `${context.task.label} · ${context.task.section} · ${context.task.text}` : undefined],
    ['Pause detail', context?.detail],
  ].filter((field): field is [string, string] => Boolean(field[1]));
  const events = context?.recent ?? [];
  assert.deepEqual(texts.map(node => page.text(node.children)), [
    ...fields.flat(), ...(events.length ? ['Recent events', ...events.map(event => `${new Date(event.at).toLocaleString()} · ${event.text}`)] : []),
    'More detail (run on the Paseo host)', command,
  ]);
  for (const value of [...fields.map(([, value]) => value), command]) {
    assert.equal(texts.find(node => page.text(node.children) === value)!.props.selectable, true);
  }
}

test('links are grouped apart from actions', async t => {
  await t.test('paseo-runs-page/secondary-navigation-group/links-share-a-group-apart-from-actions', async t => {
    const opened: unknown[] = [], lookups: unknown[] = [];
    const page = styleView([navigationRun], {
      navigation: { openWorkspace: input => { opened.push({ ...input }); } },
      client: { workspaces: { list: async (input: unknown) => { lookups.push(input); return { entries: [linkedWorkspace] }; } } },
    });
    t.after(page.dispose);
    await page.settle();
    assertNavigation(page, navigationRun, [0]);
    assert.deepEqual(page.hosts, []);
    await page.press('Details for linked-run');
    assertNavigation(page, navigationRun, [0, 1, 2]);
    assertDetails(page, navigationRun, true);
    await page.press('Abort linked-run');
    assert.ok(page.find('Confirm abort linked-run'));
    assertNavigation(page, navigationRun, [0, 1, 2]);
    const nextColors = Object.fromEntries(Object.entries(colors).map(([key, value]) => [key, `next-${value}`])) as typeof colors;
    page.setColors(nextColors);
    assertNavigation(page, navigationRun, [0, 1, 2], nextColors);
    await page.press('Open PR for linked-run');
    await page.press('Open change linked-run on GitHub');
    await page.press('Open linked-run in Paseo');
    assert.deepEqual(page.urls, [navigationRun.pr, navigationRun.context!.github]);
    assert.deepEqual(opened, [{ serverId: 'host-1', workspaceId: linkedWorkspace.id }]);
    assert.deepEqual(page.actions, []);
    assert.deepEqual(page.hosts, ['host-1']);
    assert.deepEqual(JSON.parse(JSON.stringify(lookups)), [{ page: { limit: 200 } }]);
    assert.deepEqual(page.reads.map(input => ({ ...input as object })), [{ refresh: true }]);
  });

  await t.test('paseo-runs-page/secondary-navigation-group/grouping-preserves-link-availability', async t => {
    await t.test('lookup stays lazy and shared through collapse, loading and reopening', async t => {
      let complete!: (value: unknown) => void;
      const pending = new Promise(resolve => { complete = resolve; });
      let lookups = 0;
      const first = { ...navigationRun, pr: '' };
      const second = { ...navigationRun, change: 'second-run' };
      const page = styleView([first, second], {
        navigation: { openWorkspace() {} },
        client: { workspaces: { list: () => { lookups++; return pending; } } },
      });
      t.after(page.dispose);
      await page.settle();
      assertNavigation(page, first, []);
      assertNavigation(page, second, [0]);
      assert.equal(lookups, 0);
      assert.deepEqual(page.hosts, []);
      await page.press('Details for linked-run');
      assertNavigation(page, first, [1]);
      assertDetails(page, first, true);
      await page.press('Details for linked-run');
      await page.press('Details for second-run');
      assertNavigation(page, first, []);
      assertNavigation(page, second, [0, 1]);
      assert.equal(lookups, 1);
      complete({ entries: [linkedWorkspace] });
      await page.settle();
      assertNavigation(page, first, []);
      assertDetails(page, first, false);
      assertNavigation(page, second, [0, 1, 2]);
      for (const run of [first, second]) {
        if (run === first) await page.press(`Details for ${run.change}`);
        assertNavigation(page, run, run.pr ? [0, 1, 2] : [1, 2]);
        assertDetails(page, run, true);
        await page.press(`Details for ${run.change}`);
        assertNavigation(page, run, run.pr ? [0] : []);
        assertDetails(page, run, false);
        await page.press(`Details for ${run.change}`);
        assertNavigation(page, run, run.pr ? [0, 1, 2] : [1, 2]);
        assertDetails(page, run, true);
      }
      assert.equal(lookups, 1);
      assert.deepEqual(page.hosts, ['host-1']);
      assert.equal(page.reads.length, 1);
      assert.deepEqual(page.actions, []);
      assert.deepEqual(page.urls, []);
    });

    const cases = [
      { name: 'no URLs', github: '', entries: [linkedWorkspace], visible: [2] },
      { name: 'no matching workspace', entries: [{ ...linkedWorkspace, workspaceDirectory: '/srv/other' }], visible: [1] },
      { name: 'archived workspace', entries: [{ ...linkedWorkspace, archivingAt: '2026-10-21T13:00:00Z' }], visible: [1] },
      { name: 'empty workspace list', entries: [], visible: [1] },
      { name: 'no navigation support', navigation: {}, entries: [linkedWorkspace], visible: [1] },
      { name: 'no client', client: undefined, visible: [1] },
      { name: 'no workspace API', client: {}, visible: [1] },
      { name: 'no workspace list method', client: { workspaces: {} }, visible: [1] },
      { name: 'lookup fails', fails: true, visible: [1] },
      { name: 'no context', context: undefined, entries: [linkedWorkspace], visible: [] },
      { name: 'empty context', context: { summary: '', repo: '', branch: '', task: null, detail: '', recent: [], github: '' },
        entries: [linkedWorkspace], visible: [] },
      { name: 'no visible navigation', github: '', entries: [], visible: [] },
    ];
    for (const fixture of cases) for (const pr of ['', navigationRun.pr]) {
      await t.test(`${fixture.name}, ${pr ? 'with' : 'without'} PR`, async t => {
        const run: FactoryRun = { ...navigationRun, pr, context: 'context' in fixture ? fixture.context : {
          ...navigationRun.context!, github: fixture.github ?? navigationRun.context!.github,
        } };
        let lookups = 0;
        const page = styleView([run], {
          navigation: fixture.navigation ?? { openWorkspace() {} },
          client: 'client' in fixture ? fixture.client : { workspaces: { list: async () => {
            lookups++;
            if (fixture.fails) throw new Error('offline');
            return { entries: fixture.entries ?? [] };
          } } },
        });
        t.after(page.dispose);
        await page.settle();
        const collapsed = pr ? [0] : [];
        const expanded = [...collapsed, ...fixture.visible];
        assertNavigation(page, run, collapsed);
        assertDetails(page, run, false);
        assert.deepEqual(page.hosts, []);
        assert.equal(lookups, 0);
        for (let cycle = 0; cycle < 2; cycle++) {
          await page.press('Details for linked-run');
          assertNavigation(page, run, expanded);
          assertDetails(page, run, true);
          await page.press('Details for linked-run');
          assertNavigation(page, run, collapsed);
          assertDetails(page, run, false);
        }
        assert.equal(lookups, fixture.navigation || 'client' in fixture ? 0 : 1);
        assert.deepEqual(page.hosts, fixture.navigation ? [] : ['host-1']);
        assert.equal(page.reads.length, 1);
        assert.deepEqual(page.actions, []);
        assert.deepEqual(page.urls, []);
      });
    }
  });

  await t.test('navigation failures keep the existing error messages', async t => {
    const page = styleView([navigationRun], {
      navigation: { openWorkspace() { throw new Error('unavailable'); } },
      client: { workspaces: { list: async () => ({ entries: [linkedWorkspace] }) } },
      openURL: async () => { throw new Error('unavailable'); },
    });
    t.after(page.dispose);
    await page.settle();
    await page.press('Details for linked-run');
    for (const [label, message] of [
      ['Open PR for linked-run', 'Could not open GitHub.'],
      ['Open change linked-run on GitHub', 'Could not open GitHub.'],
      ['Open linked-run in Paseo', 'Could not open the Paseo workspace.'],
    ]) {
      await page.press(label);
      assert.deepEqual(page.nodes().filter(node => node.props.accessibilityRole === 'alert')
        .map(node => page.text(node.children)), [message]);
    }
    assert.deepEqual(page.actions, []);
    assert.equal(page.reads.length, 1);
  });
});

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

const secondTheme = Object.fromEntries(Object.keys(colors).map(key => [key, `second-theme-${key}`])) as typeof colors;

function assertDisclosure(page: StylePage, label: string, expanded: boolean, tokens = colors) {
  const button = page.find(label);
  assert.ok(button);
  assert.equal(button.type, 'Pressable');
  assert.equal(button.props.accessibilityLabel, label);
  assert.equal(button.props.accessibilityRole, 'button');
  assert.deepEqual({ ...button.props.accessibilityState }, { expanded });
  assert.equal(button.props.disabled, undefined);
  assert.deepEqual({ ...button.props.style }, {
    paddingVertical: 10, alignSelf: 'flex-start', minHeight: 44,
    flexDirection: 'row', alignItems: 'center', gap: 8,
  });
  const finished = label.startsWith('Finished');
  assert.equal(page.text(button.children), finished ? label : `Details ${expanded ? '▴' : '▾'}`);
  const text = button.children[0] as Node;
  assert.equal(text.type, 'Text');
  assert.deepEqual({ ...text.props.style }, { color: tokens.foregroundMuted, fontWeight: '600' });
  assert.equal(button.children.length, finished ? 2 : 1);
  if (finished) {
    assert.equal(parentOf(page, button), page.nodes()[0], 'Finished remains a direct section child');
    const chevron = button.children[1] as Node;
    assert.equal(chevron.type, 'View');
    assert.deepEqual(chevron.children, [], 'the chevron adds no descendant text');
    assert.equal(chevron.props.accessible, false);
    assert.equal(chevron.props.accessibilityElementsHidden, true);
    assert.equal(chevron.props.importantForAccessibility, 'no-hide-descendants');
    assert.equal(chevron.props.accessibilityLabel, undefined);
    assert.equal(chevron.props.accessibilityRole, undefined);
    assert.deepEqual(JSON.parse(JSON.stringify(chevron.props.style)), {
      width: 8, height: 8, borderRightWidth: 2, borderBottomWidth: 2,
      borderColor: tokens.foregroundMuted, transform: [{ rotate: expanded ? '225deg' : '45deg' }],
    });
  } else {
    const card = parentOf(page, button);
    assert.equal(parentOf(page, card), page.nodes()[0], 'Details remains a direct card child');
    assert.equal(page.text([card.children[0]]), label.slice('Details for '.length));
  }
}

function assertRefresh(page: StylePage, loading: boolean, acting: boolean, tokens = colors) {
  const button = page.find('Refresh Factory');
  assert.ok(button);
  assert.equal(button.type, 'Pressable');
  assert.equal(button.props.accessibilityRole, 'button');
  assert.equal(button.props.accessibilityLabel, 'Refresh Factory');
  assert.equal(button.props.disabled, loading || acting);
  assert.equal(page.text(button.children), loading ? 'Checking…' : 'Refresh Factory');
  assert.equal(page.nodes()[0].children[2], button, 'Refresh stays in its header slot');
  assert.deepEqual({ ...button.props.style }, {
    paddingVertical: 8, paddingHorizontal: 12, alignSelf: 'flex-start', minHeight: 44, minWidth: 44,
    borderRadius: 8, borderWidth: 1, borderColor: tokens.border, backgroundColor: tokens.surface2,
    alignItems: 'center', justifyContent: 'center',
  });
  assert.equal(button.children.length, 1);
  const text = button.children[0] as Node;
  assert.equal(text.type, 'Text');
  assert.deepEqual({ ...text.props.style }, { color: tokens.foreground, fontSize: 13, fontWeight: '600' });
}

test('paseo-runs-page/secondary-disclosure-presentation/disclosures-decorate-existing-state', async t => {
  const finished: FactoryRun = { change: 'finished-run', status: 'done', tasks: { done: 3, total: 3 }, pr: '' };
  let lookups = 0;
  const page = styleView([navigationRun, finished], {
    navigation: { openWorkspace() {} },
    client: { workspaces: { list: async () => { lookups++; return { entries: [linkedWorkspace] }; } } },
  });
  t.after(page.dispose);
  await page.settle();
  assertDisclosure(page, 'Finished (1)', false);
  assertDisclosure(page, 'Details for linked-run', false);
  assertDetails(page, navigationRun, false);
  assert.equal(page.find('Details for finished-run'), undefined);
  for (let cycle = 0; cycle < 2; cycle++) {
    await page.press('Finished (1)');
    assertDisclosure(page, 'Finished (1)', true);
    assertDisclosure(page, 'Details for finished-run', false);
    await page.press('Finished (1)');
    assertDisclosure(page, 'Finished (1)', false);
    assert.equal(page.find('Details for finished-run'), undefined);
    assert.equal(page.reads.length, 1);
    assert.deepEqual(page.actions, []);
    assert.deepEqual(page.hosts, []);
    assert.equal(lookups, 0, 'Finished never looks up workspaces');
  }
  for (let cycle = 0; cycle < 2; cycle++) {
    await page.press('Details for linked-run');
    assertDisclosure(page, 'Details for linked-run', true);
    assertDetails(page, navigationRun, true);
    await page.press('Details for linked-run');
    assertDisclosure(page, 'Details for linked-run', false);
    assertDetails(page, navigationRun, false);
    assert.equal(lookups, 1, 'Details keeps its single shared lazy lookup');
    assert.deepEqual(page.hosts, ['host-1']);
    assert.equal(page.reads.length, 1);
    assert.deepEqual(page.actions, []);
  }
});

test('paseo-runs-page/secondary-refresh-presentation/refresh-remains-a-secondary-button', async t => {
  const runs = [runWith(['resume'])];
  const snapshot: FactorySnapshot = { runs, stale: false, fetchedAt: '', error: null };
  let completeRead!: (value: FactorySnapshot) => void;
  let completeAction!: (value: unknown) => void;
  const page = styleView(runs, {
    read: () => new Promise(resolve => { completeRead = resolve; }),
    act: () => new Promise(resolve => { completeAction = resolve; }),
  });
  t.after(page.dispose);
  await page.settle();
  function check(loading: boolean, acting: boolean) {
    for (const tokens of [colors, secondTheme]) {
      page.setColors(tokens);
      assertRefresh(page, loading, acting, tokens);
    }
  }
  check(true, false);
  await page.press('Refresh Factory');
  page.find('Refresh Factory')!.props.onPress(); // In-flight reads still coalesce native callbacks.
  await page.settle();
  assert.equal(page.reads.length, 1);
  completeRead(snapshot);
  await page.settle();
  check(false, false);
  await page.press('Refresh Factory');
  check(true, false);
  assert.equal(page.reads.length, 2, 'an idle press requests one refresh');
  await page.press('Refresh Factory');
  assert.equal(page.reads.length, 2, 'a disabled press requests nothing');
  completeRead(snapshot);
  await page.settle();
  check(false, false);
  await page.press('Resume styled-run');
  check(false, true);
  await page.press('Refresh Factory');
  assert.equal(page.reads.length, 2, 'Refresh is disabled while sending an action');
  assert.deepEqual(page.actions.map(input => ({ ...input })), [
    { change: 'styled-run', verb: 'resume', expect: 'stuck:1' },
  ]);
  completeAction({ outcome: 'done', detail: 'Resumed' });
  await page.settle();
  check(true, true);
  assert.equal(page.reads.length, 3, 'the action retains its post-action refresh');
  await page.press('Refresh Factory');
  assert.equal(page.reads.length, 3);
  completeRead(snapshot);
  await page.settle();
  check(false, false);
  assert.deepEqual(page.reads.map(input => ({ ...(input as object) })), Array.from({ length: 3 }, () => ({ refresh: true })));
  assert.equal(page.actions.length, 1);
  assert.deepEqual(page.hosts, []);
});

test('paseo-runs-page/host-theme-presentation/rendered-styles-follow-the-supplied-theme', async t => {
  for (const verb of ['publish', 'finish', 'abort'] as const) await t.test(verb, async t => {
    const run = { ...navigationRun, action: { ...navigationRun.action!, actions: ['resume', 'accept', verb] as Verb[] } };
    const runs: FactoryRun[] = [run, ...(['running', 'done', 'aborted'] as const).map(status => ({
      change: `theme-${status}`, status, tasks: { done: 1, total: 3 }, pr: '',
    }))];
    let lookups = 0;
    const page = styleView(runs, {
      navigation: { openWorkspace() {} },
      client: { workspaces: { list: async () => { lookups++; return { entries: [linkedWorkspace] }; } } },
    });
    t.after(page.dispose);
    await page.settle();
    await page.press('Details for linked-run');
    await page.press('Finished (2)');
    await page.press(`${labels[verb]} linked-run`);
    // Theme changes must preserve every rendered node's text and accessibility.
    const semantics = () => page.nodes().map(node => ({
      type: node.type, text: page.text(node.children), label: node.props.accessibilityLabel,
      role: node.props.accessibilityRole, state: { ...node.props.accessibilityState }, disabled: node.props.disabled,
      accessible: node.props.accessible, hidden: node.props.accessibilityElementsHidden,
      important: node.props.importantForAccessibility,
    }));
    const originalSemantics = semantics();
    for (const tokens of [colors, secondTheme]) {
      page.setColors(tokens);
      assert.deepEqual(semantics(), originalSemantics);
      assertNavigation(page, run, [0, 1, 2], tokens);
      assertDisclosure(page, 'Details for linked-run', true, tokens);
      assertDisclosure(page, 'Finished (2)', true, tokens);
      for (const status of ['running', 'done', 'aborted']) assertDisclosure(page, `Details for theme-${status}`, false, tokens);
      assertRefresh(page, false, false, tokens);
      const confirm = page.find(`Confirm ${verb} linked-run`)!;
      const cancel = parentOf(page, confirm).children[1] as Node;
      const destructive = verb === 'abort';
      const controls = [
        [page.find('Resume linked-run')!, 'Resume', 'Resume linked-run', true, tokens.accent, tokens.accentForeground, tokens.accent],
        [page.find('Accept linked-run')!, 'Accept', 'Accept linked-run', true, tokens.surface1, tokens.foreground, tokens.border],
        [page.find(`${labels[verb]} linked-run`)!, labels[verb], `${labels[verb]} linked-run`, true,
          tokens.surface1, destructive ? tokens.statusDanger : tokens.foreground, destructive ? tokens.statusDanger : tokens.border],
        [confirm, `Confirm ${verb}`, `Confirm ${verb} linked-run`, false,
          destructive ? tokens.surface1 : tokens.accent, destructive ? tokens.statusDanger : tokens.accentForeground,
          destructive ? tokens.statusDanger : tokens.accent],
        [cancel, 'Cancel', undefined, undefined, tokens.surface1, tokens.foreground, tokens.border],
      ] as const;
      for (const [button, text, label, disabled, background, foreground, border] of controls) {
        assert.equal(button.type, 'Pressable');
        assert.equal(button.props.accessibilityRole, 'button');
        assert.equal(button.props.accessibilityLabel, label);
        assert.equal(button.props.disabled, disabled);
        assert.equal(page.text(button.children), text);
        assert.equal(button.props.style.backgroundColor, background);
        assert.equal(button.props.style.borderColor, border);
        assert.equal((button.children[0] as Node).props.style.color, foreground);
      }
      const statusColors = { paused: tokens.statusWarning, running: tokens.accent, done: tokens.statusSuccess, aborted: tokens.foregroundMuted };
      for (const row of runs) {
        const card = parentOf(page, page.find(`Details for ${row.change}`)!);
        const sentence = card.children[1] as Node;
        const chip = sentence.children[0] as Node;
        assert.equal(page.text(sentence.children), `${row.status} · 1/3 tasks done`);
        assert.equal(sentence.props.style.color, tokens.foregroundMuted);
        assert.equal(chip.props.style.color, statusColors[row.status]);
        assert.equal(chip.props.style.backgroundColor, tokens.surface2);
      }
      assert.equal(page.reads.length, 1);
      assert.deepEqual(page.actions, []);
      assert.deepEqual(page.hosts, ['host-1']);
      assert.equal(lookups, 1, 'theme changes trigger no extra lookup');
      assert.deepEqual(page.urls, []);
    }
  });
});
