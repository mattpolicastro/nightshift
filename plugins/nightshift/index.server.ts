import type { PluginServerContext } from '@getpaseo/plugin/server';
import { attention } from './shared/attention';
import { dispose, readAttention } from './server/attention';
import { factoryRuns, factoryAct, factoryLanding, factoryLand, factoryLandStop } from './shared/factory';
import { readFactoryRuns, actOnFactoryRun, disposeFactory } from './server/factory';
import { readFactoryLanding, landFactoryRun, stopFactoryLanding, disposeFactoryLanding } from './server/factory-landing';

export default function contribute(server: PluginServerContext) {
  server.handle(attention, readAttention);
  server.handle(factoryRuns, readFactoryRuns);
  server.handle(factoryAct, actOnFactoryRun);
  server.handle(factoryLanding, readFactoryLanding);
  server.handle(factoryLand, landFactoryRun);
  server.handle(factoryLandStop, stopFactoryLanding);
  return () => { dispose(); disposeFactory(); disposeFactoryLanding(); };
}
