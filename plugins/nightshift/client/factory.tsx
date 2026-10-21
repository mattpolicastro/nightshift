import { type PluginSurfaceProps, useRpc } from '@getpaseo/plugin/client';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Linking, Pressable, Text, TextInput, View } from 'react-native';
import {
  factoryRuns, factoryAct, type FactoryRun, type FactorySnapshot, type FactoryActInput,
} from '../shared/factory';
import { decideFactoryAction, factoryButtons, orderFactoryRuns, type ActionDecision } from './factory-model';

type Colors = PluginSurfaceProps['theme']['colors'];
type Confirmation = Extract<ActionDecision, { kind: 'confirm' }>;

function FactoryRow({ run, colors, disabled, act, report }: {
  run: FactoryRun; colors: Colors; disabled: boolean;
  act: (input: FactoryActInput) => Promise<void>; report: (message: string) => void;
}) {
  const [note, setNote] = useState('');
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const buttons = factoryButtons(run);

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
    <Text style={{ color: colors.foregroundMuted }}>{run.status} · {run.tasks.done}/{run.tasks.total} tasks done</Text>
    {run.action ? <>
      <Text style={{ color: colors.foreground, fontWeight: '600' }}>{run.action.headline}</Text>
      <Text style={{ color: colors.foreground }}>{run.action.why}</Text>
    </> : null}
    {run.pr ? <Pressable accessibilityRole="link" accessibilityLabel={`Open PR for ${run.change}`}
      onPress={() => { void Linking.openURL(run.pr).catch(() => report('Could not open GitHub.')); }}
      style={{ paddingVertical: 10, alignSelf: 'flex-start' }}>
      <Text style={{ color: colors.accent, fontWeight: '600' }}>Open PR ↗</Text>
    </Pressable> : null}
    {buttons.some(button => button.verb === 'resume') ? <View style={{ gap: 4 }}>
      <Text style={{ color: colors.foregroundMuted }}>Resume note (optional)</Text>
      <TextInput accessibilityLabel={`Resume note for ${run.change}`} value={note} onChangeText={setNote}
        editable={!disabled} multiline maxLength={2000} placeholder="Guidance for the next attempt"
        placeholderTextColor={colors.foregroundMuted}
        style={{ color: colors.foreground, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 12, minHeight: 72 }} />
    </View> : null}
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 16 }}>
      {buttons.map(button => <Pressable key={button.verb} accessibilityRole="button"
        accessibilityLabel={`${button.label} ${run.change}`} disabled={disabled || confirmation !== null}
        onPress={() => choose(button.verb)} style={{ paddingVertical: 12, opacity: disabled || confirmation ? 0.5 : 1 }}>
        <Text style={{ color: colors.accent, fontWeight: '600' }}>{button.label}</Text>
      </Pressable>)}
    </View>
    {confirmation ? <View style={{ gap: 8 }}>
      <Text accessibilityRole="alert" style={{ color: colors.foreground }}>{confirmation.message}</Text>
      <View style={{ flexDirection: 'row', gap: 24 }}>
        <Pressable accessibilityRole="button" disabled={disabled} onPress={() => choose(confirmation.request.verb, true)} style={{ paddingVertical: 12 }}>
          <Text style={{ color: colors.accent, fontWeight: '600' }}>Confirm {confirmation.request.verb}</Text>
        </Pressable>
        <Pressable accessibilityRole="button" onPress={() => setConfirmation(null)} style={{ paddingVertical: 12 }}>
          <Text style={{ color: colors.foreground }}>Cancel</Text>
        </Pressable>
      </View>
    </View> : null}
  </View>;
}

export function FactorySection({ theme }: Pick<PluginSurfaceProps, 'theme'>) {
  const read = useRpc(factoryRuns);
  const send = useRpc(factoryAct);
  const [data, setData] = useState<FactorySnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState(false);
  const alive = useRef(false);
  const inFlight = useRef<Promise<void> | null>(null);
  const actionInFlight = useRef(false);
  const colors = theme.colors;

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
    setResult(null);
    try {
      const outcome = await send(input);
      if (alive.current) setResult(`${input.change}: ${outcome.outcome === 'done' ? 'Done' : 'Refused'} · ${outcome.detail}`);
    } catch {
      if (alive.current) setResult(`${input.change}: Could not confirm the action result. Check the refreshed status before retrying.`);
    } finally {
      // Wait out an older read before requesting the post-action snapshot.
      await inFlight.current;
      if (alive.current) await refresh();
      actionInFlight.current = false;
      if (alive.current) setActing(false);
    }
  }

  return <View style={{ gap: 12 }}>
    <Text accessibilityRole="header" style={{ color: colors.foreground, fontSize: 24, fontWeight: '700' }}>Factory</Text>
    <Text style={{ color: data?.stale || error ? colors.statusWarning : colors.foregroundMuted }}>
      {data ? `${data.runs.length} ${data.runs.length === 1 ? 'run' : 'runs'}${data.stale || error ? ' · Stale' : ''}` : 'Loading Factory…'}
    </Text>
    <Pressable accessibilityRole="button" accessibilityLabel="Refresh Factory" disabled={loading || acting}
      onPress={() => void refresh()} style={{ paddingVertical: 10, alignSelf: 'flex-start' }}>
      <Text style={{ color: colors.accent, fontWeight: '600' }}>{loading ? 'Checking…' : 'Refresh Factory'}</Text>
    </Pressable>
    {error || data?.error ? <Text accessibilityRole="alert" style={{ color: colors.statusWarning }}>{error || data?.error}</Text> : null}
    {result ? <Text accessibilityRole="alert" style={{ color: colors.foreground }}>{result}</Text> : null}
    {acting ? <Text style={{ color: colors.foregroundMuted }}>Sending action…</Text> : null}
    {data && !data.runs.length ? <Text style={{ color: colors.foreground }}>No Factory runs.</Text> : null}
    {orderFactoryRuns(data?.runs ?? []).map(run => <FactoryRow key={run.change} run={run} colors={colors}
      disabled={acting} act={act} report={setResult} />)}
  </View>;
}
