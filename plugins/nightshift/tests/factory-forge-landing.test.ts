import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { test, type TestContext } from 'node:test';
import { factoryLandOffer } from '../client/factory-model';
import { createFactory } from '../server/factory';
import { createFactoryLanding } from '../server/factory-landing';
import type { FactoryLandingSnapshot, FactoryRun } from '../shared/factory';
import { humanRun, landingAttempt, landingSnapshot, offer } from './factory-landing-fixtures';
import { styleView } from './factory-style-harness';

const forge = 'https://forge.example.test';
const giteaPr = `${forge}/owner/repo/pulls/7`;
const confirmation = { ...offer, checks_done: true as const };
const response = (value: unknown) => ({ code: 0, stdout: JSON.stringify(value) });

function fixture(t: TestContext) {
  const home = mkdtempSync(`${tmpdir()}/forge-landing-`), file = `${home}/origins`;
  const saved = process.env;
  process.env = { ...saved, NIGHTSHIFT_FORGE_ORIGINS_FILE: file };
  t.after(() => { process.env = saved; rmSync(home, { recursive: true, force: true }); });
  writeFileSync(file, `${forge}\n`);
  const state = {
    time: 0, runs: [humanRun] as FactoryRun[],
    current: null as ReturnType<typeof landingAttempt> | null,
    status: (change?: string): FactoryLandingSnapshot => landingSnapshot({
      offer: change ? { ...offer, change } : null,
      current: change === offer.change ? state.current : null,
    }),
  };
  const runCalls: string[][] = [], landingCalls: string[][] = [];
  const factory = createFactory(async args => {
    runCalls.push([...args]);
    assert.deepEqual(args, ['status', '--json']);
    return response({ version: 1, runs: state.runs });
  }, () => state.time, home);
  const landing = createFactoryLanding(async args => {
    landingCalls.push([...args]);
    if (args[0] === 'status') return response(state.status(args[1] === '--limit' ? undefined : args[1]));
    assert.equal(args[0], 'start');
    state.current = landingAttempt();
    return response({ version: 1, accepted: true, attempt_id: state.current.attempt_id });
  }, factory.read, () => state.time, home);
  t.after(factory.dispose);
  t.after(landing.dispose);
  return { state, factory, landing, runCalls, landingCalls,
    starts: () => landingCalls.filter(args => args[0] === 'start') };
}

