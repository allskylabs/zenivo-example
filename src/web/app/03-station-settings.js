/* Zenivo dashboard (part of the former app.js): Settings pages: station and plate solving, orientation, resolution, star boost, performance.
 * Classic script: top-level declarations are shared with the other src/web/app/*.js files; load order is fixed in index.html. */

// Station Setup, Manual Settings & Astrometric Plate-Solving (remote plate solver)
// ------------------------------------------------------------------
function azimuthToCardinal(az) {
    const dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
    const idx = Math.round((((az % 360) + 360) % 360) / 22.5) % 16;
    return dirs[idx];
}

// Hand edits save themselves (autosave, wired in 04-live-view.js); plate-solve and locate save on the server as well.
// While a field has an edit that is not saved yet, the periodic status refresh must not overwrite it.
let stationFormDirty = false;
function setStationFormDirty(dirty) {
    stationFormDirty = dirty;
}
document.querySelectorAll('[data-sgroup="station"] .settings-grid input:not([readonly])').forEach((el) =>
    el.addEventListener("input", () => setStationFormDirty(true))
);

function updateStationUI(st) {
    if (!st) return;
    state.station = st;

    const latVal = st.latitude_deg !== undefined ? st.latitude_deg : st.latitude;
    const lonVal = st.longitude_deg !== undefined ? st.longitude_deg : st.longitude;
    const nameVal = st.location_name || st.station_name || "My station";
    const sensor = st.camera_sensor || {};
    const subtitle = document.getElementById("sensor-subtitle");
    if (subtitle) subtitle.textContent = `IMX335 · ${String(nameVal).split(",")[0].trim()}`;
    const optics = st.optics || st.camera || {};
    const ori = st.orientation || {};

    // Populate inputs unless the user has unsaved edits or is typing in them
    if (!stationFormDirty) {
        if (stationNameInput && document.activeElement !== stationNameInput) {
            stationNameInput.value = nameVal;
        }
        if (stationLatInput && document.activeElement !== stationLatInput && latVal !== undefined) {
            stationLatInput.value = Number(latVal).toFixed(5);
        }
        if (stationLonInput && document.activeElement !== stationLonInput && lonVal !== undefined) {
            stationLonInput.value = Number(lonVal).toFixed(5);
        }
        if (stationElevInput && document.activeElement !== stationElevInput && st.elevation_m !== undefined) {
            stationElevInput.value = Math.round(Number(st.elevation_m));
        }

        // Populate manual optical & orientation adjustment inputs
        if (optFocalInput && document.activeElement !== optFocalInput && optics.focal_length_mm !== undefined) {
            optFocalInput.value = Number(optics.focal_length_mm).toFixed(2);
        }
        if (optFovHInput && document.activeElement !== optFovHInput && optics.fov_horizontal_deg !== undefined) {
            optFovHInput.value = Number(optics.fov_horizontal_deg).toFixed(1);
        }
        if (optFovVInput && document.activeElement !== optFovVInput && optics.fov_vertical_deg !== undefined) {
            optFovVInput.value = Number(optics.fov_vertical_deg).toFixed(1);
        }
        if (optFovDInput && document.activeElement !== optFovDInput && optics.fov_diagonal_deg !== undefined) {
            optFovDInput.value = Number(optics.fov_diagonal_deg).toFixed(1);
        }
        if (optCxInput && document.activeElement !== optCxInput && optics.optical_center_x_px !== undefined) {
            optCxInput.value = Number(optics.optical_center_x_px).toFixed(1);
        }
        if (optCyInput && document.activeElement !== optCyInput && optics.optical_center_y_px !== undefined) {
            optCyInput.value = Number(optics.optical_center_y_px).toFixed(1);
        }
        if (optR90Input && document.activeElement !== optR90Input && optics.horizon_radius_90_px !== undefined) {
            optR90Input.value = Number(optics.horizon_radius_90_px).toFixed(1);
        }
        if (oriAzInput && document.activeElement !== oriAzInput && ori.azimuth_deg !== undefined) {
            oriAzInput.value = Number(ori.azimuth_deg).toFixed(1);
        }
        if (oriTiltInput && document.activeElement !== oriTiltInput && ori.zenith_tilt_deg !== undefined) {
            oriTiltInput.value = Number(ori.zenith_tilt_deg).toFixed(2);
        }
        const remoteSolverInput = document.getElementById("station-remote-solver");
        if (remoteSolverInput && document.activeElement !== remoteSolverInput && st.remote_solver_url !== undefined) {
            const ru = st.remote_solver_url || "";
            let custom = remoteSolverInput.querySelector("option[data-custom]");
            if (custom) custom.remove();
            if (ru !== "" && ru !== "https://virtuallab3d.com") {
                // A value saved earlier that is not in the list: show it read-only so it is not silently lost
                custom = document.createElement("option");
                custom.dataset.custom = "1";
                custom.value = ru;
                custom.textContent = "Custom: " + ru;
                remoteSolverInput.appendChild(custom);
            }
            remoteSolverInput.value = ru;
        }
        if (oriNpaInput && document.activeElement !== oriNpaInput && ori.north_position_angle_deg !== undefined) {
            oriNpaInput.value = Number(ori.north_position_angle_deg).toFixed(2);
        }
    }

    syncOrientationUI(st.image_transform);
    syncResolutionUI(st.output_resolution);

    if (stationSolveBadge) {
        if (String(st.solve_source || "").startsWith("live_frame") && (st.solved_at || st.last_solved_at)) {
            stationSolveBadge.textContent = "SOLVED (WCS)";
            stationSolveBadge.className = "badge badge-success";
        } else if (st.solve_source === "manual_ui_settings") {
            stationSolveBadge.textContent = "MANUAL SETTINGS";
            stationSolveBadge.className = "badge badge-accent";
        } else {
            stationSolveBadge.textContent = "NOT SOLVED YET";
            stationSolveBadge.className = "badge badge-accent";
        }
    }

    if (stSensor) {
        stSensor.textContent = `${sensor.sensor_model || "Sony Starvis IMX335"} (${sensor.width_px || 2592}×${sensor.height_px || 1944}, ${sensor.pixel_pitch_um || 2.0}µm)`;
    }
    if (stFocal) {
        stFocal.textContent = `${Number(optics.focal_length_mm || 1.65).toFixed(2)} mm (${optics.lens_model || "Fisheye AllSky"})`;
    }
    if (stPixscale) {
        stPixscale.textContent = `${Number(optics.pixel_scale_arcsec_px || 250.0).toFixed(2)} arcsec/px`;
    }
    if (stFovHv) {
        stFovHv.textContent = `${Number(optics.fov_horizontal_deg || 180.0).toFixed(1)}° × ${Number(optics.fov_vertical_deg || 135.0).toFixed(1)}°`;
    }
    if (stFovDiag) {
        stFovDiag.textContent = `${Number(optics.fov_diagonal_deg || 225.0).toFixed(1)}°`;
    }
    if (stOptCenter) {
        stOptCenter.textContent = `(${Number(optics.optical_center_x_px || 1296.0).toFixed(1)}, ${Number(optics.optical_center_y_px || 972.0).toFixed(1)}) px`;
    }
    if (stCenterOffset) {
        const off = Array.isArray(optics.sensor_center_offset_px)
            ? { dx: optics.sensor_center_offset_px[0], dy: optics.sensor_center_offset_px[1] }
            : optics.sensor_center_offset_px || { dx: 0.0, dy: 0.0 };
        const r90 = Number(optics.horizon_radius_90_px || 894.2).toFixed(0);
        stCenterOffset.textContent = `Δ(${Number(off.dx || 0).toFixed(1)}, ${Number(off.dy || 0).toFixed(1)}) px • R90=${r90} px`;
    }
    if (stCoords) {
        const lat = Number(latVal !== undefined ? latVal : 0);
        const lon = Number(lonVal !== undefined ? lonVal : 0);
        const elev = Math.round(Number(st.elevation_m || 20));
        stCoords.textContent = `${Math.abs(lat).toFixed(5)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(5)}°${lon >= 0 ? "E" : "W"} (${elev}m)`;
    }
    if (stRadec) {
        if (ori.center_ra_hms && ori.center_ra_hms !== "--") {
            stRadec.textContent = `${ori.center_ra_hms} • ${ori.center_dec_dms}`;
        } else if (ori.center_ra_deg !== null && ori.center_ra_deg !== undefined) {
            stRadec.textContent = `RA ${Number(ori.center_ra_deg).toFixed(3)}° • Dec ${Number(ori.center_dec_deg || 0).toFixed(3)}°`;
        } else {
            stRadec.textContent = `--`;
        }
    }
    if (stAzAlt) {
        const az = Number(ori.azimuth_deg !== undefined ? ori.azimuth_deg : 180.0);
        const alt = Number(ori.altitude_deg !== undefined ? ori.altitude_deg : 90.0);
        stAzAlt.textContent = `Az ${az.toFixed(1)}° (${azimuthToCardinal(az)}) • Alt ${alt.toFixed(1)}°`;
    }
    if (stTilt) {
        stTilt.textContent = `${Number(ori.zenith_tilt_deg || 0).toFixed(2)}°`;
    }
    if (stNorthPa) {
        stNorthPa.textContent = `${Number(ori.north_position_angle_deg || 0).toFixed(2)}°`;
    }
    if (stCdMatrix && Array.isArray(ori.cd_matrix) && ori.cd_matrix.length === 4) {
        stCdMatrix.textContent = `[${ori.cd_matrix.map((v) => Number(v).toExponential(2)).join(", ")}]`;
    }
    if (stSolverMeta) {
        const consts = Array.isArray(st.constellations_in_fov) && st.constellations_in_fov.length > 0
            ? st.constellations_in_fov.slice(0, 4).join(", ")
            : "No constellations solved yet";
        const src = st.solve_source || "not_solved_yet";
        const stars = st.matched_stars_count ? ` (${st.matched_stars_count} stars)` : "";
        stSolverMeta.textContent = `${src}${stars} • ${consts}`;
    }

    const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
    setupTimezoneSelect(st);
    setVal("st-f-sensor", sensor.sensor_model || "Sony Starvis IMX335");
    setVal("st-f-pixel", Number(sensor.pixel_pitch_um || 2.0).toFixed(1));
    setVal("st-f-lens", optics.lens_model || "Fisheye AllSky");
    setVal("st-f-circle", optics.horizon_radius_90_px ? `${Math.round(Number(optics.horizon_radius_90_px) * 2)} diameter` : "--");
    const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    const solvedAt = st.solved_at || st.last_solved_at;
    const solvedDate = solvedAt ? new Date(solvedAt) : null;
    setTxt("st-solve-last", solvedDate && !isNaN(solvedDate) ? solvedDate.toISOString().slice(11, 19) + " UTC" : "--");
    setTxt("st-solve-stars", st.matched_stars_count ? String(st.matched_stars_count) : "--");
    setTxt("st-solve-source", st.solve_source || "not solved yet");

    drawReticle();
}

