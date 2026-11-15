/* Zenivo dashboard (part of the former app.js): White balance, live view controls, zoom and pan.
 * Classic script: top-level declarations are shared with the other src/web/app/*.js files; load order is fixed in index.html. */

// ------------------------------------------------------------------
// RAW10 White Balance
// ------------------------------------------------------------------
const wbModeSelect = document.getElementById("wb-mode");
const wbRInput = document.getElementById("wb-r-gain");
const wbBInput = document.getElementById("wb-b-gain");
const wbApplied = document.getElementById("wb-applied");

function syncWhiteBalanceUI(wb) {
    if (!wb || !wbModeSelect) return;
    if (document.activeElement !== wbModeSelect) wbModeSelect.value = wb.mode;
    const manual = wb.mode === "manual";
    [wbRInput, wbBInput].forEach((inp) => inp && (inp.disabled = !manual));
    // In auto mode show the live gains in the inputs; in manual mode show the saved gains
    const r = manual ? wb.r_gain : wb.applied_r_gain;
    const b = manual ? wb.b_gain : wb.applied_b_gain;
    if (wbRInput && document.activeElement !== wbRInput) wbRInput.value = Number(r).toFixed(2);
    if (wbBInput && document.activeElement !== wbBInput) wbBInput.value = Number(b).toFixed(2);
    if (wbApplied) {
        const path = wb.active_path === "isp" ? "MJPG/ISP" : "RAW10";
        wbApplied.textContent = `R×${Number(wb.applied_r_gain).toFixed(2)} • G×1.00 • B×${Number(wb.applied_b_gain).toFixed(2)} (${path})`;
    }
}

async function loadWhiteBalance() {
    try {
        const res = await fetch("/api/camera/white-balance");
        if (res.ok) syncWhiteBalanceUI(await res.json());
    } catch (e) {
        /* keep previous values */
    }
}

async function saveWhiteBalance(payload, field) {
    const ok = await settingsSave(field || wbModeSelect, async () => {
        syncWhiteBalanceUI(await sendJson("/api/camera/white-balance", payload));
    }, { field });
    if (!ok) loadWhiteBalance();
}

if (wbModeSelect) wbModeSelect.addEventListener("change", () => saveWhiteBalance({ mode: wbModeSelect.value }, wbModeSelect));
[wbRInput, wbBInput].forEach((inp) => {
    if (!inp) return;
    inp.addEventListener("change", () => {
        const r = parseFloat(wbRInput.value);
        const b = parseFloat(wbBInput.value);
        if (isNaN(r) || isNaN(b)) return;
        saveWhiteBalance({
            mode: "manual",
            r_gain: Math.min(4, Math.max(0.4, r)),
            b_gain: Math.min(4, Math.max(0.4, b)),
        }, inp);
    });
});

function readOrientationToggles() {
    return {
        flip_horizontal: !!(orientFlipH && orientFlipH.checked),
        flip_vertical: !!(orientFlipV && orientFlipV.checked),
        rotate_deg: orientRot180 && orientRot180.checked ? 180 : 0,
        rotate_isp_180: !!(orientRot180Day && orientRot180Day.checked),
    };
}

[orientFlipH, orientFlipV, orientRot180, orientRot180Day].forEach((t) => {
    if (t) t.addEventListener("change", () => saveOrientation(readOrientationToggles()));
});
if (btnOrientReset) {
    btnOrientReset.addEventListener("click", () =>
        saveOrientation({ flip_horizontal: false, flip_vertical: false, rotate_deg: 0, rotate_isp_180: false })
    );
}

async function loadStationConfig() {
    try {
        const res = await fetch("/api/station/config");
        if (res.ok) {
            const st = await res.json();
            updateStationUI(st);
            return st;
        }
    } catch (e) {}
    return null;
}

