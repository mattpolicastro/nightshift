import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { FactoryLandingOffer, FactoryLandingSnapshot, FactoryRun } from '../shared/factory';
import { createFactoryLanding } from '../server/factory-landing';
import { humanRun, landingAttempt, landingSnapshot, offer } from './factory-landing-fixtures';
import { colors, styleView, type Node } from './factory-style-harness';

type Page = ReturnType<typeof styleView>;
const runSnapshot = (runs: FactoryRun[]) => ({ runs, stale: false, fetchedAt: '', error: null });
const cancel = (page: Page) => page.nodes().find(node => node.type === 'Pressable' && page.text(node.children) === 'Cancel')!;
const row = (page: Page, change = offer.change) => page.nodes().find(node => node.type === 'View' &&
  node.children.some(child => typeof child !== 'string' && child.type === 'Text' && page.text(child.children) === change))!;
const parent = (page: Page, child: Node) => page.nodes().find(node => node.children.includes(child));
const alerts = (page: Page) => page.nodes().filter(node => node.props.accessibilityRole === 'alert');
const occurrences = (page: Page, text: string) => alerts(page).filter(node => page.text(node.children).includes(text));
function target(node: Node, background: string) {
  assert.equal(node.props.accessibilityRole, 'button');
  assert.equal(node.props.style.backgroundColor, background);
  assert.ok(node.props.style.minHeight >= 44);
  assert.ok(node.props.style.minWidth >= 44);
}

test('land action appears only at human checks', async t => {
  await t.test('paseo-runs-page/human-checks-landing/land-action-appears-only-at-human-checks', async t => {
    const runs: FactoryRun[] = [humanRun,
      { ...humanRun, change: 'other-pause', action: { ...humanRun.action!, pause_id: 'stuck:1', actions: ['resume', 'accept', 'abort'] } },
      ...(['running', 'done', 'aborted'] as const).map(status => ({ ...humanRun, change: status, status })),
      { ...humanRun, change: 'unavailable', action: undefined },
    ];
    let current = null as ReturnType<typeof landingAttempt> | null;
    const page = styleView(runs, { landing: async input => landingSnapshot({
      offer: input.change ? { ...offer, change: input.change } : null,
      current: input.change === offer.change ? current : null,
    }) });
    t.after(page.dispose);
    await page.settle();
    assert.deepEqual([...page.actions], []);
    assert.deepEqual([...page.lands], []);
    assert.deepEqual([...page.stops], []);
    await page.press('Finished (2)');
    assert.deepEqual(page.nodes().filter(node => node.props.accessibilityLabel?.startsWith('Land '))
      .map(node => node.props.accessibilityLabel), [`Land ${offer.change}`]);
    const land = page.find(`Land ${offer.change}`)!;
    const finish = page.find(`Finish ${offer.change}`)!;
    assert.notEqual(parent(page, land), parent(page, finish));
    assert.deepEqual(parent(page, finish)!.children.filter(child => typeof child !== 'string')
      .map(child => (child as Node).props.accessibilityLabel), [`Finish ${offer.change}`, `Abort ${offer.change}`]);
    target(land, colors.surface1);
    assert.ok(page.text(row(page).children).includes('waiting '));
    await page.press(`Finish ${offer.change}`);
    assert.ok(page.find(`Confirm finish ${offer.change}`));
    assert.deepEqual([...page.actions], []);
    await page.press(cancel(page));
    current = landingAttempt();
    await page.press('Refresh Factory');
    assert.equal(page.find(`Land ${offer.change}`)!.props.disabled, true);
    assert.equal(page.find(`Finish ${offer.change}`)!.props.disabled, false);
    assert.ok(page.find(`Stop landing ${offer.change}`));
    assert.ok(page.find('Resume note for other-pause'));
    assert.equal(page.find(`Details for ${offer.change}`)!.props.accessibilityState.expanded, false);
    assert.equal(page.find(`Open PR for ${offer.change}`)!.props.accessibilityRole, 'link');
    assert.equal(page.find('Finished (2)')!.props.accessibilityState.expanded, true);
    assert.deepEqual(page.hosts, []);
  });
});

