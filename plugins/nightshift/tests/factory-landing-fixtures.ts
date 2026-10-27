import type { FactoryLandingAttempt, FactoryLandingOffer, FactoryLandingSnapshot, FactoryRun } from '../shared/factory';

export const offer: FactoryLandingOffer = {
  change: 'human-checks', pr: 'https://github.com/owner/repo/pull/7',
  pause_id: 'human_checks:published', head: 'a'.repeat(40), since: 0.25,
};
export const humanRun: FactoryRun = {
  change: offer.change, pr: offer.pr, status: 'paused', tasks: { done: 3, total: 3 },
  pause: { since: offer.since, auto_resumes_remaining: 0 },
  action: { kind: 'action', headline: 'Human checks', why: 'Review the PR.', command: '',
    pause_id: offer.pause_id, actions: ['finish', 'abort'] },
};
export function landingSnapshot(fields: Partial<FactoryLandingSnapshot> = {}): FactoryLandingSnapshot {
  return { version: 1, attempts: [], total: 0, diagnostics: [], offer: null, current: null,
    truncated: false, stale: false, fetchedAt: '', error: null, ...fields };
}
export function landingAttempt(fields: Partial<FactoryLandingAttempt> = {}): FactoryLandingAttempt {
  return { ...offer, attempt_id: '1'.repeat(32), created_at: 1, updated_at: 2,
    phase: 'checking CI', outcome: null, reason: '', exit_code: null, diagnostics: [], overdue: false, ...fields };
}
