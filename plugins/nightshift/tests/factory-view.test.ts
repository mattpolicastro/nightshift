import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import React from 'react';
import type { FactoryRun } from '../shared/factory';

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
} = {}) {
  const calls = { hosts: [] as string[], lists: 0, opened: [] as unknown[], links: [] as string[] };
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
  const read = async () => ({ runs: options.runs ?? [run], stale: false, fetchedAt: '', error: null });
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
        useRpc: (rpc: unknown) => rpc === runsRpc ? read : options.act ?? (async () => {}),
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
    calls, find, text, settle,
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
