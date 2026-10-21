import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import React from 'react';
import type { FactoryRun, FactorySnapshot } from '../shared/factory';

const run: FactoryRun = {
  change: 'factory-context', status: 'paused', tasks: { done: 1, total: 3 }, pr: '',
  context: {
    summary: 'The page needs links.', repo: '~/Projects/nightshift', branch: 'spec/factory-context',
    task: { label: '2.2', section: 'Paseo plugin', text: 'Add Details.' },
    detail: 'Failed twice: x\nFull diagnostic text.',
    recent: [
      { at: '2026-10-21T12:00:00Z', text: 'task 2.2 attempt 1 failed' },
      { at: '2026-10-21T11:00:00Z', text: 'task 1.1 landed' },
    ],
    github: 'https://github.com/owner/repo/tree/spec/factory-context/openspec/changes/factory-context',
  },
};
const workspace = {
  id: 'studio-workspace', workspaceDirectory: '/Users/studio/Projects/nightshift',
  archivingAt: null, activityAt: '2026-10-21T12:00:00Z',
};

// Exercise the actual TSX and its handlers without loading the native host in Node.
// This small hook runner keeps state per component and runs effect cleanup on disposal.
type Props = Record<string, any>;
type Node = { type: unknown; props: Props; children: (Node | string)[] };
type Hook = { value?: any; deps?: unknown[]; cleanup?: () => void };
function view(options: {
  runs?: FactoryRun[]; client?: unknown; navigation?: unknown; getClient?: () => unknown;
  act?: (input: unknown) => Promise<unknown>;
  read?: () => Promise<FactorySnapshot>;
} = {}) {
  const calls = { hosts: [] as string[], lists: 0, opened: [] as unknown[], links: [] as string[], reads: 0, actions: [] as unknown[] };
  const client = 'client' in options ? options.client : { workspaces: { list: async () => {
    calls.lists++;
    return { entries: [workspace] };
  } } };
  const navigation = 'navigation' in options ? options.navigation : { openWorkspace: (input: unknown) => calls.opened.push(input) };
  const components = new Map<string, Hook[]>();
  let hooks: Hook[], cursor = 0;
  let effects: (() => void)[] = [];
  const slot = () => hooks[cursor++] ?? (hooks[cursor - 1] = {});
  const changed = (hook: Hook, deps: unknown[]) => !hook.deps || deps.some((dep, i) => !Object.is(dep, hook.deps![i]));
  const react = {
    ...React,
    useState(initial: unknown) {
      const hook = slot();
      if (!('value' in hook)) hook.value = initial;
      return [hook.value, (value: unknown) => { hook.value = typeof value === 'function' ? value(hook.value) : value; }];
    },
    useRef(initial: unknown) {
      const hook = slot();
      return hook.value ??= { current: initial };
    },
    useCallback(callback: unknown, deps: unknown[]) {
      const hook = slot();
      if (changed(hook, deps)) { hook.value = callback; hook.deps = deps; }
      return hook.value;
    },
    useEffect(effect: () => (() => void) | void, deps: unknown[]) {
      const hook = slot();
      if (changed(hook, deps)) {
        hook.deps = deps;
        effects.push(() => { hook.cleanup?.(); hook.cleanup = effect() || undefined; });
      }
    },
  };
  const runsRpc = {};
  const actRpc = {};
  const read = async () => {
    calls.reads++;
    return options.read ? options.read() : { runs: options.runs ?? [run], stale: false, fetchedAt: '', error: null };
  };
  const act = async (input: unknown) => { calls.actions.push(input); return options.act?.(input); };
  const filename = resolve('client/factory.tsx');
  const require = createRequire(filename);
  const exports: Props = {};
  const source = ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true },
  }).outputText;
  runInNewContext(source, {
    exports, setInterval: () => 1, clearInterval: () => {},
    require: (name: string) => {
      if (name === 'react') return react;
      if (name === 'react-native') return {
        View: 'View', Text: 'Text', Pressable: 'Pressable', TextInput: 'TextInput',
        AppState: { currentState: 'active', addEventListener: () => ({ remove() {} }) },
        Linking: { openURL: async (url: string) => { calls.links.push(url); } },
      };
      if (name === '@getpaseo/plugin/client') return {
        useRpc: (rpc: unknown) => rpc === runsRpc ? read : act,
        getPaseoClient: (id: string) => { calls.hosts.push(id); return options.getClient ? options.getClient() : client; },
      };
      if (name === '../shared/factory') return { factoryRuns: runsRpc, factoryAct: actRpc };
      return require(name);
    },
  });
  function render(value: unknown, path: string): (Node | string)[] {
    if (Array.isArray(value)) return value.flatMap((item, i) => render(item, `${path}/${i}`));
    if (typeof value === 'string' || typeof value === 'number') return [String(value)];
    if (!React.isValidElement<Props>(value)) return [];
    if (typeof value.type === 'function') {
      const key = `${path}/${value.key ?? value.type.name}`;
      hooks = components.get(key) ?? [];
      components.set(key, hooks);
      cursor = 0;
      return render((value.type as (props: Props) => unknown)(value.props), key);
    }
    return [{ type: value.type, props: value.props, children: render(value.props.children, path) }];
  }
  let tree: (Node | string)[] = [];
  function draw() {
    tree = render(React.createElement(exports.FactorySection, {
      theme: { colors: {} }, host: { id: 'host-1', label: 'Host' }, navigation,
    }), 'root');
    const pending = effects;
    effects = [];
    pending.forEach(effect => effect());
  }
  const nodes = (items = tree): Node[] => items.flatMap(item => typeof item === 'string' ? [] : [item, ...nodes(item.children)]);
  const text = (items = tree): string => items.map(item => typeof item === 'string' ? item : text(item.children)).join('');
  const find = (label: string) => nodes().find(node => node.props.accessibilityLabel === label);
  async function settle() { await new Promise(resolve => setImmediate(resolve)); draw(); }
  draw();
  return {
    calls, find, text, settle, nodes,
    async press(label: string) { const button = find(label); assert.ok(button, `Missing ${label}`); button.props.onPress(); draw(); await settle(); },
    dispose() { components.forEach(hooks => hooks.forEach(hook => hook.cleanup?.())); },
  };
}

