import { z } from 'zod';
import type { Snapshot, Task } from './attention.shared';

const sourceTask = z.object({
  repo: z.string().regex(/^[\w.-]+\/[\w.-]+$/), issue: z.number().int().positive(),
  title: z.string().optional(), summary: z.string().optional(), reason: z.string().optional(),
  resolution: z.string().nullable().optional(),
  state: z.enum(['awaiting_merge', 'needs_decision', 'resolved']),
  pr_url: z.string().optional(), issue_url: z.string(),
  checked_at: z.string().nullable(), error: z.string().nullable(),
});
const sourceSchema = z.object({
  version: z.literal(1), offline: z.boolean(),
  tasks: z.array(sourceTask), stale: z.array(sourceTask),
});

function project(item: z.infer<typeof sourceTask>): Task {
  const url = item.pr_url || item.issue_url;
  // Links are navigation, never arbitrary schemes or third-party commands.
  const parsed = new URL(url);
  if (parsed.origin !== 'https://github.com' || parsed.username || parsed.password ||
      !parsed.pathname.startsWith(`/${item.repo}/`)) throw new Error('Unexpected task link');
  return {
    key: `${item.repo}#${item.issue}`, repo: item.repo, issue: item.issue,
    title: item.title || `Issue #${item.issue}`,
    summary: (item.resolution || item.summary || item.reason || '').replace(/\s+/g, ' ').slice(0, 400),
    state: item.state, url, checkedAt: item.checked_at, stale: Boolean(item.error),
  };
}

export type Execute = (offline: boolean) => Promise<{ code: number; stdout: string }>;

export function createReader(execute: Execute, clock = () => Date.now()) {
  let cached: Snapshot | undefined;
  let inFlight: Promise<Snapshot> | undefined;
  let lastRefresh = -Infinity;
  let disposed = false;

  async function load(refresh: boolean): Promise<Snapshot> {
    try {
      const result = await execute(!refresh);
      // Exit 1 is Nightshift's valid stale-data response, not an empty list.
      if (result.code !== 0 && result.code !== 1) throw new Error('Command failed');
      const raw = sourceSchema.parse(JSON.parse(result.stdout));
      if (raw.offline !== !refresh) throw new Error('Unexpected refresh mode');
      const tasks = raw.tasks.map(project);
      const stale = raw.stale.map(project);
      const snapshot: Snapshot = {
        tasks, stale, cached: raw.offline, fetchedAt: new Date(clock()).toISOString(),
        error: stale.length || result.code === 1 ? 'Some outcomes could not be checked. Last known outcomes are shown.' : null,
      };
      if (!disposed) cached = snapshot;
      return snapshot;
    } catch {
      // Never forward CLI stderr, config paths or credential-bearing diagnostics.
      if (cached) {
        const snapshot = { ...cached, cached: true, error: 'Refresh unavailable. Showing the last loaded list.' };
        if (!disposed) cached = snapshot;
        return snapshot;
      }
      throw new Error('Nightshift is unavailable on this host. Check the installation on the Mac Studio.');
    }
  }

  async function read({ refresh }: { refresh: boolean }): Promise<Snapshot> {
    if (disposed) throw new Error('Nightshift plugin stopped');
    if (inFlight) return inFlight;
    if (cached && (!refresh || clock() - lastRefresh < 30_000)) return cached;
    if (refresh) lastRefresh = clock();
    const pending = load(refresh);
    inFlight = pending;
    try { return await pending; }
    finally { if (inFlight === pending) inFlight = undefined; }
  }
  return { read, dispose() { disposed = true; cached = undefined; } };
}
