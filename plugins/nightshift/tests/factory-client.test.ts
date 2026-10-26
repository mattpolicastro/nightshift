import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { FactoryActInput, FactoryRun } from '../shared/factory';
import { orderFactoryRuns, factoryButtons, decideFactoryAction, matchWorkspace, factoryMoreCommand, factoryDetails } from '../client/factory-model';
import { groupFactoryRuns } from '../client/factory-model';

test('run-context/details-in-the-factory-section/workspace-matched-for-open-in-paseo', () => {
  const older = {
    id: 'older', workspaceDirectory: '/Users/studio/Projects/nightshift',
    archivingAt: null, activityAt: '2026-10-20T12:00:00Z',
  };
  const active = { ...older, id: 'active', activityAt: '2026-10-21T12:00:00Z' };
  const archiving = { ...active, id: 'archiving', archivingAt: '2026-10-21T13:00:00Z', activityAt: '2026-10-21T13:00:00Z' };
  const unknownActivity = { ...older, id: 'unknown', activityAt: null };
  const workspaces = Object.freeze([archiving, unknownActivity, older, active]);
  const repo = '~/Projects/nightshift';
  assert.equal(matchWorkspace(repo, workspaces), active);
  assert.equal(matchWorkspace(repo, [archiving]), null);
  assert.equal(matchWorkspace(repo, [{ ...archiving, archivingAt: '' }]), null);
  assert.equal(matchWorkspace(repo, []), null);
  assert.equal(matchWorkspace('', workspaces), null);
  assert.equal(matchWorkspace('~/Projects/other', workspaces), null);
  assert.equal(matchWorkspace('~/Projects/night', workspaces), null);
  assert.equal(matchWorkspace(repo, [unknownActivity]), unknownActivity);
  assert.equal(matchWorkspace(repo, [{ ...older, activityAt: 'invalid' }, active]), active);
  assert.equal(matchWorkspace(repo, [older, { ...older, id: 'tied' }]), older);
  assert.deepEqual(workspaces, [archiving, unknownActivity, older, active]);

  for (const workspaceDirectory of ['/home/studio/Projects/nightshift', repo]) {
    const workspace = { workspaceDirectory, activityAt: null };
    assert.equal(matchWorkspace(repo, [workspace]), workspace);
  }
  for (const workspaceDirectory of ['/mnt/Users/studio/Projects/nightshift', '/Users/studio/Projects/nightshift-extra']) {
    assert.equal(matchWorkspace(repo, [{ ...active, workspaceDirectory }]), null);
  }
  const shared = { ...active, workspaceDirectory: '/srv/nightshift' };
  assert.equal(matchWorkspace('/srv/nightshift', [shared]), shared);
});

test('run-context/details-in-the-factory-section/more-command-offered', () => {
  assert.equal(factoryMoreCommand('factory-context'), 'nightshift status factory-context --events 20');
  assert.equal(factoryMoreCommand('another.change_2'), 'nightshift status another.change_2 --events 20');
  assert.equal(factoryDetails(paused('factory-context')).command, 'nightshift status factory-context --events 20');
});

function paused(change: string, actions: FactoryActInput['verb'][] = ['resume', 'abort']): FactoryRun {
  return {
    change, status: 'paused', tasks: { done: 2, total: 5 },
    pr: 'https://github.com/owner/repo/pull/7',
    action: { kind: 'action', headline: 'Review section 1', why: 'Section 1 is complete.', command: '', actions },
  };
}
const running: FactoryRun = { change: 'building', status: 'running', tasks: { done: 1, total: 4 }, pr: '' };

test('paseo-runs-page/runs-listed/waiting-run-first: phase gate precedes running with headline and display fields', () => {
  const gate = paused('gate');
  const input = Object.freeze([running, gate]);
  const rows = orderFactoryRuns(input);
  assert.deepEqual(rows.map(run => run.change), ['gate', 'building']);
  assert.equal(rows[0].action?.headline, 'Review section 1');
  assert.equal(rows[0].action?.why, 'Section 1 is complete.');
  assert.deepEqual(rows[0].tasks, { done: 2, total: 5 });
  assert.equal(rows[0].status, 'paused');
  assert.equal(rows[0].pr, 'https://github.com/owner/repo/pull/7');
  assert.deepEqual(input, [running, gate]);
});

