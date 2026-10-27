import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import type { PluginServerContext } from '@getpaseo/plugin/server';
import contribute from '../index.server';
import { createFactory } from '../server/factory';
import { createFactoryLanding, createFactoryLandingTransport } from '../server/factory-landing';
import {
  factoryRuns, factoryAct, factoryLanding, factoryLand, factoryLandStop, factoryVerbSchema,
  type FactoryLandInput, type FactoryLandingInput, type FactoryLandStopInput,
} from '../shared/factory';

const home = '/Users/landing test';
const offer = { change: 'review-me', pr: 'https://github.com/owner/repo/pull/7',
  pause_id: 'human_checks:review', head: 'a'.repeat(40), since: 0.125 };
const confirmation = { ...offer, checks_done: true as const };
const id = '1'.repeat(32);
const attempt = { ...offer, attempt_id: id, created_at: 10, updated_at: 11,
  phase: 'validating', outcome: null, reason: '', exit_code: null, diagnostics: [], overdue: false };
const run = { ...offer, status: 'paused', tasks: { done: 1, total: 1 },
  pause: { since: offer.since, reason: 'human_checks' }, action: {
    kind: 'action', headline: 'Human checks', why: '', command: '', actions: ['finish'], pause_id: offer.pause_id,
  } };
const response = (value: unknown, code = 0) => ({ code, stdout: JSON.stringify(value) });
const status = (extra: object = {}) => ({ version: 1, attempts: [], total: 0, diagnostics: [],
  offer, current: null, truncated: false, ...extra });
const accepted = () => response({ version: 1, accepted: true, attempt_id: id });
const ordinary = () => createFactory(async () => response({ version: 1, runs: [run] }));
const query = { change: offer.change };

test('factory-landing-jobs/operator-execution-isolation/plugin-uses-private-literal-transport', async () => {
  const saved = process.env;
  process.env = { ...saved, NIGHTSHIFT_CHECKOUT: '/host checkout/with spaces',
    NIGHTSHIFT_UV: '/host tools/uv custom', NIGHTSHIFT_NODE_BIN: '/node tools/bin', NIGHTSHIFT_BIN_DIR: '/host tools/bin' };
  const calls: { file: string; args: string[]; options: childProcess.ExecFileOptions }[] = [];
  const killed: string[] = [];
  const pending: (() => void)[] = [];
  let hold = false;
  const stub = mock.method(childProcess, 'execFile', (
    file: string, args: string[], options: childProcess.ExecFileOptions,
    callback: (error: Error | null, stdout: string, stderr: string) => void,
  ) => {
    calls.push({ file, args, options });
    const finish = () => callback(null, args.includes('start') ? accepted().stdout : response(status()).stdout, 'secret');
    if (hold) pending.push(finish); else queueMicrotask(finish);
    return { kill(signal: string) { killed.push(signal); } } as childProcess.ChildProcess;
  });
  syncBuiltinESMExports();
  const transport = createFactoryLandingTransport();
  const landing = createFactoryLanding(transport.execute, ordinary().read);
  try {
    await landing.read(query);
    assert.deepEqual(await landing.start(confirmation), { version: 1, accepted: true, attempt_id: id });
    assert.deepEqual(calls[0].args, ['run', '--no-sync', 'python', '-m', 'nightshift.factory_land',
      'status', offer.change, '--limit', '20', '--json']);
    assert.deepEqual(calls[1].args, ['run', '--no-sync', 'python', '-m', 'nightshift.factory_land',
      'start', offer.change, '--pr', offer.pr, '--pause-id', offer.pause_id, '--head', offer.head,
      '--since=0.125', '--checks-done', '--json']);
    for (const call of calls) {
      assert.equal(call.file, '/host tools/uv custom');
      assert.equal(call.options.cwd, '/host checkout/with spaces');
      assert.equal(call.options.env?.PATH, '/node tools/bin:/host tools/bin:/usr/bin:/bin:/usr/sbin:/sbin');
      assert.equal(call.options.shell, false);
      assert.equal(call.options.timeout, 60_000);
      assert.equal(call.options.maxBuffer, 2 * 1024 * 1024);
    }
    hold = true;
    const read = assert.rejects(landing.read(query), /stopped/);
    landing.dispose(); transport.dispose(); transport.dispose();
    assert.deepEqual(killed, ['SIGTERM'], 'only the pending transport is killed; accepted worker is not tracked');
    pending.forEach(finish => finish());
    await read;
    await assert.rejects(landing.read(query), /stopped/);
    await assert.rejects(landing.start(confirmation), /stopped/);
    await assert.rejects(landing.stop({ attempt_id: id }), /stopped/);
    assert.equal(calls.length, 3);
  } finally {
    transport.dispose(); stub.mock.restore(); process.env = saved; syncBuiltinESMExports();
  }
});

