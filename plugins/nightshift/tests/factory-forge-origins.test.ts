import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { createFactory } from '../server/factory';
import { createFactoryLanding } from '../server/factory-landing';
import { resolveForgeOriginsFile, resolveHostPaths } from '../server/host-paths';
import type { FactoryRun } from '../shared/factory';
import { decideFactoryAction } from '../client/factory-model';
import { styleView } from './factory-style-harness';

const forge = 'https://forge.example.test', github = 'https://github.com';
const unavailable = { message: 'Factory status is unavailable on this host. Try again shortly.' };
const stale = { stale: true, error: 'Refresh unavailable. Showing the last loaded list.' };
const row = (pr = `${github}/owner/repo/pull/7`, link = ''): FactoryRun => ({
  change: 'forge-change', status: 'paused', tasks: { done: 1, total: 2 }, pr,
  context: { summary: '', repo: '', branch: '', task: null, detail: '', recent: [], github: link },
  action: { kind: 'action', headline: '', why: '', command: '', actions: ['resume'] },
});
const response = (value: unknown) => ({ code: 0, stdout: JSON.stringify(value) });

test('manifest-selected publication uses the existing confirmation and transport', async t => {
  for (const pr of ['', `${github}/owner/repo/pull/7`, `${forge}/owner/repo/pulls/7`]) {
    const scenario = pr.startsWith(forge)
      ? 'paseo-runs-page/forge-publication-confirmation/gitea-uses-the-shared-action-path'
      : 'paseo-runs-page/forge-publication-confirmation/confirmation-works-before-a-pr-exists';
    for (const verb of ['publish', 'finish'] as const) await t.test(`${scenario}: ${verb} ${pr}`, async t => {
      const h = fixture(t);
      h.write(forge);
      const run = row(pr);
      run.action = { ...run.action!, actions: [verb], pause_id: `${verb}:7` };
      const calls: string[][] = [];
      const service = createFactory(async args => {
        calls.push(args);
        return response(args[0] === 'status' ? { version: 1, runs: [run] }
          : { version: 1, outcome: 'done', verb, detail: 'Completed' });
      }, () => 0, h.home);
      const snapshot = await service.read();
      assert.equal(snapshot.runs[0].pr, pr);
      const request = { change: run.change, verb, expect: `${verb}:7` };
      const label = verb === 'publish' ? 'Publish' : 'Finish';
      const message = `${label} ${run.change}? This pushes to the forge selected by the run manifest.`;
      assert.deepEqual(decideFactoryAction(snapshot.runs[0], verb), { kind: 'confirm', request, message });
      assert.deepEqual(decideFactoryAction(snapshot.runs[0], verb, '', true), { kind: 'send', request });
      const page = styleView(snapshot.runs, { act: input => service.act(input) });
      t.after(() => { page.dispose(); service.dispose(); });
      await page.settle();
      await page.press(`${label} ${run.change}`);
      assert.ok(page.nodes().some(node => node.props.accessibilityRole === 'alert' && page.text(node.children) === message));
      await page.press(page.nodes().find(node => node.type === 'Pressable' && page.text(node.children) === 'Cancel')!);
      assert.deepEqual(page.actions, []);
      assert.deepEqual(calls, [['status', '--json']]);
      await page.press(`${label} ${run.change}`);
      await page.press(`Confirm ${verb} ${run.change}`);
      assert.deepEqual(page.actions, [request]);
      assert.deepEqual(calls, [['status', '--json'], ['act', run.change, verb, `--expect=${verb}:7`, '--json']]);
      assert.deepEqual(page.lands, []);
    });
  }
});

function fixture(t: TestContext) {
  const home = mkdtempSync(`${tmpdir()}/forge-origins-`), file = `${home}/origins`;
  const saved = process.env;
  process.env = { ...saved, NIGHTSHIFT_FORGE_ORIGINS_FILE: file };
  t.after(() => { process.env = saved; rmSync(home, { recursive: true, force: true }); });
  const state = { time: 0, calls: [] as string[][], runs: [row()] };
  const factory = () => createFactory(async args => {
    state.calls.push(args);
    return response(args[0] === 'status' ? { version: 1, runs: state.runs }
      : { version: 1, outcome: 'done', verb: 'resume', detail: 'Resumed' });
  }, () => state.time, home);
  return { home, file, state, factory, write: (text: string) => writeFileSync(file, text) };
}

test('host-paths/factory-forge-origins-file-path/default-and-overridden-origins-paths', async t => {
  const h = fixture(t);
  assert.equal(resolveForgeOriginsFile({}, h.home), `${h.home}/.config/nightshift/forge-origins`);
  for (const value of ['/absent//../origins:copy $HOME $(touch nope); `whoami` * ', '~/origins:copy $HOME *']) {
    assert.equal(resolveForgeOriginsFile({ NIGHTSHIFT_FORGE_ORIGINS_FILE: value }, h.home),
      value.startsWith('~/') ? h.home + value.slice(1) : value);
  }
  mkdirSync(`${h.home}/.config/nightshift`, { recursive: true });
  for (const value of [undefined, h.file, '~/origins:copy $HOME $(touch nope); `whoami` *']) {
    if (value === undefined) delete process.env.NIGHTSHIFT_FORGE_ORIGINS_FILE;
    else process.env.NIGHTSHIFT_FORGE_ORIGINS_FILE = value;
    writeFileSync(resolveForgeOriginsFile(process.env, h.home), forge);
    h.state.runs = [row(`${forge}/o/r/pulls/7`)];
    assert.equal((await h.factory().read()).stale, false);
    for (const consumer of ['attention', 'factory'] as const) {
      assert.deepEqual(resolveHostPaths(consumer, { NIGHTSHIFT_FORGE_ORIGINS_FILE: value }, h.home),
        resolveHostPaths(consumer, {}, h.home));
    }
  }
});