test('land asks for confirmation before starting', async t => {
  await t.test('paseo-runs-page/landing-confirmation/land-asks-for-confirmation-before-starting', async t => {
    let resolveStart!: (result: unknown) => void;
    const pending = new Promise(resolve => { resolveStart = resolve; });
    const page = styleView([humanRun], {
      landing: async input => landingSnapshot({ offer: input.change ? { ...offer } : null }),
      land: async () => pending,
    });
    t.after(page.dispose);
    await page.settle();
    await page.press(`Land ${offer.change}`);
    const prompt = alerts(page).find(node => page.text(node.children).includes('Have you performed the human checks'))!;
    assert.ok(prompt);
    for (const text of [offer.change, offer.pr, offer.head, 'merge']) assert.ok(page.text(prompt.children).includes(text));
    target(page.find(`Confirm land ${offer.change}`)!, colors.accent);
    assert.equal(page.find(`Confirm land ${offer.change}`)!.children[0] &&
      (page.find(`Confirm land ${offer.change}`)!.children[0] as Node).props.style.color, colors.accentForeground);
    target(cancel(page), colors.surface1);
    assert.deepEqual([...page.lands], []);
    await page.press(cancel(page));
    assert.equal(page.find(`Confirm land ${offer.change}`), undefined);
    assert.deepEqual([...page.lands], []);
    await page.press(`Land ${offer.change}`);
    const confirm = page.find(`Confirm land ${offer.change}`)!;
    confirm.props.onPress();
    confirm.props.onPress(); // Two events before React can disable the control.
    await page.settle();
    assert.equal(page.lands.length, 1);
    assert.deepEqual({ ...page.lands[0] }, { ...offer, checks_done: true });
    assert.equal(page.find(`Land ${offer.change}`)!.props.disabled, true);
    assert.deepEqual([...page.actions], []);
    assert.deepEqual([...page.stops], []);
    resolveStart({ version: 1, accepted: true, attempt_id: '1'.repeat(32) });
    await page.settle();
    assert.equal(page.lands.length, 1);
  });
});

test('paseo-runs-page/buttons-fit-the-pause/publish-needs-confirmation: landing reads preserve publish confirmation', async t => {
  const run = { ...humanRun, action: { ...humanRun.action!, actions: ['publish', 'abort'] as const } };
  const page = styleView([{ ...run, action: { ...run.action, actions: [...run.action.actions] } }]);
  t.after(page.dispose);
  await page.settle();
  await page.press(`Publish ${offer.change}`);
  assert.deepEqual([...page.actions], []);
  await page.press(cancel(page));
  assert.deepEqual([...page.actions], []);
  await page.press(`Publish ${offer.change}`);
  await page.press(`Confirm publish ${offer.change}`);
  assert.deepEqual(page.actions.map(input => ({ ...input })), [{ change: offer.change, verb: 'publish', expect: offer.pause_id }]);
  assert.deepEqual([...page.lands], []);
  assert.deepEqual([...page.stops], []);
});

test('paseo-runs-page/landing-confirmation/changed-offer-invalidates-the-dialog', async t => {
  const changes: Partial<FactoryLandingOffer>[] = [
    { change: 'another' }, { pr: 'https://github.com/owner/repo/pull/8' },
    { pause_id: 'human_checks:republished' }, { head: 'b'.repeat(40) }, { since: 0.5 },
  ];
  for (const changed of [...changes, null]) await t.test(changed ? Object.keys(changed)[0] : 'failed read', async t => {
    let shown = { ...offer }, failed = false;
    const page = styleView([], {
      read: async () => runSnapshot([{ ...humanRun, pr: shown.pr, pause: { since: shown.since },
        action: { ...humanRun.action!, pause_id: shown.pause_id } }]),
      landing: async input => {
        if (failed) throw new Error('offline');
        return landingSnapshot({ offer: input.change ? shown : null });
      },
    });
    t.after(page.dispose);
    await page.settle();
    await page.press(`Land ${offer.change}`);
    if (changed) shown = { ...offer, ...changed }; else failed = true;
    await page.press('Refresh Factory');
    assert.ok(page.text().includes('This landing offer is stale'));
    const confirm = page.find(`Confirm land ${offer.change}`)!;
    assert.equal(confirm.props.disabled, true);
    confirm.props.onPress();
    assert.deepEqual([...page.lands], []);
    assert.ok(page.text().includes(offer.head), 'dialog keeps the originally confirmed head');
    shown = { ...offer }; failed = false;
    await page.press('Refresh Factory');
    assert.equal(page.find(`Confirm land ${offer.change}`)!.props.disabled, true, 'reverting cannot revive a stale dialog');
    await page.press(cancel(page));
    await page.press(`Land ${offer.change}`);
    assert.equal(page.find(`Confirm land ${offer.change}`)!.props.disabled, false);
    await page.press(`Confirm land ${offer.change}`);
    assert.equal(page.lands.length, 1);
  });
});