test('row ordering preserves source order within waiting and other runs, including empty lists', () => {
  const done = { ...running, change: 'complete', status: 'done' as const };
  const first = paused('first'), second = paused('second', []);
  assert.deepEqual(orderFactoryRuns([done, first, running, second]), [first, second, done, running]);
  assert.deepEqual(orderFactoryRuns([]), []);
});

test('buttons map exactly the supplied verbs, with one button per verb and no inferred permissions', () => {
  const cases: [FactoryActInput['verb'][], string[]][] = [
    [['resume', 'abort'], ['Resume', 'Abort']],
    [['resume', 'accept', 'abort'], ['Resume', 'Accept', 'Abort']],
    [['publish', 'abort'], ['Publish', 'Abort']],
    [['finish', 'abort'], ['Finish', 'Abort']],
    [['abort'], ['Abort']],
    [[], []],
  ];
  for (const [verbs, labels] of cases) {
    const buttons = factoryButtons(paused('change', verbs));
    assert.deepEqual(buttons.map(button => button.verb), verbs);
    assert.deepEqual(buttons.map(button => button.label), labels);
  }
  assert.deepEqual(factoryButtons(running), []);
  assert.deepEqual(factoryButtons(paused('change', ['resume', 'resume'])), [{ verb: 'resume', label: 'Resume' }]);
});

test('paseo-runs-page/buttons-fit-the-pause/publish-needs-confirmation: publish and finish cannot send before acceptance', () => {
  for (const verb of ['publish', 'finish'] as const) {
    const run = paused(verb === 'publish' ? 'ready-to-publish' : 'human-checks', [verb, 'abort']);
    const requests: FactoryActInput[] = [];
    const press = (confirmed = false) => {
      const decision = decideFactoryAction(run, verb, 'unrelated resume note', confirmed);
      if (decision.kind === 'send') requests.push(decision.request);
      return decision;
    };
    const pending = press();
    assert.equal(pending.kind, 'confirm');
    assert.deepEqual(requests, []);
    if (pending.kind !== 'confirm') assert.fail('Expected a confirmation');
    assert.match(pending.message, /pushes to GitHub/);
    assert.ok(pending.message.includes(run.change));
    // Dismissing the prompt sends nothing; pressing again still needs acceptance.
    assert.equal(press().kind, 'confirm');
    assert.deepEqual(requests, []);
    assert.equal(press(true).kind, 'send');
    assert.deepEqual(requests, [{ change: run.change, verb }]);
  }
});

test('resume carries its literal optional note; other immediate verbs do not carry notes', () => {
  const run = paused('stuck', ['resume', 'accept', 'abort']);
  const note = 'Keep $(echo text)\n  and spacing  ';
  assert.deepEqual(decideFactoryAction(run, 'resume', note), {
    kind: 'send', request: { change: run.change, verb: 'resume', note },
  });
  for (const verb of ['resume', 'accept'] as const) {
    assert.deepEqual(decideFactoryAction(run, verb), { kind: 'send', request: { change: run.change, verb } });
  }
  assert.equal(decideFactoryAction(run, 'abort').kind, 'confirm');
  assert.deepEqual(decideFactoryAction(run, 'abort', '', true), { kind: 'send', request: { change: run.change, verb: 'abort' } });
  assert.deepEqual(decideFactoryAction(run, 'accept', note), { kind: 'send', request: { change: run.change, verb: 'accept' } });
});

test('confirmation cannot authorize a verb removed by a refreshed pause', () => {
  const before = paused('change', ['publish', 'abort']);
  assert.equal(decideFactoryAction(before, 'publish').kind, 'confirm');
  const after = paused('change', ['resume', 'abort']);
  assert.deepEqual(decideFactoryAction(after, 'publish', '', true), { kind: 'unavailable' });
  assert.deepEqual(decideFactoryAction(running, 'finish', '', true), { kind: 'unavailable' });
});

