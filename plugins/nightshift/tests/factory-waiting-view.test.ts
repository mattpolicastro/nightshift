import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { FactoryRun, FactorySnapshot } from '../shared/factory';
import { colors, styleView, type Node } from './factory-style-harness';

type Page = ReturnType<typeof styleView>;
const since = 1_700_000_000.25;
const run: FactoryRun = {
  change: 'human-pause', status: 'paused', tasks: { done: 1, total: 3 }, pr: '',
  pause: { since, auto_resumes_remaining: 0 },
};
const nextColors = Object.fromEntries(Object.entries(colors).map(([key, value]) => [key, `next-${value}`])) as typeof colors;

function cardFor(page: Page, change: string) {
  const title = page.nodes().find(node => node.type === 'Text' && page.text(node.children) === change);
  assert.ok(title, `title for ${change}`);
  const card = page.nodes().find(node => node.children.includes(title));
  assert.ok(card);
  assert.equal(card.children[0], title);
  return card;
}

function assertRow(page: Page, candidate: FactoryRun, waiting: string | null, warning = false, tokens = colors) {
  const card = cardFor(page, candidate.change);
  const block = card.children[1] as Node;
  const sentence = waiting ? block.children[0] as Node : block;
  assert.equal(sentence.type, 'Text');
  assert.equal(page.text(sentence.children), `${candidate.status} · ${candidate.tasks.done}/${candidate.tasks.total} tasks done`);
  assert.deepEqual({ ...sentence.props.style }, { color: tokens.foregroundMuted });
  const chip = sentence.children[0] as Node;
  assert.equal(chip.type, 'Text');
  assert.deepEqual(chip.children, [candidate.status]);
  assert.deepEqual({ ...chip.props.style }, {
    color: { paused: tokens.statusWarning, running: tokens.accent, done: tokens.statusSuccess, aborted: tokens.foregroundMuted }[candidate.status],
    backgroundColor: tokens.surface2, borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2, fontWeight: '600',
  });
  assert.ok(sentence.children.slice(1).every(child => typeof child === 'string'));
  assert.equal(page.text(sentence.children.slice(1)), ` · ${candidate.tasks.done}/${candidate.tasks.total} tasks done`);
  const indicators = page.nodes(card.children).filter(node => node.type === 'Text' && /^waiting \d+[mh]$/.test(page.text(node.children)));
  assert.equal(indicators.length, waiting ? 1 : 0);
  if (waiting) {
    assert.equal(block.type, 'View', 'waiting and status share a container immediately below the title');
    assert.equal(block.props.style.flexDirection, 'row');
    assert.equal(block.props.style.flexWrap, 'wrap');
    assert.ok(block.props.style.gap > 0);
    assert.equal(block.props.style.gap, 8, 'waiting text retains the eight-pixel spacing beside status/progress');
    assert.deepEqual(block.children, [sentence, indicators[0]], 'waiting is a separate sibling after the unchanged sentence');
    assert.equal(page.text(indicators[0].children), waiting);
    assert.deepEqual({ ...indicators[0].props.style }, { color: warning ? tokens.statusWarning : tokens.foregroundMuted });
    for (const node of [block, indicators[0]]) {
      assert.equal(node.props.accessibilityRole, undefined);
      assert.equal(node.props.accessibilityLabel, undefined);
      assert.equal(node.props.accessibilityLiveRegion, undefined);
      assert.equal(node.props.accessibilityElementsHidden, undefined);
      assert.equal(node.props.accessible, undefined);
    }
  }
}

