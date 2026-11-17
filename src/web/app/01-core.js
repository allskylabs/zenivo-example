/* Zenivo dashboard (part of the former app.js): Shared state and DOM refs, clock and Red Vision, UI prefs, page routing.
 * Classic script: top-level declarations are shared with the other src/web/app/*.js files; load order is fixed in index.html. */

// Page routing basics first: the initial data loads below are gated on the page that shows them
const PAGES = {
    "/": "tab-live",
    "/live": "tab-live",
    "/index.html": "tab-live",
    "/gallery": "tab-gallery",
    "/settings": "tab-settings",
    "/calibration": "tab-calibration",
    "/logs": "tab-logs",
    "/focus": "tab-focus",
};
const currentPath = window.location.pathname.replace(/\/+$/, "") || "/";
const currentPage = PAGES[currentPath] || "tab-live";

const state = {
    mode: "auto",
    exposure_s: 0.04,
    gain_db: 12.0,
    wsConnected: false,
    ws: null,
    sseSource: null,
    streamPaused: false,
    prevFrameObjectUrl: null,
    nightVision: false,
};

// DOM Elements
const navClock = document.getElementById("nav-utc-clock");
const navLocalClock = document.getElementById("nav-local-clock");
const navLocalLabel = document.getElementById("nav-local-label");
const navStatus = document.getElementById("nav-system-status");
const navMode = document.getElementById("nav-camera-mode");
const btnNightVision = document.getElementById("btn-night-vision");
const hudStreamMode = document.getElementById("hud-stream-mode");

// Live View Elements
const liveImg = document.getElementById("live-stream-img");
const reticleCanvas = document.getElementById("reticle-canvas");
const chkSkyOverlay = document.getElementById("chk-sky-overlay");
const chkSkyLabels = document.getElementById("chk-sky-labels");
const chkGridOverlay = document.getElementById("chk-grid-overlay");
const chkEqGrid = document.getElementById("chk-eq-grid");
const chkAircraft = document.getElementById("chk-aircraft-overlay");
const chkSatellite = document.getElementById("chk-satellite-overlay");
const aircraftCanvas = document.getElementById("aircraft-canvas");
const chkSonde = document.getElementById("chk-sonde-overlay");
const chkAirports = document.getElementById("chk-airports-overlay");
const chkOverlayText = document.getElementById("chk-overlay-text");
const btnRefreshFrame = document.getElementById("btn-refresh-frame");
const btnFullscreen = document.getElementById("btn-fullscreen");
const viewportContainer = document.getElementById("viewport-container");

// Histogram Elements
const histCanvas = document.getElementById("histogram-canvas");
const histCtx = histCanvas.getContext("2d");
const histMean = document.getElementById("hist-mean");
const histStdDev = document.getElementById("hist-stddev");
const histMedian = document.getElementById("hist-median");
const histClipLow = document.getElementById("hist-clip-low");
const histClipHigh = document.getElementById("hist-clip-high");
const histCalcBadge = document.getElementById("hist-calc-badge");

// Controls Elements
const rangeExposure = document.getElementById("range-exposure");
const valExposure = document.getElementById("val-exposure");
const rangeGain = document.getElementById("range-gain");
const valGain = document.getElementById("val-gain");
const controlAlert = document.getElementById("control-alert");
const activeProfileBadge = document.getElementById("active-profile-badge");
const btnLockExposure = document.getElementById("lock-exposure");
const btnLockGain = document.getElementById("lock-gain");
const rangeTarget = document.getElementById("range-target");
const valTarget = document.getElementById("val-target");
const btnLockTarget = document.getElementById("lock-target");

// Telemetry Elements
const telemExp = document.getElementById("telem-exp");
const telemGain = document.getElementById("telem-gain");
const telemStars = document.getElementById("telem-stars");
const telemClouds = document.getElementById("telem-clouds");
const telemSkyStatus = document.getElementById("telem-sky-status");
// Rain (drops on the dome) is judged by the server from the frames; shown in place of the sky status
let rainNow = false;
async function pollRain() {
    try {
        const r = await fetch("/api/day-objects/status", { cache: "no-store" });
        if (!r.ok) return;
        const d = await r.json();
        rainNow = !!(d.tracker && d.tracker.raining);
        if (rainNow && telemSkyStatus) telemSkyStatus.textContent = "RAINING";
    } catch (e) { /* offline: keep the last state */ }
}
if (currentPage === "tab-live") {
    pollRain();
    setInterval(pollRain, 20000);
}
const telemFps = document.getElementById("telem-fps");
const telemTemp = document.getElementById("telem-temp");
const telemUptime = document.getElementById("telem-uptime");
const telemTimestamp = document.getElementById("telemetry-timestamp");
const liveFrameInfo = viewportContainer ? makeFrameInfo(viewportContainer) : null;
const hudHwDelta = document.getElementById("hud-hw-delta");

