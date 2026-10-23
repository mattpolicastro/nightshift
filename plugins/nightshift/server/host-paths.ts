import { homedir } from 'node:os';

// Trusted host configuration only; resolve at execution time, without probing paths.
export function resolveHostPaths(
  consumer: 'attention' | 'factory', env: NodeJS.ProcessEnv = process.env, home = homedir(),
) {
  function path(name: string, fallback: string, directory = false): string {
    let value = env[name] ?? fallback;
    if (value.startsWith('~/')) value = home + value.slice(1);
    if (!value.trim() || !value.startsWith('/') || /[\0\r\n]/.test(value) ||
        (directory && value.includes(':'))) {
      throw new Error(`Invalid host path setting: ${name}`);
    }
    return value;
  }
  const bin = path('NIGHTSHIFT_BIN_DIR', '/opt/homebrew/bin', true);
  const prefix = consumer === 'factory' ? [path('NIGHTSHIFT_NODE_BIN', '~/n/bin', true), bin] : [bin];
  return {
    cwd: path('NIGHTSHIFT_CHECKOUT', '~/Projects/nightshift'),
    uv: path('NIGHTSHIFT_UV', `${bin}/uv`),
    path: [...prefix, '/usr/bin', '/bin', '/usr/sbin', '/sbin'].join(':'),
  };
}