test('Factory Details starts collapsed, renders context and links, and shares a lazy host workspace lookup', async t => {
  const page = view({ runs: [run, { ...run, change: 'second-change' }] });
  t.after(page.dispose);
  await page.settle();
  assert.equal(page.find('Details for factory-context')?.props.accessibilityState.expanded, false);
  assert.equal(page.calls.lists, 0);
  assert.deepEqual(page.calls.hosts, []);
  assert.ok(!page.text().includes(run.context!.summary));
  assert.ok(!page.text().includes('specloop status'));
  await page.press('Details for factory-context');
  assert.equal(page.find('Details for factory-context')?.props.accessibilityState.expanded, true);
  for (const value of [run.context!.summary, run.context!.repo, run.context!.branch,
    '2.2 · Paseo plugin · Add Details.', run.context!.detail,
    'specloop status factory-context --events 20', ...run.context!.recent.map(event => `${new Date(event.at).toLocaleString()} · ${event.text}`)]) {
    assert.ok(page.text().includes(value), `Missing ${value}`);
  }
  assert.ok(page.text().indexOf('task 2.2 attempt 1 failed') < page.text().indexOf('task 1.1 landed'));
  assert.deepEqual(page.calls.hosts, ['host-1']);
  assert.equal(page.calls.lists, 1);
  await page.press('Open factory-context in Paseo');
  assert.equal(JSON.stringify(page.calls.opened), JSON.stringify([{ serverId: 'host-1', workspaceId: workspace.id }]));
  await page.press('Open change factory-context on GitHub');
  assert.deepEqual(page.calls.links, [run.context!.github]);
  await page.press('Details for factory-context');
  assert.ok(!page.text().includes('specloop status'));
  await page.press('Details for factory-context');
  await page.press('Details for second-change');
  assert.equal(page.calls.lists, 1);
  assert.ok(page.find('Open second-change in Paseo'));
});

test('Factory Details hides workspace navigation on missing methods, lookup failures and unmatched repositories', async t => {
  const cases = [
    { navigation: undefined }, { navigation: {} },
    { client: undefined }, { client: {} }, { client: { workspaces: {} } },
    { getClient: () => { throw new Error('SDK unavailable'); } },
    { client: { workspaces: { list: async () => { throw new Error('offline'); } } } },
    { client: { workspaces: { list: async () => ({ entries: [] }) } } },
    { client: { workspaces: { list: async () => ({ entries: [{ ...workspace, workspaceDirectory: '/srv/other' }] }) } } },
    { client: { workspaces: { list: async () => ({ entries: [{ ...workspace, archivingAt: '2026-10-21T13:00:00Z' }] }) } } },
  ];
  for (const options of cases) {
    const page = view(options);
    t.after(page.dispose);
    await page.settle();
    await page.press('Details for factory-context');
    assert.equal(page.find('Open factory-context in Paseo'), undefined);
    assert.ok(page.text().includes(run.context!.summary));
    assert.ok(page.text().includes('specloop status factory-context --events 20'));
    if ('navigation' in options) assert.deepEqual(page.calls.hosts, []);
  }
});