// ------------------------------------------------------------------
// Image Orientation (Mirror / Flip / Rotate 180°)
// ------------------------------------------------------------------
let orientationBusy = false;

function syncOrientationUI(tf) {
    if (!tf || orientationBusy) return;
    if (orientFlipH) orientFlipH.checked = !!tf.flip_horizontal;
    if (orientFlipV) orientFlipV.checked = !!tf.flip_vertical;
    if (orientRot180) orientRot180.checked = Number(tf.rotate_deg || 0) % 360 === 180;
    if (orientRot180Day) orientRot180Day.checked = !!tf.rotate_isp_180;
    if (orientStatus) {
        const parts = [];
        if (tf.flip_horizontal) parts.push("Horizontal mirror");
        if (tf.flip_vertical) parts.push("Vertical flip");
        if (Number(tf.rotate_deg || 0) % 360 === 180) parts.push("Rotate 180°");
        if (tf.rotate_isp_180) parts.push("Day frames +180°");
        orientStatus.textContent = parts.length ? `Active: ${parts.join(" + ")}` : "Native sensor orientation (no transform)";
        orientStatus.className = parts.length ? "text-accent" : "text-muted";
    }
}

async function saveOrientation(payload) {
    const toggles = [orientFlipH, orientFlipV, orientRot180, orientRot180Day];
    orientationBusy = true;
    toggles.forEach((t) => t && (t.disabled = true));
    const ok = await settingsSave(orientFlipH, async () => {
        const data = await sendJson("/api/camera/orientation", payload);
        orientationBusy = false;
        if (data.station_config) updateStationUI(data.station_config);
        drawReticle();
        if (liveImg) liveImg.src = `/api/live/snapshot.jpg?t=${Date.now()}`;
    });
    orientationBusy = false;
    toggles.forEach((t) => t && (t.disabled = false));
    if (!ok && state.station) syncOrientationUI(state.station.image_transform);
}

