import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createAttention, partialError, updateError } from '../server/attention';

const at = '2026-11-01T00:00:00Z';
const item = (number: number, extra: Record<string, unknown> = {}) => ({
  repo: 'owner/repo', number, kind: 'issue', title: `Item ${number}`, url: `https://github.com/owner/repo/issues/${number}`,
  summary: null, summary_checked_at: at, summary_stale: false, summary_error: null, summary_error_code: null, ...extra,
});
const repo = (entries: unknown[], extra: Record<string, unknown> = {}) => ({
  repo: 'owner/repo', label: 'needs-human', entries, checked_at: at, stale: false, error: null, error_code: null, ...extra,
});
const worker = { repo: 'owner/repo', issue: 9, state: 'needs_decision', issue_url: 'https://github.com/owner/repo/issues/9', checked_at: null, error: null };
function output(offline: boolean, repositories: unknown[] = [repo([item(1)])], code = 0, extra: Record<string, unknown> = {}) {
  return { code, stdout: JSON.stringify({ version: 1, offline, tasks: [worker], stale: [worker], repositories, specloop: [], specloop_error: null, ...extra }) };
}

test('attention/safe-plugin-attention-projection/structured-stale-response-reaches-page', async () => {
  const cold = { ...repo([], { checked_at: null, stale: true, error: 'GitHub read failed', error_code: 'read_failed' }), repo: 'owner/cold' };
  const warm = repo([
    item(1, { summary: '**nightshift** — Revision needs human input.' }),
    item(2, { summary: 'Retained headline', summary_stale: true, summary_error: 'GitHub read timed out', summary_error_code: 'timeout' }),
    item(3, { kind: 'pr', url: 'https://github.com/owner/repo/pull/3', summary_checked_at: null, summary_stale: true,
      summary_error: 'GitHub read failed', summary_error_code: 'read_failed' }),
  ], { stale: true, error: 'Summary unavailable for 2 item(s): GitHub read timed out', error_code: 'timeout' });
  const pause = { change: 'gate', reason: 'review_rejected', pr: 'javascript:ignored', checked_at: at, error: 'boom', stale: true };
  let time = 0;
  const adapter = createAttention(async offline => output(offline, [cold, warm], 1,
    { specloop: [pause], specloop_error: '/private/stderr token' }), () => time);
  const page = await adapter.read({ refresh: true });
  assert.deepEqual(page.tasks, []);
  assert.deepEqual(page.stale, []);
  assert.equal(page.error, partialError);
  assert.equal(page.complete, false);
  assert.equal(page.cached, false);
  assert.deepEqual(page.repositories[0], { repo: 'owner/cold', label: 'needs-human', entries: [], checkedAt: null, stale: true, error: 'GitHub read failed' });
  const [fresh, retained, unchecked] = page.repositories[1].entries;
  assert.equal(page.repositories[1].checkedAt, at);
  assert.deepEqual([fresh.summary, fresh.summaryStale, fresh.summaryError], ['**nightshift** — Revision needs human input.', false, null]);
  assert.deepEqual([retained.summary, retained.summaryCheckedAt, retained.summaryStale], ['Retained headline', at, true]);
  assert.deepEqual([unchecked.kind, unchecked.summary, unchecked.summaryCheckedAt, unchecked.summaryError], ['pr', null, null, 'GitHub read failed']);
  assert.deepEqual(page.pauses, [{ change: 'gate', reason: 'review_rejected', checkedAt: at, stale: true }]);
  assert.ok(page.pausesError && !JSON.stringify(page).includes('/private') && !JSON.stringify(page).includes('javascript'));

  // Only an online, error-free, fully checked empty snapshot is complete; cached data never is.
  const empty = createAttention(async offline => output(offline, [repo([])]), () => time);
  assert.equal((await empty.read({ refresh: false })).complete, false);
  const online = await empty.read({ refresh: true });
  assert.deepEqual([online.complete, online.error, online.repositories[0].entries], [true, null, []]);
  const notChecked = createAttention(async offline => output(offline, [repo([], { checked_at: null, stale: true, error: 'Not checked yet', error_code: 'not_checked' })]));
  assert.equal((await notChecked.read({ refresh: true })).complete, false);
  const nullSummary = createAttention(async offline => output(offline, [repo([item(4)])]));
  const ok = await nullSummary.read({ refresh: true });
  assert.deepEqual([ok.complete, ok.error, ok.repositories[0].entries[0].summary, ok.repositories[0].entries[0].summaryError], [true, null, null, null]);
});