test('waiting time shows for human pauses only', async t => {
  await t.test('paseo-runs-page/human-pause-waiting-time/waiting-time-shows-for-human-pauses-only', async t => {
    t.mock.method(Date, 'now', () => (since + 120) * 1000);
    const eligible: FactoryRun[] = ['section_complete', 'stuck', 'status_error', 'unknown'].flatMap(reason =>
      [undefined, [], ['resume', 'abort'], ['accept'], ['publish'], ['finish']].map((verbs, index) => ({
        ...run, change: `${reason}-${index}`,
        pause: { ...run.pause, reason },
        action: verbs && { kind: 'pause', headline: 'Review needed', why: reason, command: '',
          actions: verbs as NonNullable<FactoryRun['action']>['actions'] },
      })));
    const excluded: FactoryRun[] = [
      ...[undefined, null, {}, { since }, { since, auto_resumes_remaining: 1 },
        { since, auto_resumes_remaining: null }, { auto_resumes_remaining: 0 },
        { since: null, auto_resumes_remaining: 0 }].map((pause, index) => ({ ...run, change: `excluded-${index}`, pause })),
      ...(['running', 'done', 'aborted'] as const).map(status => ({ ...run, change: `retained-${status}`, status })),
    ];
    const page = styleView([...excluded, ...eligible]);
    t.after(page.dispose);
    await page.settle();
    assert.equal(page.find('Finished (2)')!.props.accessibilityState.expanded, false);
    assert.equal(page.find('Details for retained-done'), undefined);
    await page.press('Finished (2)');
    for (const candidate of eligible) assertRow(page, candidate, 'waiting 2m');
    for (const candidate of excluded) assertRow(page, candidate, null);
    assert.equal(page.nodes().filter(node => node.type === 'Text' && page.text(node.children) === 'waiting 2m').length, eligible.length);
    assert.equal(page.reads.length, 1);
    assert.deepEqual(page.hosts, []);
    assert.deepEqual(page.actions, []);
  });
});

test('waiting time turns to warning after three hours', async t => {
  await t.test('paseo-runs-page/human-pause-waiting-time/waiting-time-turns-to-warning-after-three-hours', async t => {
    let now = (since + 10_799.999) * 1000;
    t.mock.method(Date, 'now', () => now);
    const page = styleView([run]);
    t.after(page.dispose);
    await page.settle();
    for (const [elapsed, label, warning] of [
      [10_799.999, 'waiting 2h', false], [10_800, 'waiting 3h', false],
      [10_800.001, 'waiting 3h', true], [10_801, 'waiting 3h', true],
    ] as const) {
      now = (since + elapsed) * 1000;
      page.setColors(colors);
      assertRow(page, run, label, warning);
      page.setColors(nextColors);
      assertRow(page, run, label, warning, nextColors);
    }
    assert.equal(page.reads.length, 1, 'clock and theme renders do not fetch');
    assert.deepEqual(page.hosts, []);
  });
});

// Compare all existing rendered text, styles, and accessibility/interaction props
// with a row lacking pause metadata, removing only the tested waiting container.
function legacyPresentation(page: Page) {
  const root = page.nodes()[0];
  function visit(node: Node | string): unknown {
    if (typeof node === 'string') return node;
    if (node.type === 'View' && node.children.length === 2) {
      const [sentence, waiting] = node.children;
      if (typeof sentence !== 'string' && typeof waiting !== 'string' && waiting.type === 'Text' &&
        /^waiting \d+[mh]$/.test(page.text(waiting.children))) return visit(sentence);
    }
    return {
      type: node.type, props: Object.fromEntries(Object.entries(node.props)
        .filter(([key, value]) => key !== 'children' && typeof value !== 'function')),
      children: node.children.map(visit),
    };
  }
  // Props originate in separate VM contexts.
  return JSON.parse(JSON.stringify(visit(root)));
}

