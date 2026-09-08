import type { PluginContext } from '@getpaseo/plugin';
import { attention } from './attention.shared';
import { readAttention, dispose } from './attention.server';
import { MainSurface } from './main.client';

export default function contribute(plugin: PluginContext) {
  plugin.handle(attention, readAttention);
  plugin.addSurface('main', MainSurface);
  plugin.addSidebarItem({ id: 'main', title: 'Nightshift', icon: 'Moon', surface: 'main' });
  plugin.addCommandCenterItem({
    id: 'open', title: 'Nightshift attention', icon: 'Moon', context: 'global',
    onSelect({ openSurface }) { openSurface('main'); },
  });
  // Paseo v0.7 removes server imports from the client entrypoint.
  // Only the daemon bundle owns subprocess cleanup.
  return () => { if (typeof dispose === 'function') dispose(); };
}
