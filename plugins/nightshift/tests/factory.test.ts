import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { createFactory, readFactoryRuns, actOnFactoryRun, disposeFactory } from '../server/factory';

const home = '/Users/factory-test';
const run = {
  change: 'gate-change', status: 'paused', tasks: { done: 1, total: 3 },
  pr: 'https://github.com/owner/repo/pull/7',
  action: {
    kind: 'action', headline: 'Review section 1', why: `Inspect ${home}/work`,
    command: 'specloop resume gate-change', actions: ['resume', 'abort'],
  },
  pause: { detail: '/private/raw-diagnostic' }, head: 'internal-head',
  transcript: '/private/transcript',
};
const response = (value: unknown, code = 0) => ({ code, stdout: JSON.stringify(value) });
const status = (runs: unknown[] = [run]) => response({ version: 1, runs, internal: '/private/config' });
const done = () => response({ version: 1, outcome: 'done', verb: 'resume', detail: 'Resumed' });

test('run-context/details-in-the-factory-section/context-redacted-and-links-checked', async () => {
  const diagnostic = `${home}/one and ${home}/two`;
  const context = {
    summary: diagnostic, repo: `${home}/Projects/nightshift`, branch: diagnostic,
    task: { label: diagnostic, section: diagnostic, text: diagnostic, internal: '/private/task' },
    detail: diagnostic,
    recent: [{ at: diagnostic, text: diagnostic, internal: '/private/event' }],
    github: 'file:///private/change', internal: '/private/context',
  };
  const factory = createFactory(async () => status([{ ...run, context }]), () => 0, home);
  const snapshot = await factory.read();
  assert.equal(snapshot.stale, false);
  assert.deepEqual(snapshot.runs[0].context, {
    summary: '~/one and ~/two', repo: '~/Projects/nightshift', branch: '~/one and ~/two',
    task: { label: '~/one and ~/two', section: '~/one and ~/two', text: '~/one and ~/two' },
    detail: '~/one and ~/two', recent: [{ at: '~/one and ~/two', text: '~/one and ~/two' }],
    github: '',
  });
  assert.ok(!JSON.stringify(snapshot).includes(home));
  assert.ok(!JSON.stringify(snapshot).includes('/private'));

  const published = 'https://github.com/owner/repo/tree/factory-context/openspec/changes/factory-context';
  for (const [github, expected] of [
    [published, published],
    [`https://github.com/owner/repo/tree/main${home}/change`, 'https://github.com/owner/repo/tree/main~/change'],
    ['', ''], ['not a URL', ''], ['javascript:alert(1)', ''],
    ['http://github.com/owner/repo', ''], ['https://github.com.evil.test/owner/repo', ''],
    ['https://github.com@evil.test/owner/repo', ''], ['https://user:pass@github.com/owner/repo', ''],
    ['https://example.com/owner/repo', ''], ['//github.com/owner/repo', ''],
  ]) {
    const service = createFactory(async () => status([{ ...run, context: { ...context, task: null, recent: [], github } }]), () => 0, home);
    const projected = (await service.read()).runs[0].context!;
    assert.equal(projected.github, expected, github);
    assert.equal(projected.task, null);
    assert.deepEqual(projected.recent, []);
  }
});

test('context validates nested display fields and retains the last good snapshot on malformed input', async () => {
  const context = { summary: '', repo: '', branch: '', task: null, detail: '', recent: [], github: '' };
  for (const invalid of [
    null, {}, ...['summary', 'repo', 'branch', 'detail', 'github'].map(key => ({ ...context, [key]: 42 })),
    { ...context, task: { label: '2.1', section: 'Plugin', text: false } },
    { ...context, task: { label: '2.1' } },
    { ...context, recent: [{ at: '2026-10-21T12:00:00Z', text: 42 }] },
    { ...context, recent: [{ at: null, text: 'resumed' }] },
    { ...context, recent: {} },
  ]) {
    let time = 0;
    const factory = createFactory(async () => status([{ ...run, context: time ? invalid : context }]), () => time);
    const before = await factory.read();
    assert.deepEqual(before.runs[0].context, context);
    time = 15_000;
    const after = await factory.read();
    assert.equal(after.stale, true);
    assert.deepEqual(after.runs, before.runs);
  }
});

