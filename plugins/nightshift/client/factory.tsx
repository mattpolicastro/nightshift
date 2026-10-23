import { getPaseoClient, type PluginSurfaceProps, useRpc } from '@getpaseo/plugin/client';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Linking, Pressable, Text, TextInput, View } from 'react-native';
import {
  factoryRuns, factoryAct, type FactoryRun, type FactorySnapshot, type FactoryActInput,
} from '../shared/factory';
import { decideFactoryAction, factoryButtons, factoryDetails, factoryWaitingTime, groupFactoryRuns, matchWorkspace, type ActionDecision } from './factory-model';

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

function FactoryRow({ run, colors, disabled, act, report, notice, host, navigation, workspaces, onDetailsOpen }: {
  run: FactoryRun; colors: Colors; disabled: boolean;
  act: (input: FactoryActInput) => Promise<void>; report: (message: string) => void; notice: string | null;
  workspaces: Workspaces; onDetailsOpen: () => void;
} & HostProps) {
  const [note, setNote] = useState('');
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
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
        onPress={() => { void Linking.openURL(run.pr).catch(() => report('Could not open GitHub.')); }}
        style={{ paddingVertical: 10, alignSelf: 'flex-start' }}>
        <Text style={{ color: colors.accent, fontWeight: '600', textDecorationLine: 'underline' }}>Open PR ↗</Text>
      </Pressable> : null}
      {showChangeLink ? <Pressable accessibilityRole="link" accessibilityLabel={`Open change ${run.change} on GitHub`}
        onPress={() => { void Linking.openURL(details.github).catch(() => report('Could not open GitHub.')); }}
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
  </View>;
}

export function FactorySection({ theme, host, navigation }: Pick<PluginSurfaceProps, 'theme'> & HostProps) {
  const read = useRpc(factoryRuns);
  const send = useRpc(factoryAct);
  const [data, setData] = useState<FactorySnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Keep outcomes beside their rendered row, or above the list while folded or absent.
  const [notice, setNotice] = useState<{ change: string; text: string } | null>(null);
  const [finishedOpen, setFinishedOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState(false);
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
    const pending = (async () => {
      try {
        const next = await read({ refresh: true });
        if (alive.current) { setData(next); setError(null); }
      } catch {
        if (alive.current) setError('Factory refresh failed. Any previously loaded list is stale and retained.');
      } finally {
        inFlight.current = null;
        if (alive.current) setLoading(false);
      }
    })();
    inFlight.current = pending;
    return pending;
  }, [read]);

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

  const { active, finished } = groupFactoryRuns(data?.runs ?? []);
  const noticeInRow = notice && (active.some(run => run.change === notice.change) ||
    (finishedOpen && finished.some(run => run.change === notice.change)));
  const renderRow = (run: FactoryRun) => <FactoryRow key={run.change} run={run} colors={colors}
    disabled={acting} act={act} report={text => setNotice({ change: run.change, text })}
    notice={noticeInRow && notice?.change === run.change ? notice.text : null} host={host} navigation={navigation}
    workspaces={workspaces} onDetailsOpen={() => { void loadWorkspaces(); }} />;

  return <View style={{ gap: 12 }}>
    <Text accessibilityRole="header" style={{ color: colors.foreground, fontSize: 24, fontWeight: '700' }}>Factory</Text>
    <Text style={{ color: data?.stale || error ? colors.statusWarning : colors.foregroundMuted }}>
      {data ? `${active.length} ${active.length === 1 ? 'run' : 'runs'}${finished.length ? ` · ${finished.length} finished` : ''}${data.stale || error ? ' · Stale' : ''}` : 'Loading Factory…'}
    </Text>
    <Pressable accessibilityRole="button" accessibilityLabel="Refresh Factory" disabled={loading || acting}
      onPress={() => void refresh()} style={{ paddingVertical: 8, paddingHorizontal: 12, alignSelf: 'flex-start',
        minHeight: 44, minWidth: 44, borderRadius: 8, borderWidth: 1, borderColor: colors.border,
        backgroundColor: colors.surface2, alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ color: colors.foreground, fontSize: 13, fontWeight: '600' }}>{loading ? 'Checking…' : 'Refresh Factory'}</Text>
    </Pressable>
    {error || data?.error ? <Text accessibilityRole="alert" style={{ color: colors.statusWarning }}>{error || data?.error}</Text> : null}
    {notice && !noticeInRow
      ? <Text accessibilityRole="alert" style={{ color: colors.foreground }}>{`${notice.change}: ${notice.text}`}</Text> : null}
    {data && !active.length && !finished.length ? <Text style={{ color: colors.foreground }}>No Factory runs.</Text> : null}
    {active.map(renderRow)}
    {finished.length ? <Pressable accessibilityRole="button" accessibilityLabel={`Finished (${finished.length})`}
      accessibilityState={{ expanded: finishedOpen }} onPress={() => setFinishedOpen(open => !open)}
      style={{ paddingVertical: 10, alignSelf: 'flex-start', minHeight: 44,
        flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      <Text style={{ color: colors.foregroundMuted, fontWeight: '600' }}>{`Finished (${finished.length})`}</Text>
      <View accessible={false} accessibilityElementsHidden importantForAccessibility="no-hide-descendants"
        style={{ width: 8, height: 8, borderRightWidth: 2, borderBottomWidth: 2,
          borderColor: colors.foregroundMuted, transform: [{ rotate: finishedOpen ? '225deg' : '45deg' }] }} />
    </Pressable> : null}
    {finishedOpen ? finished.map(renderRow) : null}
  </View>;
}
