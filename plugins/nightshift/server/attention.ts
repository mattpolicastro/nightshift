import { execFile, type ChildProcess } from 'node:child_process';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { existsSync } from 'node:fs';
import { z } from 'zod';
import { createReader, type Execute } from './reader';
import { resolveHostPaths } from './host-paths';
import type { AttentionSnapshot, Escalation, Snapshot } from '../shared/attention';

// CLI diagnostics are fixed phrases; anything path-like or multi-line is malformed, never forwarded.
const safeText = z.string().max(400).regex(/^[^\u0000-\u001f\u007f/\\]*$/);
const code = z.enum(['read_failed', 'timeout', 'invalid_response', 'budget_exhausted', 'unsupported_forge', 'not_checked']);
const repoName = z.string().regex(/^[\w.-]+\/[\w.-]+$/);
const sourceEntry = z.object({
  repo: repoName, number: z.number().int().positive(), kind: z.enum(['issue', 'pr']),
  title: z.string(), url: z.string(), summary: z.string().max(400).nullable(),
  summary_checked_at: z.string().nullable(), summary_stale: z.boolean(),
  summary_error: safeText.nullable(), summary_error_code: code.nullable(),
});
const sourceRepository = z.object({
  repo: repoName, label: z.string(), entries: z.array(sourceEntry), checked_at: z.string().nullable(),
  stale: z.boolean(), error: safeText.nullable(), error_code: code.nullable(),
});
const sourcePause = z.object({
  change: z.string().min(1), reason: z.string(), checked_at: z.string().nullable(), stale: z.boolean(),
});
const envelope = z.object({ version: z.literal(1), offline: z.boolean() });
// Worker `tasks`/`stale` are deliberately not read: they no longer feed attention.
const sourceSchema = envelope.extend({
  repositories: z.array(sourceRepository), specloop: z.array(sourcePause), specloop_error: z.string().nullable(),
});

type Display = Omit<AttentionSnapshot, keyof Snapshot | 'complete'> & {
  error: string | null; checked: boolean;
};
export const partialError = 'Some attention sources could not be checked. Last known data is shown.';
export const updateError = 'Front-door attention is unavailable. Update Nightshift on the host Mac.';
const pausesError = 'Factory status could not be checked. Last known pauses are shown.';

function escalation(repo: string, item: z.infer<typeof sourceEntry>): Escalation {
  // Links are navigation to exactly this repository item, never arbitrary schemes or hosts.
  const url = new URL(item.url);
  const path = `/${repo}/${item.kind === 'pr' ? 'pull' : 'issues'}/${item.number}`;
  if (item.repo !== repo || url.protocol !== 'https:' || url.host !== 'github.com' || url.username ||
      url.password || /[?#]/.test(item.url) || url.pathname.toLowerCase() !== path.toLowerCase()) {
    throw new Error('Unexpected escalation link');
  }
  return {
    key: `${repo}#${item.number}`, repo, number: item.number, kind: item.kind, title: item.title,
    url: url.href, summary: item.summary, summaryCheckedAt: item.summary_checked_at,
    summaryStale: item.summary_stale, summaryError: item.summary_error,
  };
}

function project(code: number, stdout: string, offline: boolean): Display {
  const document = JSON.parse(stdout);
  if (envelope.parse(document).offline !== offline) throw new Error('Unexpected refresh mode');
  if (!('repositories' in document)) {
    // One state for a producer that predates front-door attention; its worker rows are ignored.
    return { repositories: [], pauses: [], frontDoorError: updateError, pausesError: null, error: updateError, checked: false };
  }
  const raw = sourceSchema.parse(document);
  const repositories = raw.repositories.map(repo => ({
    repo: repo.repo, label: repo.label, entries: repo.entries.map(item => escalation(repo.repo, item)),
    checkedAt: repo.checked_at, stale: repo.stale, error: repo.error,
  }));
  const pauses = raw.specloop.map(p => ({ change: p.change, reason: p.reason, checkedAt: p.checked_at, stale: p.stale }));
  const failed = code === 1 || raw.specloop_error !== null || repositories.some(r => r.stale || r.entries.some(e => e.summaryStale));
  return {
    repositories, pauses, frontDoorError: null, pausesError: raw.specloop_error === null ? null : pausesError,
    error: failed ? partialError : null, checked: !failed,
  };
}

export function createAttention(execute: Execute, clock = () => Date.now()) {
  let display: Display | undefined;
  let disposed = false;
  // The reader stays the only cache, throttle and shared load; it sees an empty legacy envelope.
  const reader = createReader(async offline => {
    const result = await execute(offline);
    if (result.code !== 0 && result.code !== 1) throw new Error('Command failed');
    const next = project(result.code, result.stdout, offline);
    if (!disposed) display = next;
    return { code: 0, stdout: JSON.stringify({ version: 1, offline, tasks: [], stale: [] }) };
  }, clock);

  async function read({ refresh }: { refresh: boolean }): Promise<AttentionSnapshot> {
    const snapshot = await reader.read({ refresh });
    // A load that finishes after disposal resolves without republishing any data.
    const { checked, error, ...fields } = display ?? {
      repositories: [], pauses: [], frontDoorError: null, pausesError: null, error: 'Nightshift plugin stopped', checked: false,
    };
    // A reader fallback keeps its transport wording and original fetched time.
    return {
      ...snapshot, ...fields, tasks: [], stale: [], error: snapshot.error ?? error,
      complete: checked && !snapshot.cached && snapshot.error === null,
    };
  }
  return { read, dispose() { disposed = true; display = undefined; reader.dispose(); } };
}

const children = new Set<ChildProcess>();
const execute: Execute = (offline) => new Promise((resolve, reject) => {
  const { cwd, uv, path } = resolveHostPaths('attention');
  // Preserve symlink/.. semantics so this check uses the same checkout as execFile.
  if (!existsSync(`${cwd}/nightshift/outcomes.py`) ||
      !existsSync(join(homedir(), '.nightshift', 'outcomes.sqlite3'))) {
    reject(new Error('Nightshift not installed')); return;
  }
  const args = ['run', '--no-sync', 'nightshift', 'attention', '--json'];
  if (offline) args.push('--offline');
  const child = execFile(uv, args, {
    cwd, shell: false, timeout: offline ? 15_000 : 60_000, maxBuffer: 2 * 1024 * 1024,
    // The daemon may have launchd's minimal PATH. The CLI loads its own auth.
    env: { ...process.env, PATH: path },
  }, (error, stdout) => {
    children.delete(child);
    if (error && (error.killed || typeof error.code !== 'number')) reject(error);
    else resolve({ code: error?.code as number || 0, stdout });
  });
  children.add(child);
});
const adapter = createAttention(execute);
export const readAttention = adapter.read;
export function dispose() {
  adapter.dispose();
  for (const child of children) child.kill('SIGTERM');
  children.clear();
}