function readManualStationPayload(sourceLabel, autoPlateSolve = false) {
    const lat = parseFloat(stationLatInput ? stationLatInput.value : "0");
    const lon = parseFloat(stationLonInput ? stationLonInput.value : "0");
    const elev = parseFloat(stationElevInput ? stationElevInput.value : "20");
    const name = stationNameInput ? stationNameInput.value.trim() : "My station";
    return {
        latitude_deg: isNaN(lat) ? 0 : lat,
        longitude_deg: isNaN(lon) ? 0 : lon,
        elevation_m: isNaN(elev) ? 20.0 : elev,
        location_name: name || "My station",
        geo_source: sourceLabel,
        auto_plate_solve: autoPlateSolve,
        fov_hint: "auto",
        focal_length_mm: optFocalInput && !isNaN(parseFloat(optFocalInput.value)) ? parseFloat(optFocalInput.value) : undefined,
        fov_horizontal_deg: optFovHInput && !isNaN(parseFloat(optFovHInput.value)) ? parseFloat(optFovHInput.value) : undefined,
        fov_vertical_deg: optFovVInput && !isNaN(parseFloat(optFovVInput.value)) ? parseFloat(optFovVInput.value) : undefined,
        fov_diagonal_deg: optFovDInput && !isNaN(parseFloat(optFovDInput.value)) ? parseFloat(optFovDInput.value) : undefined,
        optical_center_x_px: optCxInput && !isNaN(parseFloat(optCxInput.value)) ? parseFloat(optCxInput.value) : undefined,
        optical_center_y_px: optCyInput && !isNaN(parseFloat(optCyInput.value)) ? parseFloat(optCyInput.value) : undefined,
        horizon_radius_90_px: optR90Input && !isNaN(parseFloat(optR90Input.value)) ? parseFloat(optR90Input.value) : undefined,
        azimuth_deg: oriAzInput && !isNaN(parseFloat(oriAzInput.value)) ? parseFloat(oriAzInput.value) : undefined,
        zenith_tilt_deg: oriTiltInput && !isNaN(parseFloat(oriTiltInput.value)) ? parseFloat(oriTiltInput.value) : undefined,
        north_position_angle_deg: oriNpaInput && !isNaN(parseFloat(oriNpaInput.value)) ? parseFloat(oriNpaInput.value) : undefined,
        remote_solver_url: (document.getElementById("station-remote-solver") || { value: undefined }).value,
    };
}

// Button 1: Request Coordinates Automatically (Browser GPS or IP Geolocation) — does NOT auto-plate-solve indoors
if (btnStationAutoGps) {
    btnStationAutoGps.addEventListener("click", async () => {
        btnStationAutoGps.disabled = true;
        showAlert(
            stationAlert,
            "📍 Requesting coordinates from browser GPS (falling back to IP geolocation)...",
            "info",
            true
        );

        const fallbackToBackendAutoLocate = async () => {
            try {
                const res = await fetch("/api/station/auto-locate?auto_plate_solve=false", {
                    method: "POST",
                });
                const data = await res.json();
                const st = data.station_config || data.station;
                if (res.ok && st) {
                    setStationFormDirty(false);
                    updateStationUI(st);
                    showAlert(stationAlert, `✅ ${data.message || "Coordinates located successfully!"}`, "success", true);
                } else {
                    showAlert(stationAlert, `❌ Could not locate coordinates automatically: ${data.detail || "Error"}`, "error", true);
                }
            } catch (err) {
                showAlert(stationAlert, `❌ Connection error while locating: ${err.message}`, "error", true);
            } finally {
                btnStationAutoGps.disabled = false;
            }
        };

        if ("geolocation" in navigator) {
            navigator.geolocation.getCurrentPosition(
                async (pos) => {
                    const lat = Number(pos.coords.latitude.toFixed(5));
                    const lon = Number(pos.coords.longitude.toFixed(5));
                    const elev = pos.coords.altitude !== null && !isNaN(pos.coords.altitude)
                        ? Math.round(pos.coords.altitude)
                        : Number(stationElevInput ? stationElevInput.value : 20);
                    if (stationLatInput) stationLatInput.value = lat.toFixed(5);
                    if (stationLonInput) stationLonInput.value = lon.toFixed(5);
                    if (stationElevInput) stationElevInput.value = elev;
                    try {
                        const res = await fetch("/api/station/location", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({
                                latitude_deg: lat,
                                longitude_deg: lon,
                                elevation_m: elev,
                                location_name: stationNameInput ? stationNameInput.value : "GPS Station",
                                geo_source: "browser_gps",
                                auto_plate_solve: false,
                            }),
                        });
                        const data = await res.json();
                        const st = data.station_config || data.station;
                        if (res.ok && st) {
                            setStationFormDirty(false);
                            updateStationUI(st);
                            showAlert(stationAlert, `✅ Browser GPS coordinates: ${lat.toFixed(5)}°N, ${lon.toFixed(5)}°E (${elev}m).`, "success", true);
                        }
                    } catch (e) {
                        showAlert(stationAlert, `❌ Failed to save GPS coordinates: ${e.message}`, "error", true);
                    } finally {
                        btnStationAutoGps.disabled = false;
                    }
                },
                async () => {
                    await fallbackToBackendAutoLocate();
                },
                { enableHighAccuracy: true, timeout: 5000, maximumAge: 60000 }
            );
        } else {
            await fallbackToBackendAutoLocate();
        }
    });
}

