// Small astronomy helpers on top of astronomy-engine (inspector values, season points, shower table).
import { Body, Constellation, Equator, MakeTime, MoonPhase, Observer as AeObserver, SearchSunLongitude } from 'astronomy-engine';
import type { ObserverCfg } from './types';

export const DEG = Math.PI / 180;
export const OBLIQUITY = 23.4392911;
const BODY: Record<string, Body> = { sun: Body.Sun, moon: Body.Moon, mercury: Body.Mercury, venus: Body.Venus, mars: Body.Mars, jupiter: Body.Jupiter, saturn: Body.Saturn, uranus: Body.Uranus, neptune: Body.Neptune };

const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
export const compassOf = (az: number): string => COMPASS[Math.round((((az % 360) + 360) % 360) / 22.5) % 16]!;

/** Ecliptic longitude (deg) of an equatorial J2000 position. */
export function eclipticLon(raDeg: number, decDeg: number): number {
  const a = raDeg * DEG, d = decDeg * DEG, e = OBLIQUITY * DEG;
  return (Math.atan2(Math.sin(a) * Math.cos(e) + Math.tan(d) * Math.sin(e), Math.cos(a)) / DEG + 360) % 360;
}
/** Equatorial (ra, dec deg) of a point on the ecliptic. */
export function eclipticToEq(lonDeg: number): [number, number] {
  const l = lonDeg * DEG, e = OBLIQUITY * DEG;
  return [(Math.atan2(Math.sin(l) * Math.cos(e), Math.cos(l)) / DEG + 360) % 360, Math.asin(Math.sin(l) * Math.sin(e)) / DEG];
}

export function constellationOf(raDeg: number, decDeg: number): { symbol: string; name: string } | null {
  try { const c = Constellation(raDeg / 15, decDeg); return { symbol: c.symbol, name: c.name }; } catch { return null; }
}

export function bodyDistanceAu(id: string, date: Date, o: ObserverCfg): number | null {
  const b = BODY[id];
  if (!b) return null;
  try { return Equator(b, MakeTime(date), new AeObserver(o.latDeg, o.lonDeg, o.heightM ?? 0), false, false).dist; } catch { return null; }
}

export function moonPhase(date: Date): { en: string; vi: string; deg: number } {
  const d = MoonPhase(date);
  const i = Math.floor(((d + 22.5) % 360) / 45);
  const names: [string, string][] = [['New moon', 'Trăng non'], ['Waxing crescent', 'Trăng lưỡi liềm đầu tháng'], ['First quarter', 'Trăng bán nguyệt đầu tháng'], ['Waxing gibbous', 'Trăng khuyết đầu tháng'], ['Full moon', 'Trăng tròn'], ['Waning gibbous', 'Trăng khuyết cuối tháng'], ['Last quarter', 'Trăng bán nguyệt cuối tháng'], ['Waning crescent', 'Trăng lưỡi liềm cuối tháng']];
  return { en: names[i]![0], vi: names[i]![1], deg: d };
}

export interface CalPoint { id: string; en: string; vi: string; lon: number; raDeg: number; decDeg: number; major: boolean }
const TERMS: [string, string, string, number, boolean][] = [
  ['vernal', 'March equinox', 'Xuân phân', 0, true], ['lapha', 'Start of summer', 'Lập hạ', 45, false], ['solsticeN', 'June solstice', 'Hạ chí', 90, true],
  ['lapthu', 'Start of autumn', 'Lập thu', 135, false], ['autumnal', 'September equinox', 'Thu phân', 180, true], ['lapdong', 'Start of winter', 'Lập đông', 225, false],
  ['solsticeS', 'December solstice', 'Đông chí', 270, true], ['lapxuan', 'Start of spring', 'Lập xuân', 315, false],
];
export const CALENDAR_POINTS: CalPoint[] = TERMS.map(([id, en, vi, lon, major]) => { const [ra, dec] = eclipticToEq(lon); return { id, en, vi, lon, raDeg: ra, decDeg: dec, major }; });