test('Factory Details works with absent or empty context and no unpublished change link', async t => {
  for (const context of [undefined, { summary: '', repo: '', branch: '', task: null, detail: '', recent: [], github: '' }]) {
    const page = view({ runs: [{ ...run, context }] });
    t.after(page.dispose);
    await page.settle();
    await page.press('Details for factory-context');
    assert.ok(page.text().includes('specloop status factory-context --events 20'));
    assert.equal(page.find('Open change factory-context on GitHub'), undefined);
    assert.equal(page.find('Open factory-context in Paseo'), undefined);
    for (const absent of ['Summary', 'Repository', 'Branch', 'Pause detail', 'Recent events']) {
      assert.ok(!page.text().includes(absent));
    }
  }
});

test('An action result shows in the row it was sent from, and above the list once that run is gone', async t => {
  const publishable = (change: string): FactoryRun => ({
    ...run, change, context: undefined,
    action: { kind: 'decision', headline: 'Ready to publish', why: '', command: '', actions: ['publish'], pause_id: 'ready_to_publish:' },
  });
  const runs = [publishable('first-change'), publishable('second-change')];
  const sent: unknown[] = [];
  const refusal = 'origin of ~/src is github.com/upstream/repo, but the manifest publishes to owner/repo; repoint origin before publishing';
  const page = view({ runs, act: async input => {
    sent.push(input);
    return { version: 1, outcome: 'refused', verb: 'publish', detail: refusal };
  } });
  t.after(page.dispose);
  await page.settle();
  await page.press('Publish second-change');
  assert.deepEqual(sent, []);
  await page.press('Confirm publish second-change');
  assert.deepEqual(sent, [{ change: 'second-change', verb: 'publish', expect: 'ready_to_publish:' }]);
  const text = page.text();
  assert.equal(text.split(refusal).length, 2, 'shown exactly once');
  assert.ok(text.includes(`Refused · ${refusal}`));
  assert.ok(text.indexOf(refusal) > text.indexOf('second-change'), 'inside the acted-on row');
  assert.ok(!text.includes(`second-change: Refused`));
  runs.splice(1, 1);
  await page.press('Refresh Factory');
  assert.ok(page.text().includes(`second-change: Refused · ${refusal}`));
  assert.ok(page.text().indexOf(refusal) < page.text().indexOf('first-change'), 'above the list');
});