// ------------------------------------------------------------------
// Output Resolution
// ------------------------------------------------------------------
const outputResSelect = document.getElementById("output-resolution");
const outputResBadge = document.getElementById("output-res-badge");
const hudResolution = document.getElementById("hud-resolution");

// Single source of truth for derived sizes: one master frame, every output = min(chosen width, master width)
const SIZE_STEPS = [648, 960, 1296, 1920];
const sizeState = { master: null, perf: null };
const effectiveSize = (width) => {
    const m = sizeState.master;
    if (!m) return null;
    const w = Math.min(Number(width) || m.width, m.width);
    let h = Math.round(m.height * w / m.width);
    if (h % 2) h += 1;  // even height: H.264 friendly, same rule as the archiver
    return { width: w, height: h };
};
const fmtSize = (sz) => (sz ? `${sz.width} × ${sz.height}` : "--");

function fillSizeSelects() {
    const m = sizeState.master;
    if (!m) return;
    document.querySelectorAll("select[data-size-select]").forEach((el) => {
        const keep = el.value;
        const widths = SIZE_STEPS.filter((w) => w < m.width);
        el.innerHTML = "";
        widths.concat([m.width]).forEach((w) => {
            const opt = document.createElement("option");
            opt.value = String(w);
            opt.textContent = w === m.width ? `${w} px (same as image size)` : `${w} px`;
            el.appendChild(opt);
        });
        if (keep && [...el.options].some((o) => o.value === keep)) el.value = keep;
    });
}