test('attention/safe-plugin-attention-projection/legacy-and-malformed-producers-remain-visible-failures', async () => {
  const legacy = createAttention(async offline => ({ code: 0, stdout: JSON.stringify({ version: 1, offline, tasks: [worker], stale: [] }) }));
  const old = await legacy.read({ refresh: true });
  assert.deepEqual([old.tasks, old.stale, old.repositories, old.pauses], [[], [], [], []]);
  assert.deepEqual([old.frontDoorError, old.error, old.complete], [updateError, updateError, false]);

  const bad = [
    { code: 2, stdout: '{}' },
    { code: 0, stdout: 'not json' },
    output(true),
    output(false, [repo([item(1, { url: 'javascript:alert(1)' })])]),
    output(false, [repo([item(1, { url: 'https://other.example/owner/repo/issues/1' })])]),
    output(false, [repo([item(1, { url: 'https://github.com/other/repo/issues/1' })])]),
    output(false, [repo([item(1, { url: 'https://github.com/owner/repo/pull/1' })])]),
    output(false, [repo([item(1, { url: 'https://github.com/owner/repo/issues/2' })])]),
    output(false, [repo([item(1, { url: 'https://github.com/owner/repo/issues/1?x=1' })])]),
    output(false, [repo([item(1, { url: 'https://token@github.com/owner/repo/issues/1' })])]),
    output(false, [repo([item(1, { kind: 'discussion' })])]),
    output(false, [repo([item(1, { summary_error: '/Users/me/.config/secret', summary_error_code: 'read_failed' })])]),
    output(false, [repo([], { stale: true, error: 'GitHub read failed', error_code: 'credential' })]),
    output(false, [repo([item(1)], { checked_at: 5 })]),
    output(false, [repo([item(1)])], 0, { specloop: undefined }),
  ];
  for (const result of bad) {
    // Cold: visibly unavailable, never an empty healthy list.
    await assert.rejects(createAttention(async () => result).read({ refresh: true }), /unavailable/);
  }
  for (const failure of [...bad.map(result => async () => result), async () => { throw new Error('/private credential'); }]) {
    let time = 0, failing = false, calls = 0;
    const adapter = createAttention(async offline => { calls++; return failing ? failure() : output(offline, [repo([item(1, { summary: 'Good' })])]); }, () => time);
    const before = await adapter.read({ refresh: true });
    time += 60_000; failing = true;
    const after = await adapter.read({ refresh: true });
    assert.deepEqual(after, { ...before, cached: true, complete: false, error: 'Refresh unavailable. Showing the last loaded list.' });
    assert.equal(after.fetchedAt, before.fetchedAt);
    assert.ok(!JSON.stringify(after).includes('private') && !JSON.stringify(after).includes('secret'));
    assert.equal(calls, 2);
  }

  // Shared loads, the 30-second throttle and offline-first reads stay with the one reader.
  let time = 0, calls = 0, release!: () => void;
  const blocked = new Promise<void>(resolve => { release = resolve; });
  const modes: boolean[] = [];
  const adapter = createAttention(async offline => { calls++; modes.push(offline); await blocked; return output(offline); }, () => time);
  const first = adapter.read({ refresh: false }), second = adapter.read({ refresh: true });
  release();
  assert.deepEqual(await first, await second);
  assert.equal((await first).cached, true);
  await adapter.read({ refresh: true });
  time += 10_000;
  await adapter.read({ refresh: true });
  time += 30_000;
  assert.equal((await adapter.read({ refresh: true })).cached, false);
  assert.deepEqual([calls, modes], [3, [true, false, false]]);
  time += 30_000;
  assert.equal((await adapter.read({ refresh: true })).cached, false);
  assert.equal(calls, 4);
  adapter.dispose();
  await assert.rejects(adapter.read({ refresh: false }), /stopped/);
  assert.equal(calls, 4);
});