test('host-paths/factory-forge-origins-file-path/invalid-origins-paths-refuse-status', async t => {
  const h = fixture(t), service = h.factory(), before = await service.read();
  for (const value of ['', ' \t', 'relative', '~', '~other/file', '/bad\0path', '/bad\rpath', '/bad\npath']) {
    process.env.NIGHTSHIFT_FORGE_ORIGINS_FILE = value;
    assert.throws(() => resolveForgeOriginsFile(process.env, h.home),
      { message: 'Invalid host path setting: NIGHTSHIFT_FORGE_ORIGINS_FILE' });
    const calls = h.state.calls.length;
    // RPC-supplied configuration cannot rescue an invalid trusted setting.
    const input = { refresh: true, NIGHTSHIFT_FORGE_ORIGINS_FILE: h.file, origins: [forge] };
    await assert.rejects(h.factory().read(input), unavailable);
    h.state.time += 15_000;
    assert.deepEqual(await service.read(input), { ...before, ...stale });
    assert.equal(h.state.calls.length, calls);
    for (const consumer of ['attention', 'factory'] as const) {
      assert.deepEqual(resolveHostPaths(consumer, { NIGHTSHIFT_FORGE_ORIGINS_FILE: value }, h.home),
        resolveHostPaths(consumer, {}, h.home));
    }
    assert.equal((await service.act({ change: 'forge-change', verb: 'resume' })).outcome, 'done');
  }
  const landing = createFactoryLanding(async () => response({
    version: 1, attempts: [], total: 0, diagnostics: [], truncated: false,
  }), async () => { throw new Error('Unused'); });
  assert.equal((await landing.read()).stale, false);
});

test('paseo-runs-page/configured-forge-links/accepts-configured-forge-links', async t => {
  await t.test('accepts configured forge links', async t => {
    const h = fixture(t);
    for (const newline of ['\n', '\r\n']) {
      h.write(['', ' \t', '  # operator comment', forge, `${forge}:8443`, github, forge, ''].join(newline));
      h.state.runs = [row(), row(`${forge}/owner/repo/pulls/7`, `${forge}/owner/repo`),
        row(`${forge}:8443/o/r/pulls/007`, `${forge}:8443/tree/main?q=1#detail`), row('')];
      assert.deepEqual(await h.factory().read(), {
        runs: h.state.runs, stale: false, error: null, fetchedAt: new Date(0).toISOString(),
      });
    }
  });
});

test('run-context/configured-forge-context-links/configured-context-destination-retained', async t => {
  const h = fixture(t);
  h.write(forge);
  for (const [link, expected] of [
    [`${forge}/owner/repo/tree/main${h.home}/change?q=1#detail`, `${forge}/owner/repo/tree/main~/change?q=1#detail`],
    [`${github}/o/r?query#fragment`, `${github}/o/r?query#fragment`],
    ['not a URL', ''], [`https://user:pass@forge.example.test/tree/main`, ''],
    [`http://forge.example.test/tree/main`, ''], [`${forge}:8443/tree/main`, ''],
    ['https://foreign.test/tree/main', ''],
  ]) {
    h.state.runs = [row(undefined, link)];
    h.state.runs[0].context!.repo = `${h.home}/repo`;
    const snapshot = await h.factory().read();
    assert.equal(snapshot.stale, false);
    assert.equal(snapshot.runs[0].context!.github, expected);
    assert.equal(snapshot.runs[0].context!.repo, '~/repo');
    assert.ok(!JSON.stringify(snapshot).includes(h.home));
  }
});