test('paseo-runs-page/landing-rpc-isolation/submission-is-bounded-and-recoverable', async () => {
  const calls: string[][] = [];
  let admitted = false;
  const landing = createFactoryLanding(async args => {
    calls.push(args);
    if (args[0] === 'start') { admitted = true; throw new Error(`${home}/secret lost response`); }
    return response(status(admitted ? { attempts: [attempt], total: 1, current: attempt } : {}));
  }, ordinary().read);
  assert.equal((await landing.start(confirmation)).accepted, false, 'no displayed offer');
  await landing.read(query);
  const lost = await landing.start(confirmation);
  assert.equal(lost.accepted, false);
  assert.equal(lost.attempt_id, null);
  assert.match(lost.reason!, /Refresh landing status/);
  assert.ok(!lost.reason!.includes(home));
  assert.equal((await landing.start(confirmation)).accepted, false, 'invalidated offer cannot resubmit');
  const recovered = await landing.read(query);
  assert.equal(recovered.current?.attempt_id, id);
  assert.equal((await landing.start(confirmation)).accepted, false, 'current attempt blocks another start');
  assert.deepEqual(calls.map(args => args[0]), ['status', 'start', 'status']);
});

test('invalid, unknown and stale confirmations never reach private start', async () => {
  const calls: string[][] = [];
  const landing = createFactoryLanding(async args => { calls.push(args); return response(status()); }, ordinary().read);
  await landing.read(query);
  for (const value of [
    { ...confirmation, checks_done: false }, { ...confirmation, checks_done: undefined },
    { ...confirmation, since: true }, { ...confirmation, since: Infinity },
    { ...confirmation, change: '../escape' }, { ...confirmation, change: '--help' },
    ...['change', 'head', 'pr'].map(field => ({ ...confirmation,
      [field]: confirmation[field as keyof typeof confirmation] + '\n' })),
    { ...confirmation, change: 'x'.repeat(256) }, { ...confirmation, head: 'a'.repeat(39) },
    { ...confirmation, pause_id: 'human_checks:\nsecret' }, { ...confirmation, pause_id: 'other:pause' },
    { ...confirmation, pause_id: 'human_checks:' + 'x'.repeat(512) },
    ...['file:///secret', 'https://github.com.evil/owner/repo/pull/7',
      'https://user@github.com/owner/repo/pull/7', `${offer.pr}?x=1`, `${offer.pr}#fragment`,
      'https://github.com/owner/../pull/7'].map(pr => ({ ...confirmation, pr })),
    ...['cwd', 'uv', 'path', 'args', 'request_id'].map(key => ({ ...confirmation, [key]: '/untrusted' })),
  ]) await assert.rejects(landing.start(value as FactoryLandInput), /Invalid/);
  for (const changed of [
    { change: 'unknown' }, { pr: 'https://github.com/owner/repo/pull/8' },
    { pause_id: 'human_checks:changed' }, { head: 'b'.repeat(40) }, { since: 0 },
  ]) assert.equal((await landing.start({ ...confirmation, ...changed })).accepted, false);
  for (const value of [{ change: '../escape' }, { limit: 21 }, { uv: '/evil' }]) {
    await assert.rejects(landing.read(value as FactoryLandingInput), /Invalid/);
  }
  for (const value of [{ attempt_id: '../escape' }, { attempt_id: id, cwd: '/evil' }]) {
    await assert.rejects(landing.stop(value as FactoryLandStopInput), /Invalid/);
  }
  assert.equal(calls.length, 1);
  for (const runs of [[], [{ ...run, status: 'done' }], [{ ...run, pr: '' }],
    [{ ...run, action: undefined }], [{ ...run, pause: { since: 1 } }]]) {
    const factory = createFactory(async () => response({ version: 1, runs }));
    const service = createFactoryLanding(async args => {
      assert.equal(args[0], 'status'); return response(status());
    }, factory.read);
    await service.read(query);
    assert.equal((await service.start(confirmation)).accepted, false);
  }
});

