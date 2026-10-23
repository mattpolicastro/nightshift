import { test, mock, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import { createRequire, syncBuiltinESMExports } from 'node:module';
import { resolve } from 'node:path';
import { resolveHostPaths } from '../server/host-paths';

const home = '/Users/host-test';
const system = '/usr/bin:/bin:/usr/sbin:/sbin';
const settings = ['NIGHTSHIFT_BIN_DIR', 'NIGHTSHIFT_NODE_BIN', 'NIGHTSHIFT_UV', 'NIGHTSHIFT_CHECKOUT'];
const requireModule = createRequire(resolve('tests/host-paths.test.ts'));
const run = {
  change: 'gate', status: 'paused', tasks: { done: 1, total: 2 }, pr: '',
  action: { kind: 'action', headline: 'Review', why: `${home}/work`, command: 'specloop resume gate', actions: ['resume'] },
  internal: '/private/diagnostic',
};
const task = {
  repo: 'owner/repo', issue: 1, state: 'needs_decision', issue_url: 'https://github.com/owner/repo/issues/1',
  checked_at: null, error: null, internal: '/private/diagnostic',
};

// Each fixture reloads the production singletons and restores only its own host mocks.
function host(t: TestContext, env: NodeJS.ProcessEnv = {}) {
  const saved = process.env;
  process.env = { ...saved }; // A plain mapping also preserves NUL fixtures (native environ truncates them).
  settings.forEach(name => { delete process.env[name]; if (env[name] !== undefined) process.env[name] = env[name]; });
  let now = 0;
  const clock = mock.method(Date, 'now', () => now);
  const homeMock = mock.method(os, 'homedir', () => home);
  syncBuiltinESMExports();
  for (const name of ['attention', 'factory']) delete requireModule.cache[requireModule.resolve(`../server/${name}`)];
  const attention = requireModule('../server/attention') as typeof import('../server/attention');
  const factory = requireModule('../server/factory') as typeof import('../server/factory');
  const calls: { file: string; args: string[]; options: childProcess.ExecFileOptions }[] = [];
  const checks: string[] = [], killed: string[] = [];
  const realExists = fs.existsSync;
  const state = { missing: false, failure: false, code: 0, hold: false, realCheckout: false };
  const pending: (() => void)[] = [];
  const exists = mock.method(fs, 'existsSync', (path: fs.PathLike) => {
    checks.push(String(path));
    return state.realCheckout && String(path).endsWith('/nightshift/outcomes.py') ? realExists(path) : !state.missing;
  });
  const exec = mock.method(childProcess, 'execFile', (
    file: string, args: string[], options: childProcess.ExecFileOptions,
    callback: (error: Error | null, stdout: string, stderr: string) => void,
  ) => {
    calls.push({ file, args, options });
    const output = args.includes('attention')
      ? { version: 1, offline: args.includes('--offline'), tasks: [task], stale: [] }
      : args.includes('status') ? { version: 1, runs: [run] }
        : { version: 1, outcome: state.code ? 'refused' : 'done', verb: 'resume', detail: `${home}/result`, internal: '/private/diagnostic' };
    const error = state.failure ? Object.assign(new Error('/private/launch-error'), { code: 'ENOENT' })
      : state.code ? Object.assign(new Error('/private/stderr'), { code: state.code }) : null;
    const finish = () => callback(error, JSON.stringify(output), '/private/stderr');
    if (state.hold) pending.push(finish); else queueMicrotask(finish);
    return { kill(signal: string) { killed.push(signal); } } as childProcess.ChildProcess;
  });
  syncBuiltinESMExports();
  t.after(() => {
    attention.dispose(); factory.disposeFactory();
    for (const stub of [exec, exists, clock, homeMock]) stub.mock.restore();
    syncBuiltinESMExports();
    process.env = saved;
  });
  return { ...attention, ...factory, calls, checks, state, killed, pending, tick() { now += 60_000; } };
}

async function commands(h: ReturnType<typeof host>, uv: string, cwd: string, bin: string, node: string) {
  await h.readAttention({ refresh: false });
  await h.readFactoryRuns();
  await h.actOnFactoryRun({ change: 'gate', verb: 'resume' });
  assert.deepEqual(h.calls.map(call => call.args), [
    ['run', '--no-sync', 'nightshift', 'attention', '--json', '--offline'],
    ['run', '--no-sync', 'specloop', 'status', '--json'],
    ['run', '--no-sync', 'specloop', 'act', 'gate', 'resume', '--json'],
  ]);
  h.calls.forEach((call, i) => {
    assert.equal(call.file, uv); assert.equal(call.options.cwd, cwd);
    assert.equal(call.options.env?.PATH, `${i ? `${node}:` : ''}${bin}:${system}`);
    assert.equal(call.options.shell, false);
    assert.equal(call.options.timeout, [15_000, 60_000, 330_000][i]);
    assert.equal(call.options.maxBuffer, 2 * 1024 * 1024);
  });
  assert.deepEqual(h.checks, [`${cwd}/nightshift/outcomes.py`, `${home}/.nightshift/outcomes.sqlite3`]);
}

test('host-paths/trusted-host-path-settings/unconfigured-host-retains-defaults', async t => {
  await commands(host(t), '/opt/homebrew/bin/uv', `${home}/Projects/nightshift`, '/opt/homebrew/bin', `${home}/n/bin`);
});

test('host-paths/trusted-host-path-settings/explicit-settings-override-defaults', async t => {
  const h = host(t, { NIGHTSHIFT_BIN_DIR: '/tools', NIGHTSHIFT_NODE_BIN: '/node', NIGHTSHIFT_CHECKOUT: '/checkout' });
  await commands(h, '/tools/uv', '/checkout', '/tools', '/node');
  process.env.NIGHTSHIFT_UV = '/other/uv';
  await h.actOnFactoryRun({ change: 'gate', verb: 'resume' });
  assert.equal(h.calls.at(-1)?.file, '/other/uv');
});

test('host-paths/literal-path-resolution/paths-remain-literal', () => {
  const paths = resolveHostPaths('factory', {
    NIGHTSHIFT_BIN_DIR: '~/absent tools $HOME', NIGHTSHIFT_NODE_BIN: '~/node `whoami`',
    NIGHTSHIFT_CHECKOUT: '/absent//../checkout:copy $(touch nope);* ',
  }, home);
  assert.deepEqual(paths, {
    cwd: '/absent//../checkout:copy $(touch nope);* ', uv: `${home}/absent tools $HOME/uv`,
    path: `${home}/node \`whoami\`:${home}/absent tools $HOME:${system}`,
  });
});

test('host-paths/plugin-host-command-configuration/checkout-comes-from-configuration', async t => {
  const literal = 'absent tools $HOME $(touch nope); `whoami` *';
  const h = host(t, {
    NIGHTSHIFT_BIN_DIR: `~/${literal}`, NIGHTSHIFT_NODE_BIN: `/node ${literal}`,
    NIGHTSHIFT_CHECKOUT: `~/${literal}/checkout:copy`, NIGHTSHIFT_UV: `/uv ${literal}:copy`,
  });
  await commands(h, `/uv ${literal}:copy`, `${home}/${literal}/checkout:copy`, `${home}/${literal}`, `/node ${literal}`);
  const before = h.calls.length;
  await h.readAttention({ refresh: false });
  await h.readFactoryRuns(); // The action invalidated the Factory cache.
  await h.readFactoryRuns({ refresh: true });
  assert.equal(h.calls.length, before + 1);
  h.tick();
  await h.readAttention({ refresh: true });
  assert.equal(h.calls.at(-1)?.options.timeout, 60_000);
  assert.deepEqual(h.calls.at(-1)?.args, ['run', '--no-sync', 'nightshift', 'attention', '--json']);
});

test('host-paths/plugin-host-command-configuration/checkout-comes-from-configuration: symlink before ..', async t => {
  const root = fs.mkdtempSync(`${os.tmpdir()}/nightshift-paths-`);
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(`${root}/deploy`);
  fs.mkdirSync(`${root}/releases/v1`, { recursive: true });
  fs.mkdirSync(`${root}/releases/v2/nightshift/nightshift`, { recursive: true });
  fs.writeFileSync(`${root}/releases/v2/nightshift/nightshift/outcomes.py`, '');
  fs.symlinkSync(`${root}/releases/v1`, `${root}/deploy/current`, 'dir');
  const cwd = `${root}/deploy/current/../v2/nightshift`;
  assert.equal(fs.existsSync(`${cwd}/nightshift/outcomes.py`), true);
  assert.equal(fs.existsSync(resolve(cwd, 'nightshift/outcomes.py')), false);
  const h = host(t, { NIGHTSHIFT_CHECKOUT: cwd, NIGHTSHIFT_UV: '/tools/uv' });
  h.state.realCheckout = true;
  await commands(h, '/tools/uv', cwd, '/opt/homebrew/bin', `${home}/n/bin`);
});

test('host-paths/literal-path-resolution/invalid-settings-do-not-fall-back', async t => {
  for (const name of settings) {
    const values = ['', ' \t', 'relative', '~', '~other/bin', '/bad\0path', '/bad\npath', '/bad\rpath'];
    if (name === 'NIGHTSHIFT_BIN_DIR' || name === 'NIGHTSHIFT_NODE_BIN') values.push('/bad:path');
    for (const [i, value] of values.entries()) await t.test(`${name} invalid case ${i}`, async t => {
      assert.throws(() => resolveHostPaths('factory', { [name]: value }, home), { message: `Invalid host path setting: ${name}` });
      const h = host(t, { [name]: value });
      await assert.rejects(h.readFactoryRuns(), /^Error: Factory status is unavailable on this host\. Try again shortly\.$/);
      if (name === 'NIGHTSHIFT_NODE_BIN') {
        await h.readAttention({ refresh: false });
        assert.equal(h.calls.length, 1); // Attention does not consume Node configuration.
      } else {
        await assert.rejects(h.readAttention({ refresh: false }), /^Error: Nightshift is unavailable on this host\. Check the installation on the host Mac\.$/);
        assert.equal(h.calls.length, 0);
      }
    });
  }
});

test('host-paths/plugin-host-command-configuration/rpc-cannot-select-host-commands', async t => {
  const h = host(t);
  const extra = { cwd: '/rpc', uv: '/rpc/uv', path: '/rpc', NIGHTSHIFT_CHECKOUT: '/rpc', NIGHTSHIFT_UV: '/rpc/uv' };
  const attention = await h.readAttention({ refresh: false, ...extra });
  const snapshot = await h.readFactoryRuns({ ...extra, refresh: true });
  assert.equal(snapshot.runs[0].action?.why, '~/work');
  assert.ok(!JSON.stringify([attention, snapshot]).includes('/private/'));
  assert.ok(!JSON.stringify(snapshot).includes(home));
  const before = h.calls.length;
  for (const input of [{ change: 'unknown', verb: 'resume' }, { change: 'gate', verb: 'publish' }] as const) {
    assert.equal((await h.actOnFactoryRun({ ...input, ...extra })).outcome, 'refused');
  }
  assert.equal(h.calls.length, before);
  const note = '$(touch nope); `whoami` && echo "$HOME"\n--json';
  assert.deepEqual(await h.actOnFactoryRun({ change: 'gate', verb: 'resume', note, ...extra }), {
    version: 1, outcome: 'done', verb: 'resume', detail: '~/result',
  });
  assert.deepEqual(h.calls.at(-1)?.args, ['run', '--no-sync', 'specloop', 'act', 'gate', 'resume', `--note=${note}`, '--json']);
  for (const call of h.calls) {
    assert.equal(call.file, '/opt/homebrew/bin/uv');
    assert.equal(call.options.cwd, `${home}/Projects/nightshift`);
    assert.equal(call.options.shell, false);
  }
});

test('host-paths/plugin-host-command-configuration/configuration-failures-preserve-plugin-errors', async t => {
  for (const mode of ['invalid', 'missing checkout', 'missing executable']) await t.test(mode, async t => {
    const h = host(t);
    const fail = (enabled: boolean) => {
      if (mode === 'invalid') {
        if (enabled) process.env.NIGHTSHIFT_CHECKOUT = 'private-invalid'; else delete process.env.NIGHTSHIFT_CHECKOUT;
      } else {
        process.env.NIGHTSHIFT_CHECKOUT = '/absent/checkout'; process.env.NIGHTSHIFT_UV = '/absent/uv';
        h.state.missing = enabled && mode === 'missing checkout'; h.state.failure = enabled;
      }
    };
    fail(true);
    await assert.rejects(h.readAttention({ refresh: false }), { message: 'Nightshift is unavailable on this host. Check the installation on the host Mac.' });
    await assert.rejects(h.readFactoryRuns(), { message: 'Factory status is unavailable on this host. Try again shortly.' });
    fail(false); h.tick();
    const attention = await h.readAttention({ refresh: false }), factory = await h.readFactoryRuns();
    fail(true); h.tick();
    const calls = h.calls.length;
    const staleAttention = await h.readAttention({ refresh: true }), staleFactory = await h.readFactoryRuns();
    assert.deepEqual(staleAttention, { ...attention, cached: true, error: 'Refresh unavailable. Showing the last loaded list.' });
    assert.deepEqual(staleFactory, { ...factory, stale: true, error: 'Refresh unavailable. Showing the last loaded list.' });
    assert.deepEqual(await h.actOnFactoryRun({ change: 'gate', verb: 'resume' }), {
      version: 1, outcome: 'refused', verb: 'resume', detail: 'Action result unavailable. Refresh the list before trying again.',
    });
    if (mode === 'invalid') assert.equal(h.calls.length, calls);
    if (mode === 'missing checkout') assert.equal(h.calls.length, calls + 2); // Installation check prevents attention spawn.
    fail(false); h.tick(); h.state.code = 1;
    assert.equal((await h.readAttention({ refresh: true })).error, 'Some outcomes could not be checked. Last known outcomes are shown.');
    assert.deepEqual(await h.actOnFactoryRun({ change: 'gate', verb: 'resume' }), {
      version: 1, outcome: 'refused', verb: 'resume', detail: '~/result',
    });
  });
});

test('configured adapters terminate pending children and remove completed children', async t => {
  const h = host(t, { NIGHTSHIFT_CHECKOUT: '/checkout' });
  await h.readFactoryRuns(); h.tick(); h.state.hold = true;
  const attention = h.readAttention({ refresh: true });
  const factory = assert.rejects(h.readFactoryRuns(), /stopped/);
  h.dispose(); h.disposeFactory();
  assert.deepEqual(h.killed, ['SIGTERM', 'SIGTERM']);
  h.pending.forEach(finish => finish());
  await attention; await factory;
});
