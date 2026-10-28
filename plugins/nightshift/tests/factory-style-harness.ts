import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import React from 'react';
import ts from 'typescript';
import type { FactoryActInput, FactoryRun, FactorySnapshot, FactoryLandingInput, FactoryLandingSnapshot, FactoryLandInput, FactoryLandStopInput } from '../shared/factory';

// Distinct sentinels make incorrect token choices visible in rendered styles.
export const colors = {
  accent: 'theme-accent', accentForeground: 'theme-accentForeground',
  surface0: 'theme-surface0', surface1: 'theme-surface1', surface2: 'theme-surface2',
  border: 'theme-border', foreground: 'theme-foreground', foregroundMuted: 'theme-foregroundMuted',
  statusSuccess: 'theme-statusSuccess', statusWarning: 'theme-statusWarning', statusDanger: 'theme-statusDanger',
};
type Props = Record<string, any>;
export type Node = { type: unknown; props: Props; children: (Node | string)[] };
type Hook = { value?: any; deps?: unknown[]; cleanup?: () => void };

// Render the real TSX with per-component hooks; keep host dependencies inert.
export function styleView(runs: FactoryRun[], options: {
  act?: (input: FactoryActInput) => Promise<unknown>;
  read?: () => Promise<FactorySnapshot>;
  landing?: (input: FactoryLandingInput) => Promise<FactoryLandingSnapshot>;
  land?: (input: FactoryLandInput) => Promise<unknown>;
  stop?: (input: FactoryLandStopInput) => Promise<unknown>;
  client?: unknown;
  navigation?: { openWorkspace?: (input: { serverId: string; workspaceId: string }) => void };
  openURL?: (url: string) => Promise<void>;
} = {}) {
  const components = new Map<string, Hook[]>();
  let hooks: Hook[], cursor = 0;
  let effects: (() => void)[] = [];
  let themeColors = colors;
  const slot = () => hooks[cursor++] ?? (hooks[cursor - 1] = {});
  const changed = (hook: Hook, deps: unknown[]) => !hook.deps || deps.length !== hook.deps.length ||
    deps.some((dep, i) => !Object.is(dep, hook.deps![i]));
  const react = {
    ...React,
    useState(initial: unknown) {
      const hook = slot();
      if (!('value' in hook)) hook.value = initial;
      return [hook.value, (value: unknown) => { hook.value = typeof value === 'function' ? value(hook.value) : value; }];
    },
    useRef(initial: unknown) { return slot().value ??= { current: initial }; },
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
  const actions: FactoryActInput[] = [];
  const reads: unknown[] = [], hosts: string[] = [], urls: string[] = [];
  const intervals: { callback: () => void; delay: number; cleared: boolean }[] = [];
  const subscriptions: { event: string; callback: (state: string) => void; removed: boolean }[] = [];
  const appState = {
    currentState: 'active',
    addEventListener(event: string, callback: (state: string) => void) {
      const subscription = { event, callback, removed: false };
      subscriptions.push(subscription);
      return { remove() { subscription.removed = true; } };
    },
  };
  const runsRpc = {};
  const actRpc = {}, landingRpc = {}, landRpc = {}, stopRpc = {};
  const landingReads: FactoryLandingInput[] = [], lands: FactoryLandInput[] = [], stops: FactoryLandStopInput[] = [];
  const landing = async (input: FactoryLandingInput) => {
    landingReads.push(input);
    return options.landing ? options.landing(input) : {
      version: 1, attempts: [], total: 0, diagnostics: [], offer: null, current: null,
      truncated: false, stale: false, fetchedAt: '', error: null,
    };
  };
  const land = async (input: FactoryLandInput) => {
    lands.push(input);
    return options.land ? options.land(input) : { version: 1, accepted: false, attempt_id: null, reason: 'No landing configured' };
  };
  const stop = async (input: FactoryLandStopInput) => {
    stops.push(input);
    return options.stop ? options.stop(input) : { version: 1, accepted: false, attempt_id: input.attempt_id, reason: 'No Stop configured' };
  };
  const read = async (input: unknown) => {
    reads.push(input);
    return options.read ? options.read() : { runs, stale: false, fetchedAt: '', error: null };
  };
  const act = async (input: FactoryActInput) => {
    actions.push(input);
    return options.act ? options.act(input) : { outcome: 'done', detail: 'Completed' };
  };
  const filename = resolve('client/factory.tsx');
  const require = createRequire(filename);
  const exports: Props = {};
  const source = ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true },
  }).outputText;
  runInNewContext(source, {
    exports,
    setInterval: (callback: () => void, delay: number) => {
      intervals.push({ callback, delay, cleared: false });
      return intervals.length;
    },
    clearInterval: (id: number) => { intervals[id - 1].cleared = true; },
    require: (name: string) => {
      if (name === 'react') return react;
      if (name === 'react-native') return {
        View: 'View', Text: 'Text', Pressable: 'Pressable', TextInput: 'TextInput',
        AppState: appState,
        Linking: { openURL: async (url: string) => { urls.push(url); await options.openURL?.(url); } },
      };
      if (name === '@getpaseo/plugin/client') return {
        useRpc: (rpc: unknown) => {
          if (rpc === runsRpc) return read;
          if (rpc === actRpc) return act;
          if (rpc === landingRpc) return landing;
          if (rpc === landRpc) return land;
          if (rpc === stopRpc) return stop;
          throw new Error('Unknown RPC descriptor');
        },
        getPaseoClient: (host: string) => {
          hosts.push(host);
          return 'client' in options ? options.client : { workspaces: { list: async () => ({ entries: [] }) } };
        },
      };
      if (name === '../shared/factory') return { factoryRuns: runsRpc, factoryAct: actRpc, factoryLanding: landingRpc, factoryLand: landRpc, factoryLandStop: stopRpc };
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
      theme: { colors: themeColors }, host: { id: 'host-1', label: 'Host' }, navigation: options.navigation ?? {},
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
    actions, reads, landingReads, lands, stops, hosts, urls, nodes, text, find, settle, intervals, subscriptions, appState,
    setColors(next: typeof colors) { themeColors = next; draw(); },
    async press(control: string | Node) {
      const button = typeof control === 'string' ? find(control) : control;
      assert.ok(button, `Missing ${control}`);
      if (!button.props.disabled) button.props.onPress();
      draw();
      await settle();
    },
    dispose() { components.forEach(hooks => hooks.forEach(hook => hook.cleanup?.())); },
  };
}