test('landing PR URLs accept 512 characters and reject 513 in reads and confirmations', async () => {
  for (const length of [512, 513]) {
    const prefix = 'https://github.com/owner/', suffix = '/pull/7';
    const pr = prefix + 'r'.repeat(length - prefix.length - suffix.length) + suffix;
    assert.equal(pr.length, length);
    const shown = { ...offer, pr };
    const calls: string[][] = [];
    const factory = createFactory(async () => response({ version: 1, runs: [{ ...run, pr }] }));
    const landing = createFactoryLanding(async args => {
      calls.push(args);
      return args[0] === 'status' ? response(status({ offer: shown })) : accepted();
    }, factory.read);
    if (length === 512) {
      assert.equal((await landing.read(query)).offer?.pr, pr);
      assert.equal((await landing.start({ ...shown, checks_done: true })).accepted, true);
      assert.deepEqual(calls.map(args => args[0]), ['status', 'start']);
    } else {
      await assert.rejects(landing.read(query), /unavailable/);
      await assert.rejects(landing.start({ ...shown, checks_done: true }), /Invalid landing confirmation/);
      assert.deepEqual(calls.map(args => args[0]), ['status']);
    }
  }
});

test('paseo-runs-page/landing-rpc-isolation/independent-caches-discard-stale-responses', async () => {
  let time = 0, failRuns = false, failNamed = false, admitted = false, defer = false;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const calls: string[][] = [];
  const factory = createFactory(async () => {
    if (failRuns) throw new Error('private error');
    return response({ version: 1, runs: [run] });
  }, () => time);
  const landing = createFactoryLanding(async args => {
    calls.push(args);
    if (args[0] === 'start') { admitted = true; return accepted(); }
    if (args[1] === '--limit') return response(status({ offer: null, total: time }));
    if (failNamed) throw new Error('private error');
    const old = !admitted;
    if (defer && old) await gate;
    return response(status(old ? {} : { attempts: [attempt], total: 1, current: attempt }));
  }, factory.read, () => time);
  const first = await landing.read(query);
  await factory.read(); await landing.read();
  await Promise.all([landing.read(query), landing.read({ ...query, refresh: true }), landing.read({ refresh: true })]);
  assert.equal(calls.length, 2, 'each query is independently throttled');
  time = 15_000; failNamed = true;
  assert.deepEqual(await landing.read(query), { ...first, stale: true,
    error: 'Refresh unavailable. Showing the last loaded landing status.' });
  assert.equal((await landing.read()).total, time);
  assert.equal((await factory.read()).stale, false);
  assert.equal((await landing.start(confirmation)).accepted, false);
  time += 15_000; failNamed = false; failRuns = true;
  assert.equal((await factory.read()).stale, true);
  assert.equal((await landing.read(query)).stale, false);
  assert.equal((await landing.start(confirmation)).accepted, false, 'stale ordinary status cannot authorize');
  time += 15_000; failRuns = false; defer = true;
  const old = landing.read(query), shared = landing.read(query);
  await landing.start(confirmation);
  const fresh = await landing.read(query);
  assert.equal(fresh.current?.attempt_id, id);
  release();
  assert.deepEqual(await old, fresh);
  assert.deepEqual(await shared, fresh);
  assert.deepEqual(await landing.read(query), fresh);
  assert.equal(calls.filter(args => args[0] === 'start').length, 1);
});