const termCache = new Map<number, Record<string, Date>>();
/** Exact instants of the 8 solar terms of a year. */
export function solarTermDates(year: number): Record<string, Date> {
  const hit = termCache.get(year);
  if (hit) return hit;
  const out: Record<string, Date> = {};
  const start = new Date(Date.UTC(year - 1, 11, 20));
  for (const p of CALENDAR_POINTS) {
    try { const t = SearchSunLongitude(p.lon, start, 400); if (t) { let d = t.date; if (d.getUTCFullYear() !== year) { const t2 = SearchSunLongitude(p.lon, new Date(d.getTime() + 20 * 86400000), 400); if (t2) d = t2.date; } out[p.id] = d; } } catch { /* skip */ }
  }
  termCache.set(year, out);
  return out;
}

/** Observing notes for well-known constellations (subset of hoan.uk constellationFacts). */
export const CON_FACTS: Record<string, { star: string; seasonEn: string; seasonVi: string; en: string; vi: string }> = {
  Ori: { star: 'Rigel & Betelgeuse', seasonEn: 'Winter', seasonVi: 'Mùa đông', en: "The most prominent winter constellation, featuring Orion's three-star belt and the Orion Nebula (M42).", vi: 'Chòm sao rực rỡ nhất mùa đông với thắt lưng 3 ngôi sao và Tinh vân Lạp Hộ (M42).' },
  UMa: { star: 'Alioth & Dubhe', seasonEn: 'Year-round (northern)', seasonVi: 'Quanh năm (bắc bán cầu)', en: 'Contains the Big Dipper; the pointer stars Merak and Dubhe line up toward Polaris.', vi: 'Chứa mảng sao Bắc Đẩu; hai sao Merak và Dubhe chỉ tới sao Bắc Cực.' },
  UMi: { star: 'Polaris', seasonEn: 'Year-round (northern)', seasonVi: 'Quanh năm (bắc bán cầu)', en: 'Home to Polaris, almost exactly at the north celestial pole.', vi: 'Chứa sao Bắc Cực Polaris gần cực bắc thiên cầu.' },
  Cas: { star: 'Schedar', seasonEn: 'Autumn - winter', seasonVi: 'Mùa thu - đông', en: 'The W-shaped circumpolar constellation in the northern Milky Way.', vi: 'Hình chữ W nổi tiếng trên dải Ngân Hà phương bắc.' },
  Cyg: { star: 'Deneb', seasonEn: 'Summer - autumn', seasonVi: 'Mùa hè - thu', en: 'The Northern Cross; Deneb is a vertex of the Summer Triangle.', vi: 'Chữ thập phương bắc; Deneb là một đỉnh của Tam giác mùa hè.' },
  Sco: { star: 'Antares', seasonEn: 'Summer', seasonVi: 'Mùa hè', en: 'Summer constellation anchored by the red supergiant Antares, near the galactic core.', vi: 'Chòm sao mùa hè với siêu sao đỏ Antares gần tâm Ngân Hà.' },
  Leo: { star: 'Regulus', seasonEn: 'Spring', seasonVi: 'Mùa xuân', en: 'Spring constellation with the Sickle asterism and Regulus.', vi: 'Chòm sao mùa xuân với hình lưỡi liềm và sao Regulus.' },
  Tau: { star: 'Aldebaran', seasonEn: 'Winter', seasonVi: 'Mùa đông', en: 'Home to the Pleiades and Hyades clusters and Aldebaran.', vi: 'Chứa cụm sao Thất Nữ (Pleiades), Hyades và sao Aldebaran.' },
  Lyr: { star: 'Vega', seasonEn: 'Summer', seasonVi: 'Mùa hè', en: 'Small summer constellation led by brilliant Vega.', vi: 'Chòm sao mùa hè nhỏ với sao Vega rực rỡ.' },
  CMa: { star: 'Sirius', seasonEn: 'Winter', seasonVi: 'Mùa đông', en: 'Contains Sirius, the brightest star of the night sky.', vi: 'Chứa Sirius, ngôi sao sáng nhất bầu trời đêm.' },
  Cru: { star: 'Acrux', seasonEn: 'Autumn - spring (southern)', seasonVi: 'Quanh năm (nam bán cầu)', en: 'The Southern Cross, smallest of the 88 constellations.', vi: 'Chữ thập phương nam, chòm sao nhỏ nhất trong 88 chòm.' },
};

