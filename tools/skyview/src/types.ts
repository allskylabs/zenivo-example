// Public types of the skyview module. The core never talks to a backend: hosts push data in through SkyCore.setMarkers / setCamera / setObserver.
import type { Vec3 } from '@astrophoto/sky-engine';

export interface ObserverCfg { latDeg: number; lonDeg: number; heightM?: number }
/** Camera field of view drawn as a labelled rotated frame (az/alt = pointing, degrees). */
export interface CameraFov { az: number; alt: number; roll: number; fovH: number; fovV: number; label?: string }
export type Lang = 'en' | 'vi';
export type ViewMode = 'allsky' | '3d' | 'phone';

export interface SkyConfig {
  observer: ObserverCfg;
  /** Folder (with trailing slash) holding stars/index.json, stars/o{n}/{pix}.stl and catalog/{constellations,stars_named,messier}.json + milkyway.png */
  dataBaseUrl: string;
  camera?: CameraFov | null;
  lang?: Lang;
  /** localStorage key prefix for layer/lang prefs (default "skyview.") */
  storagePrefix?: string;
  /** Fixed start time (ms); omit for live. */
  startMs?: number;
}

export const LAYER_KEYS = [
  'stars', 'milkyWay', 'starLabels', 'constellationLines', 'constellationLabels', 'planets', 'moon', 'ecliptic', 'calendarPoints',
  'polarAxis', 'equator', 'raGrid', 'decGrid', 'meridian', 'altAzGrid', 'earthGlobe', 'seeThroughGround', 'deepSkyObjects',
  'daylightAtmosphere', 'cardinalDirections', 'meteors', 'camera', 'aircraft', 'satellites', 'events',
] as const;
export type LayerKey = typeof LAYER_KEYS[number];
export type Layers = Record<LayerKey, boolean>;

export const LAYER_LABELS: Record<LayerKey, [string, string]> = {
  stars: ['Stars', 'Ngôi sao'], milkyWay: ['Milky Way', 'Dải Ngân Hà'], starLabels: ['Star names', 'Tên sao'],
  constellationLines: ['Constellations', 'Chòm sao'], constellationLabels: ['Constellation names', 'Tên chòm sao'],
  planets: ['Planets', 'Hành tinh'], moon: ['Moon and Sun', 'Trăng và Mặt Trời'], ecliptic: ['Ecliptic line', 'Đường hoàng đạo'],
  calendarPoints: ['Equinoxes and solstices', 'Điểm phân và chí'], polarAxis: ['Earth polar axis', 'Trục quay Trái Đất'],
  equator: ['Celestial equator', 'Xích đạo trời'], raGrid: ['Right ascension grid', 'Lưới xích kinh (RA)'],
  decGrid: ['Declination grid', 'Lưới xích vĩ (Dec)'], meridian: ['Meridian', 'Kinh tuyến'], altAzGrid: ['Alt/az grid', 'Lưới chân trời'],
  earthGlobe: ['3D Earth globe (3D dome)', 'Quả địa cầu 3D (vòm 3D)'], seeThroughGround: ['Transparent ground', 'Nhìn xuyên chân trời'],
  deepSkyObjects: ['Messier objects', 'Thiên thể sâu'], daylightAtmosphere: ['Atmosphere haze', 'Khí quyển'],
  cardinalDirections: ['Compass headings', 'La bàn 4 phương'], meteors: ['Meteors and showers', 'Sao băng'],
  camera: ['Camera FOV', 'Khung nhìn camera'], aircraft: ['Aircraft', 'Máy bay'], satellites: ['Satellites', 'Vệ tinh'], events: ['Camera events', 'Sự kiện camera'],
};
export const LAYER_DEFAULTS: Layers = {
  stars: true, milkyWay: true, starLabels: true, constellationLines: true, constellationLabels: true, planets: true, moon: true,
  ecliptic: true, calendarPoints: false, polarAxis: false, equator: false, raGrid: false, decGrid: false, meridian: false, altAzGrid: false,
  earthGlobe: true, seeThroughGround: false, deepSkyObjects: true, daylightAtmosphere: true, cardinalDirections: true, meteors: true,
  camera: true, aircraft: true, satellites: true, events: true,
};

/** A host-supplied moving/fixed object in horizontal coordinates. */
export interface Marker {
  id: string; label: string; az: number; alt: number;
  /** degrees per millisecond, used to extrapolate between host updates (needs `t` = performance.now() of the sample) */
  vaz?: number; valt?: number; t?: number;
  color: string; shape: 'square' | 'plane' | 'dot';
  showLabel?: boolean;
  /** optional streak end (events) */
  az2?: number; alt2?: number; ts?: number;
  rows?: [string, string][];
}
export type MarkerGroup = 'aircraft' | 'satellites' | 'events';

export interface Hit { x: number; y: number; r: number; kind: string; id: string; prio: number; data: Record<string, unknown> }
export interface InspectorData { title: string; kind: string; above: boolean; rows: [string, string][]; note?: string; az: number; alt: number }
export type { Vec3 };
