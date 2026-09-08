import { execFile, type ChildProcess } from 'node:child_process';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { existsSync } from 'node:fs';
import { createReader, type Execute } from './reader.server';

// Fixed host-local configuration. RPC callers cannot choose commands or paths.
const cwd = join(homedir(), 'Projects', 'nightshift');
const uv = '/opt/homebrew/bin/uv';
const children = new Set<ChildProcess>();
const execute: Execute = (offline) => new Promise((resolve, reject) => {
  if (!existsSync(join(cwd, 'nightshift', 'outcomes.py')) ||
      !existsSync(join(homedir(), '.nightshift', 'outcomes.sqlite3'))) {
    reject(new Error('Nightshift not installed')); return;
  }
  const args = ['run', '--no-sync', 'nightshift', 'attention', '--json'];
  if (offline) args.push('--offline');
  const child = execFile(uv, args, {
    cwd, timeout: offline ? 15_000 : 60_000, maxBuffer: 2 * 1024 * 1024,
    // The daemon may have launchd's minimal PATH. The CLI loads its own auth.
    env: { ...process.env, PATH: `/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin` },
  }, (error, stdout) => {
    children.delete(child);
    if (error && (error.killed || typeof error.code !== 'number')) reject(error);
    else resolve({ code: error?.code as number || 0, stdout });
  });
  children.add(child);
});
const reader = createReader(execute);
export const readAttention = reader.read;
export function dispose() {
  reader.dispose();
  for (const child of children) child.kill('SIGTERM');
  children.clear();
}