test('land offered only for GitHub links', async t => {
  await t.test('paseo-runs-page/human-checks-landing/land-offered-only-for-github-links', async t => {
    await t.test('paseo-runs-page/human-checks-landing/land-action-appears-only-at-human-checks', async t => {
      const h = fixture(t);
      const gitea = { ...humanRun, change: 'gitea', pr: giteaPr };
      const { action: _action, ...unavailable } = humanRun;
      h.state.runs = [humanRun, gitea,
        { ...humanRun, change: 'other-pause', action: { ...humanRun.action!,
          pause_id: 'stuck:1', actions: ['resume', 'accept', 'abort'] } },
        ...(['running', 'done', 'aborted'] as const).map(status => ({ ...humanRun, change: status, status })),
        { ...unavailable, change: 'unavailable' },
      ];
      const ordinaryStatus = h.state.status;
      h.state.status = change => change === gitea.change
        ? landingSnapshot({ offer: { ...offer, change, pr: giteaPr } }) : ordinaryStatus(change);
      const runs = await h.factory.read();
      assert.equal(runs.stale, false);
      assert.deepEqual(runs.runs, h.state.runs, 'configured Gitea remains in the real ordinary projection');
      await assert.rejects(h.landing.read({ change: gitea.change }), /Landing status is unavailable/);
      for (const run of runs.runs) {
        const status = run.change === gitea.change ? undefined : await h.landing.read({ change: run.change });
        assert.deepEqual(factoryLandOffer(run, status, runs.stale), run.change === offer.change ? offer : null);
      }
      await assert.rejects(h.landing.start({ ...confirmation, change: gitea.change, pr: giteaPr }),
        /Invalid landing confirmation/);
      assert.deepEqual(h.starts(), []);

      const page = styleView([], { read: h.factory.read, landing: h.landing.read, land: h.landing.start });
      t.after(page.dispose);
      for (let i = 0; i < 3; i++) await page.settle();
      await page.press('Finished (2)');
      assert.deepEqual(page.nodes().filter(node => node.props.accessibilityLabel?.startsWith('Land '))
        .map(node => node.props.accessibilityLabel), [`Land ${offer.change}`]);
      const open = page.find(`Open PR for ${gitea.change}`)!;
      assert.equal(open.props.accessibilityRole, 'link');
      await page.press(open);
      assert.deepEqual(page.urls, [giteaPr]);
      assert.ok(page.find(`Finish ${gitea.change}`));
      const land = page.find(`Land ${offer.change}`)!, finish = page.find(`Finish ${offer.change}`)!;
      const parent = (child: typeof land) => page.nodes().find(node => node.children.includes(child));
      assert.notEqual(parent(land), parent(finish));
      assert.deepEqual(parent(finish)!.children.filter(child => typeof child !== 'string')
        .map(child => typeof child !== 'string' && child.props.accessibilityLabel),
      [`Finish ${offer.change}`, `Abort ${offer.change}`]);
      await page.press(`Finish ${offer.change}`);
      assert.ok(page.find(`Confirm finish ${offer.change}`));
      await page.press(page.nodes().find(node => node.type === 'Pressable' && page.text(node.children) === 'Cancel')!);
      assert.ok(page.text().includes('waiting '));
      assert.ok(page.find('Resume note for other-pause'));
      assert.equal(page.find(`Details for ${offer.change}`)!.props.accessibilityState.expanded, false);

      await page.press(`Land ${offer.change}`);
      assert.deepEqual(h.starts(), [], 'opening the dialog never starts landing');
      await page.press(`Confirm land ${offer.change}`);
      assert.deepEqual(h.starts(), [['start', offer.change, '--pr', offer.pr, '--pause-id', offer.pause_id,
        '--head', offer.head, `--since=${offer.since}`, '--checks-done', '--json']]);
      assert.deepEqual(page.lands.map(input => ({ ...input })), [confirmation]);
      assert.equal(page.find(`Land ${offer.change}`)!.props.disabled, true);
      assert.ok(page.find(`Stop landing ${offer.change}`));
      assert.equal(page.find(`Finish ${offer.change}`)!.props.disabled, false);
      assert.equal(page.find(`Land ${gitea.change}`), undefined);
      assert.equal(page.find('Finished (2)')!.props.accessibilityState.expanded, true);
      assert.deepEqual(page.actions, []);
      assert.deepEqual(page.stops, []);
      assert.deepEqual(page.hosts, []);
    });

    await t.test('cached GitHub offers cannot authorize a run changed to Gitea', async t => {
      const h = fixture(t);
      // Read landing later so its valid cache outlives the ordinary run cache.
      await h.factory.read();
      h.state.time = 5_000;
      const cached = await h.landing.read({ change: offer.change });
      const page = styleView([], { read: h.factory.read, landing: h.landing.read, land: h.landing.start });
      t.after(page.dispose);
      await page.settle();
      await page.press(`Land ${offer.change}`);
      h.state.runs = [{ ...humanRun, pr: giteaPr }];
      h.state.time = 15_000;
      const changed = await h.factory.read();
      assert.equal(changed.stale, false);
      assert.equal(changed.runs[0].pr, giteaPr);
      assert.strictEqual(await h.landing.read({ change: offer.change }), cached);
      assert.equal(cached.stale, false, 'PR identity must reject even a fresh cached offer');
      assert.equal(factoryLandOffer(changed.runs[0], cached), null);
      const refused = await h.landing.start(confirmation);
      assert.equal(refused.accepted, false);
      assert.match(refused.reason!, /does not match/);
      await assert.rejects(h.landing.start({ ...confirmation, pr: giteaPr }), /Invalid landing confirmation/);
      assert.deepEqual(h.starts(), []);
      await page.press('Refresh Factory');
      assert.equal(page.find(`Land ${offer.change}`), undefined);
      const confirm = page.find(`Confirm land ${offer.change}`)!;
      assert.equal(confirm.props.disabled, true);
      confirm.props.onPress();
      assert.deepEqual(page.lands, []);
      await page.press(`Open PR for ${offer.change}`);
      assert.deepEqual(page.urls, [giteaPr]);
      assert.equal(h.runCalls.length, 2);
      assert.equal(h.landingCalls.filter(args => args[1] === offer.change).length, 1);

      // A forged refresh is rejected and only the old GitHub snapshot is retained, stale.
      h.state.time = 20_000;
      h.state.status = change => landingSnapshot({ offer: change ? { ...offer, pr: giteaPr } : null });
      const stale = await h.landing.read({ change: offer.change });
      assert.equal(stale.stale, true);
      assert.deepEqual(stale.offer, offer);
      assert.equal(factoryLandOffer(changed.runs[0], stale), null);
      assert.equal((await h.landing.start(confirmation)).accepted, false);
      assert.equal((await h.factory.read()).stale, false, 'landing failure does not stale ordinary display');
      assert.equal((await h.landing.read()).stale, false, 'global landing query remains independent');
      assert.deepEqual(h.starts(), []);
    });

    await t.test('configured display origins cannot enter landing offers, current or history', async t => {
      for (const field of ['offer', 'current', 'attempts'] as const) await t.test(field, async t => {
        const h = fixture(t);
        h.state.runs = [{ ...humanRun, pr: giteaPr }];
        const attempt = landingAttempt({ pr: giteaPr });
        h.state.status = () => landingSnapshot({
          ...(field === 'offer' ? { offer: { ...offer, pr: giteaPr } }
            : field === 'current' ? { current: attempt } : { attempts: [attempt], total: 1 }),
        });
        assert.equal((await h.factory.read()).runs[0].pr, giteaPr);
        await assert.rejects(h.landing.read({ change: offer.change }), /Landing status is unavailable/);
        await assert.rejects(h.landing.start({ ...confirmation, pr: giteaPr }), /Invalid landing confirmation/);
        assert.deepEqual(h.starts(), []);
      });
    });
  });
});