test('paseo-runs-page/server-checks-before-acting/unknown-change-refused', async () => {
  const calls: string[][] = [];
  const factory = createFactory(async args => { calls.push(args); return status(); });
  assert.equal((await factory.act({ change: run.change, verb: 'resume' })).outcome, 'refused');
  assert.equal(calls.length, 0);
  await factory.read();
  assert.equal((await factory.act({ change: 'unknown', verb: 'resume' })).outcome, 'refused');
  assert.equal((await factory.act({ change: run.change, verb: 'publish' })).outcome, 'refused');
  assert.deepEqual(calls, [['status', '--json']]);
});

test('paseo-runs-page/server-checks-before-acting/argv-not-shell', async () => {
  // Observe the production execFile boundary without executing host commands.
  const calls: { file: string; args: string[]; options: childProcess.ExecFileOptions }[] = [];
  const stub = mock.method(childProcess, 'execFile', (
    file: string, args: string[], options: childProcess.ExecFileOptions,
    callback: (error: Error | null, stdout: string, stderr: string) => void,
  ) => {
    calls.push({ file, args, options });
    queueMicrotask(() => callback(null, (args.includes('status') ? status() : done()).stdout, 'private stderr'));
    return { kill() {} } as childProcess.ChildProcess;
  });
  syncBuiltinESMExports();
  try {
    await readFactoryRuns();
    for (const note of [
      'Keep $(touch /tmp/nope); `whoami` && echo "$HOME" | cat\nsecond line',
      '--no-sync', '--json', '-n', '',
    ]) {
      const before = calls.length;
      assert.equal((await actOnFactoryRun({ change: run.change, verb: 'resume', note })).outcome, 'done');
      assert.equal(calls.length, before + 1);
      const call = calls[before];
      assert.equal(call.file, '/opt/homebrew/bin/uv');
      assert.equal(call.options.shell, false);
      assert.equal(call.options.timeout, 330_000);
      assert.ok(String(call.options.env?.PATH).split(':')[0].endsWith('/n/bin'), 'finish needs npx from ~/n/bin');
      assert.deepEqual(call.args, ['run', '--no-sync', 'specloop', 'act', run.change, 'resume', `--note=${note}`, '--json']);
    }
  } finally {
    disposeFactory(); stub.mock.restore(); syncBuiltinESMExports();
  }
});

test('paseo-runs-page/stale-tolerance/read-failure-keeps-the-list', async () => {
  let time = 0, fail = false;
  const factory = createFactory(async () => {
    if (fail) throw new Error('/private/credential stderr');
    return status();
  }, () => time, home);
  const before = await factory.read();
  time = 15_000; fail = true;
  const after = await factory.read({ refresh: true });
  assert.deepEqual(after.runs, before.runs);
  assert.equal(after.fetchedAt, before.fetchedAt);
  assert.equal(after.stale, true);
  assert.ok(after.error);
  assert.ok(!JSON.stringify(after).includes('credential'));
  time += 15_000; fail = false;
  assert.equal((await factory.read()).stale, false);
});

test('status validation failures preserve the last good status and its action list', async () => {
  for (const bad of [
    { code: 0, stdout: 'not json' }, { ...status([]), code: 1 },
    response({ version: 2, runs: [run] }), response({ version: 1, runs: [{}] }),
    status([{ ...run, action: { ...run.action, actions: ['execute'] } }]),
    status([{ ...run, pr: 'file:///private/config' }]),
  ]) {
    let time = 0, reads = 0;
    const factory = createFactory(async args => args[0] === 'act' ? done() : ++reads === 1 ? status() : bad, () => time);
    const before = await factory.read();
    time = 15_000;
    const after = await factory.read();
    assert.equal(after.stale, true);
    assert.deepEqual(after.runs, before.runs);
    assert.equal((await factory.act({ change: run.change, verb: 'resume' })).outcome, 'done');
  }
});

test('cold failure is visible and retries, including manual refresh, are throttled for 15 seconds', async () => {
  let calls = 0, time = 0;
  const factory = createFactory(async () => { calls++; throw new Error('secret'); }, () => time);
  await assert.rejects(factory.read(), /unavailable/);
  await assert.rejects(factory.read({ refresh: true }), /unavailable/);
  assert.equal(calls, 1);
  time = 15_000;
  await assert.rejects(factory.read(), /unavailable/);
  assert.equal(calls, 2);
});

test('status calls are shared and cached for 15 seconds across clients', async () => {
  let calls = 0, time = 0, release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const factory = createFactory(async () => { calls++; await gate; return status(); }, () => time);
  const first = factory.read(), second = factory.read({ refresh: true });
  release();
  assert.deepEqual(await first, await second);
  time = 14_999;
  await factory.read({ refresh: true });
  assert.equal(calls, 1);
  time = 15_000;
  await factory.read();
  assert.equal(calls, 2);
});