test('paseo-runs-page/human-pause-waiting-time/waiting-text-preserves-the-existing-row', async t => {
  t.mock.method(Date, 'now', () => (since + 18_000) * 1000);
  const linked: FactoryRun = { ...run,
    pr: 'https://github.com/owner/repo/pull/42',
    action: { kind: 'pause', headline: 'Review needed', why: 'Paused', command: '',
      actions: ['resume', 'accept', 'publish', 'finish', 'abort'], pause_id: 'stuck:1' },
    context: { summary: 'Review this change.', repo: '~/Projects/nightshift', branch: 'spec/human-pause', task: null,
      detail: 'Diagnostic stays visible.', recent: [], github: 'https://github.com/owner/repo/tree/spec/human-pause' },
  };
  const opened: unknown[] = [];
  const options = {
    navigation: { openWorkspace: (input: unknown) => { opened.push(input); } },
    client: { workspaces: { list: async () => ({ entries: [{ id: 'workspace-1',
      workspaceDirectory: '/Users/studio/Projects/nightshift', archivingAt: null, activityAt: null }] }) } },
  };
  const runs: FactoryRun[] = [{ ...run, change: 'done-run', status: 'done' },
    { ...run, change: 'running-run', status: 'running' }, linked, { ...run, change: 'aborted-run', status: 'aborted' }];
  const page = styleView(runs, options);
  const baseline = styleView(runs.map(candidate => ({ ...candidate, pause: undefined })), options);
  t.after(page.dispose);
  t.after(baseline.dispose);
  await page.settle();
  await baseline.settle();
  const compare = () => {
    assertRow(page, linked, 'waiting 5h', true);
    assert.deepEqual(legacyPresentation(page), legacyPresentation(baseline));
  };
  async function press(label: string) {
    await page.press(label);
    await baseline.press(label);
    compare();
  }
  compare();
  assert.deepEqual(page.nodes().filter(node => node.props.accessibilityLabel?.startsWith('Details for '))
    .map(node => node.props.accessibilityLabel), ['Details for human-pause', 'Details for running-run']);
  assert.deepEqual(page.hosts, [], 'waiting does not trigger workspace lookup');
  const input = page.find('Resume note for human-pause')!;
  assert.equal(input.type, 'TextInput');
  assert.equal(input.props.placeholder, 'Guidance for the next attempt');
  input.props.onChangeText('Please retry carefully.');
  baseline.find('Resume note for human-pause')!.props.onChangeText('Please retry carefully.');
  await press('Details for human-pause');
  assert.equal(page.find('Details for human-pause')!.props.accessibilityState.expanded, true);
  assert.ok(page.text().includes('More detail (run on the Paseo host)specloop status human-pause --events 20'));
  for (const verb of ['publish', 'finish', 'abort']) {
    await press(`${verb[0].toUpperCase()}${verb.slice(1)} human-pause`);
    const message = verb === 'abort' ? 'Abort human-pause? This cannot be undone.'
      : `${verb[0].toUpperCase()}${verb.slice(1)} human-pause? This pushes to GitHub.`;
    assert.ok(page.nodes().some(node => node.props.accessibilityRole === 'alert' && page.text(node.children) === message));
    assert.equal(page.find('Resume human-pause')!.props.disabled, true);
    const cancel = (view: Page) => view.nodes().find(node => node.type === 'Pressable' && view.text(node.children) === 'Cancel')!;
    await page.press(cancel(page));
    await baseline.press(cancel(baseline));
    compare();
  }
  await press('Resume human-pause');
  assert.deepEqual(page.actions.map(action => ({ ...action })), [{ change: 'human-pause', verb: 'resume',
    note: 'Please retry carefully.', expect: 'stuck:1' }]);
  assert.ok(page.text().includes('Done · Completed'));
  assert.equal(page.find('Resume note for human-pause')!.props.value, '');
  await press('Open PR for human-pause');
  await press('Open change human-pause on GitHub');
  await press('Open human-pause in Paseo');
  assert.deepEqual(page.urls, [linked.pr, linked.context!.github]);
  assert.deepEqual(JSON.parse(JSON.stringify(opened)), [
    { serverId: 'host-1', workspaceId: 'workspace-1' }, { serverId: 'host-1', workspaceId: 'workspace-1' },
  ]);
  await press('Finished (2)');
  assert.equal(page.find('Finished (2)')!.props.accessibilityState.expanded, true);
  assert.deepEqual(page.nodes().filter(node => node.props.accessibilityLabel?.startsWith('Details for '))
    .map(node => node.props.accessibilityLabel), ['Details for human-pause', 'Details for running-run', 'Details for done-run', 'Details for aborted-run']);
  assertRow(page, runs[0], null);
  assertRow(page, runs[3], null);
  await press('Finished (2)');
  assert.equal(page.find('Details for done-run'), undefined);
  assert.equal(page.reads.length, 2, 'only mount and post-action refresh read');
  assert.deepEqual(page.hosts, ['host-1']);
});