// Registers
const regVmax = document.getElementById("reg-vmax");
const regHmax = document.getElementById("reg-hmax");
const regShr0 = document.getElementById("reg-shr0");
const regPgc = document.getElementById("reg-pgc");

// Calibration Elements
const btnTriggerDark = document.getElementById("btn-trigger-dark");
const btnAutoMissingDark = document.getElementById("btn-auto-missing-dark");
const btnAutoFullDark = document.getElementById("btn-auto-full-dark");
const darkFramesCount = document.getElementById("dark-frames-count");
const darkExposure = document.getElementById("dark-exposure");
const darkGain = document.getElementById("dark-gain");
const darkAlert = document.getElementById("dark-alert");
const darkStatusBadge = document.getElementById("dark-status-badge");
const darkStatusText = document.getElementById("dark-status-text");
const darkProgressPct = document.getElementById("dark-progress-pct");
const darkProgressBar = document.getElementById("dark-progress-bar");
const darkLibraryBody = document.getElementById("dark-library-body");
const darkProfilesMatrixBody = document.getElementById("dark-profiles-matrix-body");

// Station Setup & Astrometric Calibration Elements
const stationNameInput = document.getElementById("station-name");
const stationLatInput = document.getElementById("station-lat");
const stationLonInput = document.getElementById("station-lon");
const stationElevInput = document.getElementById("station-elev");
const optFocalInput = document.getElementById("opt-focal");
const optFovHInput = document.getElementById("opt-fov-h");
const optFovVInput = document.getElementById("opt-fov-v");
const optFovDInput = document.getElementById("opt-fov-d");
const optCxInput = document.getElementById("opt-cx");
const optCyInput = document.getElementById("opt-cy");
const optR90Input = document.getElementById("opt-r90");
const oriAzInput = document.getElementById("ori-az");
const oriTiltInput = document.getElementById("ori-tilt");
const oriNpaInput = document.getElementById("ori-npa");
const orientFlipH = document.getElementById("orient-flip-h");
const orientFlipV = document.getElementById("orient-flip-v");
const orientRot180 = document.getElementById("orient-rot180");
const orientRot180Day = document.getElementById("orient-rot180-day");
const orientStatus = document.getElementById("orient-status");
const btnOrientReset = document.getElementById("btn-orient-reset");
const btnStationAutoGps = document.getElementById("btn-station-auto-gps");
const btnStationSolve = document.getElementById("btn-station-solve");
const chkStationDeep = document.getElementById("chk-station-deep");
const stationAlert = document.getElementById("station-alert");
const stationSolveBadge = document.getElementById("station-solve-badge");
const stSensor = document.getElementById("st-sensor");
const stFocal = document.getElementById("st-focal");
const stPixscale = document.getElementById("st-pixscale");
const stFovHv = document.getElementById("st-fov-hv");
const stFovDiag = document.getElementById("st-fov-diag");
const stOptCenter = document.getElementById("st-opt-center");
const stCenterOffset = document.getElementById("st-center-offset");
const stCoords = document.getElementById("st-coords");
const stRadec = document.getElementById("st-radec");
const stAzAlt = document.getElementById("st-az-alt");
const stTilt = document.getElementById("st-tilt");
const stNorthPa = document.getElementById("st-north-pa");
const stCdMatrix = document.getElementById("st-cd-matrix");
const stSolverMeta = document.getElementById("st-solver-meta");

// Gallery Elements
const productsGrid = document.getElementById("products-grid");
const dayList = document.getElementById("day-list");
const dayCountBadge = document.getElementById("day-count-badge");