test('an action carries the pause the page showed, so a later pause refuses it', () => {
  const run = paused('stuck', ['resume', 'accept', 'abort']);
  const shown = { ...run, action: { ...run.action!, pause_id: 'stuck:a1b2c3' } };
  assert.deepEqual(decideFactoryAction(shown, 'accept'),
    { kind: 'send', request: { change: run.change, verb: 'accept', expect: 'stuck:a1b2c3' } });
});

test('orderFactoryRuns separates active from finished', async t => {
  const a: FactoryRun = { ...running, change: 'A', status: 'done' };
  const b: FactoryRun = { ...running, change: 'B' };
  const c = paused('C');
  const d: FactoryRun = { ...running, change: 'D', status: 'aborted' };
  const e = paused('E');
  const f: FactoryRun = { ...running, change: 'F' };
  const g: FactoryRun = { ...running, change: 'G', status: 'done' };

  await t.test('factory-finished-runs/stable-run-groups/mixed-statuses-retain-stable-groups', async t => {
    await t.test('stable ordering within active statuses and across finished statuses', () => {
      const input = [a, b, c, d, e, f, g];
      const before = structuredClone(input);
      const { active, finished } = groupFactoryRuns(input);
      assert.deepEqual(active.map(run => run.change), ['C', 'E', 'B', 'F']);
      assert.deepEqual(finished.map(run => run.change), ['A', 'D', 'G']);
      assert.deepEqual(input, before);
    });

    await t.test('original object identity is retained exactly once in fresh arrays', () => {
      const input = [a, b, c, d, e, f, g];
      const { active, finished } = groupFactoryRuns(input);
      [c, e, b, f].forEach((run, index) => assert.strictEqual(active[index], run));
      [a, d, g].forEach((run, index) => assert.strictEqual(finished[index], run));
      assert.equal(active.length + finished.length, input.length);
      assert.equal(new Set([...active, ...finished]).size, input.length);
      assert.notStrictEqual(active, input);
      assert.notStrictEqual(finished, input);
      assert.notStrictEqual(active, finished);
      const again = groupFactoryRuns(input);
      assert.notStrictEqual(again.active, active);
      assert.notStrictEqual(again.finished, finished);
    });

    await t.test('frozen input and run objects remain unchanged', () => {
      const input = Object.freeze(structuredClone([a, b, c, d, e, f, g]).map(run => {
        Object.freeze(run.tasks);
        if (run.action) {
          Object.freeze(run.action.actions);
          Object.freeze(run.action);
        }
        return Object.freeze(run);
      }));
      const before = structuredClone(input);
      const { active, finished } = groupFactoryRuns(input);
      assert.deepEqual(active, [input[2], input[4], input[1], input[5]]);
      assert.deepEqual(finished, [input[0], input[3], input[6]]);
      assert.deepEqual(input, before);
    });
  });

  await t.test('factory-finished-runs/stable-run-groups/empty-and-single-group-inputs', async t => {
    const cases: { name: string; input: FactoryRun[]; active: FactoryRun[]; finished: FactoryRun[] }[] = [
      { name: 'empty input', input: [], active: [], finished: [] },
      { name: 'only active runs', input: [b, c, f, e], active: [c, e, b, f], finished: [] },
      { name: 'only finished runs', input: [a, d, g], active: [], finished: [a, d, g] },
    ];
    for (const { name, input, active, finished } of cases) {
      await t.test(name, () => {
        const before = structuredClone(input);
        const groups = groupFactoryRuns(Object.freeze(input));
        assert.deepEqual(groups, { active, finished });
        assert.notStrictEqual(groups.active, input);
        assert.notStrictEqual(groups.finished, input);
        assert.notStrictEqual(groups.active, groups.finished);
        assert.deepEqual(input, before);
      });
    }
  });

  await t.test('factory-finished-runs/existing-ordering-interface/existing-flat-ordering-remains-compatible', () => {
    const input = Object.freeze([a, c, b, e]);
    const rows = orderFactoryRuns(input);
    assert.deepEqual(rows, [c, e, a, b]);
    [c, e, a, b].forEach((run, index) => assert.strictEqual(rows[index], run));
    assert.notStrictEqual(rows, input);
    assert.deepEqual(input, [a, c, b, e]);
  });
});