// Station & Optics: every hand edit saves itself when the field is left (coordinates, name, lens, orientation, solver URL)
const stationCard = document.getElementById("station-name") ? document.getElementById("station-name").closest(".card") : null;
if (stationCard) {
    autosave(stationCard, async (el) => {
        if (el.type === "number" && el.value.trim() === "") throw new Error("enter a number");
        const payload = readManualStationPayload("manual_ui", false);
        const data = await sendJson("/api/station/settings", payload);
        const st = data.station_config || data.station;
        setStationFormDirty(false);
        if (st) updateStationUI(st);
    }, { selector: ".settings-grid input" });
}

// Button 3: Capture & Plate-Solve via the remote plate solver (Only when user clicks!)
// mode "deep" skips the known lens: re-reads the sensor and re-measures focal length, distortion, centre
const runStationSolve = async (btn, mode) => {
        const idleLabel = btn.textContent;
        const solveButtons = [btnStationSolve, chkStationDeep].filter(Boolean);
        const payload = readManualStationPayload("manual", true);
        solveButtons.forEach((b) => (b.disabled = true));
        btn.textContent = "⏳ Capturing & plate-solving...";
        if (stationSolveBadge) {
            stationSolveBadge.textContent = "SOLVING...";
            stationSolveBadge.className = "badge badge-warning";
        }
        showAlert(
            stationAlert,
            `⏳ Capturing a series of long exposures and matching the stars (about 2-3 minutes at night)...`,
            "info",
            true
        );

        try {
            // Save coordinates first (without manual optical overrides overwriting solver output)
            const saveRes = await fetch("/api/station/location", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    latitude_deg: payload.latitude_deg,
                    longitude_deg: payload.longitude_deg,
                    elevation_m: payload.elevation_m,
                    location_name: payload.location_name,
                    geo_source: payload.geo_source,
                    auto_plate_solve: false,
                }),
            });
            if (!saveRes.ok) throw new Error(`saving coordinates failed (HTTP ${saveRes.status})`);

            // The solver can take minutes: start it in the background and poll its status
            const startRes = await fetch(`/api/station/plate-solve/start?fov_hint=auto&mode=${mode}`, { method: "POST" });
            if (!startRes.ok) throw new Error(`could not start plate-solve (HTTP ${startRes.status})`);
            const t0 = Date.now();
            let data = await startRes.json();
            const STAGES = { quick: "Quick single-frame solve", metering: "1/3 Metering", capturing: "2/3 Capturing", matching: "3/3 Matching", online: "3/3 Online solve" };
            let lastPoll = 0;
            let pollFails = 0;
            let lostContact = false;
            while (data.state === "running" && Date.now() - t0 < 6 * 60 * 1000) {
                // Re-render every second (countdown); ask the server every 3 s
                const secs = Math.round((Date.now() - t0) / 1000);
                const parts = [STAGES[data.stage] || "Starting"];
                if (data.stage === "capturing" && data.frames_total) parts.push(`frame ${Math.min(data.frame + 1, data.frames_total)}/${data.frames_total}`);
                if (data.eta_ts) parts.push(`~${Math.max(0, Math.round(data.eta_ts - Date.now() / 1000))} s left`);
                btn.textContent = `⏳ ${parts.join(" · ")}`;
                showAlert(stationAlert, `⏳ ${parts.join(" · ")} — ${data.message || "Plate-solving…"} (${secs} s elapsed)`, "info", true);
                await new Promise((r) => setTimeout(r, 1000));
                if (Date.now() - lastPoll >= 3000) {
                    lastPoll = Date.now();
                    // A single network blip must not abort the wait: retry a few times first
                    try {
                        const stRes = await fetch("/api/station/plate-solve/status");
                        if (!stRes.ok) throw new Error(`HTTP ${stRes.status}`);
                        data = await stRes.json();
                        pollFails = 0;
                    } catch (pollErr) {
                        if (++pollFails >= 3) {
                            lostContact = true;
                            break;
                        }
                    }
                }
            }
            if (data.state === "success") setStationFormDirty(false);
            if (data.station_config) updateStationUI(data.station_config);
            if (data.state === "success") {
                showAlert(stationAlert, `✅ ${data.message || "Plate-solving complete!"}`, "success", true);
            } else if (lostContact) {
                showAlert(stationAlert, "⚠️ Lost contact with the station while polling the plate-solve. It may still be running on the server; check the Activity Log or retry in a moment.", "error", true);
            } else if (data.state === "running") {
                showAlert(stationAlert, "⏳ Plate-solve is still running on the station; check the Activity Log for the result.", "info", true);
            } else {
                showAlert(stationAlert, `❌ ${data.message || data.error || "Plate-solve failed"}`, "error", true);
            }
        } catch (err) {
            showAlert(stationAlert, `❌ Network connection error: ${err.message}`, "error", true);
        } finally {
            solveButtons.forEach((b) => (b.disabled = false));
            btn.textContent = idleLabel;
        }
};
if (btnStationSolve) {
    btnStationSolve.addEventListener("click", () =>
        runStationSolve(btnStationSolve, chkStationDeep && chkStationDeep.checked ? "deep" : "auto"));
}