test('completed action invalidates status immediately and cannot authorize a removed run', async () => {
  const calls: string[][] = [];
  let acted = false;
  const factory = createFactory(async args => {
    calls.push(args);
    if (args[0] === 'act') { acted = true; return done(); }
    return status(acted ? [] : [run]);
  }, () => 0);
  await factory.read();
  await factory.act({ change: run.change, verb: 'resume' });
  assert.deepEqual((await factory.read()).runs, []);
  assert.equal((await factory.act({ change: run.change, verb: 'resume' })).outcome, 'refused');
  assert.deepEqual(calls, [['status', '--json'], ['act', run.change, 'resume', '--json'], ['status', '--json']]);
});

test('a status read started before an action cannot overwrite fresh post-action state', async () => {
  let reads = 0, time = 0, release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const factory = createFactory(async args => {
    if (args[0] === 'act') return done();
    const number = ++reads;
    if (number === 2) await gate;
    return status(number < 3 ? [run] : []);
  }, () => time);
  await factory.read(); time = 15_000;
  const old = factory.read();
  await factory.act({ change: run.change, verb: 'resume' });
  assert.deepEqual((await factory.read()).runs, []);
  release();
  assert.deepEqual((await old).runs, []);
  assert.deepEqual((await factory.read()).runs, []);
});

test('only display fields cross the boundary, with home redacted in every free-text field', async () => {
  const diagnostic = `${home}/one and ${home}/two`;
  const factory = createFactory(async args => args[0] === 'status'
    ? status([{ ...run, action: { ...run.action, kind: diagnostic, headline: diagnostic, why: diagnostic, command: diagnostic } }])
    : response({ version: 1, outcome: 'done', verb: 'resume', detail: diagnostic, stderr: '/private/secret' }), () => 0, home);
  const snapshot = await factory.read();
  assert.deepEqual(Object.keys(snapshot.runs[0]).sort(), ['action', 'change', 'pr', 'status', 'tasks']);
  assert.ok(!JSON.stringify(snapshot).includes(home));
  assert.ok(!JSON.stringify(snapshot).includes('/private'));
  assert.equal(snapshot.runs[0].action?.why, '~/one and ~/two');
  assert.deepEqual(await factory.act({ change: run.change, verb: 'resume' }), {
    version: 1, outcome: 'done', verb: 'resume', detail: '~/one and ~/two',
  });
});

test('CLI refusals are validated; malformed, failing and mismatched outcomes hide diagnostics', async () => {
  for (const output of [
    response({ version: 1, outcome: 'refused', verb: 'resume', detail: 'Pause changed' }, 1),
    { code: 2, stdout: '/private/secret' },
    response({ version: 2, outcome: 'done', verb: 'resume', detail: '/private/secret' }),
    response({ version: 1, outcome: 'done', verb: 'publish', detail: '/private/secret' }),
    { ...done(), code: 1 },
  ]) {
    const factory = createFactory(async args => args[0] === 'status' ? status() : output);
    await factory.read();
    const result = await factory.act({ change: run.change, verb: 'resume' });
    assert.equal(result.outcome, 'refused');
    assert.ok(!JSON.stringify(result).includes('/private'));
    if (output.code === 1 && output.stdout.includes('Pause changed')) assert.equal(result.detail, 'Pause changed');
  }
});

test('invalid inputs and disposed services never execute commands', async () => {
  let calls = 0;
  const factory = createFactory(async () => { calls++; return status(); });
  await factory.read();
  await assert.rejects(factory.act({ change: run.change, verb: 'resume', note: 'x'.repeat(2001) }), /Invalid/);
  await assert.rejects(factory.act({ change: '--help', verb: 'resume' }), /Invalid/);
  assert.equal(calls, 1);
  factory.dispose();
  await assert.rejects(factory.read(), /stopped/);
  await assert.rejects(factory.act({ change: run.change, verb: 'resume' }), /stopped/);
  assert.equal(calls, 1);
});

test('running rows and an empty status are valid without action metadata', async () => {
  let time = 0;
  const factory = createFactory(async () => status(time ? [] : [{
    change: 'running-change', status: 'running', tasks: { done: 0, total: 2 }, pr: '',
  }]), () => time);
  const first = await factory.read();
  assert.equal(first.stale, false);
  assert.equal(first.runs[0].action, undefined);
  assert.equal((await factory.act({ change: 'running-change', verb: 'abort' })).outcome, 'refused');
  time = 15_000;
  const empty = await factory.read();
  assert.equal(empty.stale, false);
  assert.deepEqual(empty.runs, []);
});
