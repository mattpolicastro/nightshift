import { execFile, type ChildProcess } from 'node:child_process';
import { homedir } from 'node:os';
import {
  factoryLandingInputSchema, factoryLandingStatusSchema, factoryLandInputSchema,
  factoryLandResultSchema, factoryLandStopInputSchema, factoryLandStopResultSchema,
  type FactoryLandingInput, type FactoryLandingSnapshot, type FactoryLandInput,
  type FactoryLandResult, type FactoryLandStopInput, type FactoryLandStopResult,
  type FactorySnapshot,
} from '../shared/factory';
import { readFactoryRuns, type FactoryExecute } from './factory';
import { resolveHostPaths } from './host-paths';

const unavailable = 'Landing status is unavailable on this host. Try again shortly.';
const maxBuffer = 2 * 1024 * 1024;
type Query = {
  cached?: FactoryLandingSnapshot; inFlight?: Promise<FactoryLandingSnapshot>;
  lastRead: number; generation: number;
};

export function createFactoryLanding(
  execute: FactoryExecute, readRuns: () => Promise<FactorySnapshot>,
  clock = () => Date.now(), home = homedir(),
) {
  const queries = new Map<string, Query>();
  const submitting = new Set<string>();
  let disposed = false;
  const display = (value: string) => home ? value.split(home).join('~') : value;
  // Apply redaction only after schema projection has removed private fields.
  function redact<T>(value: T): T {
    if (typeof value === 'string') return display(value) as T;
    if (Array.isArray(value)) return value.map(redact) as T;
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).map(([key, field]) => [key, redact(field)])) as T;
    }
    return value;
  }
  function active() { if (disposed) throw new Error('Factory landing plugin stopped'); }
  function query(key: string): Query {
    if (!queries.has(key)) queries.set(key, { lastRead: -Infinity, generation: 0 });
    return queries.get(key)!;
  }
  function invalidate(change?: string) {
    for (const [key, state] of queries) {
      if (change !== undefined && key !== '' && key !== change) continue;
      state.generation++;
      state.lastRead = -Infinity;
      state.inFlight = undefined;
      if (state.cached) state.cached = { ...state.cached, stale: true };
    }
  }
  function json(stdout: string): unknown {
    if (Buffer.byteLength(stdout, 'utf8') > maxBuffer) throw new Error('Transport limit');
    return JSON.parse(stdout);
  }

  async function load(key: string, state: Query, epoch: number): Promise<FactoryLandingSnapshot> {
    let snapshot: FactoryLandingSnapshot | undefined;
    try {
      const result = await execute(['status', ...(key ? [key] : []), '--limit', '20', '--json']);
      if (result.code !== 0) throw new Error('Status failed');
      const raw = factoryLandingStatusSchema.parse(json(result.stdout));
      if (raw.total < raw.attempts.length ||
          (key ? [...raw.attempts, ...[raw.current, raw.offer].filter(item => item !== null)]
            .some(item => item.change !== key) : raw.offer !== null || raw.current !== null) ||
          raw.current?.outcome != null) throw new Error('Unexpected query identity');
      // Redaction must not turn a checked PR URL into an unchecked link.
      const projected = factoryLandingStatusSchema.parse(redact(raw));
      snapshot = { ...projected, stale: false, fetchedAt: new Date(clock()).toISOString(), error: null };
    } catch {
      if (state.cached) snapshot = {
        ...state.cached, stale: true, error: 'Refresh unavailable. Showing the last loaded landing status.',
      };
    }
    active();
    // Check even failed reads: an old failure must not mark a newer snapshot stale.
    if (epoch !== state.generation) return read(key ? { change: key } : {});
    if (!snapshot) throw new Error(unavailable);
    state.cached = snapshot;
    return snapshot;
  }

  async function read(input: FactoryLandingInput = {}): Promise<FactoryLandingSnapshot> {
    active();
    const parsed = factoryLandingInputSchema.safeParse(input);
    if (!parsed.success) throw new Error('Invalid landing status request.');
    const key = parsed.data.change ?? '', state = query(key);
    if (state.inFlight) return state.inFlight;
    if (clock() - state.lastRead < 15_000) {
      if (state.cached) return state.cached;
      throw new Error(unavailable);
    }
    state.lastRead = clock();
    const pending = load(key, state, state.generation);
    state.inFlight = pending;
    try { return await pending; }
    finally { if (state.inFlight === pending) state.inFlight = undefined; }
  }

  async function start(input: FactoryLandInput): Promise<FactoryLandResult> {
    active();
    const parsed = factoryLandInputSchema.safeParse(input);
    if (!parsed.success) throw new Error('Invalid landing confirmation.');
    const confirmed = parsed.data, { change, pr, pause_id, head, since } = confirmed;
    const refused = (reason: string): FactoryLandResult => ({ version: 1, accepted: false, attempt_id: null, reason });
    const state = queries.get(change), epoch = state?.generation;
    const matches = () => state?.cached && !state.cached.stale && state.cached.offer &&
      !state.cached.current && (['change', 'pr', 'pause_id', 'head', 'since'] as const)
        .every(field => state.cached!.offer![field] === confirmed[field]);
    if (!matches() || submitting.has(change)) return refused('Landing offer is unavailable or stale. Refresh status.');
    submitting.add(change);
    try {
      let runs: FactorySnapshot;
      try { runs = await readRuns(); }
      catch { return refused('Run status is unavailable. Refresh status.'); }
      active();
      const run = runs.runs.find(item => item.change === change);
      if (runs.stale || !run || run.status !== 'paused' || run.pr !== pr ||
          run.action?.pause_id !== pause_id || run.pause?.since !== since ||
          state?.generation !== epoch || !matches()) {
        return refused('Change does not match the displayed landing offer. Refresh status.');
      }
      try {
        const result = await execute(['start', change, '--pr', pr, '--pause-id', pause_id,
          // Binding the value also supports negative epoch seconds in exponent notation.
          '--head', head, `--since=${since}`, '--checks-done', '--json']);
        const outcome = factoryLandResultSchema.parse(json(result.stdout));
        if (!outcome.attempt_id || result.code !== (outcome.accepted ? 0 : 1)) throw new Error('Unexpected start outcome');
        return redact(outcome);
      } catch {
        return refused('Landing result unavailable. Refresh landing status to recover the attempt before confirming again.');
      } finally {
        // A lost response can follow successful admission. Never replay start.
        invalidate(change);
      }
    } finally { submitting.delete(change); }
  }

  async function stop(input: FactoryLandStopInput): Promise<FactoryLandStopResult> {
    active();
    const parsed = factoryLandStopInputSchema.safeParse(input);
    if (!parsed.success) throw new Error('Invalid landing Stop request.');
    const { attempt_id } = parsed.data;
    try {
      const result = await execute(['stop', attempt_id, '--json']);
      const outcome = factoryLandStopResultSchema.parse(json(result.stdout));
      if (outcome.attempt_id !== attempt_id || result.code !== (outcome.accepted ? 0 : 1)) {
        throw new Error('Unexpected Stop outcome');
      }
      return redact(outcome);
    } catch {
      return { version: 1, accepted: false, attempt_id,
        reason: 'Stop result unavailable. Refresh landing status to inspect the attempt.' };
    } finally { invalidate(); }
  }
  return { read, start, stop, dispose() { disposed = true; invalidate(); queries.clear(); } };
}

export function createFactoryLandingTransport() {
  const children = new Set<ChildProcess>();
  const execute: FactoryExecute = args => new Promise((resolve, reject) => {
    const { cwd, uv, path } = resolveHostPaths('factory');
    const child = execFile(uv, ['run', '--no-sync', 'python', '-m', 'nightshift.factory_land', ...args], {
      cwd, shell: false, timeout: 60_000, maxBuffer, env: { ...process.env, PATH: path },
    }, (error, stdout) => {
      children.delete(child);
      if (error && (error.killed || typeof error.code !== 'number')) reject(error);
      else resolve({ code: error?.code as number || 0, stdout });
    });
    children.add(child);
  });
  return { execute, dispose() {
    // Only short transport children are tracked. Python starts its worker in a
    // separate session; plugin cleanup must never signal a worker or its group.
    for (const child of children) child.kill('SIGTERM');
    children.clear();
  } };
}
const transport = createFactoryLandingTransport();
const landing = createFactoryLanding(transport.execute, readFactoryRuns);
export const readFactoryLanding = landing.read;
export const landFactoryRun = landing.start;
export const stopFactoryLanding = landing.stop;
export function disposeFactoryLanding() { landing.dispose(); transport.dispose(); }