// One in-flight GET /api/station/sky-mask shared by the mask editor and the star-align panel (both load together)
let skyMaskInflight = null;
function fetchSkyMask() {
    if (!skyMaskInflight) {
        skyMaskInflight = fetch("/api/station/sky-mask")
            .then((res) => (res.ok ? res.json() : null))
            .finally(() => { skyMaskInflight = null; });
    }
    return skyMaskInflight;
}

// Plate-solve sky mask editor: polygons stored as frame fractions in station_config.sky_mask
const maskEditor = document.getElementById("sky-mask-editor");
if (maskEditor) {
    const maskImg = document.getElementById("mask-img");
    const maskCanvas = document.getElementById("mask-canvas");
    const maskAlert = document.getElementById("mask-alert");
    const mask = { sky: [], obstruction: [] };
    let current = { kind: "sky", pts: [] };

    const finishCurrent = () => {
        if (current.pts.length >= 3) mask[current.kind].push(current.pts);
        current = { kind: current.kind, pts: [] };
    };
    const drawMask = () => {
        const w = maskImg.clientWidth, h = maskImg.clientHeight;
        if (!w || !h) return;
        maskCanvas.width = w;
        maskCanvas.height = h;
        const ctx = maskCanvas.getContext("2d");
        ctx.clearRect(0, 0, w, h);
        const poly = (pts, kind, closed) => {
            if (!pts.length) return;
            const color = kind === "sky" ? "34,197,94" : "239,68,68";
            ctx.beginPath();
            pts.forEach(([x, y], i) => (i ? ctx.lineTo(x * w, y * h) : ctx.moveTo(x * w, y * h)));
            if (closed) {
                ctx.closePath();
                ctx.fillStyle = `rgba(${color},0.18)`;
                ctx.fill();
            }
            ctx.strokeStyle = `rgb(${color})`;
            ctx.lineWidth = 2;
            ctx.stroke();
            ctx.fillStyle = `rgb(${color})`;
            pts.forEach(([x, y]) => ctx.fillRect(x * w - 3, y * h - 3, 6, 6));
        };
        mask.sky.forEach((p) => poly(p, "sky", true));
        mask.obstruction.forEach((p) => poly(p, "obstruction", true));
        poly(current.pts, current.kind, false);
    };
    // The snapshot can fail transiently (camera busy, service restarting): retry instead of leaving a broken image
    let maskRetries = 0, maskRetryTimer = null;
    const loadMaskFrame = () => {
        clearTimeout(maskRetryTimer);
        maskImg.src = `/api/live/snapshot.jpg?t=${Date.now()}`;
    };
    maskImg.addEventListener("error", () => {
        if (maskRetries >= 8) {
            showAlert(maskAlert, "❌ Could not load the live frame. Click the frame area to retry.", "error", true);
            return;
        }
        maskRetries++;
        maskRetryTimer = setTimeout(loadMaskFrame, Math.min(1000 * maskRetries, 5000));
    });
    maskImg.addEventListener("click", () => { maskRetries = 0; loadMaskFrame(); });
    const loadMask = async () => {
        maskRetries = 0;
        loadMaskFrame();
        try {
            const data = await fetchSkyMask();
            if (data) {
                mask.sky = data.sky || [];
                mask.obstruction = data.obstruction || [];
                current = { kind: "sky", pts: [] };
                drawMask();
            }
        } catch (e) {}
    };
    maskEditor.addEventListener("toggle", () => { if (maskEditor.open) loadMask(); });
    // <details open> fires its toggle event before this script runs, so load once now as well
    if (maskEditor.open && currentPage === "tab-settings") loadMask();
    maskImg.addEventListener("load", () => { maskRetries = 0; drawMask(); });
    window.addEventListener("resize", rafDebounced(drawMask));
    maskCanvas.addEventListener("click", (ev) => {
        const r = maskCanvas.getBoundingClientRect();
        const x = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
        const y = Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height));
        current.pts.push([Math.round(x * 10000) / 10000, Math.round(y * 10000) / 10000]);
        drawMask();
    });
    document.getElementById("mask-auto").addEventListener("click", async () => {
        try {
            const res = await fetch("/api/station/sky-mask/auto", { method: "POST" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || res.statusText);
            current = { kind: "sky", pts: [] };
            mask.sky = data.sky || [];
            mask.obstruction = data.obstruction || [];
            loadMaskFrame();
            drawMask();
            showAlert(maskAlert, `✨ Proposed ${mask.sky.length} sky and ${mask.obstruction.length} obstruction polygon(s) covering ` +
                `${Math.round((data.sky_fraction || 0) * 100)} % of the frame. Check the outline, correct it, then press Apply.`, "success");
        } catch (err) {
            showAlert(maskAlert, `❌ Auto-detect failed: ${err.message}`, "error", true);
        }
    });
    document.getElementById("mask-new-sky").addEventListener("click", () => { finishCurrent(); current.kind = "sky"; drawMask(); });
    document.getElementById("mask-new-obs").addEventListener("click", () => { finishCurrent(); current.kind = "obstruction"; drawMask(); });
    document.getElementById("mask-undo").addEventListener("click", () => {
        if (current.pts.length) current.pts.pop();
        else {
            const last = mask[current.kind].pop();
            if (last) current.pts = last.slice(0, -1);
        }
        drawMask();
    });
    document.getElementById("mask-clear").addEventListener("click", () => {
        mask.sky = [];
        mask.obstruction = [];
        current = { kind: "sky", pts: [] };
        drawMask();
    });
    document.getElementById("mask-save").addEventListener("click", async () => {
        finishCurrent();
        drawMask();
        try {
            const res = await fetch("/api/station/sky-mask", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(mask),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
            showAlert(maskAlert, `✅ Sky mask applied: ${data.sky.length} sky, ${data.obstruction.length} obstruction polygon(s)`, "success");
        } catch (err) {
            showAlert(maskAlert, `❌ Could not apply the sky mask: ${err.message}`, "error", true);
        }
    });
}

// Manual star alignment: click real stars on the live frame, the server identifies them and fixes the pointing
const alignBox = document.getElementById("star-align");
if (alignBox) {
    const alignImg = document.getElementById("align-img");
    const alignCanvas = document.getElementById("align-canvas");
    const alignAlert = document.getElementById("align-alert");
    const btnApply = document.getElementById("align-apply");
    let picks = [];          // [x, y] fractions (snapped to the star once identified)
    let maskPolys = { sky: [], obstruction: [] };
    let identified = null;   // last successful preview
    let alignRetries = 0, alignTimer = null;

    const drawAlign = () => {
        const w = alignImg.clientWidth, h = alignImg.clientHeight;
        if (!w || !h) return;
        alignCanvas.width = w;
        alignCanvas.height = h;
        const ctx = alignCanvas.getContext("2d");
        ctx.clearRect(0, 0, w, h);
        const outline = (polys, rgb) => polys.forEach((pts) => {
            ctx.beginPath();
            pts.forEach(([x, y], i) => (i ? ctx.lineTo(x * w, y * h) : ctx.moveTo(x * w, y * h)));
            ctx.closePath();
            ctx.strokeStyle = `rgba(${rgb},0.7)`;
            ctx.setLineDash([6, 4]);
            ctx.lineWidth = 1.5;
            ctx.stroke();
            ctx.setLineDash([]);
        });
        outline(maskPolys.sky, "34,197,94");
        outline(maskPolys.obstruction, "239,68,68");
        const names = new Map();
        if (identified) identified.matched_stars.forEach((m) => names.set(`${m.x}|${m.y}`, m));
        picks.forEach(([x, y], i) => {
            ctx.strokeStyle = "#fbbf24";
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(x * w, y * h, 14, 0, Math.PI * 2);
            ctx.stroke();
            ctx.fillStyle = "#fbbf24";
            ctx.font = "bold 13px sans-serif";
            ctx.fillText(String(i + 1), x * w + 18, y * h - 12);
        });
        if (identified) {
            ctx.font = "bold 13px sans-serif";
            identified.points.forEach(([x, y], i) => {
                const m = identified.names[i];
                if (!m) return;
                ctx.fillStyle = "#38bdf8";
                ctx.fillText(`${m.name} (${m.mag})`, x * w + 18, y * h + 4);
            });
        }
    };
    const loadAlignFrame = () => {
        clearTimeout(alignTimer);
        alignImg.src = `/api/live/snapshot.jpg?t=${Date.now()}`;
    };
    const loadAlignMask = async () => {
        try {
            const d = await fetchSkyMask();
            if (d) {
                maskPolys = { sky: d.sky || [], obstruction: d.obstruction || [] };
                drawAlign();
            }
        } catch (e) {}
    };
    const reset = () => { picks = []; identified = null; btnApply.disabled = true; drawAlign(); };
    alignImg.addEventListener("error", () => {
        if (alignRetries >= 8) return;
        alignRetries++;
        alignTimer = setTimeout(loadAlignFrame, Math.min(1000 * alignRetries, 5000));
    });
    alignImg.addEventListener("load", () => { alignRetries = 0; drawAlign(); });
    window.addEventListener("resize", rafDebounced(drawAlign));
    alignCanvas.addEventListener("click", (ev) => {
        const r = alignCanvas.getBoundingClientRect();
        picks.push([(ev.clientX - r.left) / r.width, (ev.clientY - r.top) / r.height]);
        identified = null;
        btnApply.disabled = true;
        drawAlign();
    });
    document.getElementById("align-refresh").addEventListener("click", () => { reset(); alignRetries = 0; loadAlignFrame(); });
    document.getElementById("align-undo").addEventListener("click", () => { picks.pop(); identified = null; btnApply.disabled = true; drawAlign(); });
    document.getElementById("align-clear").addEventListener("click", reset);

    const callAlign = async (apply) => {
        const res = await fetch("/api/station/star-align", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ points: picks, apply }),
        });
        const data = await res.json();
        if (!res.ok) {
            const d = data.detail;
            throw new Error(Array.isArray(d) ? d.map((e) => e.msg || JSON.stringify(e)).join("; ") : (d || `HTTP ${res.status}`));
        }
        return data;
    };
    document.getElementById("align-identify").addEventListener("click", async () => {
        if (picks.length < 4) {
            showAlert(alignAlert, "❌ Click at least 4 stars first.", "error", true);
            return;
        }
        try {
            const data = await callAlign(false);
            if (!data.success) {
                identified = null;
                btnApply.disabled = true;
                showAlert(alignAlert, `❌ ${data.error}. Check that each click is on a real star.`, "error", true);
                return;
            }
            picks = data.points;
            const names = data.names;
            identified = { ...data, names };
            btnApply.disabled = false;
            const list = data.matched_stars.map((m) => `${m.name} (${m.mag}, ${m.err_px}px)`).join(", ");
            const p = data.pointing;
            showAlert(alignAlert,
                `✅ ${data.matched_stars.length} of ${data.clicked} clicks identified: ${list}. RMS ${data.rms_px}px, ` +
                `Az ${p.pointing_az_deg}°, Alt ${p.pointing_alt_deg}°, roll ${p.roll_deg}°.` +
                (data.warning ? ` ⚠ ${data.warning}` : "") + " Press Apply to store it.", "success", true);
            drawAlign();
        } catch (err) {
            showAlert(alignAlert, `❌ ${err.message}`, "error", true);
        }
    });
    btnApply.addEventListener("click", async () => {
        try {
            const data = await callAlign(true);
            if (!data.success || !data.applied) throw new Error(data.error || "not applied");
            btnApply.disabled = true;
            showAlert(alignAlert, `✅ Stored. ${data.message || ""}`, "success", true);
            if (typeof loadStationConfig === "function") loadStationConfig();
        } catch (err) {
            showAlert(alignAlert, `❌ ${err.message}`, "error", true);
        }
    });
    if (currentPage === "tab-settings") {
        loadAlignFrame();
        loadAlignMask();
    }
}

