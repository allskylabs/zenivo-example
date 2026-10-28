// Phone finder (lazy chunk): back camera video under a transparent star canvas, view driven by DeviceOrientation.
// Without sensors (desktop) the normal drag-to-look of the 2D view stays active. A selected object gets pointing guidance.
import type { SkyCore } from './engine-adapter';
import { DEG } from './astro';

type M3 = number[][];
const mul = (a: M3, b: M3): M3 => a.map((_, i) => [0, 1, 2].map(j => a[i]![0]! * b[0]![j]! + a[i]![1]! * b[1]![j]! + a[i]![2]! * b[2]![j]!));
const wrap180 = (d: number): number => ((d + 540) % 360) - 180;

export interface PhoneHandle { enter(): Promise<void>; exit(): void }

export function createPhone(core: SkyCore, root: HTMLElement, guide: HTMLElement): PhoneHandle {
  let video: HTMLVideoElement | null = null, stream: MediaStream | null = null, sensors = false, timer = 0;
  let off: number | null = null, az = 0, alt = 45, roll = 0, have = false;
  const lerpAngle = (a: number, b: number, k: number): number => a + wrap180(b - a) * k;

  const onOrient = (e: DeviceOrientationEvent): void => {
    if (e.alpha === null || e.beta === null || e.gamma === null) return;
    sensors = true; core.panEnabled = false;
    const a = e.alpha * DEG, b = e.beta * DEG, g = e.gamma * DEG;
    const Rz: M3 = [[Math.cos(a), -Math.sin(a), 0], [Math.sin(a), Math.cos(a), 0], [0, 0, 1]];
    const Rx: M3 = [[1, 0, 0], [0, Math.cos(b), -Math.sin(b)], [0, Math.sin(b), Math.cos(b)]];
    const Ry: M3 = [[Math.cos(g), 0, Math.sin(g)], [0, 1, 0], [-Math.sin(g), 0, Math.cos(g)]];
    const R = mul(mul(Rz, Rx), Ry); // device -> ENU (X east, Y north, Z up)
    const th = ((screen.orientation?.angle ?? 0) * DEG);
    const dir = (v: number[]): number[] => [0, 1, 2].map(i => R[i]![0]! * v[0]! + R[i]![1]! * v[1]! + R[i]![2]! * v[2]!);
    const f = dir([0, 0, -1]), u = dir([Math.sin(th), Math.cos(th), 0]), top = dir([0, 1, 0]);
    const azOf = (v: number[]): number => ((Math.atan2(v[0]!, v[1]!) / DEG) + 360) % 360;
    const heading = (e as unknown as { webkitCompassHeading?: number }).webkitCompassHeading;
    if (typeof heading === 'number' && Math.hypot(top[0]!, top[1]!) > 0.3) { const o = wrap180(heading - azOf(top)); off = off === null ? o : off + wrap180(o - off) * 0.1; }
    const o2 = e.type === 'deviceorientationabsolute' || typeof heading !== 'number' ? 0 : (off ?? 0);
    const fz = f[2]!, nAz = (azOf(f) + o2 + 360) % 360, nAlt = Math.asin(Math.max(-1, Math.min(1, fz))) / DEG;
    // roll relative to the roll-free view (horizontal frame x=N, y=W, z=up)
    const ar = nAz * DEG, right0 = [-Math.sin(ar), -Math.cos(ar), 0];
    const fh = [f[1]!, -f[0]!, f[2]!], uh = [u[1]!, -u[0]!, u[2]!];
    const up0 = [right0[1]! * fh[2]! - right0[2]! * fh[1]!, right0[2]! * fh[0]! - right0[0]! * fh[2]!, right0[0]! * fh[1]! - right0[1]! * fh[0]!];
    const nRoll = Math.atan2(uh[0]! * right0[0]! + uh[1]! * right0[1]! + uh[2]! * right0[2]!, uh[0]! * up0[0]! + uh[1]! * up0[1]! + uh[2]! * up0[2]!) / DEG;
    if (!have) { az = nAz; alt = nAlt; roll = nRoll; have = true; } else { az = lerpAngle(az, nAz, 0.3); alt += (nAlt - alt) * 0.3; roll = lerpAngle(roll, nRoll, 0.3); }
    core.goTo(az, alt, undefined, roll);
  };

  const updateGuide = (): void => {
    const t = core.selectedAzAlt();
    if (!t || !core.selected) { guide.classList.remove('show'); return; }
    const v = core.sky.view;
    const cAz = ((v.azRad / DEG) % 360 + 360) % 360, cAlt = v.altRad / DEG;
    const dAz = wrap180(t[0] - cAz), dAlt = t[1] - cAlt;
    const name = core.selectedHit ? core.inspect(core.selectedHit).title : '';
    if (Math.hypot(dAz * Math.cos(cAlt * DEG), dAlt) < 3) guide.textContent = `${name}: ${core.t('on target', 'đúng hướng')}`;
    else guide.textContent = `${name}: ${dAz >= 0 ? '→' : '←'} ${Math.abs(dAz).toFixed(0)}°  ${dAlt >= 0 ? '↑' : '↓'} ${Math.abs(dAlt).toFixed(0)}°` + (t[1] < 0 ? ' (' + core.t('below horizon', 'dưới chân trời') + ')' : '');
    guide.classList.add('show');
  };

  return {
    async enter() {
      core.setMode('phone'); core.sky.setTransparent(true); core.panEnabled = true; have = false; off = null; sensors = false;
      core.goTo(core.camera?.az ?? 180, 45, 65);
      try {
        const D = DeviceOrientationEvent as unknown as { requestPermission?: () => Promise<string> };
        if (typeof D.requestPermission === 'function') await D.requestPermission();
        window.addEventListener('deviceorientationabsolute', onOrient as EventListener); window.addEventListener('deviceorientation', onOrient);
      } catch { /* sensors denied: drag to look */ }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
        video = document.createElement('video'); video.className = 'sv-video'; video.muted = true; video.playsInline = true; video.srcObject = stream;
        root.insertBefore(video, core.gl); await video.play();
      } catch { /* no camera: plain sky */ }
      timer = window.setInterval(updateGuide, 250);
    },
    exit() {
      window.removeEventListener('deviceorientationabsolute', onOrient as EventListener); window.removeEventListener('deviceorientation', onOrient);
      clearInterval(timer); guide.classList.remove('show');
      stream?.getTracks().forEach(t => t.stop()); stream = null; video?.remove(); video = null;
      core.sky.setTransparent(false); core.panEnabled = true; void sensors; core.allSkyView();
    },
  };
}
