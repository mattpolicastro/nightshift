import { getPaseoClient, openExternalUrl, type PluginSurfaceProps, useRpc } from '@getpaseo/plugin/client';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Pressable, Text, TextInput, View } from 'react-native';
import {
  factoryRuns, factoryAct, type FactoryRun, type FactorySnapshot, type FactoryActInput,
  factoryLanding, factoryLand, factoryLandStop, type FactoryLandingSnapshot,
  type FactoryLandingOffer, type FactoryLandInput, type FactoryLandingAttempt,
} from '../shared/factory';
import { arrangeFactoryRuns, type FactorySort, decideFactoryAction, factoryButtons, factoryDetails, factoryWaitingTime, groupFactoryRuns, matchWorkspace, type ActionDecision,
  factoryLandOffer, sameLandingOffer, landingAttemptNotice, retainLandingNotices, type LandingNotice } from './factory-model';

type Colors = PluginSurfaceProps['theme']['colors'];
type Confirmation = Extract<ActionDecision, { kind: 'confirm' }>;
type Workspaces = Awaited<ReturnType<ReturnType<typeof getPaseoClient>['workspaces']['list']>>['entries'];
type HostProps = Pick<PluginSurfaceProps, 'host' | 'navigation'>;

function actionStyles(colors: Colors, role: 'primary' | 'destructive' | 'outlined') {
  const primary = role === 'primary';
  const destructive = role === 'destructive';
  return {
    button: {
      minHeight: 44, minWidth: 44, paddingHorizontal: 14, paddingVertical: 10,
      borderRadius: 8, borderWidth: 1, alignItems: 'center' as const, justifyContent: 'center' as const,
      backgroundColor: primary ? colors.accent : colors.surface1,
      borderColor: destructive ? colors.statusDanger : primary ? colors.accent : colors.border,
    },
    text: {
      color: destructive ? colors.statusDanger : primary ? colors.accentForeground : colors.foreground,
      fontWeight: '600' as const,
    },
  };
}

function LandingNotices({ notices, colors, named = false }: { notices: LandingNotice[]; colors: Colors; named?: boolean }) {
  return <>{notices.map(notice => <Text key={notice.id} accessibilityRole="alert" style={{ color: colors.foreground }}>
    {`${named ? `${notice.change}: ` : ''}${[notice.text, ...notice.messages].join(' · ')}`}
  </Text>)}</>;
}

