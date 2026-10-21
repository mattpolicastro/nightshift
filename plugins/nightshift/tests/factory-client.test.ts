import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { FactoryActInput, FactoryRun } from '../shared/factory';
import { orderFactoryRuns, factoryButtons, decideFactoryAction } from '../client/factory-model';

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