export interface Shower { id: string; name: string; zhr: number; vel: number; ra: number; dec: number; peak: [number, number]; from: [number, number]; to: [number, number] }
// [month 0-11, day]; source: hoan.uk meteorPredictionEngine radiant table
export const SHOWERS: Shower[] = [
  { id: 'qua', name: 'Quadrantids', zhr: 110, vel: 41, ra: 230.1, dec: 49.5, peak: [0, 3], from: [11, 28], to: [0, 12] },
  { id: 'lyr', name: 'Lyrids', zhr: 18, vel: 49, ra: 271.4, dec: 34.3, peak: [3, 22], from: [3, 14], to: [3, 30] },
  { id: 'eta', name: 'Eta Aquariids', zhr: 50, vel: 66, ra: 338.0, dec: -1.0, peak: [4, 6], from: [3, 19], to: [4, 28] },
  { id: 'sda', name: 'Southern Delta Aquariids', zhr: 25, vel: 41, ra: 339.8, dec: -16.4, peak: [6, 30], from: [6, 12], to: [7, 23] },
  { id: 'per', name: 'Perseids', zhr: 100, vel: 59, ra: 48.2, dec: 57.6, peak: [7, 12], from: [6, 17], to: [7, 24] },
  { id: 'dra', name: 'Draconids', zhr: 10, vel: 20, ra: 262.0, dec: 54.0, peak: [9, 8], from: [9, 6], to: [9, 10] },
  { id: 'ori', name: 'Orionids', zhr: 20, vel: 66, ra: 95.0, dec: 16.0, peak: [9, 21], from: [9, 2], to: [10, 7] },
  { id: 'sta', name: 'Southern Taurids', zhr: 8, vel: 27, ra: 52.0, dec: 13.0, peak: [10, 5], from: [8, 25], to: [10, 25] },
  { id: 'nta', name: 'Northern Taurids', zhr: 8, vel: 29, ra: 58.0, dec: 22.0, peak: [10, 12], from: [9, 20], to: [10, 10] },
  { id: 'leo', name: 'Leonids', zhr: 15, vel: 71, ra: 152.0, dec: 22.0, peak: [10, 17], from: [10, 6], to: [10, 30] },
  { id: 'gem', name: 'Geminids', zhr: 150, vel: 35, ra: 112.3, dec: 32.5, peak: [11, 14], from: [11, 4], to: [11, 20] },
  { id: 'urs', name: 'Ursids', zhr: 10, vel: 33, ra: 217.0, dec: 76.0, peak: [11, 22], from: [11, 17], to: [11, 26] },
];
const doy = (m: number, d: number): number => m * 31 + d;
/** Showers active on a date with a 0..1 activity weight (triangular around the peak). */
export function activeShowers(date: Date): { s: Shower; w: number }[] {
  const x = doy(date.getUTCMonth(), date.getUTCDate());
  const out: { s: Shower; w: number }[] = [];
  for (const s of SHOWERS) {
    let a = doy(...s.from), b = doy(...s.to), p = doy(...s.peak), xx = x;
    if (b < a) { b += 372; if (xx < a) xx += 372; if (p < a) p += 372; }
    if (xx < a || xx > b) continue;
    const half = xx <= p ? Math.max(1, p - a) : Math.max(1, b - p);
    out.push({ s, w: Math.max(0.12, 1 - Math.abs(xx - p) / half) });
  }
  return out;
}
