import type { PluginClientContext } from '@getpaseo/plugin/client';
import { MainSurface } from './client/main';

export default function contribute(client: PluginClientContext) {
  client.addSurface('main', MainSurface);
  client.addSidebarItem({ id: 'main', title: 'Nightshift', icon: 'Moon', surface: 'main' });
  client.addCommandCenterItem({
    id: 'open', title: 'Nightshift attention', icon: 'Moon', context: 'global',
    onSelect({ openSurface }) { openSurface('main'); },
  });
  return () => {};
}