test('finished runs are folded under a collapsed toggle', async t => {
  const makeRun = (change: string, status: FactoryRun['status']): FactoryRun => ({
    ...run, change, status, context: undefined,
  });
  const mixed = [makeRun('done-a', 'done'), makeRun('running-b', 'running'), makeRun('paused-c', 'paused'),
    makeRun('aborted-d', 'aborted'), makeRun('paused-e', 'paused'), makeRun('running-f', 'running'), makeRun('done-g', 'done')];
  const snapshot = (runs: FactoryRun[], stale = false): FactorySnapshot => ({ runs, stale, fetchedAt: '', error: null });
  const hasText = (page: ReturnType<typeof view>, text: string) =>
    page.nodes().some(node => node.type === 'Text' && page.text(node.children) === text);

  await t.test('factory-finished-runs/finished-disclosure/finished-rows-start-folded-and-can-be-toggled', async t => {
    const page = view({ runs: mixed });
    t.after(page.dispose);
    await page.settle();
    const callsBefore = JSON.stringify(page.calls);
    for (let presses = 0; presses <= 2; presses++) {
      if (presses) await page.press('Finished (3)');
      const expanded = presses === 1;
      const toggle = page.find('Finished (3)')!;
      assert.ok(toggle);
      assert.equal(toggle.props.accessibilityRole, 'button');
      assert.equal(page.text(toggle.children), 'Finished (3)');
      assert.equal(toggle.props.accessibilityState.expanded, expanded);
      const order = ['paused-c', 'paused-e', 'running-b', 'running-f', 'Finished (3)',
        ...(expanded ? ['done-a', 'aborted-d', 'done-g'] : [])];
      let previous = -1;
      for (const value of order) {
        const position = page.text().indexOf(value);
        assert.ok(position > previous, `${value} follows the previous row`);
        previous = position;
      }
      for (const change of ['done-a', 'aborted-d', 'done-g']) {
        assert.equal(Boolean(page.find(`Details for ${change}`)), expanded);
        assert.equal(page.text().includes(change), expanded);
      }
      assert.equal(JSON.stringify(page.calls), callsBefore, 'toggle causes no RPC or workspace requests');
    }
  });

  await t.test('factory-finished-runs/finished-disclosure/expanded-rows-retain-existing-content', async t => {
    const finished = { ...run, status: 'done' as const, pr: 'https://github.com/owner/repo/pull/42' };
    const page = view({ runs: [finished] });
    t.after(page.dispose);
    await page.settle();
    await page.press('Finished (1)');
    assert.ok(hasText(page, finished.change));
    assert.ok(hasText(page, 'done · 1/3 tasks done'));
    assert.equal(page.find('Details for factory-context')?.props.accessibilityState.expanded, false);
    assert.equal(page.calls.lists, 0);
    assert.equal(page.find('Open PR for factory-context')?.props.accessibilityRole, 'link');
    await page.press('Open PR for factory-context');
    await page.press('Details for factory-context');
    assert.equal(page.find('Details for factory-context')?.props.accessibilityState.expanded, true);
    for (const value of ['Summary', 'Repository', 'Branch', 'Task', 'Pause detail', 'Recent events',
      run.context!.summary, run.context!.repo, run.context!.branch, '2.2 · Paseo plugin · Add Details.',
      run.context!.detail, ...run.context!.recent.map(event => `${new Date(event.at).toLocaleString()} · ${event.text}`),
      'More detail (run on the Paseo host)', 'specloop status factory-context --events 20']) {
      assert.ok(hasText(page, value), `Missing ${value}`);
    }
    assert.ok(page.text().indexOf('task 2.2 attempt 1 failed') < page.text().indexOf('task 1.1 landed'));
    await page.press('Open change factory-context on GitHub');
    await page.press('Open factory-context in Paseo');
    assert.deepEqual(page.calls.links, [finished.pr, run.context!.github]);
    assert.equal(JSON.stringify(page.calls.opened), JSON.stringify([{ serverId: 'host-1', workspaceId: workspace.id }]));
    assert.equal(page.calls.lists, 1);
  });

  await t.test('factory-finished-runs/group-counts-and-empty-state/counts-describe-active-and-finished-runs', async t => {
    let stale = false;
    const page = view({ read: async () => snapshot(mixed.slice(0, 4).concat(mixed[6]), stale) });
    t.after(page.dispose);
    await page.settle();
    for (const expected of ['2 runs · 3 finished', '2 runs · 3 finished · Stale']) {
      await page.press('Refresh Factory');
      assert.ok(hasText(page, expected));
      await page.press('Finished (3)');
      assert.ok(hasText(page, expected));
      await page.press('Finished (3)');
      assert.ok(hasText(page, expected));
      stale = true;
    }
  });

  await t.test('factory-finished-runs/group-counts-and-empty-state/active-only-finished-only-and-empty-snapshots', async t => {
    for (const [runs, summary, finishedCount] of [
      [[mixed[1]], '1 run', 0], [[mixed[0], mixed[3]], '0 runs · 2 finished', 2], [[], '0 runs', 0],
    ] as const) {
      const page = view({ runs: [...runs] });
      t.after(page.dispose);
      assert.ok(hasText(page, 'Loading Factory…'));
      assert.ok(!hasText(page, 'No Factory runs.'));
      await page.settle();
      assert.ok(hasText(page, summary));
      const toggles = page.nodes().filter(node => node.props.accessibilityLabel?.startsWith('Finished ('));
      assert.equal(toggles.length, finishedCount ? 1 : 0);
      assert.equal(hasText(page, 'No Factory runs.'), runs.length === 0);
      if (finishedCount) {
        await page.press(`Finished (${finishedCount})`);
        assert.ok(hasText(page, summary));
        assert.ok(!hasText(page, 'No Factory runs.'));
      }
    }
  });

  await t.test('factory-finished-runs/visible-action-results/action-result-follows-a-finished-run', async t => {
    for (const verb of ['finish', 'abort'] as const) for (const initiallyOpen of [false, true]) {
      await t.test(`${verb}, initially ${initiallyOpen ? 'expanded' : 'collapsed'}`, async t => {
        const target: FactoryRun = { ...makeRun('acted-change', 'paused'), action: {
          kind: 'decision', headline: 'Human checks', why: '', command: '', actions: [verb], pause_id: 'human_checks:',
        } };
        let runs = [makeRun('other-active', 'running'), target, mixed[0]];
        const detail = `${verb} completed for acted-change`;
        const page = view({ read: async () => snapshot(runs), act: async () => {
          runs = runs.map(item => item === target ? { ...target, status: verb === 'finish' ? 'done' : 'aborted', action: undefined } : item);
          return { version: 1, outcome: 'done', verb, detail };
        } });
        t.after(page.dispose);
        await page.settle();
        if (initiallyOpen) await page.press('Finished (1)');
        await page.press(`${verb === 'finish' ? 'Finish' : 'Abort'} acted-change`);
        assert.deepEqual(page.calls.actions, [], 'confirmation sends nothing');
        await page.press(`Confirm ${verb} acted-change`);
        assert.deepEqual(page.calls.actions, [{ change: 'acted-change', verb, expect: 'human_checks:' }]);
        assert.equal(page.calls.reads, 2, 'action refreshes the snapshot');
        assert.ok(hasText(page, '1 run · 2 finished'));
        const checkNotice = (expanded: boolean, removed = false) => {
          assert.equal(page.find(`Finished (${removed ? 1 : 2})`)?.props.accessibilityState.expanded, expanded);
          const inRow = expanded && !removed;
          assert.equal(Boolean(page.find('Details for acted-change')), inRow);
          assert.equal(page.text().split(detail).length, 2, 'result appears exactly once');
          const alerts = page.nodes().filter(node => node.props.accessibilityRole === 'alert');
          assert.equal(alerts.length, 1);
          assert.equal(page.text(alerts[0].children), `${inRow ? '' : 'acted-change: '}Done · ${detail}`);
          assert.equal(page.nodes()[0].children.includes(alerts[0]), !inRow, 'only folded or absent notices use the above-list slot');
          if (inRow) {
            const row = page.nodes().find(node => node.type === 'View' && node.children.some(child =>
              typeof child !== 'string' && child.type === 'Text' && page.text(child.children) === 'acted-change'))!;
            assert.ok(row.children.includes(alerts[0]), 'notice is in the originating row');
          } else assert.ok(page.text().indexOf(detail) < page.text().indexOf('other-active'));
        };
        checkNotice(initiallyOpen);
        await page.press('Finished (2)');
        checkNotice(!initiallyOpen);
        await page.press('Finished (2)');
        checkNotice(initiallyOpen);
        runs = runs.filter(item => item.change !== target.change);
        await page.press('Refresh Factory');
        checkNotice(initiallyOpen, true);
        await page.press('Finished (1)');
        checkNotice(!initiallyOpen, true);
      });
    }
  });
});