test('landing projection validates bounded fields and hides private transport data', async () => {
  let time = 0;
  let value: unknown = status({ attempts: [{ ...attempt, reason: `${home}/output`,
    diagnostics: [`${home}/diagnostic`], process: { pid: 123 }, output: 'private log' }], total: 1,
    diagnostics: [`${home}/status`], stderr: 'private stderr' });
  const landing = createFactoryLanding(async () => response(value), ordinary().read, () => time, home);
  const good = await landing.read(query);
  assert.equal(good.attempts[0].reason, '~/output');
  assert.deepEqual(good.attempts[0].diagnostics, ['~/diagnostic']);
  assert.deepEqual(good.diagnostics, ['~/status']);
  assert.ok(!JSON.stringify(good).includes('private'));
  assert.ok(!JSON.stringify(good).includes(home));
  for (const invalid of [
    { version: 2 }, { total: -1 }, { total: 0, attempts: [attempt] },
    { attempts: Array(21).fill(attempt), total: 21 }, { diagnostics: ['x'.repeat(513)] },
    { diagnostics: Array(21).fill('error') }, { offer: { ...offer, change: 'wrong' } },
    { current: { ...attempt, outcome: 'succeeded' } },
    ...[{ outcome: 'uncertain' }, { phase: 'unknown' }, { exit_code: 0.5 },
      { reason: '😀'.repeat(8193) }, { diagnostics: [false] }, { pr: 'javascript:alert(1)' },
      { attempt_id: '../bad' }, { since: false }].map(extra => ({ attempts: [{ ...attempt, ...extra }], total: 1 })),
  ]) {
    time += 15_000; value = status(invalid);
    const stale = await landing.read(query);
    assert.equal(stale.stale, true);
    assert.deepEqual(stale.attempts, good.attempts);
  }
  time += 15_000; value = status({ attempts: [{ ...attempt, reason: '😀'.repeat(8192) }], total: 1 });
  assert.equal((await landing.read(query)).stale, false, 'detail limit counts Unicode characters');
  const overlappingHome = createFactoryLanding(async () => response(status({ offer: {
    ...offer, pr: 'https://github.com/Users/landing/pull/7',
  } })), ordinary().read, () => 0, '/Users/landing');
  await assert.rejects(overlappingHome.read(query), /unavailable/, 'redaction cannot create an invalid GitHub link');
});

test('start and Stop validate singular version-1 outcomes and route Stop privately', async () => {
  for (const output of [
    accepted(), response({ version: 1, accepted: false, attempt_id: id, reason: `${home}/refusal` }, 1),
    response({ version: 1, accepted: true }), { ...accepted(), code: 1 },
    { ...accepted(), code: 2 }, { code: 0, stdout: accepted().stdout + '\n' + accepted().stdout },
    response({ version: 2, accepted: true, attempt_id: id }),
    response({ version: 1, accepted: false, attempt_id: id, reason: 'x'.repeat(8193) }, 1),
  ]) {
    const landing = createFactoryLanding(async args => args[0] === 'status' ? response(status()) : output,
      ordinary().read, () => 0, home);
    await landing.read(query);
    const result = await landing.start(confirmation);
    if (output.code === 0 && output.stdout === accepted().stdout) assert.equal(result.accepted, true);
    else if (output.stdout.includes('/refusal')) assert.equal(result.reason, '~/refusal');
    else { assert.equal(result.accepted, false); assert.match(result.reason!, /result unavailable/); }
  }
  const calls: string[][] = [];
  let output = response({ version: 1, accepted: true, attempt_id: id, stop_requested: true, reason: `${home}/Stop requested` });
  const landing = createFactoryLanding(async args => { calls.push(args); return output; }, async () => {
    throw new Error('Stop must not depend on ordinary status');
  }, () => 0, home);
  assert.deepEqual(await landing.stop({ attempt_id: id }), { version: 1, accepted: true,
    attempt_id: id, stop_requested: true, reason: '~/Stop requested' });
  assert.deepEqual(calls, [['stop', id, '--json']]);
  for (const invalid of [
    { version: 1, accepted: true, attempt_id: id, reason: 'missing Stop marker' },
    { version: 1, accepted: true, attempt_id: '2'.repeat(32), stop_requested: true, reason: '' },
    { version: 1, accepted: false, attempt_id: id, reason: 'wrong exit' },
  ]) {
    output = response(invalid);
    assert.match((await landing.stop({ attempt_id: id })).reason, /result unavailable/);
  }
  output = response({ version: 1, accepted: false, attempt_id: id, reason: 'Worker identity gone' }, 1);
  assert.equal((await landing.stop({ attempt_id: id })).reason, 'Worker identity gone');
});

test('cold failures and over-limit output are throttled without exposing raw errors', async () => {
  for (const output of [{ code: 0, stdout: 'private error' }, { code: 1, stdout: response(status()).stdout },
    { code: 0, stdout: JSON.stringify(status({ private: 'x'.repeat(2 * 1024 * 1024) })) }]) {
    let calls = 0, time = 0;
    const landing = createFactoryLanding(async () => { calls++; return output; }, ordinary().read, () => time);
    await assert.rejects(landing.read(query), { message: 'Landing status is unavailable on this host. Try again shortly.' });
    time = 14_999;
    await assert.rejects(landing.read({ ...query, refresh: true }), /unavailable/);
    assert.equal(calls, 1);
    time = 15_000;
    await assert.rejects(landing.read(query), /unavailable/);
    assert.equal(calls, 2);
  }
});

