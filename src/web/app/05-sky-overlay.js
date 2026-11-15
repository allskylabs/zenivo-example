/* Zenivo dashboard (part of the former app.js): Celestial sky overlay, aircraft/satellite/radiosonde overlays.
 * Classic script: top-level declarations are shared with the other src/web/app/*.js files; load order is fixed in index.html. */

// ------------------------------------------------------------------
// Real-Time Celestial Sky Overlay (Stars, Constellations, Labels & Grid)
// Uses solved station parameters: (lat, lon, optical_center, r90, north_pa, zenith_tilt, azimuth)
// ------------------------------------------------------------------
// Low-precision Sun (Astronomical Almanac, ~0.01°) and Moon (main Meeus terms, ~1°) positions
function sunMoonEquatorial(dateObj) {
    const d2r = Math.PI / 180.0;
    const n = dateObj.getTime() / 86400000.0 + 2440587.5 - 2451545.0;
    const eps = (23.439 - 0.0000004 * n) * d2r;
    const toEq = (lonDeg, latDeg) => {
        const l = lonDeg * d2r, b = latDeg * d2r;
        const ra = Math.atan2(Math.sin(l) * Math.cos(eps) - Math.tan(b) * Math.sin(eps), Math.cos(l));
        const dec = Math.asin(Math.sin(b) * Math.cos(eps) + Math.cos(b) * Math.sin(eps) * Math.sin(l));
        return { ra: ((ra / d2r) % 360 + 360) % 360, dec: dec / d2r };
    };
    const g = (357.528 + 0.9856003 * n) * d2r;
    const sunLon = 280.46 + 0.9856474 * n + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g);
    const Lm = 218.316 + 13.176396 * n;
    const Mm = (134.963 + 13.064993 * n) * d2r;
    const F = (93.272 + 13.22935 * n) * d2r;
    const D = (297.85 + 12.190749 * n) * d2r;
    const moonLon = Lm + 6.289 * Math.sin(Mm) + 1.274 * Math.sin(2 * D - Mm) + 0.658 * Math.sin(2 * D) - 0.186 * Math.sin(g);
    const moonLat = 5.128 * Math.sin(F);
    const elong = Math.acos(Math.cos((moonLon - sunLon) * d2r) * Math.cos(moonLat * d2r));
    return {
        sun: toEq(sunLon, 0),
        moon: { ...toEq(moonLon, moonLat), illum: (1 - Math.cos(elong)) / 2 },
    };
}

// Moon illumination and Sun altitude tiles (computed locally from the station position; no backend field exists)
function updateAstroTiles() {
    const moonEl = document.getElementById("telem-moon");
    const sunEl = document.getElementById("telem-sun-alt");
    if (!moonEl && !sunEl) return;
    const d2r = Math.PI / 180.0;
    const lat = stationLatInput && !isNaN(parseFloat(stationLatInput.value)) ? parseFloat(stationLatInput.value) : 0;
    const lon = stationLonInput && !isNaN(parseFloat(stationLonInput.value)) ? parseFloat(stationLonInput.value) : 0;
    const now = new Date();
    const sm = sunMoonEquatorial(now);
    if (moonEl) moonEl.textContent = `${Math.round(sm.moon.illum * 100)}%`;
    if (sunEl) {
        const ha = (computeLmstDeg(lon, now) - sm.sun.ra) * d2r;
        const sinAlt = Math.sin(lat * d2r) * Math.sin(sm.sun.dec * d2r) + Math.cos(lat * d2r) * Math.cos(sm.sun.dec * d2r) * Math.cos(ha);
        const alt = Math.asin(Math.max(-1, Math.min(1, sinAlt))) / d2r;
        sunEl.textContent = `${alt >= 0 ? "+" : "\u2212"}${Math.abs(alt).toFixed(1)}\u00b0`;
    }
}
setInterval(updateAstroTiles, 30000);

// Planets move slowly: refresh every 10 min and draw them like bright stars in the sky overlay
function loadPlanets() {
    fetch("/api/planets").then((r) => (r.ok ? r.json() : [])).then((list) => {
        if (Array.isArray(list)) window.ALLSKY_PLANETS = list;
    }).catch(() => {});
}
if (currentPage === "tab-live") {
    loadPlanets();
    setInterval(loadPlanets, 600000);
}
setTimeout(updateAstroTiles, 0);

function computeLmstDeg(lonDeg, dateObj) {
    const jd = dateObj.getTime() / 86400000.0 + 2440587.5;
    const d = jd - 2451545.0;
    const t = d / 36525.0;
    const gmst =
        (280.46061837 +
            360.98564736629 * d +
            0.000387933 * t * t -
            (t * t * t) / 38710000.0) %
        360.0;
    return ((gmst + lonDeg) % 360.0 + 360.0) % 360.0;
}

function formatLstHms(lmstDeg) {
    const totalHours = ((lmstDeg % 360.0) + 360.0) % 360.0 / 15.0;
    const h = Math.floor(totalHours);
    const m = Math.floor((totalHours - h) * 60.0);
    const s = Math.floor(((totalHours - h) * 60.0 - m) * 60.0);
    return `${String(h).padStart(2, "0")}h${String(m).padStart(2, "0")}m${String(s).padStart(2, "0")}s`;
}