function renderSizePipeline() {
    const cur = (sizeState.perf && sizeState.perf.current) || {};
    const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
    set("size-live", fmtSize(effectiveSize(cur.stream_max_width)));
    set("size-analysis", fmtSize(effectiveSize(cur.analysis_max_width)));
    set("size-archive", sizeState.perf && sizeState.perf.archive_width ? fmtSize(effectiveSize(sizeState.perf.archive_width)) : "--");
}

function syncResolutionUI(res) {
    if (!res || !res.width) return;
    sizeState.master = { width: Number(res.width), height: Number(res.height) };
    fillSizeSelects();
    if (sizeState.perf) syncPerformanceUI(sizeState.perf);
    renderSizePipeline();
    const txt = `${res.width} × ${res.height}`;
    if (outputResBadge) outputResBadge.textContent = txt;
    if (hudResolution) hudResolution.textContent = txt;
    if (outputResSelect && document.activeElement !== outputResSelect) {
        outputResSelect.value = `${res.width}x${res.height}`;
    }
}

// ------------------------------------------------------------------
// Camera picker (several cameras attached)
// ------------------------------------------------------------------
const cameraSelect = document.getElementById("camera-device");
const cameraBadge = document.getElementById("camera-current-badge");
const cameraRescan = document.getElementById("camera-rescan");

let cameraHints = [];

async function loadCameraDevices() {
    if (!cameraSelect) return 0;
    let count = 0;
    try {
        const res = await fetch("/api/camera/devices");
        if (!res.ok) throw new Error(res.status === 401 ? "sign in as admin to list cameras" : res.statusText);
        const data = await res.json();
        count = (data.devices || []).length;
        cameraHints = data.hints || [];
        cameraSelect.innerHTML = "";
        const cur = data.current || {};
        const add = (value, label) => {
            const opt = document.createElement("option");
            opt.value = value;
            opt.textContent = label;
            cameraSelect.appendChild(opt);
        };
        (data.devices || []).forEach((d) => add(d.id, d.name));
        add("simulator:0", "Simulator (no hardware)");
        const found = Array.from(cameraSelect.options).some((o) => o.value === cur.id);
        if (!found && cur.id) add(cur.id, cur.sensor || cur.id);
        cameraSelect.value = cur.id || "simulator:0";
        if (cameraBadge) cameraBadge.textContent = cur.width ? `${cur.width} × ${cur.height}` : "--";
    } catch (err) {
        showAlert(stationAlert, `❌ Could not list cameras: ${err.message}`, "error", true);
    }
    return count;
}