test('Finished disclosure preserves its touch spacing and semibold label across toggles', async t => {
  const page = view({ runs: [{ ...run, status: 'done' }] });
  t.after(page.dispose);
  await page.settle();

  for (const expanded of [false, true, false]) {
    const toggle = page.find('Finished (1)');
    assert.ok(toggle);
    assert.equal(toggle.props.accessibilityState.expanded, expanded);
    // Keep the disclosure's touch spacing and emphasis consistent with Details.
    assert.equal(toggle.props.style.paddingVertical, 10);
    assert.equal(toggle.props.style.alignSelf, 'flex-start');
    const label = toggle.children.find((child): child is Node =>
      typeof child !== 'string' && child.type === 'Text');
    assert.ok(label);
    assert.equal(page.text(label.children), 'Finished (1)');
    assert.equal(label.props.style.fontWeight, '600');
    if (expanded) {
      const details = page.find(`Details for ${run.change}`);
      assert.ok(details);
      assert.equal(toggle.props.style.paddingVertical, details.props.style.paddingVertical);
    }
    await page.press('Finished (1)');
  }
});

function assertActionNotice(page: ReturnType<typeof view>, change: string, text: string, inRow: boolean) {
  const alerts = page.nodes().filter(node => node.props.accessibilityRole === 'alert');
  assert.equal(alerts.length, 1, 'exactly one visible notice');
  assert.equal(page.text().split(text).length - 1, 1, 'notice text is not duplicated');
  assert.equal(page.text(alerts[0].children), inRow ? text : `${change}: ${text}`);
  const section = page.nodes()[0];
  assert.equal(section.children.includes(alerts[0]), !inRow, 'above-list slot only when the row is hidden');
  const row = page.nodes().find(node => node.type === 'View' && node.children.some(child =>
    typeof child !== 'string' && child.type === 'Text' && page.text(child.children) === change));
  assert.equal(Boolean(row), inRow, 'originating row visibility');
  if (inRow) assert.ok(page.nodes(row!.children).includes(alerts[0]), 'notice belongs to the originating row');
  else {
    const firstRow = section.children.find(child => typeof child !== 'string' && child.type === 'View');
    assert.ok(firstRow, 'another run remains visible');
    assert.ok(section.children.indexOf(alerts[0]) < section.children.indexOf(firstRow), 'notice is above the list');
  }
}