test('land result and refusal stay in the row', async t => {
  await t.test('paseo-runs-page/landing-notices-stay-visible/land-result-and-refusal-stay-in-the-row', async t => {
    const refusal = landingAttempt({ outcome: 'refused', reason: 'duplicate marker matches; resolve duplicates before retrying' });
    const next = landingAttempt({ attempt_id: '2'.repeat(32) });
    let attempts = [refusal], current: typeof next | null = null, fail = false;
    const page = styleView([humanRun], {
      read: async () => { if (fail) throw new Error('offline'); return runSnapshot([humanRun]); },
      landing: async input => {
        if (fail) throw new Error('offline');
        return landingSnapshot({ attempts, total: attempts.length,
          offer: input.change ? offer : null, current: input.change ? current : null });
      },
      land: async () => {
        attempts = [next]; current = next;
        throw new Error('Response lost after admission');
      },
    });
    t.after(page.dispose);
    await page.settle();
    await page.press(`Land ${offer.change}`);
    await page.press(`Confirm land ${offer.change}`);
    assert.equal(page.lands.length, 1);
    assert.ok(page.text(row(page).children).includes('Refresh landing status to recover the attempt'));
    assert.ok(page.text(row(page).children).includes('checking CI'));
    assert.equal(occurrences(page, refusal.reason).length, 1);
    await page.press(`Finish ${offer.change}`);
    await page.press(`Confirm finish ${offer.change}`);
    assert.ok(page.text(row(page).children).includes('Done · Completed'));
    assert.equal(occurrences(page, refusal.reason).length, 1);
    assert.equal(page.actions.length, 1);
    assert.equal(page.lands.length, 1);
    attempts = [{ ...next, outcome: 'succeeded', reason: 'Merge confirmed', updated_at: 3 }]; current = null;
    await page.press('Refresh Factory');
    fail = true;
    await page.press('Refresh Factory');
    for (const message of [refusal.reason, 'Merge confirmed', 'Done · Completed']) {
      assert.equal(occurrences(page, message).length, 1);
      assert.ok(page.text(row(page).children).includes(message));
    }
    fail = false; attempts = []; // The bounded history can later exclude either attempt.
    await page.press('Refresh Factory');
    assert.equal(occurrences(page, refusal.reason).length, 1);
    assert.equal(occurrences(page, 'Merge confirmed').length, 1);
    assert.equal(page.lands.length, 1, 'status recovery never retries Land');
  });
});

test('Stop is separate, guards duplicate clicks and retains control reasons with the attempt', async t => {
  let current: ReturnType<typeof landingAttempt> | null = landingAttempt();
  let attempts = [current];
  let resolveStop!: (value: unknown) => void;
  const page = styleView([humanRun], {
    landing: async input => landingSnapshot({ offer: input.change ? offer : null,
      attempts, total: attempts.length, current: input.change ? current : null }),
    stop: async () => new Promise(resolve => { resolveStop = resolve; }),
  });
  t.after(page.dispose);
  await page.settle();
  const stop = page.find(`Stop landing ${offer.change}`)!;
  target(stop, colors.surface1);
  stop.props.onPress(); stop.props.onPress();
  await page.settle();
  assert.equal(page.stops.length, 1);
  assert.deepEqual({ ...page.stops[0] }, { attempt_id: current.attempt_id });
  assert.equal(page.find(`Stop landing ${offer.change}`)!.props.disabled, true);
  resolveStop({ version: 1, accepted: true, attempt_id: current.attempt_id, stop_requested: true, reason: 'SIGTERM sent; inspect progress' });
  await page.settle();
  assert.ok(page.find(`Stop landing ${offer.change}`), 'a signal request is not confirmed completion');
  assert.equal(occurrences(page, 'Stop requested').length, 1);
  await page.press(`Stop landing ${offer.change}`);
  resolveStop({ version: 1, accepted: false, attempt_id: current.attempt_id, reason: 'Worker identity unavailable' });
  await page.settle();
  assert.equal(occurrences(page, 'Worker identity unavailable').length, 1);
  attempts = [{ ...current, outcome: 'stopped', reason: 'Worker stopped', updated_at: 4 }]; current = null;
  await page.press('Refresh Factory');
  assert.equal(page.find(`Stop landing ${offer.change}`), undefined);
  assert.equal(occurrences(page, 'Worker stopped').length, 1);
  assert.equal(occurrences(page, 'Stop requested').length, 1);
  assert.equal(occurrences(page, 'Worker identity unavailable').length, 1);
  assert.deepEqual([...page.actions], []);
  assert.deepEqual([...page.lands], []);
});

