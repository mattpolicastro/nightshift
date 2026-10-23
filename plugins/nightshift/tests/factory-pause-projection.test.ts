import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createFactory } from '../server/factory';
import { factoryRunSchema } from '../shared/factory';

const home = '/Users/pause-test';
const run = {
  change: 'pause-display', status: 'paused', tasks: { done: 1, total: 3 },
  pr: 'https://github.com/owner/repo/pull/7',
  action: {
    kind: 'action', headline: 'Review section 1', why: `Inspect ${home}/work`,
    command: 'specloop resume pause-display', actions: ['resume', 'abort'],
    pause_id: 'section_complete:1',
  },
  context: {
    summary: 'Review required', repo: `${home}/repo`, branch: 'spec/pause-display',
    task: null, detail: `Inspect ${home}/detail`, recent: [],
    github: 'https://github.com/owner/repo',
  },
};
const projectedRun = {
  ...run,
  action: { ...run.action, why: 'Inspect ~/work' },
  context: { ...run.context, repo: '~/repo', detail: 'Inspect ~/detail' },
};
const response = (value: unknown) => ({ code: 0, stdout: JSON.stringify(value) });
const status = (row: unknown) => response({ version: 1, runs: [row] });

test('paseo-runs-page/pause-display-metadata/only-pause-display-fields-cross-the-server', async t => {
  for (const pause of [
    { since: 1_700_000_000.25, auto_resumes_remaining: 0 },
    { since: 0, auto_resumes_remaining: 2 },
    { since: -0.5, auto_resumes_remaining: 1 },
    { since: null, auto_resumes_remaining: null },
    { since: null, auto_resumes_remaining: 0 },
    { since: 1_700_000_000, auto_resumes_remaining: null },
  ]) {
    await t.test(JSON.stringify(pause), async () => {
      const calls: string[][] = [];
      const factory = createFactory(async args => {
        calls.push(args);
        return args[0] === 'status' ? status({
          ...run, head: '/private/head',
          pause: {
            ...pause, reason: 'section_complete', task: '1.1', detail: '/private/detail',
            resume_at: 1_800_000_000, internal: { secret: '/private/secret' },
          },
        }) : response({ version: 1, outcome: 'done', verb: 'resume', detail: 'Resumed' });
      }, () => 0, home);
      assert.deepEqual(await factory.read(), {
        runs: [{ ...projectedRun, pause }], stale: false,
        fetchedAt: new Date(0).toISOString(), error: null,
      });
      assert.deepEqual(await factory.act({
        change: run.change, verb: 'resume', expect: run.action.pause_id,
      }), { version: 1, outcome: 'done', verb: 'resume', detail: 'Resumed' });
      assert.deepEqual(calls, [
        ['status', '--json'],
        ['act', run.change, 'resume', `--expect=${run.action.pause_id}`, '--json'],
      ]);
    });
  }
});

test('paseo-runs-page/pause-display-metadata/older-producers-remain-supported', async t => {
  const cases = [
    { name: 'absent pause', input: {}, expected: {} },
    { name: 'null pause', input: { pause: null }, expected: { pause: null } },
    { name: 'empty pause', input: { pause: {} }, expected: {} },
    { name: 'internal fields only', input: { pause: { reason: 'gate', detail: '/private/detail' } }, expected: {} },
    ...[{ since: 0 }, { since: 123.5 }, { since: null },
      { auto_resumes_remaining: 0 }, { auto_resumes_remaining: 2 }, { auto_resumes_remaining: null },
    ].map(pause => ({ name: JSON.stringify(pause), input: { pause }, expected: { pause } })),
  ];
  for (const { name, input, expected } of cases) {
    await t.test(name, async () => {
      const factory = createFactory(async () => status({ ...run, ...input }), () => 0, home);
      assert.deepEqual(await factory.read(), {
        runs: [{ ...projectedRun, ...expected }], stale: false,
        fetchedAt: new Date(0).toISOString(), error: null,
      });
    });
  }
});

test('paseo-runs-page/pause-display-metadata/invalid-metadata-retains-validation-behavior', async t => {
  const pause = { since: 1_700_000_000.25, auto_resumes_remaining: 0 };
  const validRun = { ...run, pause };
  const invalid = [
    ...['1700000000', true, false, {}, []].map(since => ({
      name: `invalid since ${JSON.stringify(since)}`, output: status({ ...run, pause: { ...pause, since } }),
    })),
    ...[-1, 0.5, '0', true, false, {}, []].map(auto_resumes_remaining => ({
      name: `invalid budget ${JSON.stringify(auto_resumes_remaining)}`,
      output: status({ ...run, pause: { ...pause, auto_resumes_remaining } }),
    })),
    ...['pause', 0, false, []].map(value => ({
      name: `invalid pause ${JSON.stringify(value)}`, output: status({ ...run, pause: value }),
    })),
    // JSON.stringify converts infinities to null; overflowing JSON numbers must fail validation.
    ...['since', 'auto_resumes_remaining'].flatMap(field => ['1e400', '-1e400'].map(value => ({
      name: `nonfinite ${field} ${value}`,
      output: { code: 0, stdout: status({ ...run, pause: { [field]: 'overflow' } }).stdout.replace('"overflow"', value) },
    }))),
    { name: 'version', output: response({ version: 2, runs: [validRun] }) },
    { name: 'status', output: status({ ...validRun, status: 'waiting' }) },
    { name: 'action', output: status({ ...validRun, action: { ...run.action, actions: ['execute'] } }) },
    ...['file:///private/config', 'https://github.com.evil.test/owner/repo/pull/7',
      'https://user:pass@github.com/owner/repo/pull/7', 'https://github.com/owner/repo/pull/7?secret=1',
    ].map(pr => ({ name: `PR ${pr}`, output: status({ ...validRun, pr }) })),
    { name: 'context', output: status({ ...validRun, context: { ...run.context, recent: [{ at: '', text: 42 }] } }) },
  ];
  for (const { name, output } of invalid) {
    await t.test(name, async () => {
      const cold = createFactory(async () => output, () => 0, home);
      await assert.rejects(cold.read(), {
        message: 'Factory status is unavailable on this host. Try again shortly.',
      });

      let time = 0;
      const calls: string[][] = [];
      const factory = createFactory(async args => {
        calls.push(args);
        return time === 15_000 ? output : status(validRun);
      }, () => time, home);
      const before = await factory.read();
      assert.deepEqual(before.runs, [{ ...projectedRun, pause }]);
      time = 15_000;
      const after = await factory.read({ refresh: true });
      assert.deepEqual(after, {
        ...before, stale: true, error: 'Refresh unavailable. Showing the last loaded list.',
      });
      time = 30_000;
      assert.deepEqual(await factory.read(), {
        ...before, fetchedAt: new Date(time).toISOString(),
      });
      assert.deepEqual(calls, Array.from({ length: 3 }, () => ['status', '--json']));
    });
  }
  await t.test('shared schema rejects nonfinite numbers before serialization', () => {
    for (const value of [NaN, Infinity, -Infinity]) {
      for (const field of ['since', 'auto_resumes_remaining']) {
        assert.equal(factoryRunSchema.safeParse({ ...run, pause: { [field]: value } }).success, false);
      }
    }
  });
});
