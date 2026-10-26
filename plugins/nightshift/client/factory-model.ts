import type { FactoryActInput, FactoryRun } from '../shared/factory';

type Verb = FactoryActInput['verb'];
const labels: Record<Verb, string> = {
  resume: 'Resume', accept: 'Accept', publish: 'Publish', finish: 'Finish', abort: 'Abort',
};

// Only the SDK descriptor fields used to match; preserve the full entry for navigation.
type WorkspaceMatchFields = {
  workspaceDirectory: string;
  archivingAt?: string | null;
  activityAt: string | null;
};

export function matchWorkspace<T extends WorkspaceMatchFields>(repo: string, workspaces: readonly T[]): T | null {
  if (!repo) return null;
  const activity = (workspace: T) => {
    const time = Date.parse(workspace.activityAt ?? '');
    return Number.isFinite(time) ? time : -Infinity;
  };
  return workspaces.filter(workspace => workspace.archivingAt == null &&
    workspace.workspaceDirectory.replace(/^\/(?:Users|home)\/[^/]+(?=\/|$)/, '~') === repo)
    .sort((a, b) => activity(b) - activity(a))[0] ?? null;
}

export function factoryMoreCommand(change: FactoryRun['change']): string {
  // Changes come from the validated run schema's shell-safe identifier.
  return `nightshift status ${change} --events 20`;
}

export function factoryDetails(run: FactoryRun) {
  const context = run.context;
  return {
    fields: [
      { label: 'Summary', text: context?.summary },
      { label: 'Repository', text: context?.repo },
      { label: 'Branch', text: context?.branch },
      { label: 'Task', text: context?.task
        ? [context.task.label, context.task.section, context.task.text].filter(Boolean).join(' · ') : '' },
      { label: 'Pause detail', text: context?.detail },
    ].filter((field): field is { label: string; text: string } => Boolean(field.text)),
    recent: context?.recent ?? [],
    github: context?.github ?? '',
    command: factoryMoreCommand(run.change),
  };
}

export function orderFactoryRuns(runs: readonly FactoryRun[]): FactoryRun[] {
  // Stable within each group, without changing the cached snapshot.
  return [...runs.filter(run => run.status === 'paused'), ...runs.filter(run => run.status !== 'paused')];
}

export function groupFactoryRuns(runs: readonly FactoryRun[]): { active: FactoryRun[]; finished: FactoryRun[] } {
  return {
    active: orderFactoryRuns(runs.filter(run => run.status === 'paused' || run.status === 'running')),
    finished: runs.filter(run => run.status === 'done' || run.status === 'aborted'),
  };
}

export function factoryWaitingTime(
  run: FactoryRun, nowMs = Date.now(),
): { text: string; warning: boolean } | null {
  const since = run.pause?.since;
  if (run.status !== 'paused' || run.pause?.auto_resumes_remaining !== 0 ||
    typeof since !== 'number' || !Number.isFinite(since)) return null;

  const elapsedSeconds = Math.max(0, nowMs / 1000 - since);
  const duration = elapsedSeconds < 3600
    ? `${Math.floor(elapsedSeconds / 60)}m`
    : `${Math.floor(elapsedSeconds / 3600)}h`;
  return { text: `waiting ${duration}`, warning: elapsedSeconds > 10_800 };
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
