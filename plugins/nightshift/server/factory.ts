import { execFile, type ChildProcess } from 'node:child_process';
import { homedir } from 'node:os';
import { z } from 'zod';
import { resolveHostPaths } from './host-paths';
import {
  factoryRunSchema, factoryActInputSchema, factoryActResultSchema,
  type FactoryRun, type FactorySnapshot, type FactoryActInput, type FactoryActResult,
} from '../shared/factory';

const sourceSchema = z.object({ version: z.literal(1), runs: z.array(factoryRunSchema) });
const unavailable = 'Factory status is unavailable on this host. Try again shortly.';
export type FactoryExecute = (args: string[]) => Promise<{ code: number; stdout: string }>;

export function createFactory(execute: FactoryExecute, clock = () => Date.now(), home = homedir()) {
  let cached: FactorySnapshot | undefined;
  let inFlight: Promise<FactorySnapshot> | undefined;
  let lastRead = -Infinity;
  let generation = 0;
  let disposed = false;
  const display = (text: string) => home ? text.split(home).join('~') : text;

  function githubLink(text: string): string {
    const link = display(text);
    if (!link.startsWith('https://github.com/')) return '';
    try {
      const url = new URL(link);
      return url.origin === 'https://github.com' && !url.username && !url.password ? link : '';
    } catch {
      return '';
    }
  }

  function project(run: FactoryRun): FactoryRun {
    if (run.pr) {
      const url = new URL(run.pr);
      if (url.origin !== 'https://github.com' || url.username || url.password ||
          !/^\/[^/]+\/[^/]+\/pull\/\d+$/.test(url.pathname) || url.search || url.hash) {
        throw new Error('Unexpected PR link');
      }
    }
    return {
      change: display(run.change), status: run.status, tasks: run.tasks, pr: display(run.pr),
      ...(run.pause === null ? { pause: null } : run.pause &&
          (run.pause.since !== undefined || run.pause.auto_resumes_remaining !== undefined) ? { pause: {
        ...(run.pause.since !== undefined ? { since: run.pause.since } : {}),
        ...(run.pause.auto_resumes_remaining !== undefined
          ? { auto_resumes_remaining: run.pause.auto_resumes_remaining } : {}),
      } } : {}),
      ...(run.context ? { context: {
        summary: display(run.context.summary), repo: display(run.context.repo),
        branch: display(run.context.branch), detail: display(run.context.detail),
        task: run.context.task ? {
          label: display(run.context.task.label), section: display(run.context.task.section),
          text: display(run.context.task.text),
        } : null,
        recent: run.context.recent.map(event => ({ at: display(event.at), text: display(event.text) })),
        github: githubLink(run.context.github),
      } } : {}),
      ...(run.action ? { action: {
        kind: display(run.action.kind), headline: display(run.action.headline),
        why: display(run.action.why), command: display(run.action.command),
        actions: run.action.actions,
        ...(run.action.pause_id ? { pause_id: run.action.pause_id } : {}),
      } } : {}),
    };
  }

  async function load(epoch: number): Promise<FactorySnapshot> {
    let snapshot: FactorySnapshot;
    try {
      const result = await execute(['status', '--json']);
      if (result.code !== 0) throw new Error('Status failed');
      const raw = sourceSchema.parse(JSON.parse(result.stdout));
      snapshot = {
        runs: raw.runs.map(project), stale: false,
        fetchedAt: new Date(clock()).toISOString(), error: null,
      };
    } catch {
      if (!cached) throw new Error(unavailable);
      snapshot = { ...cached, stale: true, error: 'Refresh unavailable. Showing the last loaded list.' };
    }
    if (disposed) throw new Error('Factory plugin stopped');
    // A status started before an action must not replace the post-action cache.
    if (epoch !== generation) return read();
    cached = snapshot;
    return snapshot;
  }

  async function read(_input: { refresh?: boolean } = {}): Promise<FactorySnapshot> {
    if (disposed) throw new Error('Factory plugin stopped');
    if (inFlight) return inFlight;
    if (clock() - lastRead < 15_000) {
      if (cached) return cached;
      throw new Error(unavailable);
    }
    lastRead = clock();
    const pending = load(generation);
    inFlight = pending;
    try { return await pending; }
    finally { if (inFlight === pending) inFlight = undefined; }
  }

  function invalidate() {
    generation++;
    lastRead = -Infinity;
    inFlight = undefined;
    if (cached) cached = { ...cached, stale: true };
  }

  async function act(input: FactoryActInput): Promise<FactoryActResult> {
    if (disposed) throw new Error('Factory plugin stopped');
    const parsed = factoryActInputSchema.safeParse(input);
    if (!parsed.success) throw new Error('Invalid Factory action request.');
    const { change, verb, note, expect } = parsed.data;
    const refused = (detail: string): FactoryActResult => ({ version: 1, outcome: 'refused', verb, detail });
    const run = cached?.runs.find(item => item.change === change);
    if (!run) return refused('Change is not in the last loaded status. Refresh the list.');
    if (!run.action?.actions.includes(verb)) return refused('Action is not available for this change. Refresh the list.');
    const args = ['act', change, verb];
    // Bind the value so argparse also accepts notes beginning with a hyphen.
    if (note !== undefined) args.push(`--note=${note}`);
    if (expect) args.push(`--expect=${expect}`);
    args.push('--json');
    try {
      const result = await execute(args);
      const outcome = factoryActResultSchema.parse(JSON.parse(result.stdout));
      if (outcome.verb !== verb || result.code !== (outcome.outcome === 'done' ? 0 : 1)) {
        throw new Error('Unexpected action outcome');
      }
      return { ...outcome, detail: display(outcome.detail) };
    } catch {
      // Neither stderr nor unvalidated stdout belongs in the RPC response.
      return refused('Action result unavailable. Refresh the list before trying again.');
    } finally {
      // Even a lost response can follow a successful state transition.
      invalidate();
    }
  }

  return { read, act, dispose() { disposed = true; invalidate(); cached = undefined; } };
}

// Trusted host configuration; RPC callers supply neither executable nor cwd.
export function factoryPath() {
  return resolveHostPaths('factory').path;
}
const children = new Set<ChildProcess>();
const execute: FactoryExecute = args => new Promise((resolve, reject) => {
  const { cwd, uv, path } = resolveHostPaths('factory');
  const child = execFile(uv, ['run', '--no-sync', 'nightshift', ...args], {
    cwd, shell: false,
    // act may push (publish, finish): nightshift allows 300 s for a push.
    timeout: args[0] === 'act' ? 330_000 : 60_000, maxBuffer: 2 * 1024 * 1024,
    // ~/n/bin: finish archives the change through OpenSpec's npx, which lives there.
    env: { ...process.env, PATH: path },
  }, (error, stdout) => {
    children.delete(child);
    if (error && (error.killed || typeof error.code !== 'number')) reject(error);
    else resolve({ code: error?.code as number || 0, stdout });
  });
  children.add(child);
});
const factory = createFactory(execute);
export const readFactoryRuns = factory.read;
export const actOnFactoryRun = factory.act;
export function disposeFactory() {
  factory.dispose();
  for (const child of children) child.kill('SIGTERM');
  children.clear();
}
