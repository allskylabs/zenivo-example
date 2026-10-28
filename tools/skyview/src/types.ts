// Public types of the skyview module. The core never talks to a backend: hosts push data in through SkyCore.setMarkers / setCamera / setObserver.
import type { Vec3 } from '@astrophoto/sky-engine';

export interface ObserverCfg { latDeg: number; lonDeg: number; heightM?: number }
/** Camera field of view drawn as a labelled rotated frame (az/alt = pointing, degrees). */
export interface CameraFov { az: number; alt: number; roll: number; fovH: number; fovV: number; label?: string }
export type Lang = 'en' | 'vi';
export type ViewMode = 'allsky' | '3d' | 'eye';

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
  'polarAxis', 'equator', 'raGrid', 'decGrid', 'meridian', 'altAzGrid', 'seeThroughGround', 'deepSkyObjects',
  'daylightAtmosphere', 'cardinalDirections', 'meteors', 'camera', 'cameraImage', 'aircraft', 'satellites', 'events',
] as const;
export type LayerKey = typeof LAYER_KEYS[number];
export type Layers = Record<LayerKey, boolean>;

export const LAYER_LABELS: Record<LayerKey, [string, string]> = {
  stars: ['Stars', 'Ngôi sao'], milkyWay: ['Milky Way', 'Dải Ngân Hà'], starLabels: ['Star names', 'Tên sao'],
  constellationLines: ['Constellation lines', 'Nối các chòm sao'], constellationLabels: ['Constellation names', 'Tên chòm sao'],
  planets: ['Planets', 'Hành tinh'], moon: ['Sun and Moon', 'Mặt Trời, Mặt Trăng'], ecliptic: ['Ecliptic', 'Hoàng đạo'],
  calendarPoints: ['Equinox / solstice points', 'Điểm phân, điểm chí'], polarAxis: ['Celestial poles', 'Cực thiên cầu'],
  equator: ['Celestial equator', 'Xích đạo thiên cầu'], raGrid: ['RA grid', 'Lưới xích kinh (RA)'],
  decGrid: ['Dec grid', 'Lưới xích vĩ (Dec)'], meridian: ['Meridian', 'Kinh tuyến'], altAzGrid: ['Alt-az grid', 'Lưới độ cao - phương vị'],
 seeThroughGround: ['See through the ground', 'Nhìn xuyên mặt đất'],
  deepSkyObjects: ['Messier objects', 'Thiên thể Messier'], daylightAtmosphere: ['Atmosphere and daylight', 'Khí quyển, ánh sáng ngày'],
  cardinalDirections: ['Compass (N E S W)', 'Hướng N E S W'], meteors: ['Meteor showers', 'Mưa sao băng'],
  camera: ['Camera frame', 'Khung camera /live'], cameraImage: ['Camera picture', 'Ảnh camera /live'], aircraft: ['Aircraft', 'Máy bay'], satellites: ['Satellites', 'Vệ tinh'], events: ['Detected events', 'Sự kiện đã bắt'],
};
export const LAYER_DEFAULTS: Layers = {
  stars: true, milkyWay: true, starLabels: true, constellationLines: true, constellationLabels: true, planets: true, moon: true,
  ecliptic: true, calendarPoints: false, polarAxis: false, equator: false, raGrid: false, decGrid: false, meridian: false, altAzGrid: false,
  seeThroughGround: false, deepSkyObjects: true, daylightAtmosphere: true, cardinalDirections: true, meteors: true,
  camera: true, cameraImage: false, aircraft: true, satellites: true, events: true,
};

/** Layer preset applied while in the 'eye' (observer) mode; the user's own layers are restored on leaving. */
export const EYE_LAYERS: Layers = {
  ...LAYER_DEFAULTS,
  stars: true, milkyWay: true, moon: true, planets: true, cardinalDirections: true, daylightAtmosphere: true, aircraft: true, satellites: true, events: true,
  starLabels: false, constellationLines: true, constellationLabels: false, ecliptic: false, calendarPoints: false, polarAxis: false, equator: false,
  raGrid: false, decGrid: false, meridian: false, altAzGrid: false, seeThroughGround: false, deepSkyObjects: false, meteors: false, camera: true, cameraImage: true,
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

/** Option panel layout: sections in display order; `only` limits a section's item to one view mode. */
export const LAYER_GROUPS: { id: string; title: [string, string]; open: boolean; keys: { k: LayerKey; only?: ViewMode }[] }[] = [
  { id: 'sky', title: ['Sky', 'Bầu trời'], open: true, keys: [{ k: 'stars' }, { k: 'milkyWay' }, { k: 'daylightAtmosphere' }, { k: 'meteors' }] },
  { id: 'objects', title: ['Objects', 'Thiên thể'], open: true, keys: [{ k: 'moon' }, { k: 'planets' }, { k: 'starLabels' }, { k: 'deepSkyObjects' }] },
  { id: 'cons', title: ['Constellations', 'Chòm sao'], open: true, keys: [{ k: 'constellationLines' }, { k: 'constellationLabels' }] },
  { id: 'live', title: ['Live data', 'Dữ liệu trực tiếp'], open: true, keys: [{ k: 'camera' }, { k: 'cameraImage' }, { k: 'aircraft' }, { k: 'satellites' }, { k: 'events' }] },
  { id: 'ground', title: ['Horizon', 'Chân trời'], open: true, keys: [{ k: 'cardinalDirections' }, { k: 'seeThroughGround', only: '3d' }] },
  { id: 'lines', title: ['Lines and grids', 'Đường và lưới'], open: false, keys: [{ k: 'ecliptic' }, { k: 'equator' }, { k: 'meridian' }, { k: 'altAzGrid' }, { k: 'raGrid' }, { k: 'decGrid' }, { k: 'polarAxis' }, { k: 'calendarPoints' }] },
];
