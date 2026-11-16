import { openExternalUrl, type PluginSurfaceProps, useRpc } from '@getpaseo/plugin/client';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Pressable, ScrollView, Text, View } from 'react-native';
import { attention, type AttentionSnapshot, type Escalation } from '../shared/attention';
import { FactorySection } from './factory';

function date(value: string | null) {
  return value ? new Date(value).toLocaleString() : 'Not checked yet';
}

export function MainSurface(props: PluginSurfaceProps) {
  return <AttentionSurface key={props.host.id} {...props} />;
}

function AttentionSurface({ theme, host, layout, navigation }: PluginSurfaceProps) {
  const rpc = useRpc(attention);
  const [data, setData] = useState<AttentionSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
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

  const warn = { color: colors.statusWarning, fontSize: 12 };
  function row(item: Escalation) {
    const cold = item.summary === null && item.summaryStale;
    return (
      <View key={item.key} style={{ padding: 16, gap: 8, borderWidth: 1, borderColor: colors.border, borderRadius: 12, backgroundColor: colors.surface1 }}>
        <Text style={{ color: colors.foregroundMuted, fontSize: 12 }}>
          {item.kind === 'pr' ? 'PR' : 'Issue'} · {item.repo} #{item.number}{item.repo.endsWith('-sandbox') ? ' · Sandbox' : ''}
        </Text>
        <Text style={{ color: colors.foreground, fontSize: 17, fontWeight: '600' }}>{item.title}</Text>
        {item.summary !== null ? <Text style={{ color: colors.foreground }}>{item.summary}</Text> : null}
        {cold ? <Text style={{ color: colors.foregroundMuted }}>Summary unavailable</Text> : null}
        {item.summaryStale ? <Text style={warn}>
          {cold ? '' : 'Stale summary · '}{item.summaryError || 'Summary could not be checked.'} · Summary checked: {date(item.summaryCheckedAt)}
        </Text> : null}
        <Pressable accessibilityRole="link" accessibilityLabel={`Open ${item.title} on GitHub`}
          onPress={() => { setLinkError(null); void openExternalUrl(item.url).catch(() => { if (alive.current) setLinkError('Could not open GitHub.'); }); }}
          style={{ paddingVertical: 8, alignSelf: 'flex-start' }}>
          <Text style={{ color: colors.accent, fontWeight: '600' }}>Open on GitHub ↗</Text>
        </Pressable>
      </View>
    );
  }
  const repositories = data?.repositories || [];
  const pauses = data?.pauses || [];
  const count = repositories.reduce((total, repo) => total + repo.entries.length, 0);
  const alerts = [...new Set([error, linkError, data?.error, data?.frontDoorError, data?.pausesError].filter((m): m is string => !!m))];
  const empty = data && count === 0 && pauses.length === 0;
  return (
    <ScrollView style={{ flex: 1, backgroundColor: colors.surface0 }} contentContainerStyle={{ padding: layout.compact ? 16 : 28, gap: 20 }}>
      <View style={{ gap: 8 }}>
        <Text style={{ color: colors.foregroundMuted }}>{host.label}</Text>
        <Text accessibilityRole="header" style={{ color: colors.foreground, fontSize: 28, fontWeight: '700' }}>What needs you</Text>
        <Text style={{ color: colors.foregroundMuted }}>
          {data ? `${count} open ${count === 1 ? 'escalation' : 'escalations'}` : 'Loading Nightshift…'}
          {data?.cached ? ' · Cached' : ''}
        </Text>
        <Pressable accessibilityRole="button" accessibilityLabel="Refresh Nightshift" disabled={busy}
          onPress={() => void refresh(true)} style={{ paddingVertical: 10, alignSelf: 'flex-start' }}>
          <Text style={{ color: colors.accent, fontWeight: '600' }}>{busy ? 'Checking…' : 'Refresh'}</Text>
        </Pressable>
      </View>
      {alerts.map(message => <Text key={message} accessibilityRole="alert" style={{ color: colors.statusWarning }}>{message}</Text>)}
      {empty ? <Text style={{ color: colors.foreground }}>
        {data.complete ? 'Nothing needs your attention.'
          : data.cached ? 'Nothing listed in cached data. Not yet confirmed.'
          : 'Nothing listed, but some sources could not be checked.'}
      </Text> : null}
      {repositories.filter(repo => repo.entries.length || repo.stale || repo.error).map(repo => (
        <View key={repo.repo} style={{ gap: 12 }}>
          <Text accessibilityRole="header" style={{ color: colors.foreground, fontSize: 20, fontWeight: '600' }}>{repo.repo}</Text>
          {repo.stale || repo.error ? <Text style={warn}>
            Stale · {repo.error || 'Could not be fully checked.'} · Listing checked: {date(repo.checkedAt)}
          </Text> : null}
          {repo.entries.map(row)}
        </View>
      ))}
      {pauses.length ? <View style={{ gap: 8 }}>
        <Text accessibilityRole="header" style={{ color: colors.foreground, fontSize: 20, fontWeight: '600' }}>Paused builds</Text>
        {pauses.map(pause => (
          <Text key={pause.change} style={{ color: pause.stale ? colors.statusWarning : colors.foreground, flexShrink: 1 }}>
            {pause.stale ? 'Stale · ' : ''}{pause.change} — {pause.reason} · See Factory below: {pause.change}
          </Text>
        ))}
      </View> : null}
      {data ? <Text style={{ color: colors.foregroundMuted, fontSize: 12 }}>List loaded: {date(data.fetchedAt)} · Opening an item does not resolve it.</Text> : null}
      <FactorySection theme={theme} host={host} navigation={navigation} />
    </ScrollView>
  );
}
