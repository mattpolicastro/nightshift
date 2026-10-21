import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import React from 'react';
import ts from 'typescript';
import type { FactoryActInput, FactoryRun } from '../shared/factory';

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
export function styleView(runs: FactoryRun[], options: { act?: (input: FactoryActInput) => Promise<unknown> } = {}) {
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
  const runsRpc = {}, actRpc = {};
  const read = async () => ({ runs, stale: false, fetchedAt: '', error: null });
  const act = async (input: FactoryActInput) => {
    actions.push(input);
    return options.act ? options.act(input) : { outcome: 'done', detail: 'Completed' };
  };
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
        Linking: { openURL: async () => {} },
      };
      if (name === '@getpaseo/plugin/client') return {
        useRpc: (rpc: unknown) => rpc === runsRpc ? read : act,
        getPaseoClient: () => ({ workspaces: { list: async () => ({ entries: [] }) } }),
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
      theme: { colors: themeColors }, host: { id: 'host-1', label: 'Host' }, navigation: {},
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
    actions, nodes, text, find, settle,
    setColors(next: typeof colors) { themeColors = next; draw(); },
    async press(label: string) {
      const button = find(label);
      assert.ok(button, `Missing ${label}`);
      if (!button.props.disabled) button.props.onPress();
      draw();
      await settle();
    },
    dispose() { components.forEach(hooks => hooks.forEach(hook => hook.cleanup?.())); },
  };
}