test('paseo-runs-page/configured-forge-links/rejects-foreign-origins', async t => {
  await t.test('rejects foreign origins', async t => {
    const h = fixture(t);
    h.write(`${forge}\n${github}`);
    const bad = ['https://foreign.test', `${forge}.evil.test`, 'https://sub.forge.example.test', `${forge}:8443`]
      .map(origin => `${origin}/o/r/pulls/7`);
    for (const [origin, shape] of [[github, 'pull'], [forge, 'pulls']]) {
      bad.push(...['/o/r/issues/7', `/o/r/${shape}/x`, `/o/r/${shape}/-1`, `/o/r/${shape}/1.5`,
        `/o/r/${shape}/7/`, `//r/${shape}/7`, `/o//${shape}/7`, `/o/x/r/${shape}/7`]
        .map(path => origin + path), `${origin}/o/r/${shape}/7?q=1`, `${origin}/o/r/${shape}/7#detail`,
        `${origin.replace('https:', 'http:')}/o/r/${shape}/7`,
        `${origin.replace('https://', 'https://user:pass@')}/o/r/${shape}/7`);
    }
    bad.push(`${github}/o/r/pulls/7`, `${forge}/o/r/pull/7`, 'not a URL');
    for (const pr of bad) {
      h.state.runs = [row()];
      const service = h.factory(), before = await service.read();
      h.state.runs.push(row(pr));
      await assert.rejects(h.factory().read(), unavailable);
      h.state.time += 15_000;
      assert.deepEqual(await service.read(), { ...before, ...stale }, pr);
    }
    h.write('');
    h.state.runs = [row(`${forge}/o/r/pulls/7`)];
    await assert.rejects(h.factory().read({ refresh: true, ...{ origins: [forge], NIGHTSHIFT_FORGE_ORIGINS_FILE: h.file } }), unavailable);
  });
});

test('paseo-runs-page/configured-forge-links/invalid-origins-file-allows-github-only', async t => {
  await t.test('invalid origins file allows GitHub only', async t => {
    const h = fixture(t);
    const invalid = ['http://forge.example.test', `${forge}/`, `${forge}/path`, `${forge}?q=1`, `${forge}#f`,
      'https://user:pass@forge.example.test', ` ${forge}`, `${forge}\t`, 'https://FORGE.example.test',
      'HTTPS://forge.example.test', `${forge}:443`, `${forge} # inline`, `${forge}\r\r`, `${forge}\0`,
      `${forge}\rhttps://other.test`, 'not a URL'];
    const contents = ['', ' \t\n # comments only', ...invalid.flatMap(line => [`${forge}\n${line}`, `${line}\n${forge}`])];
    for (const content of [...contents, null, 'directory']) {
      rmSync(h.file, { force: true, recursive: true });
      if (content === 'directory') mkdirSync(h.file); // Deterministic read failure, including privileged test hosts.
      else if (content !== null) h.write(content);
      h.state.runs = [row(undefined, `${github}/o/r?q=1#detail`), row(undefined, `${forge}/o/r`)];
      const snapshot = await h.factory().read();
      assert.equal(snapshot.stale, false);
      assert.deepEqual(snapshot.runs, [h.state.runs[0], row()]);
      h.state.runs.push(row(`${forge}/o/r/pulls/7`));
      await assert.rejects(h.factory().read(), unavailable);
    }
  });
});

test('origin files reload per uncached status, with shared loads and no retained extras', async t => {
  const h = fixture(t), service = h.factory();
  h.state.runs = [row(undefined, `${forge}/o/r`)];
  const missing = await service.read();
  assert.equal(missing.runs[0].context!.github, '');
  h.write(forge);
  h.state.time = 14_999;
  assert.deepEqual(await service.read({ refresh: true }), missing);
  assert.equal(h.state.calls.length, 1);
  h.state.time++;
  const [first, second] = await Promise.all([service.read(), service.read({ refresh: true })]);
  assert.deepEqual(first, second);
  assert.equal(first.runs[0].context!.github, `${forge}/o/r`);
  assert.equal(h.state.calls.length, 2);
  for (const content of [`${forge}\ninvalid`, forge, null]) {
    if (content === null) rmSync(h.file); else h.write(content);
    h.state.time += 15_000;
    assert.equal((await service.read()).runs[0].context!.github, content === forge ? `${forge}/o/r` : '');
  }
  h.write(forge);
  h.state.runs = [row(`${forge}/o/r/pulls/7`)];
  h.state.time += 15_000;
  const before = await service.read();
  h.write('invalid');
  h.state.time += 15_000;
  assert.deepEqual(await service.read(), { ...before, ...stale });
  await assert.rejects(h.factory().read(), unavailable);
  h.write(forge);
  const during = createFactory(async () => {
    h.write('invalid'); // Both PR and context must use the file read before command execution.
    return response({ version: 1, runs: [row(`${forge}/o/r/pulls/7`, `${forge}/tree/main`)] });
  }, () => 0, h.home);
  assert.equal((await during.read()).runs[0].context!.github, `${forge}/tree/main`);
});

test('the server-owned loader runs once per shared load and is throttled after cold failures', async () => {
  let time = 0, loads = 0, commands = 0, fail = true;
  const service = createFactory(async () => {
    commands++;
    return response({ version: 1, runs: [row(`${forge}/o/r/pulls/7`)] });
  }, () => time, '/Users/test', () => {
    loads++;
    if (fail) throw new Error('/private/host-setting');
    return new Set([github, forge]);
  });
  await assert.rejects(service.read(), unavailable);
  time = 14_999;
  await assert.rejects(service.read({ refresh: true }), unavailable);
  assert.deepEqual([loads, commands], [1, 0]);
  time++; fail = false;
  const [first, second] = await Promise.all([service.read(), service.read({ refresh: true })]);
  assert.equal(first.stale, false);
  assert.deepEqual(first, second);
  assert.deepEqual(await service.read({ refresh: true }), first);
  assert.deepEqual([loads, commands], [2, 1]);
});
