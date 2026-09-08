import { type PluginSurfaceProps, useRpc } from '@getpaseo/plugin';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Linking, Pressable, ScrollView, Text, View } from 'react-native';
import { attention, type Snapshot, type Task } from './attention.shared';

function date(value: string | null) {
  return value ? new Date(value).toLocaleString() : 'Not checked yet';
}

export function MainSurface(props: PluginSurfaceProps) {
  return <AttentionSurface key={props.host.id} {...props} />;
}

function AttentionSurface({ theme, host, layout }: PluginSurfaceProps) {
  const rpc = useRpc(attention);
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const alive = useRef(false);
  const running = useRef(false);
  const colors = theme.colors;

  const refresh = useCallback(async (fresh: boolean) => {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    try {
      const next = await rpc({ refresh: fresh });
      if (alive.current) { setData(next); setError(null); }
    } catch {
      if (alive.current) setError('Could not reach Nightshift on this host. Any previously loaded list is retained.');
    } finally {
      running.current = false;
      if (alive.current) setBusy(false);
    }
  }, [rpc]);

  useEffect(() => {
    alive.current = true;
    setData(null);
    setError(null);
    void refresh(false).then(() => { if (alive.current) void refresh(true); });
    const timer = setInterval(() => {
      if (AppState.currentState === 'active' || AppState.currentState === null) void refresh(true);
    }, 60_000);
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') void refresh(true);
    });
    return () => { alive.current = false; clearInterval(timer); subscription.remove(); };
  }, [refresh, host.id]);

  function row(task: Task) {
    return (
      <View key={task.key} style={{ padding: 16, gap: 8, borderWidth: 1, borderColor: colors.border, borderRadius: 12, backgroundColor: colors.surface1 }}>
        <Text style={{ color: colors.foregroundMuted, fontSize: 12 }}>
          {task.repo} #{task.issue}{task.repo.endsWith('-sandbox') ? ' · Sandbox' : ''}
        </Text>
        <Text style={{ color: colors.foreground, fontSize: 17, fontWeight: '600' }}>{task.title}</Text>
        {task.summary && task.summary !== task.title ? <Text style={{ color: colors.foreground }}>{task.summary}</Text> : null}
        <Text style={{ color: task.stale ? colors.statusWarning : colors.foregroundMuted, fontSize: 12 }}>
          {task.stale ? 'Stale · ' : ''}Last checked: {date(task.checkedAt)}
        </Text>
        <Pressable accessibilityRole="link" accessibilityLabel={`Open ${task.title} on GitHub`}
          onPress={() => { void Linking.openURL(task.url).catch(() => setError('Could not open GitHub.')); }}
          style={{ paddingVertical: 8, alignSelf: 'flex-start' }}>
          <Text style={{ color: colors.accent, fontWeight: '600' }}>Open on GitHub ↗</Text>
        </Pressable>
      </View>
    );
  }
  const tasks = data?.tasks || [];
  return (
    <ScrollView style={{ flex: 1, backgroundColor: colors.surface0 }} contentContainerStyle={{ padding: layout.compact ? 16 : 28, gap: 20 }}>
      <View style={{ gap: 8 }}>
        <Text style={{ color: colors.foregroundMuted }}>{host.label}</Text>
        <Text accessibilityRole="header" style={{ color: colors.foreground, fontSize: 28, fontWeight: '700' }}>What needs you</Text>
        <Text style={{ color: colors.foregroundMuted }}>
          {data ? `${tasks.length} outstanding ${tasks.length === 1 ? 'task' : 'tasks'}` : 'Loading Nightshift…'}
          {data?.cached ? ' · Cached' : ''}
        </Text>
        <Pressable accessibilityRole="button" accessibilityLabel="Refresh Nightshift" disabled={busy}
          onPress={() => void refresh(true)} style={{ paddingVertical: 10, alignSelf: 'flex-start' }}>
          <Text style={{ color: colors.accent, fontWeight: '600' }}>{busy ? 'Checking…' : 'Refresh'}</Text>
        </Pressable>
      </View>
      {error || data?.error ? <Text accessibilityRole="alert" style={{ color: colors.statusWarning }}>{error || data?.error}</Text> : null}
      {data && tasks.length === 0 ? <Text style={{ color: colors.foreground }}>No recorded tasks need your attention.</Text> : null}
      {(['needs_decision', 'awaiting_merge'] as const).map(state => {
        const group = tasks.filter(task => task.state === state);
        return group.length ? <View key={state} style={{ gap: 12 }}>
          <Text accessibilityRole="header" style={{ color: colors.foreground, fontSize: 20, fontWeight: '600' }}>
            {state === 'needs_decision' ? 'Needs a decision' : 'Awaiting merge'} · {group.length}
          </Text>
          {group.map(row)}
        </View> : null;
      })}
      {data?.stale.filter(task => !tasks.some(t => t.key === task.key)).map(task => (
        <Text key={task.key} style={{ color: colors.statusWarning }}>Could not recheck {task.key}. Last known: {task.state.replaceAll('_', ' ')} · {date(task.checkedAt)}</Text>
      ))}
      {data ? <Text style={{ color: colors.foregroundMuted, fontSize: 12 }}>List loaded: {date(data.fetchedAt)} · Opening a task does not resolve it.</Text> : null}
    </ScrollView>
  );
}
