import assert from 'node:assert/strict';
import { test } from 'node:test';
import { factoryLandOffer, landingAttemptNotice, retainLandingNotices, sameLandingOffer } from '../client/factory-model';
import { humanRun, landingAttempt, landingSnapshot, offer } from './factory-landing-fixtures';

test('paseo-runs-page/human-checks-landing/land-action-appears-only-at-human-checks: model requires matching readable metadata', () => {
  const snapshot = landingSnapshot({ offer });
  assert.equal(factoryLandOffer(humanRun, snapshot), offer);
  for (const run of [
    ...(['running', 'done', 'aborted'] as const).map(status => ({ ...humanRun, status })),
    { ...humanRun, change: 'other' }, { ...humanRun, pr: '' }, { ...humanRun, action: undefined },
    { ...humanRun, pause: null }, { ...humanRun, pause: { since: 1 } },
    { ...humanRun, action: { ...humanRun.action!, pause_id: 'stuck:published' } },
  ]) assert.equal(factoryLandOffer(run, snapshot), null);
  assert.equal(factoryLandOffer(humanRun), null);
  assert.equal(factoryLandOffer(humanRun, snapshot, true), null);
  assert.equal(factoryLandOffer(humanRun, { ...snapshot, stale: true }), null);
  assert.equal(factoryLandOffer(humanRun, landingSnapshot()), null);
  assert.equal(factoryLandOffer({ ...humanRun, pause: { since: 0 } },
    landingSnapshot({ offer: { ...offer, since: 0 } }))?.since, 0);
});

test('paseo-runs-page/landing-confirmation/changed-offer-invalidates-the-dialog: every frozen field matters', () => {
  assert.equal(sameLandingOffer(offer, { ...offer }), true);
  assert.equal(sameLandingOffer(offer, null), false);
  for (const changed of [
    { change: 'another' }, { pr: 'https://github.com/owner/repo/pull/8' },
    { pause_id: 'human_checks:another' }, { head: 'b'.repeat(40) }, { since: 0.5 },
  ]) assert.equal(sameLandingOffer(offer, { ...offer, ...changed }), false);
});

test('paseo-runs-page/landing-notices-stay-visible/land-result-and-refusal-stay-in-the-row: retain by attempt without regression', () => {
  const progress = landingAttemptNotice(landingAttempt());
  const success = landingAttemptNotice(landingAttempt({ outcome: 'succeeded', reason: 'Merged', updated_at: 3 }));
  const refused = landingAttemptNotice(landingAttempt({ attempt_id: '2'.repeat(32), outcome: 'refused', reason: 'Head changed' }));
  const previous = [progress];
  const notices = retainLandingNotices(previous, [success, refused, success]);
  assert.deepEqual(previous, [progress]);
  assert.deepEqual(notices, [success, refused]);
  assert.deepEqual(retainLandingNotices(notices, [progress]), notices);
  assert.deepEqual(retainLandingNotices(notices, []), notices);
  const stopped = { ...progress, messages: ['Stop requested'] };
  assert.deepEqual(retainLandingNotices(notices, [stopped, stopped])[0], { ...success, messages: ['Stop requested'] });
});