if (cameraSelect) {
    cameraSelect.addEventListener("change", async () => {
        const [type, index] = cameraSelect.value.split(":");
        cameraSelect.disabled = true;
        const ok = await settingsSave(cameraSelect, async () => {
            await sendJson("/api/camera/select", { type, index: Number(index) });
        });
        cameraSelect.disabled = false;
        await loadCameraDevices();
        if (ok) { loadResolutionOptions(); loadStationConfig(); }
    });
}
if (cameraRescan) {
    cameraRescan.addEventListener("click", async () => {
        const note = document.getElementById("camera-scan-note");
        cameraRescan.disabled = true;
        if (note) note.textContent = "Scanning for cameras…";
        const n = await loadCameraDevices();
        cameraRescan.disabled = false;
        const found = n ? `${n} camera${n > 1 ? "s" : ""} found.` : "No camera found (plug it in, check the vendor SDK, then Rescan).";
        if (note) note.textContent = [found, ...cameraHints].join(" ");
    });
}

async function loadResolutionOptions() {
    if (!outputResSelect) return;
    try {
        const res = await fetch("/api/camera/resolution");
        const data = await res.json();
        outputResSelect.innerHTML = "";
        (data.presets || []).forEach((p) => {
            const opt = document.createElement("option");
            opt.value = `${p.width}x${p.height}`;
            opt.textContent = p.label;
            outputResSelect.appendChild(opt);
        });
        syncResolutionUI(data.current);
    } catch (err) {
        showAlert(stationAlert, `❌ Could not load resolution presets: ${err.message}`, "error", true);
    }
}

if (outputResSelect) {
    outputResSelect.addEventListener("change", async () => {
        const [width, height] = outputResSelect.value.split("x").map(Number);
        outputResSelect.disabled = true;
        const ok = await settingsSave(outputResSelect, async () => {
            const data = await sendJson("/api/camera/resolution", { width, height });
            syncResolutionUI(data.current);
            if (data.station_config) updateStationUI(data.station_config);
            loadPerformance();
        });
        if (!ok && state.station) syncResolutionUI(state.station.output_resolution);
        outputResSelect.disabled = false;
    });
}

// ------------------------------------------------------------------
// Star boost (night sum-binning of the live view)
// ------------------------------------------------------------------
const starBoostEnabled = document.getElementById("star-boost-enabled");
const starBoostBin = document.getElementById("star-boost-bin");

async function loadStarBoost() {
    if (!starBoostEnabled) return;
    try {
        const data = await (await fetch("/api/camera/star-boost")).json();
        starBoostEnabled.checked = !!data.enabled;
        starBoostBin.value = String(data.bin || 3);
    } catch (err) {
        showAlert(stationAlert, `❌ Could not load star boost: ${err.message}`, "error", true);
    }
}

async function saveStarBoost() {
    starBoostEnabled.disabled = starBoostBin.disabled = true;
    const ok = await settingsSave(starBoostEnabled, async () => {
        const data = await sendJson("/api/camera/star-boost", { enabled: starBoostEnabled.checked, bin: Number(starBoostBin.value) });
        starBoostEnabled.checked = !!data.enabled;
        starBoostBin.value = String(data.bin);
    });
    if (!ok) loadStarBoost();
    starBoostEnabled.disabled = starBoostBin.disabled = false;
}

if (starBoostEnabled) {
    starBoostEnabled.addEventListener("change", saveStarBoost);
    starBoostBin.addEventListener("change", saveStarBoost);
}

// ------------------------------------------------------------------
// Performance (capture interval, analysis and live-view size)
// ------------------------------------------------------------------
const perfFields = {
    min_capture_interval_s: document.getElementById("perf-capture-interval"),
    sky_analysis_enabled: document.getElementById("perf-sky-enabled"),
    analysis_interval_s: document.getElementById("perf-sky-interval"),
    analysis_max_width: document.getElementById("perf-analysis-width"),
    stream_max_width: document.getElementById("perf-stream-width"),
    stream_jpeg_quality: document.getElementById("perf-stream-quality"),
    background_capture_interval_s: document.getElementById("perf-bg-capture"),
};
const perfBinNote = document.getElementById("perf-bin-note");

