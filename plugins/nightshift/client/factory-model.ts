import type { FactoryActInput, FactoryRun } from '../shared/factory';

type Verb = FactoryActInput['verb'];
const labels: Record<Verb, string> = {
  resume: 'Resume', accept: 'Accept', publish: 'Publish', finish: 'Finish', abort: 'Abort',
};

export function orderFactoryRuns(runs: readonly FactoryRun[]): FactoryRun[] {
  // Stable within each group, without changing the cached snapshot.
  return [...runs.filter(run => run.status === 'paused'), ...runs.filter(run => run.status !== 'paused')];
}

export function factoryButtons(run: FactoryRun) {
  return [...new Set(run.action?.actions ?? [])].map(verb => ({ verb, label: labels[verb] }));
}

export type ActionDecision =
  | { kind: 'unavailable' }
  | { kind: 'confirm'; request: FactoryActInput; message: string }
  | { kind: 'send'; request: FactoryActInput };

export function decideFactoryAction(
  run: FactoryRun, verb: Verb, note = '', confirmed = false,
): ActionDecision {
  if (!factoryButtons(run).some(button => button.verb === verb)) return { kind: 'unavailable' };
  const request: FactoryActInput = {
    change: run.change, verb, ...(verb === 'resume' && note ? { note } : {}),
    ...(run.action?.pause_id ? { expect: run.action.pause_id } : {}),
  };
  if ((verb === 'publish' || verb === 'finish') && !confirmed) {
    return { kind: 'confirm', request, message: `${labels[verb]} ${run.change}? This pushes to GitHub.` };
  }
  if (verb === 'abort' && !confirmed) {
    return { kind: 'confirm', request, message: `Abort ${run.change}? This cannot be undone.` };
  }
  return { kind: 'send', request };
}