// ------------------------------------------------------------------
// Clock & Night Vision
// ------------------------------------------------------------------
// Local wall-clock time for an ISO timestamp (any offset, incl. older UTC records): "YYYY-MM-DD HH:MM:SS"
// Run fn at most once per animation frame, however many events arrive (resize storms)
function rafDebounced(fn) {
    let raf = 0;
    return (...args) => {
        if (raf) return;
        raf = requestAnimationFrame(() => {
            raf = 0;
            fn(...args);
        });
    };
}
// Every wall-clock display uses the STATION's zone (the server publishes it in /api/station/config:
// timezone_info = {name: IANA or null, offset_min}), never the viewer's browser zone. Until it arrives the
// browser's own offset is used.
let STATION_TZ_NAME = null;
let STATION_UTC_OFFSET_MIN = -new Date().getTimezoneOffset();
// Offset (minutes) of the station zone at instant d: follows daylight saving when the zone has a name
function stationOffsetMin(d) {
    if (STATION_TZ_NAME) {
        try {
            const p = {};
            for (const x of new Intl.DateTimeFormat("en-US", { timeZone: STATION_TZ_NAME, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", second: "numeric" }).formatToParts(d)) p[x.type] = x.value;
            return Math.round((Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - Math.floor(d.getTime() / 1000) * 1000) / 60000);
        } catch (e) { /* unknown to this browser: the fixed offset */ }
    }
    return STATION_UTC_OFFSET_MIN;
}
// Date whose getUTC* fields read as station wall-clock time
function stationDate(d) {
    return new Date(d.getTime() + stationOffsetMin(d) * 60000);
}
function makeTzLabel() {
    const m = stationOffsetMin(new Date());
    return `UTC${m >= 0 ? "+" : "-"}${Math.floor(Math.abs(m) / 60)}${Math.abs(m) % 60 ? ":" + String(Math.abs(m) % 60).padStart(2, "0") : ""}`;
}
let tzLabel = makeTzLabel();
function setStationTz(info) {
    if (!info || typeof info.offset_min !== "number") return;
    STATION_TZ_NAME = info.name || null;
    STATION_UTC_OFFSET_MIN = info.offset_min;
    tzLabel = makeTzLabel();
}
(window.zenivoStationConfig ? window.zenivoStationConfig() : fetch("/api/station/config").then((r) => (r.ok ? r.json() : null))).then((st) => { if (st) setStationTz(st.timezone_info); }).catch(() => {});
function fmtLocalIso(iso, timeOnly) {
    const d = new Date(iso);
    if (isNaN(d)) return iso || "";
    const sd = stationDate(d);
    const p = (n) => String(n).padStart(2, "0");
    const hms = `${p(sd.getUTCHours())}:${p(sd.getUTCMinutes())}:${p(sd.getUTCSeconds())}`;
    return timeOnly ? hms : `${sd.getUTCFullYear()}-${p(sd.getUTCMonth() + 1)}-${p(sd.getUTCDate())} ${hms}`;
}

// Capture info (time / exposure / gain / brightness) is drawn as HTML over images, never into pixels
function fmtExposure(exp) {
    return exp < 0.01 ? (exp * 1000).toFixed(2) + " ms" : exp < 0.1 ? (exp * 1000).toFixed(1) + " ms" : exp.toFixed(exp < 1 ? 3 : 1) + " s";
}
function setFrameInfo(el, time, exp, gain, brightness, events) {
    if (!el) return;
    if (!time) {
        el.hidden = true;
        return;
    }
    const parts = [];
    if (typeof exp === "number") parts.push(`Exp ${fmtExposure(exp)}`);
    if (typeof gain === "number") parts.push(`Gain ${gain.toFixed(1)} dB`);
    el.innerHTML = `<div>${fmtLocalIso(time)} ${tzLabel}</div>`
        + (parts.length ? `<div>${parts.join("  ")}</div>` : "")
        + (typeof brightness === "number" ? `<div>Brightness ${brightness.toFixed(0)}/255</div>` : "")
        + (Array.isArray(events) && events.length ? `<div class="frame-event">Event: ${[...new Set(events)].map(escapeHtml).join(", ")}</div>` : "");
    el.hidden = false;
}
function makeFrameInfo(parent) {
    const el = document.createElement("div");
    el.className = "frame-info";
    el.hidden = true;
    parent.appendChild(el);
    return el;
}
// Timelapse <video>: per-frame info from the sidecar JSON written next to the MP4
function attachVideoInfo(video, infoUrl) {
    if (!video || !infoUrl) return;
    const el = makeFrameInfo(video.parentElement);
    fetch(`${infoUrl}${infoUrl.includes("?") ? "&" : "?"}t=${Date.now()}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((info) => {
            if (!info || !Array.isArray(info.frames) || !info.frames.length) return;
            const fps = info.fps || 12;
            let last = -1;
            const show = () => {
                const i = Math.min(info.frames.length - 1, Math.max(0, Math.floor(video.currentTime * fps + 1e-3)));
                if (i === last) return;
                last = i;
                const [t, exp, gain, bri, evs] = info.frames[i];
                setFrameInfo(el, t, exp, gain, bri, evs);
            };
            if (video.requestVideoFrameCallback) {
                const tick = () => {
                    show();
                    video.requestVideoFrameCallback(tick);
                };
                video.requestVideoFrameCallback(tick);
            }
            video.addEventListener("timeupdate", show);
            video.addEventListener("seeked", show);
            show();
        })
        .catch(() => {});
}

function updateClock() {
    const nowIso = new Date().toISOString();
    navClock.textContent = nowIso.slice(11, 19);
    if (navLocalClock) navLocalClock.textContent = fmtLocalIso(nowIso, true).slice(0, 5);
    if (navLocalLabel) navLocalLabel.textContent = `Local (${tzLabel.replace("UTC", "GMT")})`;
}
setInterval(updateClock, 1000);
updateClock();

// ------------------------------------------------------------------
// Per-browser UI preferences (overlays, zoom, night mode) kept across visits
// ------------------------------------------------------------------
const PREFS_KEY = "allsky.ui.prefs.v1";
const prefs = (() => {
    try {
        return JSON.parse(localStorage.getItem(PREFS_KEY)) || {};
    } catch (e) {
        return {};
    }
})();
function savePrefs(patch) {
    Object.assign(prefs, patch);
    try {
        localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch (e) {
        /* storage unavailable: preferences only last for this visit */
    }
}

function applyNightVision(on) {
    state.nightVision = on;
    document.body.classList.toggle("night-mode", on);
    if (on) document.documentElement.setAttribute("data-theme", "red");
    else document.documentElement.removeAttribute("data-theme");
    btnNightVision.classList.toggle("btn-primary", on);
    btnNightVision.setAttribute("aria-checked", on ? "true" : "false");
}
applyNightVision(!!prefs.nightVision);

btnNightVision.addEventListener("click", () => {
    applyNightVision(!state.nightVision);
    savePrefs({ nightVision: state.nightVision });
});

// Mobile (<=768px) 5-tab bar + More sheet
(function initMobileNav() {
    const bar = document.getElementById("m-tabbar");
    const moreBtn = document.getElementById("m-more-btn");
    const sheet = document.getElementById("m-more-sheet");
    const backdrop = document.getElementById("m-sheet-backdrop");
    const rvProxy = document.getElementById("m-rv-toggle");
    if (!bar || !moreBtn || !sheet || !backdrop) return;
    const path = window.location.pathname.replace(/\/+$/, "") || "/";
    document.querySelectorAll("[data-mpath]").forEach((el) => {
        const hit = el.getAttribute("data-mpath").split(",").includes(path);
        el.classList.toggle("active", hit);
        if (hit && el.tagName === "A") el.setAttribute("aria-current", "page");
    });
    const setOpen = (open) => {
        sheet.hidden = !open;
        backdrop.hidden = !open;
        moreBtn.setAttribute("aria-expanded", open ? "true" : "false");
    };
    moreBtn.addEventListener("click", () => setOpen(sheet.hidden));
    backdrop.addEventListener("click", () => setOpen(false));
    sheet.querySelectorAll("a.m-sheet-item").forEach((a) => a.addEventListener("click", () => setOpen(false)));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
    if (rvProxy) {
        const sync = () => {
            rvProxy.classList.toggle("btn-primary", !!state.nightVision);
            rvProxy.setAttribute("aria-checked", state.nightVision ? "true" : "false");
        };
        rvProxy.addEventListener("click", () => { btnNightVision.click(); sync(); });
        btnNightVision.addEventListener("click", sync);
        sync();
    }
})();

// Overlays default to hidden; restore the viewer's last choice and sync with OBS/server
[
    [chkSkyOverlay, "skyOverlay", "sky_overlay"],
    [chkSkyLabels, "skyLabels", "sky_labels"],
    [chkGridOverlay, "gridOverlay", "grid_overlay"],
].forEach(([chk, prefKey, apiKey]) => {
    if (!chk) return;
    chk.checked = !!prefs[prefKey];
    chk.addEventListener("change", () => {
        savePrefs({ [prefKey]: chk.checked });
        if (!(window.zenivoAuth && window.zenivoAuth.admin)) return;  // guests keep the choice in this browser only
        fetch("/api/obs/overlay", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [apiKey]: chk.checked }),
        }).catch(() => {});
    });
});

if (chkEqGrid) {
    chkEqGrid.checked = !!prefs.eqGrid;
    chkEqGrid.addEventListener("change", () => savePrefs({ eqGrid: chkEqGrid.checked }));
}

if (chkSatellite) {
    chkSatellite.checked = !!prefs.satelliteOverlay;
    chkSatellite.addEventListener("change", () => {
        savePrefs({ satelliteOverlay: chkSatellite.checked });
        if (typeof sendOverlaySubscription === "function") sendOverlaySubscription();
        if (chkSatellite.checked) refreshSatellites(); else drawAircraft();
    });
}

if (chkSonde) {
    chkSonde.checked = !!prefs.sondeOverlay;
    chkSonde.addEventListener("change", () => {
        savePrefs({ sondeOverlay: chkSonde.checked });
        if (typeof sendOverlaySubscription === "function") sendOverlaySubscription();
        if (chkSonde.checked) refreshSondes(); else drawAircraft();
    });
}

if (chkAirports) {
    chkAirports.checked = !!prefs.airportsOverlay;
    chkAirports.addEventListener("change", () => {
        savePrefs({ airportsOverlay: chkAirports.checked });
        if (typeof refreshAirports === "function") refreshAirports(); else drawAircraft();
    });
}

// "Overlay text": the capture-info box (time, exposure) and the sidereal-time line drawn on the live picture; on by default
if (chkOverlayText) {
    const applyText = () => {
        if (viewportContainer) viewportContainer.classList.toggle("no-otext", !chkOverlayText.checked);
        if (typeof drawReticle === "function") drawReticle();
    };
    chkOverlayText.checked = prefs.overlayText !== false;
    chkOverlayText.addEventListener("change", () => { savePrefs({ overlayText: chkOverlayText.checked }); applyText(); });
    if (viewportContainer) viewportContainer.classList.toggle("no-otext", !chkOverlayText.checked);
}

if (chkAircraft) {
    chkAircraft.checked = !!prefs.aircraftOverlay;
    chkAircraft.addEventListener("change", () => {
        savePrefs({ aircraftOverlay: chkAircraft.checked });
        if (typeof sendOverlaySubscription === "function") sendOverlaySubscription();
        if (chkAircraft.checked) refreshAircraft(); else drawAircraft();
    });
}

// Initialize with synchronized OBS overlay state if available
if (currentPage === "tab-live") fetch("/api/obs/overlay")
    .then((r) => r.json())
    .then((data) => {
        if (data && typeof data === "object") {
            if (chkSkyOverlay && typeof data.sky_overlay === "boolean") chkSkyOverlay.checked = data.sky_overlay;
            if (chkSkyLabels && typeof data.sky_labels === "boolean") chkSkyLabels.checked = data.sky_labels;
            if (chkGridOverlay && typeof data.grid_overlay === "boolean") chkGridOverlay.checked = data.grid_overlay;
            drawReticle();
        }
    })
    .catch(() => {});

// ------------------------------------------------------------------
// Page Routing: every section has its own URL (/live, /gallery, /settings, /calibration)
// ------------------------------------------------------------------
const PAGE_TITLES = {
    "tab-live": "Live",
    "tab-gallery": "Gallery & Events",
    "tab-settings": "Settings",
    "tab-calibration": "Master Dark",
    "tab-logs": "Activity Log",
    "tab-focus": "Focus",
};
document.querySelectorAll(".tab-content").forEach((c) => c.classList.toggle("active", c.id === currentPage));
document.querySelectorAll(".tab-btn").forEach((b) => {
    const isActive = b.getAttribute("data-tab") === currentPage;
    b.classList.toggle("active", isActive);
    if (isActive) b.setAttribute("aria-current", "page");
});
// On phone widths the tab bar scrolls sideways: bring the current tab into view without scrolling the page.
const tabBar = document.querySelector(".tab-bar");
const activeTab = tabBar && tabBar.querySelector(".tab-btn.active");
if (tabBar && activeTab && tabBar.scrollWidth > tabBar.clientWidth) {
    tabBar.scrollLeft = activeTab.offsetLeft - (tabBar.clientWidth - activeTab.offsetWidth) / 2;
}
document.title = currentPage === "tab-live" ? "Zenivo — All-sky observatory" : `${PAGE_TITLES[currentPage]} — Zenivo`;

// ------------------------------------------------------------------
// Settings autosave: one save model for every Settings card, with a status chip in the card header
//   "Saving…" -> "Saved ✓" (fades after ~2 s) -> "Not saved: <reason>" (stays until the next change retries).
// settingsSave(el, fn): run one save and show its outcome in the chip of the card holding `el`.
// autosave(card, saveFn): wire every editable field of a card: text / number / textarea save on `change` (blur or
// Enter) after a short debounce, switches / selects / checkboxes at once. Invalid fields are not sent: the chip says why.
// Filling a form from the API never fires `change`, so loading does not save.
// ------------------------------------------------------------------
const settingsChips = new WeakMap();

function settingsChip(el) {
    const card = el && el.closest ? el.closest(".card") : null;
    if (!card) return null;
    let c = settingsChips.get(card);
    if (c) return c;
    const node = document.createElement("span");
    node.className = "save-chip";
    node.setAttribute("role", "status");
    node.setAttribute("aria-live", "polite");
    node.hidden = true;
    const actions = card.querySelector(".page-header-actions");
    const head = card.querySelector(".card-header, .page-header");
    if (actions) actions.insertBefore(node, actions.firstChild);
    else if (head && head.firstElementChild) head.firstElementChild.after(node);
    else if (head) head.appendChild(node);
    else card.insertBefore(node, card.firstChild);
    c = { node, seq: 0, timer: null };
    settingsChips.set(card, c);
    return c;
}

function settingsChipShow(c, state, text) {
    clearTimeout(c.timer);
    c.node.hidden = false;
    c.node.dataset.state = state;
    c.node.textContent = text;
    if (state === "saved") c.timer = setTimeout(() => { c.node.hidden = true; }, 2000);
}

// Human reason for a failed save: 401 -> sign in, FastAPI validation / detail -> its message
function saveErrorText(err) {
    if (err && err.status === 401) return "Sign in as admin";
    const m = err && err.message ? String(err.message) : "unknown error";
    return m.length > 140 ? `${m.slice(0, 137)}…` : m;
}

// POST/PUT JSON and return the parsed body; throws an Error (with .status) built from the server's `detail`
async function sendJson(url, body, method = "POST") {
    const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok || (data && data.success === false)) {
        const d = data && data.detail;
        const msg = Array.isArray(d) ? d.map((e) => `${(e.loc || []).slice(1).join(".")} ${e.msg}`.trim()).join("; ")
            : d ? (typeof d === "string" ? d : JSON.stringify(d)) : (res.status === 401 ? "Sign in as admin" : `HTTP ${res.status}`);
        const err = new Error(msg);
        err.status = res.status;
        throw err;
    }
    return data;
}

// Runs fn() and reports in the card's chip. opts.field: an input that must be valid first. Resolves to true / false.
async function settingsSave(el, fn, opts = {}) {
    const c = settingsChip(el);
    const f = opts.field;
    if (f && f.checkValidity && !f.checkValidity()) {
        const label = (f.labels && f.labels[0] ? f.labels[0].textContent : f.id || "Value").trim().replace(/\s*[:(].*$/, "");
        if (c) settingsChipShow(c, "error", `Not saved: ${label}: ${f.validationMessage}`);
        return false;
    }
    const seq = c ? ++c.seq : 0;
    if (c) settingsChipShow(c, "saving", "Saving…");
    try {
        await fn();
        if (c && c.seq === seq) settingsChipShow(c, "saved", "Saved ✓");
        return true;
    } catch (err) {
        if (c) settingsChipShow(c, "error", `Not saved: ${saveErrorText(err)}`);
        return false;
    }
}

const AUTOSAVE_SKIP = "[readonly],[type=button],[type=submit],[type=file],[data-no-autosave]";
function autosave(card, saveFn, opts = {}) {
    if (!card) return null;
    const debounceMs = opts.debounceMs === undefined ? 600 : opts.debounceMs;
    const timers = new Map();
    const fire = (el) => { timers.delete(el); return settingsSave(el, () => saveFn(el), { field: el }); };
    const fields = card.querySelectorAll(opts.selector || "input, select, textarea");
    fields.forEach((el) => {
        if (el.matches(AUTOSAVE_SKIP)) return;
        const instant = el.tagName === "SELECT" || el.type === "checkbox" || el.type === "radio";
        el.addEventListener("change", () => {
            clearTimeout(timers.get(el));
            if (instant || !debounceMs) { fire(el); return; }
            timers.set(el, setTimeout(() => fire(el), debounceMs));
        });
    });
    return { flush: () => Promise.all([...timers.keys()].map((el) => { clearTimeout(timers.get(el)); return fire(el); })) };
}