test('paseo-runs-page/landing-notices-stay-visible/land-result-and-refusal-stay-in-the-row: admission refusal is retained once', async t => {
  const refusal = landingAttempt({ outcome: 'refused', reason: 'Another worker holds the PR lock' });
  const success = landingAttempt({ attempt_id: '2'.repeat(32), outcome: 'succeeded', reason: 'Merge confirmed' });
  let attempt: typeof refusal | null = null;
  const page = styleView([humanRun], {
    landing: async input => landingSnapshot({ offer: input.change ? offer : null,
      attempts: attempt ? [attempt] : [], total: attempt ? 1 : 0 }),
    land: async () => {
      attempt = attempt ? success : refusal;
      return { version: 1, accepted: attempt === success, attempt_id: attempt.attempt_id, reason: attempt.reason };
    },
  });
  t.after(page.dispose);
  await page.settle();
  for (let i = 0; i < 2; i++) {
    await page.press(`Land ${offer.change}`);
    await page.press(`Confirm land ${offer.change}`);
    await page.press('Refresh Factory');
    assert.equal(occurrences(page, refusal.reason).length, 1);
    assert.ok(page.text(row(page).children).includes(refusal.reason));
  }
  assert.equal(page.lands.length, 2);
  assert.equal(occurrences(page, success.reason).length, 1);
});

test('paseo-runs-page/landing-confirmation/changed-offer-invalidates-the-dialog: stale channels and late reads cannot authorize Land', async t => {
  let runStale = false, landingStale = false, defer = false;
  let resolveRead!: (value: FactoryLandingSnapshot) => void;
  let current: ReturnType<typeof landingAttempt> | null = null;
  const page = styleView([], {
    read: async () => ({ ...runSnapshot([humanRun]), stale: runStale }),
    landing: async input => {
      if (input.change && defer) {
        defer = false;
        return new Promise(resolve => { resolveRead = resolve; });
      }
      return landingSnapshot({ offer: input.change ? offer : null, stale: landingStale,
        current: input.change ? current : null, error: landingStale ? 'Retained landing status is stale' : null });
    },
    land: async () => {
      current = landingAttempt();
      return { version: 1, accepted: true, attempt_id: current.attempt_id };
    },
  });
  t.after(page.dispose);
  await page.settle();
  for (const channel of ['run', 'landing']) {
    await page.press(`Land ${offer.change}`);
    runStale = channel === 'run'; landingStale = channel === 'landing';
    await page.press('Refresh Factory');
    assert.equal(page.find(`Land ${offer.change}`), undefined);
    assert.equal(page.find(`Confirm land ${offer.change}`)!.props.disabled, true);
    assert.equal(page.lands.length, 0);
    await page.press(cancel(page));
    runStale = false; landingStale = false;
    await page.press('Refresh Factory');
  }
  await page.press(`Land ${offer.change}`);
  defer = true;
  await page.press('Refresh Factory');
  assert.equal(page.find('Refresh Factory')!.props.disabled, true);
  await page.press(`Confirm land ${offer.change}`);
  assert.equal(page.lands.length, 1);
  resolveRead(landingSnapshot({ offer: { ...offer, head: 'b'.repeat(40) },
    attempts: [landingAttempt({ outcome: 'refused', reason: 'Obsolete read', attempt_id: '9'.repeat(32) })] }));
  await page.settle();
  assert.equal(occurrences(page, 'Obsolete read').length, 0);
  assert.ok(page.find(`Stop landing ${offer.change}`));
  assert.equal(page.find(`Land ${offer.change}`)!.props.disabled, true);
  assert.equal(page.lands.length, 1);
});

