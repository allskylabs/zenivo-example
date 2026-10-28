// Public module API of skyview. Usage:
//   const sky = await createSky(containerElement, { observer: {latDeg, lonDeg}, dataBaseUrl: '/data/sky/', camera?: {...} });
//   sky.core.setMarkers('aircraft', [...]);  sky.core.setCamera(...);  sky.core.setObserver(...)
import { SkyCore } from './engine-adapter';
import { buildUi } from './ui';
import type { Ui } from './ui';
import type { SkyConfig, ViewMode } from './types';

export type { SkyConfig, CameraFov, Marker, MarkerGroup, Layers, LayerKey, Lang, ViewMode, ObserverCfg } from './types';
export { SkyCore } from './engine-adapter';

export interface SkyHandle { core: SkyCore; ui: Ui; setMode(m: ViewMode): Promise<void>; destroy(): void }

export async function createSky(container: HTMLElement, cfg: SkyConfig): Promise<SkyHandle> {
  const root = document.createElement('div'); root.className = 'sv-root'; container.append(root);
  const gl = document.createElement('canvas'); gl.className = 'sv-gl'; gl.style.cursor = 'grab';
  const ov = document.createElement('canvas'); ov.className = 'sv-ov';
  root.append(gl, ov);
  const core = new SkyCore(gl, ov, cfg);
  let dome: { enter(): void; exit(): void } | null = null;
  let current: ViewMode = 'allsky';
  const modes: ViewMode[] = ['eye', '3d'];
  const setMode = async (m: ViewMode): Promise<void> => {
    if (m === current) return;
    try {
      if (current === '3d') dome?.exit();
      current = m; ui.setModeActive(m);
      if (m === '3d') {
        if (!dome) { const mod = await import('./dome3d'); dome = await mod.createDome(core, root); }
        dome.enter();
      } else if (m === 'eye') { core.setMode('eye'); core.eyeView(); }
      else { core.setMode('allsky'); core.allSkyView(); }
    } catch (e) {
      console.error('mode switch failed', e);
      // a deploy replaced the lazy chunk this tab still points at: load the new build
      if (/dynamically imported module|Failed to fetch|Importing a module script failed/i.test(String(e))) { location.reload(); return; }
      current = 'eye'; core.setMode('eye'); core.eyeView(); ui.setModeActive('eye'); }
  };
  const ui = buildUi(core, root, { setMode, modes });
  await core.init();
  core.start();
  await setMode('eye');   // default: the ordinary observer's view; zoom out to reach the all-sky dome
  return { core, ui, setMode, destroy: () => { core.stop(); root.remove(); } };
}