function actionNoticeRuns(verb: 'finish' | 'abort'): FactoryRun[] {
  return [
    { ...run, change: 'other-active', status: 'running', context: undefined },
    { ...run, change: 'notice-target', context: undefined, action: {
      kind: 'decision', headline: 'Human checks', why: '', command: '', actions: [verb], pause_id: 'human_checks:',
    } },
    { ...run, change: 'other-finished', status: 'done', context: undefined },
  ];
}

test('factory-finished-runs/visible-action-results/action-result-follows-a-finished-run', async t => {
  for (const verb of ['finish', 'abort'] as const) for (const initiallyOpen of [false, true]) {
    await t.test(`${verb}: delayed response and refresh, initially ${initiallyOpen ? 'expanded' : 'collapsed'}`, async t => {
      let runs = actionNoticeRuns(verb);
      const snapshot = (): FactorySnapshot => ({ runs, stale: false, fetchedAt: '', error: null });
      let completeAction!: (result: unknown) => void;
      const actionResponse = new Promise<unknown>(resolve => { completeAction = resolve; });
      let completeRefresh!: (result: FactorySnapshot) => void;
      let pendingRefresh: Promise<FactorySnapshot> | undefined;
      const page = view({ read: async () => pendingRefresh ?? snapshot(), act: async () => actionResponse });
      t.after(page.dispose);
      await page.settle();
      if (initiallyOpen) await page.press('Finished (1)');
      assert.equal(page.nodes().filter(node => node.props.accessibilityRole === 'alert').length, 0);
      await page.press(`${verb === 'finish' ? 'Finish' : 'Abort'} notice-target`);
      assert.deepEqual(page.calls.actions, [], 'opening confirmation does not send');
      assert.equal(page.calls.reads, 1, 'opening confirmation does not refresh');
      assertActionNotice(page, 'notice-target', verb === 'finish'
        ? 'Finish notice-target? This pushes to GitHub.' : 'Abort notice-target? This cannot be undone.', true);

      pendingRefresh = new Promise(resolve => { completeRefresh = resolve; });
      await page.press(`Confirm ${verb} notice-target`);
      assert.deepEqual(page.calls.actions, [{ change: 'notice-target', verb, expect: 'human_checks:' }]);
      assert.equal(page.calls.reads, 1, 'refresh waits for the action response');
      assertActionNotice(page, 'notice-target', 'Sending action…', true);
      for (const expanded of [!initiallyOpen, initiallyOpen]) {
        await page.press('Finished (1)');
        assert.equal(page.find('Finished (1)')?.props.accessibilityState.expanded, expanded);
        assertActionNotice(page, 'notice-target', 'Sending action…', true);
      }
      const detail = `${verb} completed for notice-target`;
      completeAction({ version: 1, outcome: 'done', verb, detail });
      await page.settle();
      assert.equal(page.calls.reads, 2, 'confirmed action requests a post-action snapshot');
      assertActionNotice(page, 'notice-target', `Done · ${detail}`, true);
      const status = verb === 'finish' ? 'done' : 'aborted';
      runs = runs.map(item => item.change === 'notice-target' ? { ...item, status, action: undefined } : item);
      completeRefresh(snapshot());
      pendingRefresh = undefined;
      await page.settle();

      const checkFinished = (expanded: boolean, removed = false) => {
        assert.equal(page.find(`Finished (${removed ? 1 : 2})`)?.props.accessibilityState.expanded, expanded);
        assert.equal(Boolean(page.find('Details for notice-target')), expanded && !removed);
        assertActionNotice(page, 'notice-target', `Done · ${detail}`, expanded && !removed);
        if (expanded && !removed) {
          const row = page.nodes().find(node => node.type === 'View' && node.children.some(child =>
            typeof child !== 'string' && child.props.accessibilityLabel === 'Details for notice-target'))!;
          assert.ok(page.text(row.children).includes(`${status} · 1/3 tasks done`));
        }
      };
      checkFinished(initiallyOpen);
      const callsAfterAction = JSON.stringify(page.calls);
      for (const expanded of [!initiallyOpen, initiallyOpen]) {
        await page.press('Finished (2)');
        checkFinished(expanded);
      }
      assert.equal(JSON.stringify(page.calls), callsAfterAction, 'toggling preserves the result without RPCs');
      runs = runs.filter(item => item.change !== 'notice-target');
      await page.press('Refresh Factory');
      assert.equal(page.calls.reads, 3);
      checkFinished(initiallyOpen, true);
      for (const expanded of [!initiallyOpen, initiallyOpen]) {
        await page.press('Finished (1)');
        checkFinished(expanded, true);
      }
      assert.equal(page.calls.actions.length, 1, 'the action is never resent');
      assert.equal(page.calls.reads, 3, 'toggles do not refresh');
    });
  }
});

