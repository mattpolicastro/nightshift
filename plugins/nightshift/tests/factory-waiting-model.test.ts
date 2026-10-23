import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { FactoryRun } from '../shared/factory';
import { factoryWaitingTime } from '../client/factory-model';

const since = 1_700_000_000.25;
const run: FactoryRun = {
  change: 'human-pause', status: 'paused', tasks: { done: 1, total: 3 }, pr: '',
  pause: { since, auto_resumes_remaining: 0 },
};
const nowMs = (elapsedSeconds: number) => (since + elapsedSeconds) * 1000;

test('waiting time shows for human pauses only', async t => {
  await t.test('paseo-runs-page/human-pause-waiting-time/waiting-time-shows-for-human-pauses-only', async t => {
    await t.test('zero budget qualifies regardless of pause reason or available verbs', () => {
      const actions: FactoryRun['action'][] = [
        undefined,
        ...[[], ['resume', 'abort'], ['accept'], ['publish'], ['finish']].map(verbs => ({
          kind: 'action', headline: '', why: '', command: '',
          actions: verbs as NonNullable<FactoryRun['action']>['actions'],
        })),
      ];
      for (const reason of ['section_complete', 'stuck', 'status_error', 'unknown']) {
        for (const action of actions) {
          // Reasons may exist on source objects, but never determine waiting eligibility.
          const candidate = { ...run, action, pause: { ...run.pause, reason } };
          assert.deepEqual(factoryWaitingTime(candidate, nowMs(60)), {
            text: 'waiting 1m', warning: false,
          });
        }
      }
    });

    await t.test('every other status suppresses retained pause metadata', () => {
      for (const status of ['running', 'done', 'aborted'] as const) {
        assert.equal(factoryWaitingTime({ ...run, status }, nowMs(18_000)), null, status);
      }
    });

    const excluded: { name: string; pause: FactoryRun['pause'] }[] = [
      { name: 'absent pause', pause: undefined },
      { name: 'null pause', pause: null },
      { name: 'empty pause', pause: {} },
      { name: 'missing budget', pause: { since } },
      { name: 'unlimited budget', pause: { since, auto_resumes_remaining: null } },
      { name: 'one resume remaining', pause: { since, auto_resumes_remaining: 1 } },
      { name: 'multiple resumes remaining', pause: { since, auto_resumes_remaining: 5 } },
      { name: 'missing timestamp', pause: { auto_resumes_remaining: 0 } },
      { name: 'null timestamp', pause: { since: null, auto_resumes_remaining: 0 } },
    ];
    for (const { name, pause } of excluded) {
      await t.test(name, () => {
        assert.equal(factoryWaitingTime({ ...run, pause }, nowMs(18_000)), null);
      });
    }

    await t.test('timestamp must be finite and numeric and budget must be exactly numeric zero', () => {
      // Exercise the helper's guards directly, without schema coercion or validation.
      for (const value of [NaN, Infinity, -Infinity, '1700000000', false, true]) {
        const pause = { since: value, auto_resumes_remaining: 0 } as unknown as FactoryRun['pause'];
        assert.equal(factoryWaitingTime({ ...run, pause }, nowMs(60)), null);
      }
      for (const value of ['0', false, true, -1, 0.5, NaN, Infinity]) {
        const pause = { since, auto_resumes_remaining: value } as unknown as FactoryRun['pause'];
        assert.equal(factoryWaitingTime({ ...run, pause }, nowMs(60)), null);
      }
    });
  });
});

test('paseo-runs-page/human-pause-waiting-time/waiting-duration-uses-whole-units', async t => {
  const cases: [number, string, boolean][] = [
    [0, 'waiting 0m', false],
    [59, 'waiting 0m', false],
    [59.999, 'waiting 0m', false],
    [60, 'waiting 1m', false],
    [3599, 'waiting 59m', false],
    [3599.999, 'waiting 59m', false],
    [3600, 'waiting 1h', false],
    [7199.999, 'waiting 1h', false],
    [18_000, 'waiting 5h', true],
    [90_000, 'waiting 25h', true],
    [-60, 'waiting 0m', false],
  ];
  for (const [elapsed, text, warning] of cases) {
    await t.test(`${elapsed} seconds with a millisecond clock and fractional epoch timestamp`, () => {
      assert.deepEqual(factoryWaitingTime(run, nowMs(elapsed)), { text, warning });
    });
  }
  await t.test('epoch zero is a known timestamp', () => {
    const epoch = { ...run, pause: { since: 0, auto_resumes_remaining: 0 } };
    assert.deepEqual(factoryWaitingTime(epoch, 0), { text: 'waiting 0m', warning: false });
    assert.deepEqual(factoryWaitingTime(epoch, 60_000), { text: 'waiting 1m', warning: false });
  });
});

test('waiting time turns to warning after three hours', async t => {
  await t.test('paseo-runs-page/human-pause-waiting-time/waiting-time-turns-to-warning-after-three-hours', () => {
    assert.deepEqual(factoryWaitingTime(run, nowMs(10_799.999)), { text: 'waiting 2h', warning: false });
    assert.deepEqual(factoryWaitingTime(run, nowMs(10_800)), { text: 'waiting 3h', warning: false });
    assert.deepEqual(factoryWaitingTime(run, nowMs(10_800.001)), { text: 'waiting 3h', warning: true });
    assert.deepEqual(factoryWaitingTime(run, nowMs(10_801)), { text: 'waiting 3h', warning: true });
  });
});

test('default clock uses Date.now and calculations leave the run unchanged', t => {
  t.mock.method(Date, 'now', () => nowMs(60));
  const frozen = Object.freeze({ ...run, tasks: Object.freeze({ ...run.tasks }),
    pause: Object.freeze({ ...run.pause }) });
  const before = structuredClone(frozen);
  assert.deepEqual(factoryWaitingTime(frozen), { text: 'waiting 1m', warning: false });
  assert.deepEqual(factoryWaitingTime(frozen, nowMs(18_000)), { text: 'waiting 5h', warning: true });
  assert.deepEqual(frozen, before);
});
