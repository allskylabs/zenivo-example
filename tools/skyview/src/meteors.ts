// Meteor layer: meteors spawn in the equatorial (J2000) frame so a shower radiant turns with the stars, and fly along great
// circles away from the radiant (short near it, longest ~60 deg away). Ported in spirit from hoan.uk sky3d/meteorLayer.js.
import { mat3MulVec, radecToVec } from '@astrophoto/sky-engine';
import type { Mat3, Vec3 } from '@astrophoto/sky-engine';
import { activeShowers, DEG } from './astro';

interface Meteor { from: Vec3; dir: Vec3; start: number; len: number; life: number; age: number; bright: number; rgb: [number, number, number] }
const MAX = 24;
const PALETTE: [number, number, number][] = [[0.4, 1, 0.75], [0.6, 0.95, 0.85], [1, 0.9, 0.5], [1, 0.75, 0.45], [0.8, 0.7, 1]];
const norm = (a: Vec3): Vec3 => { const n = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / n, a[1] / n, a[2] / n]; };
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a: Vec3, b: Vec3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
function rand(): Vec3 { const z = Math.random() * 2 - 1, a = Math.random() * 2 * Math.PI, s = Math.sqrt(1 - z * z); return [s * Math.cos(a), s * Math.sin(a), z]; }

export interface MeteorSeg { a: Vec3; b: Vec3; alpha: number; rgb: [number, number, number] }
export interface Radiant { id: string; name: string; v: Vec3; raDeg: number; decDeg: number; w: number; zhr: number }

export class MeteorSim {
  private slots: Meteor[] = [];
  private carry = 0;
  radiants: Radiant[] = [];
  private cumul: number[] = [];

  setDate(date: Date): void {
    const act = activeShowers(date);
    this.radiants = act.map(({ s, w }) => ({ id: s.id, name: s.name, v: radecToVec({ raRad: s.ra * DEG, decRad: s.dec * DEG }), raDeg: s.ra, decDeg: s.dec, w, zhr: s.zhr * w }));
    let c = 0; this.cumul = this.radiants.map(r => (c += r.zhr));
  }
  clear(): void { this.slots = []; }

  /** dt = real seconds; speed = sim time multiplier (rate is compressed so playback stays watchable); M = J2000 -> horizontal (rejects spawns under the horizon). */
  tick(dt: number, speed: number, M: Mat3): void {
    const showerRate = this.cumul.length ? this.cumul[this.cumul.length - 1]! : 0;
    const perSec = Math.min(2.5, 0.12 + showerRate / 60) * Math.min(3, Math.max(1, Math.pow(Math.max(1, speed), 0.3)));
    this.carry += perSec * Math.min(dt, 0.1);
    while (this.carry >= 1) { this.carry -= 1; if (this.slots.length < MAX) this.spawn(M, showerRate); }
    for (const m of this.slots) m.age += Math.min(dt, 0.1);
    this.slots = this.slots.filter(m => m.age < m.life);
  }

  private spawn(M: Mat3, showerRate: number): void {
    const useShower = showerRate > 0 && Math.random() < showerRate / (showerRate + 12);
    let rad: Radiant | null = null;
    if (useShower) { const r = Math.random() * showerRate; rad = this.radiants[this.cumul.findIndex(c => c >= r)] ?? this.radiants[0]!; }
    for (let k = 0; k < 12; k++) {
      let from: Vec3, dir: Vec3, start: number, len: number;
      if (rad) {
        start = 4 + Math.pow(Math.random(), 1.1) * 58;
        const p = rand(); const d = dot(p, rad.v);
        dir = norm([p[0] - rad.v[0] * d, p[1] - rad.v[1] * d, p[2] - rad.v[2] * d]); from = rad.v;
        len = (6.5 + Math.random() * 11.5) * Math.max(0.22, Math.sin(Math.min(90, start + 12) * DEG));
      } else { from = rand(); dir = norm(cross(rand(), from)); start = 0; len = 6 + Math.random() * 9; }
      const a = (start + len * 0.55) * DEG;
      const mid: Vec3 = [from[0] * Math.cos(a) + dir[0] * Math.sin(a), from[1] * Math.cos(a) + dir[1] * Math.sin(a), from[2] * Math.cos(a) + dir[2] * Math.sin(a)];
      if (mat3MulVec(M, mid)[2] > 0.06 || k === 11) {
        if (k === 11) return;
        this.slots.push({ from, dir, start, len, life: 0.5 + Math.random() * 0.6, age: 0, bright: Math.min(1.4, 0.65 + Math.pow(Math.random(), 1.6) * 0.8), rgb: PALETTE[Math.floor(Math.random() * PALETTE.length)]! });
        return;
      }
    }
  }

  segments(): MeteorSeg[] {
    return this.slots.map(m => {
      const k = m.age / m.life;
      const head = m.start + m.len * Math.min(1, k * 1.12), tail = Math.max(m.start, head - m.len * 0.56);
      const pt = (deg: number): Vec3 => { const a = deg * DEG; return [m.from[0] * Math.cos(a) + m.dir[0] * Math.sin(a), m.from[1] * Math.cos(a) + m.dir[1] * Math.sin(a), m.from[2] * Math.cos(a) + m.dir[2] * Math.sin(a)]; };
      const fade = (k < 0.14 ? k / 0.14 : 1) * (k > 0.66 ? (1 - k) / 0.34 : 1) * m.bright;
      return { a: pt(tail), b: pt(head), alpha: Math.min(1, fade), rgb: m.rgb };
    });
  }
}