// JPEG quality is a slider: the number beside it follows the thumb live
function paintStreamQuality() {
    const q = perfFields.stream_jpeg_quality, out = document.getElementById("perf-stream-quality-val");
    if (q && out) out.textContent = q.value;
}
if (perfFields.stream_jpeg_quality) perfFields.stream_jpeg_quality.addEventListener("input", paintStreamQuality);

function syncPerformanceUI(data) {
    if (!data || !data.current) return;
    sizeState.perf = data;
    Object.entries(perfFields).forEach(([key, el]) => {
        if (!el || document.activeElement === el || data.current[key] === undefined) return;
        if (el.type === "checkbox") {
            el.checked = !!Number(data.current[key]);
            return;
        }
        const scale = Number(el.dataset.scale || 1);
        let val = String(scale > 1 ? Number(data.current[key]) / scale : data.current[key]);
        if (el.hasAttribute("data-size-select") && sizeState.master) val = String(Math.min(Number(val), sizeState.master.width));
        if (el.tagName === "SELECT" && ![...el.options].some((o) => o.value === val)) {
            const opt = document.createElement("option");
            opt.value = val;
            opt.textContent = `${val} px`;
            el.appendChild(opt);
        }
        el.value = val;
    });
    paintStreamQuality();
    renderSizePipeline();
    if (perfBinNote) {
        perfBinNote.textContent = data.raw10_bin > 1 ? `RAW10 binning in use: ${data.raw10_bin}×${data.raw10_bin}.` : "";
    }
}

async function loadPerformance() {
    if (!perfFields.min_capture_interval_s) return;
    try {
        const res = await fetch("/api/camera/performance");
        syncPerformanceUI(await res.json());
    } catch (err) {
        showAlert(stationAlert, `❌ Could not load performance settings: ${err.message}`, "error", true);
    }
}

Object.entries(perfFields).forEach(([key, el]) => {
    if (!el) return;
    el.addEventListener("change", async () => {
        const value = el.type === "checkbox" ? (el.checked ? 1 : 0) : Number(el.value) * Number(el.dataset.scale || 1);
        if (el.type !== "checkbox" && (el.value.trim() === "" || !Number.isFinite(value))) {
            await settingsSave(el, async () => { throw new Error("enter a number"); });
            return;
        }
        const ok = await settingsSave(el, async () => {
            syncPerformanceUI(await sendJson("/api/camera/performance", { [key]: value }));
        }, { field: el });
        if (!ok) loadPerformance();
    });
});


// Station timezone: the operating system's (default), a whole-hour offset from the longitude, or any IANA zone
function setupTimezoneSelect(st) {
    const sel = document.getElementById("st-f-tz");
    if (!sel) return;
    const info = st.timezone_info || {};
    if (!sel.dataset.ready) {
        sel.dataset.ready = "1";
        let zones = [];
        try { zones = Intl.supportedValuesOf("timeZone"); } catch (e) { /* older browser: the typed list below */ }
        const opts = [["", "Server (operating system)"], ["coords", "From station coordinates"], ...zones.map((z) => [z, z])];
        sel.innerHTML = opts.map(([v, t]) => `<option value="${escapeHtml(v)}">${escapeHtml(t)}</option>`).join("");
        sel.addEventListener("change", async () => {
            try {
                const res = await fetch("/api/station/timezone", {
                    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ timezone: sel.value }),
                });
                const d = await res.json();
                if (!res.ok) throw new Error(d.detail || res.status);
                setStationTz(d.timezone_info);
                setupTimezoneSelect({ timezone: d.timezone, timezone_info: d.timezone_info });
                if (typeof updateClock === "function") updateClock();
            } catch (e) {
                const note = document.getElementById("st-f-tz-note");
                if (note) note.textContent = `Could not change the timezone: ${e.message || e}`;
            }
        });
    }
    const want = st.timezone || "";
    if (![...sel.options].some((o) => o.value === want)) sel.add(new Option(want, want));
    sel.value = want;
    const m = info.offset_min;
    const note = document.getElementById("st-f-tz-note");
    if (note && typeof m === "number") note.textContent = `Now UTC${m >= 0 ? "+" : "-"}${Math.floor(Math.abs(m) / 60)}${Math.abs(m) % 60 ? ":" + String(Math.abs(m) % 60).padStart(2, "0") : ""}${info.name ? " · " + info.name : ""}`;
}
