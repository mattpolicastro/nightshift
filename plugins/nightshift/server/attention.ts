import { execFile, type ChildProcess } from 'node:child_process';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { existsSync } from 'node:fs';
import { createReader, type Execute } from './reader';
import { resolveHostPaths } from './host-paths';

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
const reader = createReader(execute);
export const readAttention = reader.read;
export function dispose() {
  reader.dispose();
  for (const child of children) child.kill('SIGTERM');
  children.clear();
}