// ------------------------------------------------------------------
// Live View Controls
// ------------------------------------------------------------------
btnRefreshFrame.addEventListener("click", () => {
    const label = btnRefreshFrame.textContent;
    const done = () => {
        btnRefreshFrame.disabled = false;
        btnRefreshFrame.textContent = label;
    };
    btnRefreshFrame.disabled = true;
    btnRefreshFrame.textContent = "Loading…";
    liveImg.addEventListener("load", done, { once: true });
    liveImg.addEventListener("error", done, { once: true });
    setTimeout(done, 8000);
    liveImg.src = `/api/live/snapshot.jpg?t=${Date.now()}`;
});

// ------------------------------------------------------------------
// Live View Zoom & Pan (wheel / pinch / drag / click / + - 0 keys)
// ------------------------------------------------------------------
const ZOOM_MIN = 1;
const ZOOM_MAX = 8;
const zoomStage = document.getElementById("zoom-stage");
const btnZoomIn = document.getElementById("btn-zoom-in");
const btnZoomOut = document.getElementById("btn-zoom-out");
const btnZoomReset = document.getElementById("btn-zoom-reset");
// Pan is stored as the view centre in stage fractions (0..1) so it survives viewport resizes
const view = {
    zoom: Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Number(prefs.zoom) || 1)),
    cx: Number.isFinite(prefs.zoomCx) ? prefs.zoomCx : 0.5,
    cy: Number.isFinite(prefs.zoomCy) ? prefs.zoomCy : 0.5,
};
let zoomRedrawTimer = null;

