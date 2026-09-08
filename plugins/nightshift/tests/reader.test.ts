import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createReader } from '../reader.server';

const task = {
  repo: 'owner/repo', issue: 1, title: 'Fix retries', summary: 'Retries now recover',
  state: 'awaiting_merge', pr_url: 'https://github.com/owner/repo/pull/2',
  issue_url: 'https://github.com/owner/repo/issues/1', checked_at: '2026-09-05T00:00:00Z', error: null as string | null,
  implement_transcript: '/secret/local/path',
};
function payload(offline = false, tasks = [task], stale: typeof task[] = []) {
  return { code: stale.length ? 1 : 0, stdout: JSON.stringify({ version: 1, offline, tasks, stale }) };
}

test('projects only display fields and distinguishes cached data', async () => {
  const reader = createReader(async offline => payload(offline));
  const cached = await reader.read({ refresh: false });
  assert.equal(cached.cached, true);
  assert.equal(cached.tasks[0].url, task.pr_url);
  assert.ok(!JSON.stringify(cached).includes('/secret'));
  assert.equal((await reader.read({ refresh: true })).cached, false);
});

test('resolution removes a row on the next refresh', async () => {
  let time = 0, resolved = false;
  const reader = createReader(async offline => payload(offline, resolved ? [] : [task]), () => time);
  assert.equal((await reader.read({ refresh: true })).tasks.length, 1);
  resolved = true; time += 60_000;
  assert.equal((await reader.read({ refresh: true })).tasks.length, 0);
});

test('exit 1 preserves stale resolved records as well as actionable rows', async () => {
  const staleTask = { ...task, state: 'resolved', error: 'HTTP 404' };
  const reader = createReader(async offline => payload(offline, [], [staleTask]));
  const result = await reader.read({ refresh: true });
  assert.equal(result.stale[0].state, 'resolved');
  assert.equal(result.stale[0].stale, true);
  assert.ok(result.error);
});

test('failed or malformed refresh retains the last list and time without leaking errors', async () => {
  let time = 0, fail = false;
  const reader = createReader(async offline => {
    if (fail) throw new Error('credential secret');
    return payload(offline);
  }, () => time);
  const before = await reader.read({ refresh: true });
  time += 60_000; fail = true;
  const after = await reader.read({ refresh: true });
  assert.deepEqual(after.tasks, before.tasks);
  assert.equal(after.fetchedAt, before.fetchedAt);
  assert.equal(after.cached, true);
  assert.ok(!JSON.stringify(after).includes('secret'));
});

test('deduplicates simultaneous clients and throttles refreshes', async () => {
  let calls = 0, release!: () => void;
  const blocked = new Promise<void>(resolve => { release = resolve; });
  const reader = createReader(async offline => { calls++; await blocked; return payload(offline); });
  const first = reader.read({ refresh: true });
  const second = reader.read({ refresh: true });
  release();
  assert.deepEqual(await first, await second);
  await reader.read({ refresh: true });
  assert.equal(calls, 1);
});

test('invalid links and unexpected CLI contracts fail visibly, never as an empty healthy list', async () => {
  for (const output of [
    { code: 2, stdout: '{}' },
    { code: 0, stdout: 'not json' },
    payload(false, [{ ...task, pr_url: 'javascript:alert(1)' }]),
    payload(false, [{ ...task, pr_url: 'https://other.example/owner/repo/pull/2' }]),
  ]) {
    await assert.rejects(createReader(async () => output).read({ refresh: true }), /unavailable/);
  }
});

test('cleanup prevents new commands', async () => {
  const reader = createReader(async offline => payload(offline));
  reader.dispose();
  await assert.rejects(reader.read({ refresh: true }), /stopped/);
});