function FactoryRow({ run, colors, disabled, act, report, notice, host, navigation, workspaces, onDetailsOpen,
  offer, currentLanding, landingBusy, land, stopLanding, landingNotices }: {
  run: FactoryRun; colors: Colors; disabled: boolean;
  act: (input: FactoryActInput) => Promise<void>; report: (message: string) => void; notice: string | null;
  workspaces: Workspaces; onDetailsOpen: () => void;
  offer: FactoryLandingOffer | null; currentLanding: FactoryLandingAttempt | null; landingBusy: boolean;
  land: (input: FactoryLandInput) => Promise<void>; stopLanding: (attempt: FactoryLandingAttempt) => Promise<void>;
  landingNotices: LandingNotice[];
} & HostProps) {
  const [note, setNote] = useState('');
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [landConfirmation, setLandConfirmation] = useState<FactoryLandingOffer | null>(null);
  const invalidLandConfirmation = useRef(false);
  if (landConfirmation && (!sameLandingOffer(landConfirmation, offer) || currentLanding)) {
    invalidLandConfirmation.current = true;
  }
  const landStale = invalidLandConfirmation.current;
  const landStyles = actionStyles(colors, 'outlined');
  const confirmLandStyles = actionStyles(colors, 'primary');
  const buttons = factoryButtons(run);
  const details = factoryDetails(run);
  const waiting = factoryWaitingTime(run);
  const workspace = matchWorkspace(run.context?.repo ?? '', workspaces);
  const showChangeLink = detailsOpen && Boolean(details.github);
  const showWorkspaceLink = detailsOpen && workspace !== null && typeof navigation?.openWorkspace === 'function';
  const confirmStyles = actionStyles(colors, confirmation?.request.verb === 'abort' ? 'destructive' : 'primary');
  const cancelStyles = actionStyles(colors, 'outlined');
  const statusColor = {
    paused: colors.statusWarning, running: colors.accent,
    done: colors.statusSuccess, aborted: colors.foregroundMuted,
  }[run.status];
  const statusProgress = <Text style={{ color: colors.foregroundMuted }}>
    <Text style={{ color: statusColor, backgroundColor: colors.surface2, borderRadius: 6,
      paddingHorizontal: 6, paddingVertical: 2, fontWeight: '600' }}>{run.status}</Text>
    {' · '}{run.tasks.done}/{run.tasks.total} tasks done
  </Text>;

  function choose(verb: FactoryActInput['verb'], confirmed = false) {
    if (disabled) return;
    const decision = decideFactoryAction(run, verb, note, confirmed);
    if (decision.kind === 'confirm') setConfirmation(decision);
    else {
      setConfirmation(null);
      if (decision.kind === 'send') {
        if (decision.request.verb === 'resume') setNote('');
        void act(decision.request);
      }
      else report('This action is no longer available. Refresh the list.');
    }
  }

  return <View style={{ padding: 16, gap: 8, borderWidth: 1, borderColor: colors.border, borderRadius: 12, backgroundColor: colors.surface1 }}>
    <Text style={{ color: colors.foreground, fontSize: 17, fontWeight: '600' }}>{run.change}</Text>
    {waiting ? <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {statusProgress}
      <Text style={{ color: waiting.warning ? colors.statusWarning : colors.foregroundMuted }}>{waiting.text}</Text>
    </View> : statusProgress}
    {run.action ? <>
      <Text style={{ color: colors.foreground, fontWeight: '600' }}>{run.action.headline}</Text>
      <Text style={{ color: colors.foreground }}>{run.action.why}</Text>
    </> : null}
    {buttons.some(button => button.verb === 'resume') ? <View style={{ gap: 4 }}>
      <Text style={{ color: colors.foregroundMuted }}>Resume note (optional)</Text>
      <TextInput accessibilityLabel={`Resume note for ${run.change}`} value={note} onChangeText={setNote}
        editable={!disabled} multiline maxLength={2000} placeholder="Guidance for the next attempt"
        placeholderTextColor={colors.foregroundMuted}
        style={{ color: colors.foreground, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 12, minHeight: 72 }} />
    </View> : null}
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {buttons.map((button, index) => {
        const styles = actionStyles(colors, button.verb === 'abort' ? 'destructive' : index === 0 ? 'primary' : 'outlined');
        return <Pressable key={button.verb} accessibilityRole="button"
          accessibilityLabel={`${button.label} ${run.change}`} disabled={disabled || confirmation !== null}
          onPress={() => choose(button.verb)} style={{ ...styles.button, opacity: disabled || confirmation ? 0.5 : 1 }}>
          <Text style={styles.text}>{button.label}</Text>
        </Pressable>;
      })}
    </View>
    {offer || currentLanding ? <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {offer ? <Pressable accessibilityRole="button" accessibilityLabel={`Land ${run.change}`}
        disabled={disabled || landingBusy || Boolean(currentLanding) || landConfirmation !== null}
        onPress={() => { invalidLandConfirmation.current = false; setLandConfirmation(Object.freeze({ ...offer })); }}
        style={landStyles.button}><Text style={landStyles.text}>Land</Text></Pressable> : null}
      {currentLanding ? <Pressable accessibilityRole="button" accessibilityLabel={`Stop landing ${run.change}`}
        disabled={landingBusy} onPress={() => { void stopLanding(currentLanding); }} style={landStyles.button}>
        <Text style={landStyles.text}>Stop landing</Text>
      </Pressable> : null}
    </View> : null}
    {landConfirmation ? <View style={{ gap: 8 }}>
      <Text accessibilityRole="alert" style={{ color: colors.foreground }}>
        {`Land ${landConfirmation.change}? Have you performed the human checks for ${landConfirmation.pr} at head ${landConfirmation.head}? Confirm land to finish and merge this PR into its base branch.`}
      </Text>
      {landStale ? <Text accessibilityRole="alert" style={{ color: colors.statusWarning }}>
        This landing offer is stale. Cancel and refresh status before confirming again.
      </Text> : null}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        <Pressable accessibilityRole="button" accessibilityLabel={`Confirm land ${run.change}`}
          disabled={disabled || landingBusy || landStale || Boolean(currentLanding)}
          onPress={() => {
            if (disabled || landingBusy || invalidLandConfirmation.current || currentLanding ||
                !sameLandingOffer(landConfirmation, offer)) return;
            setLandConfirmation(null);
            void land({ ...landConfirmation, checks_done: true });
          }} style={confirmLandStyles.button}><Text style={confirmLandStyles.text}>Confirm land</Text></Pressable>
        <Pressable accessibilityRole="button" onPress={() => setLandConfirmation(null)} style={cancelStyles.button}>
          <Text style={cancelStyles.text}>Cancel</Text>
        </Pressable>
      </View>
    </View> : null}
    {confirmation ? <View style={{ gap: 8 }}>
      <Text accessibilityRole="alert" style={{ color: colors.foreground }}>{confirmation.message}</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        <Pressable accessibilityRole="button" accessibilityLabel={`Confirm ${confirmation.request.verb} ${run.change}`}
          disabled={disabled} onPress={() => choose(confirmation.request.verb, true)} style={confirmStyles.button}>
          <Text style={confirmStyles.text}>Confirm {confirmation.request.verb}</Text>
        </Pressable>
        <Pressable accessibilityRole="button" onPress={() => setConfirmation(null)} style={cancelStyles.button}>
          <Text style={cancelStyles.text}>Cancel</Text>
        </Pressable>
      </View>
    </View> : null}
    {run.pr || showChangeLink || showWorkspaceLink ? <View style={{
      flexDirection: 'row', flexWrap: 'wrap', gap: 12,
      borderTopWidth: 1, borderColor: colors.border, paddingTop: 8,
    }}>
      {run.pr ? <Pressable accessibilityRole="link" accessibilityLabel={`Open PR for ${run.change}`}
        onPress={() => { void openExternalUrl(run.pr).catch(() => report('Could not open GitHub.')); }}
        style={{ paddingVertical: 10, alignSelf: 'flex-start' }}>
        <Text style={{ color: colors.accent, fontWeight: '600', textDecorationLine: 'underline' }}>Open PR ↗</Text>
      </Pressable> : null}
      {showChangeLink ? <Pressable accessibilityRole="link" accessibilityLabel={`Open change ${run.change} on GitHub`}
        onPress={() => { void openExternalUrl(details.github).catch(() => report('Could not open GitHub.')); }}
        style={{ paddingVertical: 10, alignSelf: 'flex-start' }}>
        <Text style={{ color: colors.accent, fontWeight: '600', textDecorationLine: 'underline' }}>Open change on GitHub ↗</Text>
      </Pressable> : null}
      {showWorkspaceLink && workspace ? <Pressable accessibilityRole="button"
        accessibilityLabel={`Open ${run.change} in Paseo`}
        onPress={() => {
          try { navigation.openWorkspace({ serverId: host.id, workspaceId: workspace.id }); }
          catch { report('Could not open the Paseo workspace.'); }
        }} style={{ paddingVertical: 10, alignSelf: 'flex-start' }}>
        <Text style={{ color: colors.accent, fontWeight: '600', textDecorationLine: 'underline' }}>Open in Paseo</Text>
      </Pressable> : null}
    </View> : null}
    <Pressable accessibilityRole="button" accessibilityLabel={`Details for ${run.change}`}
      accessibilityState={{ expanded: detailsOpen }}
      onPress={() => { if (!detailsOpen) onDetailsOpen(); setDetailsOpen(!detailsOpen); }}
      style={{ paddingVertical: 10, alignSelf: 'flex-start', minHeight: 44,
        flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      <Text style={{ color: colors.foregroundMuted, fontWeight: '600' }}>Details {detailsOpen ? '▴' : '▾'}</Text>
    </Pressable>
    {detailsOpen ? <View style={{ gap: 8 }}>
      {details.fields.map(field => <View key={field.label} style={{ gap: 4 }}>
        <Text style={{ color: colors.foregroundMuted }}>{field.label}</Text>
        <Text selectable style={{ color: colors.foreground }}>{field.text}</Text>
      </View>)}
      {details.recent.length ? <View style={{ gap: 4 }}>
        <Text style={{ color: colors.foregroundMuted }}>Recent events</Text>
        {details.recent.map((event, index) => <Text key={`${event.at}:${index}`} selectable style={{ color: colors.foreground }}>
          {new Date(event.at).toLocaleString()} · {event.text}
        </Text>)}
      </View> : null}
      <Text style={{ color: colors.foregroundMuted }}>More detail (run on the Paseo host)</Text>
      <Text selectable style={{ color: colors.foreground }}>{details.command}</Text>
    </View> : null}
    {notice ? <Text accessibilityRole="alert" style={{ color: colors.foreground }}>{notice}</Text> : null}
    <LandingNotices notices={landingNotices} colors={colors} />
  </View>;
}

export function FactorySection({ theme, host, navigation }: Pick<PluginSurfaceProps, 'theme'> & HostProps) {
  const read = useRpc(factoryRuns);
  const send = useRpc(factoryAct);
  const readLanding = useRpc(factoryLanding);
  const sendLand = useRpc(factoryLand);
  const sendStop = useRpc(factoryLandStop);
  const [data, setData] = useState<FactorySnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Keep outcomes beside their rendered row, or above the list while folded or absent.
  const [notice, setNotice] = useState<{ change: string; text: string } | null>(null);
  const [finishedOpen, setFinishedOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<FactorySort>('recent');
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState(false);
  const [landings, setLandings] = useState<Record<string, FactoryLandingSnapshot>>({});
  const [landingNotices, setLandingNotices] = useState<LandingNotice[]>([]);
  const [landingError, setLandingError] = useState<string | null>(null);
  const [landingBusy, setLandingBusy] = useState(false);
  const landingInFlight = useRef(false);
  const landingGeneration = useRef(0);
  const localNoticeId = useRef(0);
  const trackedChanges = useRef(new Set<string>());
  const latestRuns = useRef<FactorySnapshot | null>(null);
  const latestLandings = useRef<Record<string, FactoryLandingSnapshot>>({});
  const [workspaces, setWorkspaces] = useState<Workspaces>([]);
  const workspacesRequested = useRef(false);
  const alive = useRef(false);
  const inFlight = useRef<Promise<void> | null>(null);
  const actionInFlight = useRef(false);
  const colors = theme.colors;

  async function loadWorkspaces() {
    if (workspacesRequested.current || typeof navigation?.openWorkspace !== 'function') return;
    workspacesRequested.current = true;
    try {
      const client = getPaseoClient(host.id);
      if (typeof client?.workspaces?.list !== 'function') return;
      // Follow pagination: the matching workspace may not be on the first page.
      const entries = [];
      let cursor: string | undefined;
      for (let page = 0; page < 20; page++) {
        const result = await client.workspaces.list({ page: { limit: 200, ...(cursor ? { cursor } : {}) } });
        entries.push(...result.entries);
        if (!result.pageInfo?.hasMore || !result.pageInfo.nextCursor) break;
        cursor = result.pageInfo.nextCursor;
      }
      if (alive.current) setWorkspaces(entries);
    } catch {
      // Older hosts and failed reads still offer the rest of Details.
    }
  }

  const refresh = useCallback((): Promise<void> => {
    if (inFlight.current) return inFlight.current;
    setLoading(true);
    const generation = landingGeneration.current;
    const readLandingStatus = async (change?: string) => {
      try {
        const snapshot = await readLanding({ ...(change ? { change } : {}), refresh: true });
        if (!alive.current || generation !== landingGeneration.current) return;
        if (change) {
          latestLandings.current = { ...latestLandings.current, [change]: snapshot };
          setLandings(latestLandings.current);
        } else setLandingError(snapshot.error);
        if (change && snapshot.error) setLandingError(`${change}: ${snapshot.error}`);
        const attempts = [...snapshot.attempts, ...(snapshot.current ? [snapshot.current] : [])];
        attempts.forEach(attempt => trackedChanges.current.add(attempt.change));
        setLandingNotices(previous => retainLandingNotices(previous, attempts.map(landingAttemptNotice)));
      } catch {
        if (!alive.current || generation !== landingGeneration.current) return;
        if (change && latestLandings.current[change]) {
          latestLandings.current = { ...latestLandings.current,
            [change]: { ...latestLandings.current[change], stale: true } };
          setLandings(latestLandings.current);
        }
        setLandingError('Landing refresh failed. Previously loaded results are retained. Refresh status before confirming again.');
      }
    };
    const pending = (async () => {
      try {
        await Promise.all([readLandingStatus(), (async () => {
          try {
            const next = await read({ refresh: true });
            if (alive.current) { latestRuns.current = next; setData(next); setError(null); }
          } catch {
            if (alive.current) {
              if (latestRuns.current) latestRuns.current = { ...latestRuns.current, stale: true };
              setError('Factory refresh failed. Any previously loaded list is stale and retained.');
            }
          }
        })()]);
        const changes = [...new Set([...(latestRuns.current?.runs.map(run => run.change) ?? []), ...trackedChanges.current])];
        // Reuse the page's refresh lifecycle, with at most four named reads in flight.
        let index = 0;
        await Promise.all(Array.from({ length: Math.min(4, changes.length) }, async () => {
          while (alive.current && generation === landingGeneration.current && index < changes.length) {
            await readLandingStatus(changes[index++]);
          }
        }));
      } finally {
        inFlight.current = null;
        if (alive.current) setLoading(false);
      }
    })();
    inFlight.current = pending;
    return pending;
  }, [read, readLanding]);

  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = setInterval(() => {
      if (AppState.currentState === 'active' || AppState.currentState === null) void refresh();
    }, 60_000);
    const subscription = AppState.addEventListener('change', state => {
      if (state === 'active') void refresh();
    });
    return () => { alive.current = false; clearInterval(timer); subscription.remove(); };
  }, [refresh]);

  async function act(input: FactoryActInput) {
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setActing(true);
    setNotice({ change: input.change, text: 'Sending action…' });
    try {
      const outcome = await send(input);
      if (alive.current) setNotice({ change: input.change, text: `${outcome.outcome === 'done' ? 'Done' : 'Refused'} · ${outcome.detail}` });
    } catch {
      if (alive.current) setNotice({ change: input.change, text: 'Could not confirm the action result. Check the refreshed status before retrying.' });
    } finally {
      // Wait out an older read before requesting the post-action snapshot.
      await inFlight.current;
      if (alive.current) await refresh();
      actionInFlight.current = false;
      if (alive.current) setActing(false);
    }
  }

  async function landingAction(change: string, operation: () => Promise<LandingNotice>, attempt?: FactoryLandingAttempt) {
    if (landingInFlight.current) return;
    landingInFlight.current = true;
    landingGeneration.current++;
    trackedChanges.current.add(change);
    setLandingBusy(true);
    try {
      const result = await operation();
      if (alive.current) setLandingNotices(previous => retainLandingNotices(previous, [result]));
    } catch {
      const message = `${attempt ? 'Stop' : 'Landing'} result unavailable. Refresh landing status to recover the attempt before confirming again.`;
      const result = attempt ? { ...landingAttemptNotice(attempt), messages: [message] }
        : { id: `local-${++localNoticeId.current}`, change, text: message, terminal: true, updatedAt: Date.now(), messages: [] };
      if (alive.current) setLandingNotices(previous => retainLandingNotices(previous, [result]));
    } finally {
      landingGeneration.current++;
      // A response lost after admission is recovered only by reading status.
      await inFlight.current;
      if (alive.current) await refresh();
      landingInFlight.current = false;
      if (alive.current) setLandingBusy(false);
    }
  }

  async function land(input: FactoryLandInput) {
    const runs = latestRuns.current;
    const run = runs?.runs.find(item => item.change === input.change);
    const snapshot = latestLandings.current[input.change];
    if (!run || !sameLandingOffer(input, factoryLandOffer(run, snapshot, runs?.stale)) || snapshot?.current) return;
    await landingAction(input.change, async () => {
      const result = await sendLand(input);
      return {
        id: result.attempt_id ?? `local-${++localNoticeId.current}`, change: input.change,
        text: result.accepted ? `Landing ${result.attempt_id.slice(0, 8)} · starting`
          : `Landing refused · ${result.reason}`,
        terminal: !result.accepted, updatedAt: 0, messages: [],
      };
    });
  }

  async function stopLanding(attempt: FactoryLandingAttempt) {
    await landingAction(attempt.change, async () => {
      const result = await sendStop({ attempt_id: attempt.attempt_id });
      return { ...landingAttemptNotice(attempt), messages: [
        `${result.accepted ? 'Stop requested' : 'Stop refused'} · ${result.reason}`,
      ] };
    }, attempt);
  }

  // Counts describe the whole snapshot; search and sort only change what is listed.
  const totals = groupFactoryRuns(data?.runs ?? []);
  const searching = query.trim() !== '';
  const { active, finished } = groupFactoryRuns(arrangeFactoryRuns(data?.runs ?? [], query, sort));
  // A search reaches finished runs without opening the fold.
  const showFinished = finishedOpen || searching;
  const visibleChanges = new Set([...active, ...(showFinished ? finished : [])].map(run => run.change));
  const noticeInRow = notice && (active.some(run => run.change === notice.change) ||
    (showFinished && finished.some(run => run.change === notice.change)));
  const sorts: { value: FactorySort; label: string }[] = [
    { value: 'recent', label: 'Recent' }, { value: 'oldest', label: 'Oldest' }, { value: 'name', label: 'A–Z' },
  ];
  const renderRow = (run: FactoryRun) => <FactoryRow key={run.change} run={run} colors={colors}
    disabled={acting} act={act} report={text => setNotice({ change: run.change, text })}
    notice={noticeInRow && notice?.change === run.change ? notice.text : null} host={host} navigation={navigation}
    offer={factoryLandOffer(run, landings[run.change], Boolean(data?.stale || error))}
    currentLanding={landings[run.change]?.current ?? null} landingBusy={landingBusy}
    land={land} stopLanding={stopLanding} landingNotices={landingNotices.filter(item => item.change === run.change)}
    workspaces={workspaces} onDetailsOpen={() => { void loadWorkspaces(); }} />;

  return <View style={{ gap: 12 }}>
    <Text accessibilityRole="header" style={{ color: colors.foreground, fontSize: 24, fontWeight: '700' }}>Factory</Text>
    <Text style={{ color: data?.stale || error ? colors.statusWarning : colors.foregroundMuted }}>
      {data ? `${totals.active.length} ${totals.active.length === 1 ? 'run' : 'runs'}${totals.finished.length ? ` · ${totals.finished.length} finished` : ''}${data.stale || error ? ' · Stale' : ''}` : 'Loading Factory…'}
    </Text>
    <Pressable accessibilityRole="button" accessibilityLabel="Refresh Factory" disabled={loading || acting}
      onPress={() => void refresh()} style={{ paddingVertical: 8, paddingHorizontal: 12, alignSelf: 'flex-start',
        minHeight: 44, minWidth: 44, borderRadius: 8, borderWidth: 1, borderColor: colors.border,
        backgroundColor: colors.surface2, alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ color: colors.foreground, fontSize: 13, fontWeight: '600' }}>{loading ? 'Checking…' : 'Refresh Factory'}</Text>
    </Pressable>
    {error || data?.error ? <Text accessibilityRole="alert" style={{ color: colors.statusWarning }}>{error || data?.error}</Text> : null}
    {landingError ? <Text accessibilityRole="alert" style={{ color: colors.statusWarning }}>{landingError}</Text> : null}
    {notice && !noticeInRow
      ? <Text accessibilityRole="alert" style={{ color: colors.foreground }}>{`${notice.change}: ${notice.text}`}</Text> : null}
    <LandingNotices notices={landingNotices.filter(item => !visibleChanges.has(item.change))} colors={colors} named />
    {data?.runs.length ? <View style={{ gap: 8 }}>
      <TextInput accessibilityLabel="Search Factory runs" value={query} onChangeText={setQuery}
        placeholder="Search runs" placeholderTextColor={colors.foregroundMuted} autoCapitalize="none" autoCorrect={false}
        style={{ color: colors.foreground, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 12, minHeight: 44 }} />
      <View accessibilityRole="radiogroup" accessibilityLabel="Sort Factory runs" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {sorts.map(option => {
          const selected = option.value === sort;
          return <Pressable key={option.value} accessibilityRole="radio" accessibilityLabel={`Sort by ${option.label}`}
            accessibilityState={{ selected }} onPress={() => setSort(option.value)}
            style={{ minHeight: 44, minWidth: 44, paddingHorizontal: 12, borderRadius: 8, borderWidth: 1,
              borderColor: selected ? colors.accent : colors.border, backgroundColor: selected ? colors.surface2 : colors.surface1,
              alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ color: selected ? colors.foreground : colors.foregroundMuted, fontSize: 13, fontWeight: '600' }}>{option.label}</Text>
          </Pressable>;
        })}
      </View>
      {searching ? <Text style={{ color: colors.foregroundMuted }}>
        {active.length + finished.length ? `${active.length + finished.length} of ${data.runs.length} match` : 'No runs match.'}
      </Text> : null}
    </View> : null}
    {data && !totals.active.length && !totals.finished.length ? <Text style={{ color: colors.foreground }}>No Factory runs.</Text> : null}
    {active.map(renderRow)}
    {finished.length && !searching ? <Pressable accessibilityRole="button" accessibilityLabel={`Finished (${finished.length})`}
      accessibilityState={{ expanded: finishedOpen }} onPress={() => setFinishedOpen(open => !open)}
      style={{ paddingVertical: 10, alignSelf: 'flex-start', minHeight: 44,
        flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      <Text style={{ color: colors.foregroundMuted, fontWeight: '600' }}>{`Finished (${finished.length})`}</Text>
      <View accessible={false} accessibilityElementsHidden importantForAccessibility="no-hide-descendants"
        style={{ width: 8, height: 8, borderRightWidth: 2, borderBottomWidth: 2,
          borderColor: colors.foregroundMuted, transform: [{ rotate: finishedOpen ? '225deg' : '45deg' }] }} />
    </Pressable> : null}
    {showFinished ? finished.map(renderRow) : null}
  </View>;
}
