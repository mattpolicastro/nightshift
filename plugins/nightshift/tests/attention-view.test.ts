import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import React from 'react';
import type { AttentionSnapshot, Escalation, RepositoryAttention } from '../shared/attention';

// Renders the actual page TSX with the same SDK/native mocks as the Factory view tests.
type Props = Record<string, any>;
type Node = { type: unknown; props: Props; children: (Node | string)[] };
type Hook = { value?: any; deps?: unknown[]; cleanup?: () => void };
type Reply = AttentionSnapshot | Error | (AttentionSnapshot & { tasks: unknown[] });
function page(replies: Reply[], options: { open?: (url: string) => Promise<void> } = {}) {
  const calls = { reads: [] as boolean[], links: [] as string[], factory: [] as Props[], intervals: [] as (() => void)[], foreground: [] as ((s: string) => void)[] };
  const components = new Map<string, Hook[]>();
  let hooks: Hook[], cursor = 0, effects: (() => void)[] = [];
  const slot = () => hooks[cursor++] ?? (hooks[cursor - 1] = {});
  const changed = (hook: Hook, deps: unknown[]) => !hook.deps || deps.some((dep, i) => !Object.is(dep, hook.deps![i]));
  const react = {
    ...React,
    useState(initial: unknown) {
      const hook = slot();
      if (!('value' in hook)) hook.value = initial;
      return [hook.value, (value: unknown) => { hook.value = typeof value === 'function' ? value(hook.value) : value; }];
    },
    useRef(initial: unknown) { const hook = slot(); return hook.value ??= { current: initial }; },
    useCallback(callback: unknown, deps: unknown[]) {
      const hook = slot();
      if (changed(hook, deps)) { hook.value = callback; hook.deps = deps; }
      return hook.value;
    },
    useEffect(effect: () => (() => void) | void, deps: unknown[]) {
      const hook = slot();
      if (changed(hook, deps)) { hook.deps = deps; effects.push(() => { hook.cleanup?.(); hook.cleanup = effect() || undefined; }); }
    },
  };
  const rpc = {};
  const read = async ({ refresh }: { refresh: boolean }) => {
    calls.reads.push(refresh);
    const next = replies.length > 1 ? replies.shift()! : replies[0];
    if (next instanceof Error) throw next;
    return next;
  };
  function FactorySection(props: Props) { calls.factory.push(props); return React.createElement('Factory'); }
  const filename = resolve('client/main.tsx');
  const require = createRequire(filename);
  const exports: Props = {};
  const source = ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true },
  }).outputText;
  runInNewContext(source, {
    exports, setInterval: (callback: () => void) => { calls.intervals.push(callback); return 1; }, clearInterval: () => {},
    require: (name: string) => {
      if (name === 'react') return react;
      if (name === 'react-native') return {
        View: 'View', Text: 'Text', Pressable: 'Pressable', ScrollView: 'ScrollView',
        AppState: { currentState: 'active', addEventListener: (_: string, listener: (s: string) => void) => { calls.foreground.push(listener); return { remove() {} }; } },
      };
      if (name === '@getpaseo/plugin/client') return {
        openExternalUrl: async (url: string) => { calls.links.push(url); await options.open?.(url); },
        useRpc: (descriptor: unknown) => { assert.equal(descriptor, rpc); return read; },
      };
      if (name === '../shared/attention') return { attention: rpc };
      if (name === './factory') return { FactorySection };
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
  const surface = { theme: { colors: { statusWarning: 'warn', foreground: 'fg', foregroundMuted: 'muted', border: 'line', surface0: 'bg', surface1: 'card', accent: 'link' } }, host: { id: 'host-1', label: 'Studio' }, layout: { compact: true }, navigation: { openWorkspace() {} } };
  let tree: (Node | string)[] = [];
  function draw() {
    tree = render(React.createElement(exports.MainSurface, surface), 'root');
    const pending = effects; effects = [];
    pending.forEach(effect => effect());
  }
  const nodes = (items = tree): Node[] => items.flatMap(item => typeof item === 'string' ? [] : [item, ...nodes(item.children)]);
  const text = (items = tree): string => items.map(item => typeof item === 'string' ? item : text(item.children)).join('');
  const lines = () => nodes().filter(node => node.type === 'Text').map(node => text(node.children));
  const find = (label: string) => nodes().find(node => node.props.accessibilityLabel === label);
  async function settle() { for (let i = 0; i < 4; i++) { await new Promise(r => setImmediate(r)); draw(); } }
  draw();
  return {
    calls, text, lines, find, settle, surface, draw, nodes,
    async press(label: string) { const button = find(label); assert.ok(button, `Missing ${label}`); button.props.onPress(); await settle(); },
    dispose() { components.forEach(hooks => hooks.forEach(hook => hook.cleanup?.())); },
  };
}

const at = '2026-11-15T10:00:00Z', earlier = '2026-11-14T09:00:00Z';
const when = (value: string) => new Date(value).toLocaleString();
function item(number: number, fields: Partial<Escalation> = {}): Escalation {
  const kind = fields.kind ?? 'issue';
  return {
    key: `owner/repo#${number}`, repo: 'owner/repo', number, kind, title: `Item ${number}`,
    url: `https://github.com/owner/repo/${kind === 'pr' ? 'pull' : 'issues'}/${number}`,
    summary: null, summaryCheckedAt: at, summaryStale: false, summaryError: null, ...fields,
  };
}
function repo(fields: Partial<RepositoryAttention> = {}): RepositoryAttention {
  return { repo: 'owner/repo', label: 'needs-human', entries: [], checkedAt: at, stale: false, error: null, ...fields };
}
function snap(fields: Partial<AttentionSnapshot> = {}): AttentionSnapshot {
  return {
    tasks: [], stale: [], cached: false, fetchedAt: at, error: null, repositories: [], pauses: [],
    frontDoorError: null, pausesError: null, complete: false, ...fields,
  };
}
const partial = 'Some attention sources could not be checked. Last known data is shown.';

test('attention/front-door-escalations/labelled-issues-and-prs-listed: rows show title, kind, repository, number, summary and link', async t => {
  const healthy = snap({ complete: true, repositories: [repo({ entries: [
    item(4, { title: 'Draft needs input', summary: '**nightshift** — The spec draft needs human input.' }),
    item(9, { kind: 'pr', title: 'Revise PR', key: 'owner/repo#9' }),
  ] }), repo({ repo: 'owner/custom', label: 'blocked' })] });
  const view = page([healthy]);
  t.after(view.dispose);
  await view.settle();
  const lines = view.lines();
  assert.ok(lines.includes('Issue · owner/repo #4'));
  assert.ok(lines.includes('PR · owner/repo #9'));
  assert.ok(lines.includes('Draft needs input'));
  assert.ok(lines.includes('**nightshift** — The spec draft needs human input.'));
  assert.ok(lines.includes('2 open escalations'));
  // A successful null summary is warning-free and is not "unavailable".
  assert.ok(!view.text().includes('Summary unavailable'));
  assert.ok(!view.text().includes('Stale'));
  // A healthy repository with no rows adds no heading.
  assert.ok(!lines.includes('owner/custom'));
  assert.ok(!view.text().includes('Nothing needs your attention'));
  await view.press('Open Revise PR on GitHub');
  assert.deepEqual(view.calls.links, ['https://github.com/owner/repo/pull/9']);
});

test('attention/escalation-comment-summary/failed-summary-does-not-hide-listing-changes: retained, cold and recovered summaries', async t => {
  const failing = snap({ error: partial, repositories: [repo({ stale: true, error: 'Some escalation summaries could not be checked.', entries: [
    item(1, { summary: 'Old headline', summaryCheckedAt: earlier, summaryStale: true, summaryError: 'Comment read failed.' }),
    item(2, { title: 'Newly listed', summary: null, summaryCheckedAt: null, summaryStale: true, summaryError: 'Comment read failed.' }),
    item(3, { summary: 'Fresh headline' }),
  ] })] });
  const recovered = snap({ complete: true, repositories: [repo({ entries: [item(1, { summary: 'New headline' }), item(2, { title: 'Newly listed' })] })] });
  const view = page([failing, failing, recovered]);
  t.after(view.dispose);
  await view.settle();
  const lines = view.lines();
  assert.ok(lines.includes('Old headline'));
  assert.ok(lines.includes(`Stale summary · Comment read failed. · Summary checked: ${when(earlier)}`));
  assert.ok(lines.includes('Newly listed'));
  assert.ok(lines.includes('Summary unavailable'));
  assert.ok(lines.includes('Comment read failed. · Summary checked: Not checked yet'));
  assert.ok(lines.includes('Fresh headline'));
  assert.ok(lines.includes(`Stale · Some escalation summaries could not be checked. · Listing checked: ${when(at)}`));
  assert.ok(lines.includes(partial));
  view.calls.intervals[0]();
  await view.settle();
  assert.ok(view.lines().includes('New headline'));
  assert.ok(!view.text().includes('Stale') && !view.text().includes('Summary unavailable') && !view.text().includes('Item 3'));
});

test('attention/safe-plugin-attention-projection/structured-stale-response-reaches-page: cold and warm repository failures and empty states', async t => {
  const stale = snap({ error: partial, repositories: [
    repo({ repo: 'owner/cold', checkedAt: null, stale: true, error: 'Not checked yet.' }),
    repo({ stale: true, error: 'Could not read the repository listing.', checkedAt: earlier, entries: [item(5, { summary: 'Kept' })] }),
  ] });
  const view = page([snap({ cached: true }), stale]);
  t.after(view.dispose);
  await view.settle();
  assert.deepEqual(view.calls.reads, [false, true]);
  const lines = view.lines();
  assert.ok(lines.includes('Stale · Not checked yet. · Listing checked: Not checked yet'));
  assert.ok(lines.includes(`Stale · Could not read the repository listing. · Listing checked: ${when(earlier)}`));
  assert.ok(lines.includes('Kept') && lines.includes('Item 5'));
  assert.ok(!view.text().includes('Nothing'));

  const cached = page([snap({ cached: true, repositories: [repo()] })]);
  t.after(cached.dispose);
  await cached.settle();
  assert.ok(cached.lines().includes('0 open escalations · Cached'));
  assert.ok(cached.lines().includes('Nothing listed in cached data. Not yet confirmed.'));
  assert.ok(!cached.text().includes('Nothing needs your attention'));
  const incomplete = page([snap({ error: partial, repositories: [repo({ stale: true, error: 'Timed out.' })] })]);
  t.after(incomplete.dispose);
  await incomplete.settle();
  assert.ok(incomplete.lines().includes('Nothing listed, but some sources could not be checked.'));
  const checked = page([snap({ complete: true, repositories: [repo()] })]);
  t.after(checked.dispose);
  await checked.settle();
  assert.ok(checked.lines().includes('Nothing needs your attention.'));
});

test('attention/additive-attention-json/worker-history-no-longer-renders: a legacy worker payload renders no rows or counts', async t => {
  const worker = { key: 'owner/repo#7', repo: 'owner/repo', issue: 7, title: 'Worker task title', summary: 'Worker summary', state: 'needs_decision', url: 'https://github.com/owner/repo/issues/7', checkedAt: at, stale: true };
  const update = 'Front-door attention is unavailable. Update Nightshift on the host Mac.';
  const legacy = snap({ error: update, frontDoorError: update, tasks: [worker as never], stale: [{ ...worker, key: 'owner/repo#8' } as never] });
  const view = page([legacy]);
  t.after(view.dispose);
  await view.settle();
  const text = view.text();
  for (const absent of ['Worker task title', 'Worker summary', 'Needs a decision', 'Awaiting merge', 'outstanding', 'Could not recheck', '#7', '#8', 'Nothing needs your attention']) {
    assert.ok(!text.includes(absent), `Unexpected ${absent}`);
  }
  assert.equal(view.lines().filter(line => line === update).length, 1);
  assert.ok(view.lines().includes('0 open escalations'));
});

test('attention/factory-pause-pointers/paused-runs-point-to-factory: compact pointers name the change; Factory props and refresh triggers are unchanged', async t => {
  const view = page([snap({ complete: true, pauses: [
    { change: 'add-widgets', reason: 'Review section 1', checkedAt: at, stale: false },
    { change: 'old-run', reason: 'Task failed', checkedAt: earlier, stale: true },
  ] })]);
  t.after(view.dispose);
  await view.settle();
  const lines = view.lines();
  assert.ok(lines.includes('add-widgets — Review section 1 · See Factory below: add-widgets'));
  assert.ok(lines.includes('Stale · old-run — Task failed · See Factory below: old-run'));
  assert.ok(!view.text().includes('Nothing needs your attention'));
  // Pointers carry no controls; Factory gets exactly the surface's theme, host and navigation.
  assert.ok(view.find('Refresh Nightshift'));
  assert.ok(!view.find('Resume add-widgets') && !view.find('Open add-widgets on GitHub'));
  const props = view.calls.factory.at(-1)!;
  assert.deepEqual(Object.keys(props).sort(), ['host', 'navigation', 'theme']);
  assert.equal(props.theme, view.surface.theme);
  assert.equal(props.host, view.surface.host);
  assert.equal(props.navigation, view.surface.navigation);
  assert.deepEqual(view.calls.reads, [false, true]);
  view.calls.intervals[0]();
  await view.settle();
  view.calls.foreground[0]('background');
  view.calls.foreground[0]('active');
  await view.settle();
  await view.press('Refresh Nightshift');
  assert.deepEqual(view.calls.reads, [false, true, true, true, true]);
});

test('attention/safe-plugin-attention-projection/legacy-and-malformed-producers-remain-visible-failures: failed refresh retains the last good page', async t => {
  const good = snap({ complete: true, repositories: [repo({ entries: [item(6, { title: 'Still here' })] })] });
  const view = page([good, good, new Error('Invalid attention payload')]);
  t.after(view.dispose);
  await view.settle();
  await view.press('Refresh Nightshift');
  const lines = view.lines();
  assert.ok(lines.includes('Could not reach Nightshift on this host. Any previously loaded list is retained.'));
  assert.ok(lines.includes('Still here'));
  assert.ok(!view.text().includes('Invalid attention payload'));
  const cold = page([new Error('spawn failed')]);
  t.after(cold.dispose);
  await cold.settle();
  assert.ok(cold.lines().includes('Loading Nightshift…'));
  assert.ok(cold.lines().includes('Could not reach Nightshift on this host. Any previously loaded list is retained.'));
  assert.ok(!cold.text().includes('Nothing'));
});

test('attention/safe-plugin-attention-projection/external-escalation-link-opens-browser: open failure stays visible and nothing else runs', async t => {
  const view = page([snap({ complete: true, repositories: [repo({ entries: [item(12, { title: 'Broken link' })] })] })],
    { open: async () => { throw new Error('no browser'); } });
  t.after(view.dispose);
  await view.settle();
  const reads = view.calls.reads.length;
  await view.press('Open Broken link on GitHub');
  assert.deepEqual(view.calls.links, ['https://github.com/owner/repo/issues/12']);
  assert.ok(view.lines().includes('Could not open GitHub.'));
  assert.equal(view.calls.reads.length, reads);
  // A successful refresh does not hide the open failure.
  await view.press('Refresh Nightshift');
  assert.ok(view.lines().includes('Could not open GitHub.'));
});

test('attention page styles: rows, titles, footer and repository warnings use their theme tokens', async t => {
  const view = page([snap({ complete: false, repositories: [
    repo({ repo: 'owner/stale-only', stale: true, error: null }),
    repo({ repo: 'owner/error-only', stale: false, error: 'Listing failed.' }),
    repo({ entries: [item(3, { title: 'Styled row' })] }),
  ] })]);
  t.after(view.dispose);
  await view.settle();
  const textOf = (node: Node): string => node.children.map(c => typeof c === 'string' ? c : textOf(c)).join('');
  const nodes = view.nodes();
  const texts = nodes.filter(node => node.type === 'Text');
  const title = texts.find(node => textOf(node) === 'Styled row')!;
  assert.deepEqual({ ...title.props.style }, { color: 'fg', fontSize: 17, fontWeight: '600' });
  const card = nodes.find(node => node.type === 'View' && node.children.includes(title))!;
  assert.ok(card, 'row container');
  assert.deepEqual({ ...card.props.style }, { padding: 16, gap: 8, borderWidth: 1, borderColor: 'line', borderRadius: 12, backgroundColor: 'card' });
  const footer = texts.find(node => textOf(node).startsWith('List loaded: '))!;
  assert.equal(textOf(footer), `List loaded: ${when(at)} · Opening an item does not resolve it.`);
  assert.deepEqual({ ...footer.props.style }, { color: 'muted', fontSize: 12 });
  // Either stale or an error alone produces the repository warning.
  const staleOnly = texts.find(node => textOf(node) === `Stale · Could not be fully checked. · Listing checked: ${when(at)}`);
  const errorOnly = texts.find(node => textOf(node) === `Stale · Listing failed. · Listing checked: ${when(at)}`);
  assert.ok(staleOnly && errorOnly);
  assert.deepEqual({ ...staleOnly.props.style }, { color: 'warn', fontSize: 12 });
  assert.deepEqual({ ...errorOnly.props.style }, { color: 'warn', fontSize: 12 });
});