test('factory-finished-runs/visible-action-results/result-remains-in-an-active-row', async t => {
  for (const verb of ['finish', 'abort'] as const) for (const outcome of ['done', 'refused', 'transport-error'] as const) {
    for (const status of ['paused', 'running'] as const) for (const initiallyOpen of [false, true]) {
      await t.test(`${verb}: ${outcome}, remains ${status}, initially ${initiallyOpen ? 'expanded' : 'collapsed'}`, async t => {
        let runs = actionNoticeRuns(verb);
        const detail = outcome === 'done' ? `${verb} completed for notice-target` : 'The pause changed; refresh before retrying.';
        const expected = outcome === 'transport-error'
          ? 'Could not confirm the action result. Check the refreshed status before retrying.'
          : `${outcome === 'done' ? 'Done' : 'Refused'} · ${detail}`;
        const page = view({
          read: async () => ({ runs, stale: false, fetchedAt: '', error: null }),
          act: async () => {
            runs = runs.map(item => item.change === 'notice-target' ? { ...item, status, action: undefined } : item);
            if (outcome === 'transport-error') throw new Error('RPC connection lost');
            return { version: 1, outcome, verb, detail };
          },
        });
        t.after(page.dispose);
        await page.settle();
        if (initiallyOpen) await page.press('Finished (1)');
        await page.press(`${verb === 'finish' ? 'Finish' : 'Abort'} notice-target`);
        assert.deepEqual(page.calls.actions, [], 'no action before confirmation');
        await page.press(`Confirm ${verb} notice-target`);
        assert.deepEqual(page.calls.actions, [{ change: 'notice-target', verb, expect: 'human_checks:' }]);
        assert.equal(page.calls.reads, 2, 'success, refusal, and transport error all refresh');
        const callsAfterAction = JSON.stringify(page.calls);
        const checkActive = (expanded: boolean) => {
          assert.equal(page.find('Finished (1)')?.props.accessibilityState.expanded, expanded);
          assert.ok(page.find('Details for notice-target'));
          assertActionNotice(page, 'notice-target', expected, true);
          const row = page.nodes().find(node => node.type === 'View' && node.children.some(child =>
            typeof child !== 'string' && child.props.accessibilityLabel === 'Details for notice-target'))!;
          assert.ok(page.text(row.children).includes(`${status} · 1/3 tasks done`));
        };
        checkActive(initiallyOpen);
        for (const expanded of [!initiallyOpen, initiallyOpen]) {
          await page.press('Finished (1)');
          checkActive(expanded);
        }
        assert.equal(JSON.stringify(page.calls), callsAfterAction, 'toggles neither clear the notice nor send requests');
        await page.press('Refresh Factory');
        checkActive(initiallyOpen);
        assert.equal(page.calls.reads, 3, 'a later refresh preserves the notice');
        assert.equal(page.calls.actions.length, 1);
      });
    }
  }
});