test('paseo-runs-page/landing-notices-stay-visible/landing-notice-follows-a-hidden-run', async t => {
  const old = landingAttempt({ outcome: 'failed', reason: 'Finish push refused' });
  const outsideHistory = landingAttempt({ attempt_id: 'f'.repeat(32), change: 'older-running', phase: 'merging' });
  const recent = Array.from({ length: 20 }, (_, i) => landingAttempt({
    attempt_id: (i + 2).toString(16).padStart(32, '0'), change: 'removed', outcome: 'refused', reason: `Recent refusal ${i}.`,
  }));
  let runs = [humanRun, { ...humanRun, change: outsideHistory.change }], includeOld = true;
  const options = {
    read: async () => runSnapshot(runs),
    landing: async ({ change }: { change?: string }) => landingSnapshot({
      attempts: change ? (change === offer.change && includeOld ? [old] : []) : recent,
      total: 25, current: change === outsideHistory.change ? outsideHistory : null,
    }),
  };
  const page = styleView([], options);
  t.after(page.dispose);
  await page.settle();
  assert.ok(page.text(row(page).children).includes(old.reason));
  assert.equal(occurrences(page, old.reason).length, 1);
  assert.ok(page.text(row(page, outsideHistory.change).children).includes('merging'));
  includeOld = false;
  runs = [{ ...humanRun, status: 'done' }, { ...humanRun, change: outsideHistory.change }];
  await page.press('Refresh Factory');
  assert.equal(page.find('Finished (1)')!.props.accessibilityState.expanded, false);
  assert.equal(row(page), undefined);
  assert.equal(occurrences(page, old.reason).length, 1);
  assert.ok(page.text(occurrences(page, old.reason)[0].children).startsWith(`${offer.change}: `));
  assert.ok(page.nodes().indexOf(occurrences(page, old.reason)[0]) < page.nodes().indexOf(row(page, outsideHistory.change)),
    'a hidden run notice appears above the remaining rows');
  const reads = page.landingReads.length;
  for (let i = 0; i < 2; i++) {
    await page.press('Finished (1)');
    assert.equal(occurrences(page, old.reason).length, 1);
    assert.ok(page.text(row(page).children).includes(old.reason));
    await page.press('Finished (1)');
    assert.equal(occurrences(page, old.reason).length, 1);
  }
  assert.equal(page.landingReads.length, reads);
  runs = [];
  await page.press('Refresh Factory');
  assert.equal(occurrences(page, old.reason).length, 1);
  assert.ok(page.landingReads.some(input => input.change === outsideHistory.change));
  page.dispose();
  runs = [{ ...humanRun, change: outsideHistory.change }];
  const remount = styleView([], options);
  t.after(remount.dispose);
  await remount.settle();
  assert.equal(occurrences(remount, old.reason).length, 0, 'remount restores bounded server history');
  assert.equal(occurrences(remount, 'Recent refusal ').length, 20);
  assert.ok(remount.text(row(remount, outsideHistory.change).children).includes('merging'), 'named current is outside newest 20');
  assert.deepEqual(remount.lands, []);
  assert.deepEqual(remount.stops, []);
});

test('paseo-runs-page/landing-refresh-integration/refresh-never-submits-an-action', async t => {
  let now = 0, concurrent = 0, maxConcurrent = 0;
  const runs = Array.from({ length: 7 }, (_, i) => ({ ...humanRun, change: `run-${i}` }));
  const commands: string[][] = [];
  const service = createFactoryLanding(async args => {
    commands.push([...args]);
    assert.equal(args[0], 'status');
    const change = args[1] === '--limit' ? undefined : args[1];
    if (change) { concurrent++; maxConcurrent = Math.max(maxConcurrent, concurrent); }
    await new Promise(resolve => setImmediate(resolve));
    if (change) concurrent--;
    return { code: 0, stdout: JSON.stringify(landingSnapshot({ offer: change ? { ...offer, change } : null })) };
  }, async () => runSnapshot(runs), () => now);
  t.after(service.dispose);
  const page = styleView(runs, { landing: service.read });
  t.after(page.dispose);
  for (let i = 0; i < 5; i++) await page.settle();
  assert.equal(commands.length, 8);
  assert.equal(maxConcurrent, 4);
  assert.equal(page.reads.length, 1);
  assert.equal(page.intervals.length, 1);
  assert.equal(page.intervals[0].delay, 60_000);
  assert.equal(page.subscriptions.length, 1);
  await page.press('Details for run-0');
  await page.press('Details for run-0');
  assert.equal(page.landingReads.length, 8, 'Details never reads status');
  await page.press('Refresh Factory');
  assert.equal(commands.length, 8, 'landing reads retain their independent 15-second throttle');
  assert.equal(page.reads.length, 2, 'ordinary refresh still happens');
  now = 15_000;
  page.intervals[0].callback();
  page.subscriptions[0].callback('active'); // Coalesced with the active refresh.
  for (let i = 0; i < 5; i++) await page.settle();
  assert.equal(commands.length, 16);
  assert.equal(page.reads.length, 3);
  page.appState.currentState = 'background';
  page.intervals[0].callback();
  assert.equal(page.reads.length, 3);
  now = 30_000;
  page.appState.currentState = 'active';
  page.subscriptions[0].callback('active');
  for (let i = 0; i < 5; i++) await page.settle();
  assert.equal(commands.length, 24);
  assert.equal(page.reads.length, 4);
  assert.deepEqual([...page.actions], []);
  assert.deepEqual([...page.lands], []);
  assert.deepEqual([...page.stops], []);
  assert.deepEqual(page.hosts, []);
  page.dispose();
  assert.equal(page.intervals[0].cleared, true);
  assert.equal(page.subscriptions[0].removed, true);
});