function clampView() {
    const half = 0.5 / view.zoom;
    view.cx = Math.min(1 - half, Math.max(half, view.cx));
    view.cy = Math.min(1 - half, Math.max(half, view.cy));
}

function applyView(persist = true) {
    if (!zoomStage || !viewportContainer) return;
    clampView();
    const w = viewportContainer.clientWidth;
    const h = viewportContainer.clientHeight;
    const tx = w * (0.5 - view.cx * view.zoom);
    const ty = h * (0.5 - view.cy * view.zoom);
    zoomStage.style.transform = `translate(${tx}px, ${ty}px) scale(${view.zoom})`;
    viewportContainer.classList.add("zoomable");
    viewportContainer.classList.toggle("zoomed", view.zoom > 1.001);
    if (btnZoomReset) btnZoomReset.textContent = `${Math.round(view.zoom * 100)}%`;
    if (persist) {
        savePrefs({ zoom: view.zoom, zoomCx: view.cx, zoomCy: view.cy });
        // Re-render the overlay at the new density once zooming settles
        clearTimeout(zoomRedrawTimer);
        zoomRedrawTimer = setTimeout(drawReticle, 120);
    }
}

// Zoom keeping the stage point under (px, py) — viewport pixel coords — fixed on screen
function zoomAt(newZoom, px, py) {
    if (!viewportContainer) return;
    const w = viewportContainer.clientWidth;
    const h = viewportContainer.clientHeight;
    const z = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, newZoom));
    const sx = view.cx + (px / w - 0.5) / view.zoom;
    const sy = view.cy + (py / h - 0.5) / view.zoom;
    view.zoom = z;
    view.cx = sx - (px / w - 0.5) / z;
    view.cy = sy - (py / h - 0.5) / z;
    applyView();
}

