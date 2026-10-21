import type { PluginServerContext } from '@getpaseo/plugin/server';
import { attention } from './shared/attention';
import { dispose, readAttention } from './server/attention';
import { factoryRuns, factoryAct } from './shared/factory';
import { readFactoryRuns, actOnFactoryRun, disposeFactory } from './server/factory';

export default function contribute(server: PluginServerContext) {
  server.handle(attention, readAttention);
  server.handle(factoryRuns, readFactoryRuns);
  server.handle(factoryAct, actOnFactoryRun);
  return () => { dispose(); disposeFactory(); };
}