test('transport timeout, buffer overflow and spawn errors remove short-lived children', async () => {
  let error: childProcess.ExecFileException | null = null;
  const killed: string[] = [];
  const stub = mock.method(childProcess, 'execFile', (
    _file: string, _args: string[], _options: childProcess.ExecFileOptions,
    callback: (error: childProcess.ExecFileException | null, stdout: string, stderr: string) => void,
  ) => {
    queueMicrotask(() => callback(error, 'private stdout', 'private stderr'));
    return { kill(signal: string) { killed.push(signal); } } as childProcess.ChildProcess;
  });
  syncBuiltinESMExports();
  const transport = createFactoryLandingTransport();
  try {
    for (const fields of [{ killed: true, code: 1 }, { code: 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER' }, { code: 'ENOENT' }]) {
      error = Object.assign(new Error('private transport error'), fields);
      await assert.rejects(transport.execute(['status', '--json']), /private transport error/);
    }
    for (const code of [1, 2]) {
      error = Object.assign(new Error('domain or syntax refusal'), { code });
      assert.deepEqual(await transport.execute(['stop', id, '--json']), { code, stdout: 'private stdout' });
    }
    transport.dispose();
    assert.deepEqual(killed, [], 'completed and failed transports are removed before cleanup');
  } finally { transport.dispose(); stub.mock.restore(); syncBuiltinESMExports(); }
});

test('zero and fractional identity stays literal; new deliberate confirmations are fresh starts', async () => {
  for (const since of [0, -0.0000001, 0.125]) {
    const shown = { ...offer, since, pause_id: 'human_checks:$(touch nope); `whoami` "a b"' };
    const factory = createFactory(async () => response({ version: 1, runs: [{ ...run,
      pause: { since }, action: { ...run.action, pause_id: shown.pause_id },
    }] }));
    const calls: string[][] = [];
    let count = 0;
    const landing = createFactoryLanding(async args => {
      calls.push(args);
      return args[0] === 'status' ? response(status({ offer: shown })) : response({
        version: 1, accepted: true, attempt_id: String(++count).repeat(32),
      });
    }, factory.read);
    await landing.read(query);
    assert.equal((await landing.start({ ...shown, checks_done: true })).attempt_id, '1'.repeat(32));
    // Fresh read and explicit confirmation are required; there is no UUID replay.
    await landing.read(query);
    assert.equal((await landing.start({ ...shown, checks_done: true })).attempt_id, '2'.repeat(32));
    for (const args of calls.filter(args => args[0] === 'start')) {
      assert.deepEqual(args, ['start', shown.change, '--pr', shown.pr, '--pause-id', shown.pause_id,
        '--head', shown.head, `--since=${since}`, '--checks-done', '--json']);
    }
  }
});

test('submission invalidates global and originating query while unrelated query stays cached', async () => {
  const calls: string[][] = [];
  const landing = createFactoryLanding(async args => {
    calls.push(args);
    if (args[0] === 'start') return accepted();
    return response(status({ offer: args[1] === '--limit' ? null : { ...offer, change: args[1] } }));
  }, ordinary().read, () => 0);
  await landing.read(); await landing.read(query); await landing.read({ change: 'another-run' });
  await landing.start(confirmation);
  await landing.read({ change: 'another-run' });
  assert.equal(calls.length, 4);
  await landing.read(); await landing.read(query);
  assert.equal(calls.length, 6);
});

test('server registers separate landing descriptors without extending ordinary verbs', () => {
  const handlers = new Map<unknown, unknown>();
  const dispose = contribute({ handle(rpc: unknown, handler: unknown) { handlers.set(rpc, handler); } } as PluginServerContext);
  for (const rpc of [factoryRuns, factoryAct, factoryLanding, factoryLand, factoryLandStop]) assert.ok(handlers.has(rpc));
  assert.equal(new Set(handlers.values()).size, 6);
  assert.deepEqual(factoryVerbSchema.options, ['resume', 'accept', 'publish', 'finish', 'abort']);
  dispose();
});