function zoomCentre(factor) {
    if (!viewportContainer) return;
    zoomAt(view.zoom * factor, viewportContainer.clientWidth / 2, viewportContainer.clientHeight / 2);
}

if (viewportContainer && zoomStage) {
    viewportContainer.addEventListener(
        "wheel",
        (e) => {
            e.preventDefault();
            const rect = viewportContainer.getBoundingClientRect();
            const factor = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0015));
            zoomAt(view.zoom * factor, e.clientX - rect.left, e.clientY - rect.top);
        },
        { passive: false }
    );

    // Click toggles zoom (3× at the clicked point / back to fit); a drag or pinch is not a click
    let tapStart = null;
    viewportContainer.addEventListener("click", (e) => {
        if (e.target.closest(".zoom-controls, .vp-chips, .vp-actions, button, label, input") || !tapStart) return;
        const moved = Math.hypot(e.clientX - tapStart.x, e.clientY - tapStart.y);
        const wasMulti = tapStart.multi;
        tapStart = null;
        if (moved > 6 || wasMulti) return;
        const rect = viewportContainer.getBoundingClientRect();
        zoomAt(view.zoom > 1.001 ? 1 : 3, e.clientX - rect.left, e.clientY - rect.top);
    });

    // Drag to pan, two-finger pinch to zoom
    const pointers = new Map();
    let pinchStart = null;
    viewportContainer.addEventListener("pointerdown", (e) => {
        if (e.target.closest(".zoom-controls, .vp-chips, .vp-actions, button, label, input")) return;
        if (pointers.size === 0) tapStart = { x: e.clientX, y: e.clientY, multi: false };
        else if (tapStart) tapStart.multi = true;
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        viewportContainer.setPointerCapture(e.pointerId);
        if (pointers.size === 2) {
            const [a, b] = [...pointers.values()];
            pinchStart = { dist: Math.hypot(a.x - b.x, a.y - b.y), zoom: view.zoom };
        }
    });
    viewportContainer.addEventListener("pointermove", (e) => {
        const prev = pointers.get(e.pointerId);
        if (!prev) return;
        const cur = { x: e.clientX, y: e.clientY };
        pointers.set(e.pointerId, cur);
        const rect = viewportContainer.getBoundingClientRect();
        if (pointers.size === 2 && pinchStart) {
            const [a, b] = [...pointers.values()];
            const dist = Math.hypot(a.x - b.x, a.y - b.y);
            zoomAt(pinchStart.zoom * (dist / Math.max(1, pinchStart.dist)), (a.x + b.x) / 2 - rect.left, (a.y + b.y) / 2 - rect.top);
        } else if (pointers.size === 1 && view.zoom > 1.001) {
            viewportContainer.classList.add("panning");
            view.cx -= (cur.x - prev.x) / (rect.width * view.zoom);
            view.cy -= (cur.y - prev.y) / (rect.height * view.zoom);
            applyView(false);
        }
    });
    const endPointer = (e) => {
        if (!pointers.delete(e.pointerId)) return;
        if (pointers.size < 2) pinchStart = null;
        if (pointers.size === 0) {
            viewportContainer.classList.remove("panning");
            savePrefs({ zoom: view.zoom, zoomCx: view.cx, zoomCy: view.cy });
        }
    };
    viewportContainer.addEventListener("pointerup", endPointer);
    viewportContainer.addEventListener("pointercancel", endPointer);

    if (btnZoomIn) btnZoomIn.addEventListener("click", () => zoomCentre(1.5));
    if (btnZoomOut) btnZoomOut.addEventListener("click", () => zoomCentre(1 / 1.5));
    if (btnZoomReset) btnZoomReset.addEventListener("click", () => zoomAt(1, 0, 0));

    document.addEventListener("keydown", (e) => {
        if (currentPage !== "tab-live" || e.target.closest("input, select, textarea")) return;
        if (e.key === "+" || e.key === "=") zoomCentre(1.5);
        else if (e.key === "-" || e.key === "_") zoomCentre(1 / 1.5);
        else if (e.key === "0") zoomAt(1, 0, 0);
    });

    window.addEventListener("resize", rafDebounced(() => applyView(false)));
    document.addEventListener("fullscreenchange", () => setTimeout(() => applyView(false), 60));
    applyView(false);
}

btnFullscreen.addEventListener("click", () => {
    if (!document.fullscreenElement) {
        viewportContainer.requestFullscreen().catch(() => {});
    } else {
        document.exitFullscreen().catch(() => {});
    }
});