test('paseo-runs-page/human-pause-waiting-time/existing-refreshes-update-the-wait', async t => {
  let now = (since + 59) * 1000;
  t.mock.method(Date, 'now', () => now);
  let current = run;
  let failure: 'rpc' | 'stale' | null = null;
  const staleError = 'Factory status unavailable. Last successful snapshot retained.';
  const page = styleView([], { read: async (): Promise<FactorySnapshot> => {
    if (failure === 'rpc') throw new Error('Connection lost');
    return { runs: [current], stale: failure === 'stale', fetchedAt: '', error: failure === 'stale' ? staleError : null };
  } });
  t.after(page.dispose);
  await page.settle();
  assertRow(page, current, 'waiting 0m');
  assert.equal(page.intervals.length, 1);
  assert.equal(page.intervals[0].delay, 60_000);
  assert.deepEqual(page.subscriptions.map(subscription => subscription.event), ['change']);
  now = (since + 60) * 1000;
  page.intervals[0].callback();
  await page.settle();
  assertRow(page, current, 'waiting 1m');
  assert.equal(page.reads.length, 2);
  page.appState.currentState = 'background';
  page.intervals[0].callback();
  assert.equal(page.reads.length, 2, 'polling remains inactive in the background');
  now = (since + 10_800.001) * 1000;
  page.appState.currentState = 'active';
  page.subscriptions[0].callback('active');
  await page.settle();
  assertRow(page, current, 'waiting 3h', true);
  assert.equal(page.reads.length, 3);
  now = (since + 18_000) * 1000;
  await page.press('Refresh Factory');
  assertRow(page, current, 'waiting 5h', true);
  current = { ...run, pause: { since: now / 1000 - 120, auto_resumes_remaining: 0 } };
  await page.press('Refresh Factory');
  assertRow(page, current, 'waiting 2m');
  for (const mode of ['rpc', 'stale'] as const) {
    failure = mode;
    now += 60_000;
    await page.press('Refresh Factory');
    assertRow(page, current, mode === 'rpc' ? 'waiting 3m' : 'waiting 4m');
    assert.ok(page.text().includes('1 run · Stale'));
    assert.deepEqual(page.nodes().filter(node => node.props.accessibilityRole === 'alert').map(node => page.text(node.children)),
      [mode === 'rpc' ? 'Factory refresh failed. Any previously loaded list is stale and retained.' : staleError]);
    assert.equal(page.find('Refresh Factory')!.props.disabled, false);
  }
  failure = null;
  for (const status of ['running', 'done', 'aborted'] as const) {
    current = { ...current, status };
    await page.press('Refresh Factory');
    if (status === 'done') await page.press('Finished (1)');
    assertRow(page, current, null);
    assert.equal(page.text().includes('Stale'), false);
    assert.deepEqual(page.nodes().filter(node => node.props.accessibilityRole === 'alert'), []);
  }
  assert.equal(page.reads.length, 10, 'only the existing mount, polling, foreground, and requested refreshes read');
  assert.ok(page.reads.every(input => JSON.stringify(input) === '{"refresh":true}'));
  assert.deepEqual(page.actions, []);
  assert.deepEqual(page.hosts, []);
  assert.equal(page.intervals.length, 1, 'renders never add a waiting timer');
  assert.equal(page.subscriptions.length, 1);
  page.dispose();
  assert.equal(page.intervals[0].cleared, true);
  assert.equal(page.subscriptions[0].removed, true);
});
