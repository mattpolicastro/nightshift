import { homedir } from 'node:os';

function literalPath(name: string, value: string, home: string, directory = false): string {
  if (value.startsWith('~/')) value = home + value.slice(1);
  if (!value.trim() || !value.startsWith('/') || /[\0\r\n]/.test(value) ||
      (directory && value.includes(':'))) {
    throw new Error(`Invalid host path setting: ${name}`);
  }
  return value;
}

export function resolveForgeOriginsFile(env: NodeJS.ProcessEnv = process.env, home = homedir()): string {
  return literalPath('NIGHTSHIFT_FORGE_ORIGINS_FILE',
    env.NIGHTSHIFT_FORGE_ORIGINS_FILE ?? '~/.config/nightshift/forge-origins', home);
}

// Trusted host configuration only; resolve at execution time, without probing paths.
export function resolveHostPaths(
  consumer: 'attention' | 'factory', env: NodeJS.ProcessEnv = process.env, home = homedir(),
) {
  function path(name: string, fallback: string, directory = false): string {
    return literalPath(name, env[name] ?? fallback, home, directory);
  }
  const bin = path('NIGHTSHIFT_BIN_DIR', '/opt/homebrew/bin', true);
  const prefix = consumer === 'factory' ? [path('NIGHTSHIFT_NODE_BIN', '~/n/bin', true), bin] : [bin];
  return {
    cwd: path('NIGHTSHIFT_CHECKOUT', '~/Projects/nightshift'),
    uv: path('NIGHTSHIFT_UV', `${bin}/uv`),
    path: [...prefix, '/usr/bin', '/bin', '/usr/sbin', '/sbin'].join(':'),
  };
}
