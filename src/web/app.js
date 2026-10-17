/**
 * IMX335 AllSky Observatory Edge Dashboard Controller.
 * Handles real-time telemetry (/api/telemetry, /ws/live, /api/stream/sse), canvas histogram rendering,
 * camera hardware parameter controls (/api/camera/exposure), and gallery/timelapse management.
 */

document.addEventListener("DOMContentLoaded", () => {
    // State
    const state = {
        mode: "auto",
        exposure_s: 0.04,
        gain_db: 12.0,
        wsConnected: false,
        ws: null,
        sseSource: null,
        prevFrameObjectUrl: null,
        nightVision: false,
    };

    // DOM Elements
    const navClock = document.getElementById("nav-utc-clock");
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
    const btnApplySettings = document.getElementById("btn-apply-settings");
    const controlAlert = document.getElementById("control-alert");
    const btnModeManual = document.getElementById("btn-mode-manual");
    const btnModeAuto = document.getElementById("btn-mode-auto");
    const activeProfileBadge = document.getElementById("active-profile-badge");

    // Telemetry Elements
    const telemExp = document.getElementById("telem-exp");
    const telemGain = document.getElementById("telem-gain");
    const telemStars = document.getElementById("telem-stars");
    const telemClouds = document.getElementById("telem-clouds");
    const telemSkyStatus = document.getElementById("telem-sky-status");
    const telemFps = document.getElementById("telem-fps");
    const telemTemp = document.getElementById("telem-temp");
    const telemUptime = document.getElementById("telem-uptime");
    const telemTimestamp = document.getElementById("telemetry-timestamp");
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
    const btnStationSave = document.getElementById("btn-station-save");
    const btnStationSolve = document.getElementById("btn-station-solve");
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
    const eventsTableBody = document.getElementById("events-table-body");
    const eventCountBadge = document.getElementById("event-count-badge");

    // ------------------------------------------------------------------
    // Clock & Night Vision
    // ------------------------------------------------------------------
    function updateClock() {
        const now = new Date();
        navClock.textContent = now.toUTCString().split(" ")[4] + " UTC";
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
        btnNightVision.classList.toggle("btn-primary", on);
    }
    applyNightVision(!!prefs.nightVision);

    btnNightVision.addEventListener("click", () => {
        applyNightVision(!state.nightVision);
        savePrefs({ nightVision: state.nightVision });
    });

    // Overlays default to hidden; restore the viewer's last choice
    [
        [chkSkyOverlay, "skyOverlay"],
        [chkSkyLabels, "skyLabels"],
        [chkGridOverlay, "gridOverlay"],
    ].forEach(([chk, key]) => {
        if (!chk) return;
        chk.checked = !!prefs[key];
        chk.addEventListener("change", () => savePrefs({ [key]: chk.checked }));
    });

    // ------------------------------------------------------------------
    // Page Routing: every section has its own URL (/live, /gallery, /settings, /calibration)
    // ------------------------------------------------------------------
    const PAGES = {
        "/": "tab-live",
        "/live": "tab-live",
        "/index.html": "tab-live",
        "/gallery": "tab-gallery",
        "/settings": "tab-settings",
        "/calibration": "tab-calibration",
        "/logs": "tab-logs",
    };
    const PAGE_TITLES = {
        "tab-live": "Live Observatory",
        "tab-gallery": "Gallery & Events",
        "tab-settings": "Settings",
        "tab-calibration": "Master Dark",
        "tab-logs": "Activity Log",
    };
    const currentPath = window.location.pathname.replace(/\/+$/, "") || "/";
    const currentPage = PAGES[currentPath] || "tab-live";
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.toggle("active", c.id === currentPage));
    document.querySelectorAll(".tab-btn").forEach((b) => {
        const isActive = b.getAttribute("data-tab") === currentPage;
        b.classList.toggle("active", isActive);
        if (isActive) b.setAttribute("aria-current", "page");
    });
    document.title = `${PAGE_TITLES[currentPage]} — AllSky Observatory`;

    // ------------------------------------------------------------------
    // Range Controls Synchronization
    // ------------------------------------------------------------------
    rangeExposure.addEventListener("input", (e) => {
        const val = parseFloat(e.target.value);
        valExposure.textContent = val < 0.01 ? (val * 1000).toFixed(2) + " ms" : val < 0.1 ? (val * 1000).toFixed(1) + " ms" : val.toFixed(3) + " s";
    });

    rangeGain.addEventListener("input", (e) => {
        const val = parseFloat(e.target.value);
        valGain.textContent = val.toFixed(1) + " dB";
    });

    document.querySelectorAll(".btn-preset").forEach((btn) => {
        btn.addEventListener("click", () => {
            const exp = parseFloat(btn.getAttribute("data-exp"));
            rangeExposure.value = exp;
            valExposure.textContent = exp < 0.01 ? (exp * 1000).toFixed(2) + " ms" : exp < 0.1 ? (exp * 1000).toFixed(1) + " ms" : exp.toFixed(3) + " s";
        });
    });

    document.querySelectorAll(".btn-preset-gain").forEach((btn) => {
        btn.addEventListener("click", () => {
            const gain = parseFloat(btn.getAttribute("data-gain"));
            rangeGain.value = gain;
            valGain.textContent = gain.toFixed(1) + " dB";
        });
    });

    // Mode Toggle
    btnModeManual.addEventListener("click", async () => {
        await setMode("manual");
    });

    btnModeAuto.addEventListener("click", async () => {
        await setMode("auto");
    });

    function buildAsc1CommandPacket(cmdId, modeVal, exposureS, gainDb) {
        const buf = new ArrayBuffer(16);
        const dv = new DataView(buf);
        // Magic "ASC1"
        dv.setUint8(0, 0x41);
        dv.setUint8(1, 0x53);
        dv.setUint8(2, 0x43);
        dv.setUint8(3, 0x31);
        dv.setUint8(4, cmdId & 0xff);
        dv.setUint8(5, modeVal & 0xff);
        dv.setUint16(6, 0, true);
        dv.setFloat32(8, Number(exposureS || 0), true);
        dv.setFloat32(12, Number(gainDb || 0), true);
        return buf;
    }

    async function setMode(mode) {
        if (state.wsConnected && state.ws && state.ws.readyState === WebSocket.OPEN) {
            const modeVal = mode === "auto" ? 1 : 0;
            state.ws.send(buildAsc1CommandPacket(1, modeVal, 0, 0));
            syncModeButtons(mode);
            return;
        }
        try {
            const res = await fetch("/api/camera/mode", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ mode }),
            });
            if (res.ok) {
                const data = await res.json();
                syncModeButtons(data.mode);
            }
        } catch (err) {
            console.error("Failed to toggle mode:", err);
        }
    }

    // Apply Settings
    btnApplySettings.addEventListener("click", async () => {
        const exp = parseFloat(rangeExposure.value);
        const gain = parseFloat(rangeGain.value);
        btnApplySettings.disabled = true;
        btnApplySettings.textContent = "Applying...";

        if (state.wsConnected && state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(buildAsc1CommandPacket(2, 0, exp, gain));
            syncModeButtons("manual");
            showAlert(controlAlert, "Exposure & Gain sent via 16-byte Binary Frame (ASC1)!", "success");
            btnApplySettings.disabled = false;
            btnApplySettings.textContent = "Apply Exposure & Gain";
            return;
        }

        try {
            const resExp = await fetch("/api/camera/exposure", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ exposure_s: exp, gain_db: gain }),
            });

            if (resExp.ok) {
                showAlert(controlAlert, "Exposure & Gain successfully updated!", "success");
            } else {
                showAlert(controlAlert, `Failed to apply settings: HTTP ${resExp.status}`, "error");
            }
        } catch (err) {
            showAlert(controlAlert, `Network error: ${err.message}`, "error");
        } finally {
            btnApplySettings.disabled = false;
            btnApplySettings.textContent = "Apply Exposure & Gain";
        }
    });

    function showAlert(el, msg, type, persist = false) {
        if (!el) return;
        el.textContent = msg;
        el.className = `alert-box alert-${type} mt-3`;
        el.classList.remove("hidden");
        if (el._hideTimer) {
            clearTimeout(el._hideTimer);
            el._hideTimer = null;
        }
        if (!persist) {
            el._hideTimer = setTimeout(() => {
                el.classList.add("hidden");
            }, 4000);
        }
    }

    // ------------------------------------------------------------------
    // Calibration Master Dark & Live Progress
    // ------------------------------------------------------------------
    function updateCalibrationUI(cal) {
        if (!cal) return;
        const st = (cal.state || "idle").toLowerCase();
        const hasProgress = typeof cal.progress_pct === "number";
        // Ignore partial ASB1 binary WebSocket packets during active calibration so progress bar never jumps to 0%
        if (st === "running" && !hasProgress) {
            return;
        }
        const pct = hasProgress ? cal.progress_pct : (st === "completed" ? 100 : 0);
        const readyCnt = cal.ready_profiles_count !== undefined ? cal.ready_profiles_count : 0;
        const totalCnt = cal.total_profiles_count !== undefined ? cal.total_profiles_count : 6;

        if (darkStatusBadge) {
            if (st === "running") {
                darkStatusBadge.textContent = `AUTO-CAPTURING ${cal.current_frame || 0}/${cal.total_frames || 0}`;
                darkStatusBadge.className = "badge badge-warning";
            } else if (readyCnt >= totalCnt && totalCnt > 0) {
                darkStatusBadge.textContent = `ALL ${readyCnt}/${totalCnt} READY`;
                darkStatusBadge.className = "badge badge-success";
            } else if (st === "completed") {
                darkStatusBadge.textContent = `${readyCnt}/${totalCnt} READY`;
                darkStatusBadge.className = "badge badge-success";
            } else if (st === "error") {
                darkStatusBadge.textContent = "ERROR";
                darkStatusBadge.className = "badge badge-danger";
            } else {
                darkStatusBadge.textContent = `${readyCnt}/${totalCnt} READY`;
                darkStatusBadge.className = "badge badge-accent";
            }
        }

        if (darkStatusText && cal.message) {
            darkStatusText.textContent = cal.message;
        }
        if (darkProgressPct) {
            darkProgressPct.textContent = `${Math.round(pct)}%`;
        }
        if (darkProgressBar) {
            darkProgressBar.style.width = `${Math.min(100, Math.max(0, pct))}%`;
        }

        const isRunning = st === "running";
        if (btnTriggerDark) {
            btnTriggerDark.disabled = isRunning;
            if (isRunning) {
                btnTriggerDark.textContent = `Capturing Profile ${cal.current_profile || 1}/${cal.total_profiles || 7} — Frame ${cal.current_frame || 0}/${cal.total_frames || "?"} (${Math.round(pct)}%)...`;
            } else {
                const totalProfs = Array.isArray(cal.profiles) ? cal.profiles.length : 7;
                const readyProfs = Array.isArray(cal.profiles) ? cal.profiles.filter((p) => p.ready).length : 0;
                if (readyProfs > 0 && readyProfs < totalProfs) {
                    btnTriggerDark.textContent = `Capture Missing Master Dark Profiles (${totalProfs - readyProfs}/${totalProfs} Remaining)`;
                } else {
                    btnTriggerDark.textContent = `Capture & Build All Master Dark Profiles (${totalProfs} Profiles: 0.5s – 15.0s)`;
                }
            }
        }

        if (cal.profiles && darkProfilesMatrixBody) {
            darkProfilesMatrixBody.innerHTML = cal.profiles
                .map((p) => {
                    const isCurrent =
                        isRunning &&
                        Math.abs((cal.exposure_s || 0) - p.exposure_s) < 0.05 &&
                        Math.abs((cal.gain_db || 0) - p.gain_db) < 0.5;
                    let badgeHtml = `<span class="badge">NOT CAPTURED</span>`;
                    if (isCurrent) {
                        badgeHtml = `<span class="badge badge-warning">⏳ CAPTURING (${cal.current_frame || 0}/${cal.total_frames || p.num_frames})</span>`;
                    } else if (p.ready) {
                        badgeHtml = `<span class="badge badge-success">✅ READY (${p.size_kb} KB)</span>`;
                    }
                    const updatedStr = p.modified_at
                        ? `${p.raw_filename || p.filename} • ${p.modified_at.replace("T", " ").split(".")[0]} UTC`
                        : `${p.raw_filename || p.filename}`;
                    return `
                        <tr>
                            <td><strong>${p.label}</strong></td>
                            <td><span class="text-accent">${p.exposure_s.toFixed(1)}s</span> @ <strong>${p.gain_db.toFixed(0)} dB</strong></td>
                            <td>${p.num_frames} frames</td>
                            <td>${badgeHtml}</td>
                            <td class="text-muted">${updatedStr}</td>
                        </tr>`;
                })
                .join("");
        }

        if (cal.library && darkLibraryBody) {
            if (cal.library.length === 0) {
                darkLibraryBody.innerHTML = `<tr><td colspan="4" class="text-muted" style="text-align:center;">No Master Dark frames generated yet.</td></tr>`;
            } else {
                darkLibraryBody.innerHTML = cal.library
                    .map(
                        (f) => `
                        <tr>
                            <td><strong class="text-accent">${f.filename}</strong></td>
                            <td>${f.size_kb} KB</td>
                            <td>${f.modified_at ? f.modified_at.replace("T", " ").split(".")[0] + " UTC" : "--"}</td>
                            <td class="text-muted">${f.file_path}</td>
                        </tr>`
                    )
                    .join("");
            }
        }
    }

    async function pollCalibrationStatus() {
        try {
            const res = await fetch("/api/calibration/status");
            if (res.ok) {
                const cal = await res.json();
                updateCalibrationUI(cal);
                return cal;
            }
        } catch (e) {}
        return null;
    }

    async function runAutoDarkLibrary(onlyMissing = false) {
        if (btnTriggerDark) {
            btnTriggerDark.disabled = true;
            btnTriggerDark.textContent = "Capturing Master Dark Profile (0%)...";
        }
        showAlert(
            darkAlert,
            "⏳ Capturing and median-stacking 10-bit RAW Master Dark Profiles (0.5s–15.0s / 12–48 dB)...",
            "info",
            true
        );

        const calPollTimer = setInterval(pollCalibrationStatus, 500);
        try {
            const res = await fetch(`/api/calibration/auto-library?only_missing=${onlyMissing ? "true" : "false"}`, {
                method: "POST",
            });
            const rawText = await res.text();
            let data = {};
            try {
                data = JSON.parse(rawText);
            } catch (_) {
                data = { detail: rawText };
            }
            const cal = await pollCalibrationStatus();
            if (res.ok && data.success) {
                showAlert(darkAlert, `✅ ${data.message}`, "success", true);
            } else if (cal && cal.state === "running") {
                // If reverse proxy timed out while backend is still running, keep polling until completion
                while (true) {
                    await new Promise((r) => setTimeout(r, 1000));
                    const st = await pollCalibrationStatus();
                    if (!st || st.state !== "running") {
                        if (st && st.state === "completed") {
                            showAlert(darkAlert, `✅ ${st.message}`, "success", true);
                        } else if (st && st.state === "error") {
                            showAlert(darkAlert, `❌ ${st.message}`, "error", true);
                        }
                        break;
                    }
                }
            } else {
                const errMsg = (cal && cal.state === "error" && cal.message) || data.detail || "Error";
                showAlert(darkAlert, `❌ Master Dark calibration failed: ${errMsg}`, "error", true);
            }
        } catch (err) {
            showAlert(darkAlert, `❌ Network error: ${err.message}`, "error", true);
        } finally {
            clearInterval(calPollTimer);
            if (btnTriggerDark) {
                btnTriggerDark.disabled = false;
                btnTriggerDark.textContent = "Capture & Build All Master Dark Profiles (6 Profiles)";
            }
        }
    }

    if (btnTriggerDark) {
        btnTriggerDark.addEventListener("click", () => runAutoDarkLibrary(false));
    }

    // ------------------------------------------------------------------
    // Station Setup, Manual Settings & Astrometric Plate-Solving (hoan.uk/astronomy/plate-solving)
    // ------------------------------------------------------------------
    function azimuthToCardinal(az) {
        const dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
        const idx = Math.round((((az % 360) + 360) % 360) / 22.5) % 16;
        return dirs[idx];
    }

    function updateStationUI(st) {
        if (!st) return;
        state.station = st;

        const latVal = st.latitude_deg !== undefined ? st.latitude_deg : st.latitude;
        const lonVal = st.longitude_deg !== undefined ? st.longitude_deg : st.longitude;
        const nameVal = st.location_name || st.station_name || "Hanoi, Vietnam";
        const sensor = st.camera_sensor || {};
        const optics = st.optics || st.camera || {};
        const ori = st.orientation || {};

        // Populate inputs if user is not actively typing in them
        if (stationNameInput && document.activeElement !== stationNameInput) {
            stationNameInput.value = nameVal;
        }
        if (stationLatInput && document.activeElement !== stationLatInput && latVal !== undefined) {
            stationLatInput.value = Number(latVal).toFixed(4);
        }
        if (stationLonInput && document.activeElement !== stationLonInput && lonVal !== undefined) {
            stationLonInput.value = Number(lonVal).toFixed(4);
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
        if (oriNpaInput && document.activeElement !== oriNpaInput && ori.north_position_angle_deg !== undefined) {
            oriNpaInput.value = Number(ori.north_position_angle_deg).toFixed(2);
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
            const lat = Number(latVal !== undefined ? latVal : 21.0285);
            const lon = Number(lonVal !== undefined ? lonVal : 105.8542);
            const elev = Math.round(Number(st.elevation_m || 20));
            stCoords.textContent = `${Math.abs(lat).toFixed(4)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(4)}°${lon >= 0 ? "E" : "W"} (${elev}m)`;
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
        try {
            const res = await fetch("/api/camera/orientation", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (!res.ok || !data.success) throw new Error(data.detail || "Unknown error");
            orientationBusy = false;
            if (data.station_config) updateStationUI(data.station_config);
            drawReticle();
            if (liveImg) liveImg.src = `/api/live/snapshot.jpg?t=${Date.now()}`;
        } catch (err) {
            orientationBusy = false;
            showAlert(stationAlert, `❌ Failed to update image orientation: ${err.message}`, "error", true);
            if (state.station) syncOrientationUI(state.station.image_transform);
        } finally {
            orientationBusy = false;
            toggles.forEach((t) => t && (t.disabled = false));
        }
    }

    // ------------------------------------------------------------------
    // Output Resolution
    // ------------------------------------------------------------------
    const outputResSelect = document.getElementById("output-resolution");
    const outputResBadge = document.getElementById("output-res-badge");
    const hudResolution = document.getElementById("hud-resolution");

    function syncResolutionUI(res) {
        if (!res || !res.width) return;
        const txt = `${res.width} × ${res.height}`;
        if (outputResBadge) outputResBadge.textContent = txt;
        if (hudResolution) hudResolution.textContent = txt;
        if (outputResSelect && document.activeElement !== outputResSelect) {
            outputResSelect.value = `${res.width}x${res.height}`;
        }
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
            try {
                const res = await fetch("/api/camera/resolution", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ width, height }),
                });
                const data = await res.json();
                if (!res.ok || !data.success) throw new Error(data.detail || "Unknown error");
                syncResolutionUI(data.current);
                if (data.station_config) updateStationUI(data.station_config);
                loadPerformance();
            } catch (err) {
                showAlert(stationAlert, `❌ Failed to set resolution: ${err.message}`, "error", true);
                if (state.station) syncResolutionUI(state.station.output_resolution);
            } finally {
                outputResSelect.disabled = false;
            }
        });
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
    };
    const perfBinNote = document.getElementById("perf-bin-note");

    function syncPerformanceUI(data) {
        if (!data || !data.current) return;
        Object.entries(perfFields).forEach(([key, el]) => {
            if (!el || document.activeElement === el || data.current[key] === undefined) return;
            if (el.type === "checkbox") {
                el.checked = !!Number(data.current[key]);
                return;
            }
            const scale = Number(el.dataset.scale || 1);
            const val = String(scale > 1 ? Number(data.current[key]) / scale : data.current[key]);
            if (el.tagName === "SELECT" && ![...el.options].some((o) => o.value === val)) {
                const opt = document.createElement("option");
                opt.value = val;
                opt.textContent = `${val} px`;
                el.appendChild(opt);
            }
            el.value = val;
        });
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
            if (!Number.isFinite(value)) return;
            try {
                const res = await fetch("/api/camera/performance", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ [key]: value }),
                });
                const data = await res.json();
                if (!res.ok || !data.success) throw new Error(JSON.stringify(data.detail || "Unknown error"));
                syncPerformanceUI(data);
            } catch (err) {
                showAlert(stationAlert, `❌ Failed to save performance setting: ${err.message}`, "error", true);
                loadPerformance();
            }
        });
    });

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

    async function saveWhiteBalance(payload) {
        try {
            const res = await fetch("/api/camera/white-balance", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (!res.ok || !data.success) throw new Error(JSON.stringify(data.detail || "Unknown error"));
            syncWhiteBalanceUI(data);
        } catch (err) {
            showAlert(stationAlert, `❌ White balance error: ${err.message}`, "error", true);
            loadWhiteBalance();
        }
    }

    if (wbModeSelect) wbModeSelect.addEventListener("change", () => saveWhiteBalance({ mode: wbModeSelect.value }));
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
            });
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
        const lat = parseFloat(stationLatInput ? stationLatInput.value : "21.0285");
        const lon = parseFloat(stationLonInput ? stationLonInput.value : "105.8542");
        const elev = parseFloat(stationElevInput ? stationElevInput.value : "20");
        const name = stationNameInput ? stationNameInput.value.trim() : "Hanoi, Vietnam";
        return {
            latitude_deg: isNaN(lat) ? 21.0285 : lat,
            longitude_deg: isNaN(lon) ? 105.8542 : lon,
            elevation_m: isNaN(elev) ? 20.0 : elev,
            location_name: name || "Hanoi, Vietnam",
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
                        if (stationLatInput) stationLatInput.value = lat.toFixed(4);
                        if (stationLonInput) stationLonInput.value = lon.toFixed(4);
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
                                updateStationUI(st);
                                showAlert(stationAlert, `✅ Browser GPS coordinates: ${lat.toFixed(4)}°N, ${lon.toFixed(4)}°E (${elev}m).`, "success", true);
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

    // Button 2: Save Coordinates & Manual Optical/Orientation Settings ("fix, settings" on UI)
    if (btnStationSave) {
        btnStationSave.addEventListener("click", async () => {
            const payload = readManualStationPayload("manual_ui", false);
            if (payload.latitude_deg < -90 || payload.latitude_deg > 90 || payload.longitude_deg < -180 || payload.longitude_deg > 180) {
                showAlert(stationAlert, "❌ Latitude must be within [-90, 90] and longitude within [-180, 180].", "error", true);
                return;
            }
            btnStationSave.disabled = true;
            try {
                const res = await fetch("/api/station/settings", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
                const data = await res.json();
                const st = data.station_config || data.station;
                if (res.ok && st) {
                    updateStationUI(st);
                    showAlert(stationAlert, `✅ ${data.message || "Configuration and manual adjustments saved!"}`, "success", true);
                } else {
                    showAlert(stationAlert, `❌ Failed to save configuration: ${data.detail || "Unknown error"}`, "error", true);
                }
            } catch (err) {
                showAlert(stationAlert, `❌ Network error: ${err.message}`, "error", true);
            } finally {
                btnStationSave.disabled = false;
            }
        });
    }

    // Button 3: Capture & Plate-Solve via https://hoan.uk/astronomy/plate-solving (Only when user clicks!)
    if (btnStationSolve) {
        btnStationSolve.addEventListener("click", async () => {
            const payload = readManualStationPayload("manual", true);
            btnStationSolve.disabled = true;
            btnStationSolve.textContent = "⏳ Capturing & plate-solving via hoan.uk...";
            if (stationSolveBadge) {
                stationSolveBadge.textContent = "SOLVING...";
                stationSolveBadge.className = "badge badge-warning";
            }
            showAlert(
                stationAlert,
                `⏳ Capturing a frame and sending it to the hoan.uk plate solver (can take up to a minute)...`,
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
                const startRes = await fetch("/api/station/plate-solve/start?fov_hint=auto", { method: "POST" });
                if (!startRes.ok) throw new Error(`could not start plate-solve (HTTP ${startRes.status})`);
                const t0 = Date.now();
                let data = await startRes.json();
                while (data.state === "running" && Date.now() - t0 < 6 * 60 * 1000) {
                    const secs = Math.round((Date.now() - t0) / 1000);
                    btnStationSolve.textContent = `⏳ Solving… ${secs}s`;
                    showAlert(stationAlert, `⏳ ${data.message || "Plate-solving…"} (${secs}s elapsed)`, "info", true);
                    await new Promise((r) => setTimeout(r, 3000));
                    const stRes = await fetch("/api/station/plate-solve/status");
                    if (stRes.ok) data = await stRes.json();
                }
                if (data.station_config) updateStationUI(data.station_config);
                if (data.state === "success") {
                    showAlert(stationAlert, `✅ ${data.message || "Plate-solving complete!"}`, "success", true);
                } else if (data.state === "running") {
                    showAlert(stationAlert, "⏳ Plate-solve is still running on the station; check the Activity Log for the result.", "info", true);
                } else {
                    showAlert(stationAlert, `❌ ${data.message || data.error || "Plate-solve failed"}`, "error", true);
                }
            } catch (err) {
                showAlert(stationAlert, `❌ Network connection error: ${err.message}`, "error", true);
            } finally {
                btnStationSolve.disabled = false;
                btnStationSolve.textContent = "🔭 Capture & Auto Plate-Solve";
            }
        });
    }

    // ------------------------------------------------------------------
    // Live View Controls
    // ------------------------------------------------------------------
    btnRefreshFrame.addEventListener("click", () => {
        liveImg.src = `/api/live/snapshot.jpg?t=${Date.now()}`;
    });

    // ------------------------------------------------------------------
    // Live View Zoom & Pan (wheel / pinch / drag / double-click / + - 0 keys)
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

        viewportContainer.addEventListener("dblclick", (e) => {
            if (e.target.closest(".zoom-controls")) return;
            const rect = viewportContainer.getBoundingClientRect();
            zoomAt(view.zoom > 1.001 ? 1 : 3, e.clientX - rect.left, e.clientY - rect.top);
        });

        // Drag to pan, two-finger pinch to zoom
        const pointers = new Map();
        let pinchStart = null;
        viewportContainer.addEventListener("pointerdown", (e) => {
            if (e.target.closest(".zoom-controls")) return;
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

        window.addEventListener("resize", () => applyView(false));
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

    function drawReticle() {
        if (!reticleCanvas || !viewportContainer) return;
        const w = viewportContainer.clientWidth;
        const h = viewportContainer.clientHeight;
        if (w <= 10 || h <= 10) return;

        // Render the overlay at zoomed pixel density so it stays sharp when magnified
        const pxRatio = Math.min(4, (window.devicePixelRatio || 1) * view.zoom);
        reticleCanvas.width = Math.round(w * pxRatio);
        reticleCanvas.height = Math.round(h * pxRatio);
        const ctx = reticleCanvas.getContext("2d");
        ctx.setTransform(pxRatio, 0, 0, pxRatio, 0, 0);
        ctx.clearRect(0, 0, w, h);

        const showSky = chkSkyOverlay ? chkSkyOverlay.checked : true;
        const showLabels = chkSkyLabels ? chkSkyLabels.checked : true;
        const showGrid = chkGridOverlay ? chkGridOverlay.checked : false;

        if (!showSky && !showLabels && !showGrid) return;

        // Read solved station, camera sensor, optics & orientation parameters
        const st = state.station || {};
        const sensor = st.camera_sensor || {};
        const optics = st.optics || st.camera || {};
        const ori = st.orientation || {};

        const sensorW = Number(sensor.width_px || 2592);
        const sensorH = Number(sensor.height_px || 1944);

        const latDeg =
            st.latitude_deg !== undefined
                ? Number(st.latitude_deg)
                : stationLatInput && !isNaN(parseFloat(stationLatInput.value))
                ? parseFloat(stationLatInput.value)
                : 21.0285;
        const lonDeg =
            st.longitude_deg !== undefined
                ? Number(st.longitude_deg)
                : stationLonInput && !isNaN(parseFloat(stationLonInput.value))
                ? parseFloat(stationLonInput.value)
                : 105.8542;

        const optX =
            optics.optical_center_x_px !== undefined
                ? Number(optics.optical_center_x_px)
                : optCxInput && !isNaN(parseFloat(optCxInput.value))
                ? parseFloat(optCxInput.value)
                : sensorW / 2.0;
        const optY =
            optics.optical_center_y_px !== undefined
                ? Number(optics.optical_center_y_px)
                : optCyInput && !isNaN(parseFloat(optCyInput.value))
                ? parseFloat(optCyInput.value)
                : sensorH / 2.0;
        const r90SensorPx =
            optics.horizon_radius_90_px !== undefined
                ? Number(optics.horizon_radius_90_px)
                : optR90Input && !isNaN(parseFloat(optR90Input.value))
                ? parseFloat(optR90Input.value)
                : Math.min(sensorW, sensorH) * 0.46;

        const northPaDeg =
            ori.north_position_angle_deg !== undefined
                ? Number(ori.north_position_angle_deg)
                : oriNpaInput && !isNaN(parseFloat(oriNpaInput.value))
                ? parseFloat(oriNpaInput.value)
                : 0.0;
        const tiltDeg =
            ori.zenith_tilt_deg !== undefined
                ? Number(ori.zenith_tilt_deg)
                : oriTiltInput && !isNaN(parseFloat(oriTiltInput.value))
                ? parseFloat(oriTiltInput.value)
                : 0.0;
        const tiltAzDeg =
            ori.azimuth_deg !== undefined
                ? Number(ori.azimuth_deg)
                : oriAzInput && !isNaN(parseFloat(oriAzInput.value))
                ? parseFloat(oriAzInput.value)
                : 180.0;

        // Compute rendered 4:3 image box inside viewportContainer (handles fullscreen letterboxing)
        const containerAspect = w / Math.max(1, h);
        const sensorAspect = sensorW / Math.max(1, sensorH);
        let renderW = w;
        let renderH = h;
        let offsetX = 0;
        let offsetY = 0;
        if (containerAspect > sensorAspect) {
            renderH = h;
            renderW = h * sensorAspect;
            offsetX = (w - renderW) / 2.0;
        } else {
            renderW = w;
            renderH = w / sensorAspect;
            offsetY = (h - renderH) / 2.0;
        }

        const scaleX = renderW / sensorW;
        const scaleY = renderH / sensorH;
        const scaleMean = (scaleX + scaleY) * 0.5;

        // Keep every overlay mark inside the displayed image (not in the letterbox bars)
        ctx.beginPath();
        ctx.rect(offsetX, offsetY, renderW, renderH);
        ctx.clip();

        // Displayed image is mirrored/flipped server-side; map native sensor coords the same way
        const tf = st.image_transform || {};
        const rot180 = Number(tf.rotate_deg || 0) % 360 === 180;
        const mirrorX = !!tf.flip_horizontal !== rot180;
        const mirrorY = !!tf.flip_vertical !== rot180;
        const toImgX = (sx) => (mirrorX ? sensorW - sx : sx);
        const toImgY = (sy) => (mirrorY ? sensorH - sy : sy);

        const cxCanvas = offsetX + toImgX(optX) * scaleX;
        const cyCanvas = offsetY + toImgY(optY) * scaleY;
        const r90Canvas = r90SensorPx * scaleMean;

        const nowUtc = new Date();
        const lmstDeg = computeLmstDeg(lonDeg, nowUtc);
        const latRad = (latDeg * Math.PI) / 180.0;
        const sinLat = Math.sin(latRad);
        const cosLat = Math.cos(latRad);
        const northAngleRad = (northPaDeg * Math.PI) / 180.0;
        const tiltRad = (tiltDeg * Math.PI) / 180.0;
        const tiltAzRad = (tiltAzDeg * Math.PI) / 180.0;
        const cosTilt = Math.cos(tiltRad);
        const sinTilt = Math.sin(tiltRad);
        const cosTiltAz = Math.cos(tiltAzRad);
        const sinTiltAz = Math.sin(tiltAzRad);

        // Rectilinear (non-fisheye) lens solved by pointing: optical axis at (azimuth_deg, altitude_deg),
        // image "up" = local vertical rotated by the roll angle (same model as the bright-star solver)
        const hfovDeg = Number(optics.fov_horizontal_deg || 0);
        const lensProj = optics.lens_projection || (/gnomonic|rectilinear/i.test(String(optics.projection_type || "")) ? "rectilinear" : "");
        const isFisheyePointing = !!optics.pointing_model && lensProj === "equidistant" && Number(optics.focal_length_px) > 0;
        const isRectilinear = isFisheyePointing || (lensProj === "rectilinear" && hfovDeg > 1 && hfovDeg < 170);
        const distK = Number(optics.distortion_k || 0);
        let camX = null, camY = null, camZ = null, fPx = 0;
        if (isRectilinear) {
            const d2r = Math.PI / 180.0;
            const pAlt = Number(ori.altitude_deg !== undefined ? ori.altitude_deg : 90.0 - tiltDeg) * d2r;
            const pAz = tiltAzDeg * d2r;
            const roll = northPaDeg * d2r;
            fPx = Number(optics.focal_length_px) > 0
                ? Number(optics.focal_length_px)
                : (sensorW / 2.0) / Math.tan((hfovDeg / 2.0) * d2r);
            // ENU vectors [east, north, up]
            camZ = [Math.cos(pAlt) * Math.sin(pAz), Math.cos(pAlt) * Math.cos(pAz), Math.sin(pAlt)];
            let vert = [-camZ[2] * camZ[0], -camZ[2] * camZ[1], 1.0 - camZ[2] * camZ[2]];
            const vLen = Math.hypot(vert[0], vert[1], vert[2]) || 1;
            vert = vert.map((c) => c / vLen);
            const zxv = [
                camZ[1] * vert[2] - camZ[2] * vert[1],
                camZ[2] * vert[0] - camZ[0] * vert[2],
                camZ[0] * vert[1] - camZ[1] * vert[0],
            ];
            const up = vert.map((c, i) => c * Math.cos(roll) + zxv[i] * Math.sin(roll));
            camY = up.map((c) => -c); // image y grows downward
            camX = [
                camY[1] * camZ[2] - camY[2] * camZ[1],
                camY[2] * camZ[0] - camY[0] * camZ[2],
                camY[0] * camZ[1] - camY[1] * camZ[0],
            ];
        }

        // Project local Horizontal (altDeg, azDeg) -> Canvas (x, y)
        function projectAltAzToCanvas(altDeg, azDeg) {
            if (isRectilinear) {
                const a = (altDeg * Math.PI) / 180.0;
                const z = (azDeg * Math.PI) / 180.0;
                const v = [Math.cos(a) * Math.sin(z), Math.cos(a) * Math.cos(z), Math.sin(a)];
                const dot = (u) => u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
                const cz = dot(camZ);
                const thetaRad = Math.acos(Math.max(-1, Math.min(1, cz)));
                const theta = (thetaRad * 180.0) / Math.PI;
                let sensorPx, sensorPy;
                if (isFisheyePointing) {
                    // Equidistant fisheye with cubic distortion: r = f (theta + k theta^3)
                    if (theta > 110) return { x: NaN, y: NaN, alt: altDeg, az: azDeg, theta, visible: false };
                    const cx = dot(camX), cy = dot(camY);
                    const rho = Math.hypot(cx, cy) || 1e-12;
                    const r = fPx * (thetaRad + distK * thetaRad * thetaRad * thetaRad);
                    sensorPx = optX + (r * cx) / rho;
                    sensorPy = optY + (r * cy) / rho;
                } else {
                    if (cz < 0.05) return { x: NaN, y: NaN, alt: altDeg, az: azDeg, theta, visible: false };
                    sensorPx = optX + (fPx * dot(camX)) / cz;
                    sensorPy = optY + (fPx * dot(camY)) / cz;
                }
                const canvasX = offsetX + toImgX(sensorPx) * scaleX;
                const canvasY = offsetY + toImgY(sensorPy) * scaleY;
                const inViewport =
                    canvasX >= offsetX - 10 && canvasX <= offsetX + renderW + 10 &&
                    canvasY >= offsetY - 10 && canvasY <= offsetY + renderH + 10;
                return { x: canvasX, y: canvasY, alt: altDeg, az: azDeg, theta, visible: inViewport && altDeg >= -1.5 };
            }
            const altRad = (altDeg * Math.PI) / 180.0;
            const azRad = (azDeg * Math.PI) / 180.0;
            const cosAlt = Math.cos(altRad);
            const sinAlt = Math.sin(altRad);

            // 3D local horizon unit vector: N (+north), E (+east), Z (+zenith)
            let vN = cosAlt * Math.cos(azRad);
            let vE = cosAlt * Math.sin(azRad);
            let vZ = sinAlt;

            // Apply camera zenith tilt toward tiltAzDeg if non-zero
            if (Math.abs(tiltDeg) > 0.01) {
                const uPar = vN * cosTiltAz + vE * sinTiltAz;
                const uPerp = -vN * sinTiltAz + vE * cosTiltAz;
                const uParRot = uPar * cosTilt - vZ * sinTilt;
                vZ = uPar * sinTilt + vZ * cosTilt;
                vN = uParRot * cosTiltAz - uPerp * sinTiltAz;
                vE = uParRot * sinTiltAz + uPerp * cosTiltAz;
            }

            const thetaDeg = (Math.acos(Math.max(-1.0, Math.min(1.0, vZ))) * 180.0) / Math.PI;
            const rPx = r90SensorPx * (thetaDeg / 90.0);
            const camAzRad = Math.atan2(vE, vN) - northAngleRad;

            // Sky dome parity looking up: North = UP (-Y), East = LEFT (-X), South = DOWN (+Y), West = RIGHT (+X)
            const sensorPx = optX - rPx * Math.sin(camAzRad);
            const sensorPy = optY - rPx * Math.cos(camAzRad);

            const canvasX = offsetX + toImgX(sensorPx) * scaleX;
            const canvasY = offsetY + toImgY(sensorPy) * scaleY;
            const inDome = thetaDeg <= 93.0 && altDeg >= -1.5;
            const inViewport =
                canvasX >= offsetX - 10 &&
                canvasX <= offsetX + renderW + 10 &&
                canvasY >= offsetY - 10 &&
                canvasY <= offsetY + renderH + 10;

            return {
                x: canvasX,
                y: canvasY,
                alt: altDeg,
                az: azDeg,
                theta: thetaDeg,
                visible: inDome && inViewport,
            };
        }

        // Project Celestial J2000 (raDeg, decDeg) -> Canvas (x, y, alt, az, visible)
        function projectRaDecToCanvas(raDeg, decDeg) {
            const haRad = ((((lmstDeg - raDeg) % 360.0) + 360.0) % 360.0) * (Math.PI / 180.0);
            const decRad = (decDeg * Math.PI) / 180.0;
            const sinDec = Math.sin(decRad);
            const cosDec = Math.cos(decRad);

            const sinAlt = Math.max(-1.0, Math.min(1.0, sinLat * sinDec + cosLat * cosDec * Math.cos(haRad)));
            const altRad = Math.asin(sinAlt);
            const altDeg = (altRad * 180.0) / Math.PI;

            const y = -Math.sin(haRad) * cosDec;
            const x = sinDec * cosLat - cosDec * sinLat * Math.cos(haRad);
            const azDeg = ((Math.atan2(y, x) * 180.0) / Math.PI + 360.0) % 360.0;

            return projectAltAzToCanvas(altDeg, azDeg);
        }

        // 1a. Rectilinear lens: altitude lines and azimuth ticks instead of fisheye rings
        if (showGrid && isRectilinear) {
            const polyline = (pts) => {
                let pen = false;
                ctx.beginPath();
                pts.forEach((p) => {
                    if (!Number.isFinite(p.x) || p.theta > (isFisheyePointing ? 100 : 85)) { pen = false; return; }
                    if (pen) ctx.lineTo(p.x, p.y); else ctx.moveTo(p.x, p.y);
                    pen = true;
                });
                ctx.stroke();
            };
            ctx.save();
            for (let alt = 0; alt <= 75; alt += 15) {
                ctx.strokeStyle = alt === 0 ? "rgba(56, 189, 248, 0.6)" : "rgba(56, 189, 248, 0.25)";
                ctx.lineWidth = alt === 0 ? 1.5 : 1;
                ctx.setLineDash(alt === 0 ? [] : [4, 4]);
                const pts = [];
                for (let az = 0; az <= 360; az += 1) pts.push(projectAltAzToCanvas(alt, az));
                polyline(pts);
                const tag = projectAltAzToCanvas(alt, tiltAzDeg);
                if (alt > 0 && tag.visible) {
                    ctx.fillStyle = "rgba(148, 163, 184, 0.8)";
                    ctx.font = "10px monospace";
                    ctx.fillText(`${alt}°`, tag.x + 4, tag.y - 3);
                }
            }
            ctx.setLineDash([]);
            ctx.fillStyle = "#38bdf8";
            ctx.font = "bold 13px monospace";
            ctx.textAlign = "center";
            ctx.shadowColor = "rgba(2, 6, 23, 0.95)";
            ctx.shadowBlur = 4;
            ["N", "NE", "E", "SE", "S", "SW", "W", "NW"].forEach((label, i) => {
                for (const alt of [2, 20, 40]) {
                    const p = projectAltAzToCanvas(alt, i * 45);
                    if (p.visible) { ctx.fillText(label, p.x, p.y - 6); break; }
                }
            });
            ctx.restore();
        }

        // 1. Draw Horizon, Altitude Circles & Compass Reticle
        if (showGrid && !isRectilinear) {
            // Unit direction (canvas space) from optical center toward a horizon azimuth
            const dirTo = (azDeg) => {
                const p = projectAltAzToCanvas(0.0, azDeg);
                const dx = p.x - cxCanvas;
                const dy = p.y - cyCanvas;
                const len = Math.hypot(dx, dy) || 1;
                return { x: dx / len, y: dy / len };
            };
            const dN = dirTo(0.0);
            const dE = dirTo(90.0);
            const dS = dirTo(180.0);
            const dW = dirTo(270.0);

            ctx.save();
            ctx.translate(cxCanvas, cyCanvas);

            // Outer Horizon Circle (0 deg altitude = R90)
            ctx.strokeStyle = "rgba(56, 189, 248, 0.45)";
            ctx.lineWidth = 1.25;
            ctx.beginPath();
            ctx.arc(0, 0, r90Canvas, 0, 2 * Math.PI);
            ctx.stroke();

            // 30° and 60° altitude rings
            ctx.strokeStyle = "rgba(56, 189, 248, 0.25)";
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 4]);
            ctx.beginPath();
            ctx.arc(0, 0, r90Canvas * (60.0 / 90.0), 0, 2 * Math.PI);
            ctx.moveTo(r90Canvas * (30.0 / 90.0), 0);
            ctx.arc(0, 0, r90Canvas * (30.0 / 90.0), 0, 2 * Math.PI);
            ctx.stroke();

            // Cardinal N-S and E-W axes
            ctx.beginPath();
            ctx.moveTo(dN.x * r90Canvas, dN.y * r90Canvas);
            ctx.lineTo(dS.x * r90Canvas, dS.y * r90Canvas);
            ctx.moveTo(dE.x * r90Canvas, dE.y * r90Canvas);
            ctx.lineTo(dW.x * r90Canvas, dW.y * r90Canvas);
            ctx.stroke();
            ctx.setLineDash([]);

            // Altitude ring degree tags (along the North axis)
            ctx.fillStyle = "rgba(148, 163, 184, 0.75)";
            ctx.font = "10px monospace";
            ctx.textAlign = "left";
            ctx.fillText("60°", dN.x * r90Canvas * (30.0 / 90.0) + 4, dN.y * r90Canvas * (30.0 / 90.0) + 11);
            ctx.fillText("30°", dN.x * r90Canvas * (60.0 / 90.0) + 4, dN.y * r90Canvas * (60.0 / 90.0) + 11);

            // Cardinal direction labels follow PA and mirror/flip
            ctx.fillStyle = "#38bdf8";
            ctx.font = "bold 13px monospace";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.shadowColor = "rgba(2, 6, 23, 0.95)";
            ctx.shadowBlur = 4;
            const cardRadius = Math.min(r90Canvas - 14, Math.min(renderW, renderH) * 0.46);
            [[dN, "N"], [dS, "S"], [dE, "E"], [dW, "W"]].forEach(([d, label]) => {
                ctx.fillText(label, d.x * cardRadius, d.y * cardRadius);
            });
            ctx.restore();

            // Zenith marker (+)
            const zenPt = projectAltAzToCanvas(90.0, 0.0);
            ctx.save();
            ctx.strokeStyle = "rgba(250, 204, 21, 0.75)";
            ctx.lineWidth = 1.2;
            ctx.beginPath();
            ctx.moveTo(zenPt.x - 6, zenPt.y);
            ctx.lineTo(zenPt.x + 6, zenPt.y);
            ctx.moveTo(zenPt.x, zenPt.y - 6);
            ctx.lineTo(zenPt.x, zenPt.y + 6);
            ctx.stroke();
            ctx.restore();
        }

        const catalog = window.ALLSKY_CATALOG || { constellations: [], stars: [], messier: [] };
        const occupiedBoxes = [];

        function canPlaceLabel(x, y, textWidth, textHeight) {
            const box = {
                x1: x - 3,
                y1: y - textHeight,
                x2: x + textWidth + 3,
                y2: y + 3,
            };
            if (box.x1 < offsetX + 4 || box.x2 > offsetX + renderW - 4 || box.y1 < offsetY + 4 || box.y2 > offsetY + renderH - 4) {
                return false;
            }
            for (let i = 0; i < occupiedBoxes.length; i++) {
                const b = occupiedBoxes[i];
                if (!(box.x2 < b.x1 || box.x1 > b.x2 || box.y2 < b.y1 || box.y1 > b.y2)) {
                    return false;
                }
            }
            occupiedBoxes.push(box);
            return true;
        }

        let visibleConstellationsCount = 0;
        let visibleStarsCount = 0;

        // 2. Draw IAU Constellation Stick Figures & Collect Visible Constellation Centroids
        const visibleConstellationLabels = [];
        if ((showSky || showLabels) && Array.isArray(catalog.constellations)) {
            ctx.save();
            ctx.strokeStyle = "rgba(56, 189, 248, 0.52)";
            ctx.lineWidth = 1.35;
            ctx.lineCap = "round";

            const maxSegLen = isRectilinear ? renderW * (isFisheyePointing ? 0.35 : 0.6) : r90Canvas * 0.65;

            catalog.constellations.forEach((con) => {
                let sumX = 0;
                let sumY = 0;
                let countPts = 0;
                let anySegmentVisible = false;

                if (Array.isArray(con.lines)) {
                    con.lines.forEach((seg) => {
                        if (!Array.isArray(seg) || seg.length < 2) return;
                        const p1 = projectRaDecToCanvas(seg[0][0], seg[0][1]);
                        const p2 = projectRaDecToCanvas(seg[1][0], seg[1][1]);
                        if (p1.alt >= 0.0 && p2.alt >= 0.0 && (p1.visible || p2.visible)) {
                            const segDist = Math.hypot(p2.x - p1.x, p2.y - p1.y);
                            if (segDist <= maxSegLen) {
                                anySegmentVisible = true;
                                if (showSky) {
                                    ctx.beginPath();
                                    ctx.moveTo(p1.x, p1.y);
                                    ctx.lineTo(p2.x, p2.y);
                                    ctx.stroke();
                                }
                                sumX += p1.x + p2.x;
                                sumY += p1.y + p2.y;
                                countPts += 2;
                            }
                        }
                    });
                }

                const centerProj = projectRaDecToCanvas(con.ra, con.dec);
                if (anySegmentVisible || (centerProj.visible && centerProj.alt >= 8.0)) {
                    visibleConstellationsCount++;
                    const labelX = countPts > 0 ? sumX / countPts : centerProj.x;
                    const labelY = countPts > 0 ? sumY / countPts : centerProj.y;
                    if (centerProj.alt >= 5.0 || countPts >= 4) {
                        visibleConstellationLabels.push({
                            id: con.id,
                            name: con.name,
                            common: con.common || "",
                            x: labelX,
                            y: labelY,
                            alt: centerProj.alt,
                        });
                    }
                }
            });
            ctx.restore();
        }

        // 3. Draw Messier Deep-Sky Objects (DSOs)
        const visibleMessier = [];
        if ((showSky || showLabels) && Array.isArray(catalog.messier)) {
            ctx.save();
            catalog.messier.forEach((dso) => {
                const pt = projectRaDecToCanvas(dso.ra, dso.dec);
                if (!pt.visible || pt.alt < 5.0) return;
                visibleMessier.push({ ...dso, x: pt.x, y: pt.y, alt: pt.alt });
                if (showSky) {
                    ctx.strokeStyle = "rgba(250, 204, 21, 0.6)";
                    ctx.lineWidth = 1;
                    ctx.beginPath();
                    ctx.moveTo(pt.x, pt.y - 5);
                    ctx.lineTo(pt.x + 5, pt.y);
                    ctx.lineTo(pt.x, pt.y + 5);
                    ctx.lineTo(pt.x - 5, pt.y);
                    ctx.closePath();
                    ctx.stroke();
                }
            });
            ctx.restore();
        }

        // 4. Mark bright navigation stars (thin rings, never covering the star itself)
        const visibleBrightStars = [];
        if ((showSky || showLabels) && Array.isArray(catalog.stars)) {
            ctx.save();
            catalog.stars.forEach((star) => {
                const pt = projectRaDecToCanvas(star.ra, star.dec);
                if (!pt.visible || pt.alt < 1.0) return;
                visibleStarsCount++;

                const mag = Number(star.mag !== undefined ? star.mag : 3.0);
                const radius = Math.max(1.4, Math.min(4.8, 3.8 - mag * 0.65));

                if (showSky && mag <= 3.0) {
                    // Thin open ring around the catalog position: the real (often faint) star stays visible inside
                    ctx.strokeStyle = "rgba(186, 230, 253, 0.55)";
                    ctx.lineWidth = 1;
                    ctx.beginPath();
                    ctx.arc(pt.x, pt.y, radius + 4, 0, 2 * Math.PI);
                    ctx.stroke();
                }

                if (star.name && mag <= 2.55) {
                    visibleBrightStars.push({
                        name: star.name,
                        bayer: star.bayer || "",
                        mag: mag,
                        x: pt.x,
                        y: pt.y,
                        r: radius,
                        color: star.color || "#f8fafc",
                    });
                }
            });
            ctx.restore();
        }

        // 5. Draw Labels (Constellation Names, Bright Navigation Stars, Iconic Messier DSOs)
        if (showLabels) {
            ctx.save();
            ctx.shadowColor = "rgba(2, 6, 23, 0.95)";
            ctx.shadowBlur = 4;

            // 5a. Constellation Labels (prioritize highest altitude near zenith)
            visibleConstellationLabels.sort((a, b) => b.alt - a.alt);
            ctx.font = "bold 11px Inter, system-ui, sans-serif";
            ctx.textAlign = "center";

            visibleConstellationLabels.forEach((con) => {
                const labelText = con.common
                    ? `${con.name.toUpperCase()} (${con.common})`
                    : con.name.toUpperCase();
                const metrics = ctx.measureText(labelText);
                const tw = metrics.width;
                const th = 13;
                const lx = con.x - tw / 2;
                const ly = con.y;

                if (canPlaceLabel(lx, ly, tw, th)) {
                    ctx.fillStyle = "rgba(15, 23, 42, 0.32)";
                    ctx.fillRect(lx - 4, ly - 11, tw + 8, 14);
                    ctx.fillStyle = "rgba(125, 211, 252, 0.85)";
                    ctx.fillText(labelText, con.x, ly);
                }
            });

            // 5b. Bright Star Labels (sorted brightest first)
            visibleBrightStars.sort((a, b) => a.mag - b.mag);
            ctx.font = "500 10.5px Inter, system-ui, sans-serif";
            ctx.textAlign = "left";

            visibleBrightStars.forEach((stItem) => {
                const labelText = stItem.name;
                const tw = ctx.measureText(labelText).width;
                const lx = stItem.x + stItem.r + 8;
                const ly = stItem.y + 3;

                if (canPlaceLabel(lx, ly, tw, 12)) {
                    ctx.fillStyle = "rgba(254, 249, 195, 0.85)";
                    ctx.fillText(labelText, lx, ly);
                }
            });

            // 5c. Iconic Messier DSO Labels (M45, M42, M31, M44, etc.)
            ctx.font = "600 10px monospace";
            visibleMessier.forEach((dso) => {
                const labelText = `${dso.id} ${dso.name}`;
                const tw = ctx.measureText(labelText).width;
                const lx = dso.x + 7;
                const ly = dso.y + 3;
                if (canPlaceLabel(lx, ly, tw, 11)) {
                    ctx.fillStyle = "rgba(253, 224, 71, 0.8)";
                    ctx.fillText(labelText, lx, ly);
                }
            });

            ctx.restore();
        }

        // 5d. Sun and Moon (drawn even when the sky catalog is hidden: they are the brightest things in view)
        {
            const sm = sunMoonEquatorial(nowUtc);
            const bodies = [
                { key: "sun", label: "Sun", pos: sm.sun, ring: "rgba(250, 204, 21, 0.95)", text: "rgba(254, 240, 138, 0.95)", r: 11 },
                { key: "moon", label: `Moon ${Math.round(sm.moon.illum * 100)}%`, pos: sm.moon, ring: "rgba(226, 232, 240, 0.9)", text: "rgba(241, 245, 249, 0.95)", r: 9 },
            ];
            ctx.save();
            ctx.font = "600 11px Inter, system-ui, sans-serif";
            ctx.textAlign = "left";
            ctx.shadowColor = "rgba(2, 6, 23, 0.95)";
            ctx.shadowBlur = 4;
            bodies.forEach((b) => {
                const pt = projectRaDecToCanvas(b.pos.ra, b.pos.dec);
                if (!pt.visible) return;
                ctx.strokeStyle = b.ring;
                ctx.lineWidth = 1.6;
                ctx.beginPath();
                ctx.arc(pt.x, pt.y, b.r, 0, 2 * Math.PI);
                ctx.stroke();
                if (b.key === "sun") {
                    for (let i = 0; i < 8; i++) {
                        const a = (i * Math.PI) / 4;
                        ctx.beginPath();
                        ctx.moveTo(pt.x + Math.cos(a) * (b.r + 3), pt.y + Math.sin(a) * (b.r + 3));
                        ctx.lineTo(pt.x + Math.cos(a) * (b.r + 8), pt.y + Math.sin(a) * (b.r + 8));
                        ctx.stroke();
                    }
                }
                ctx.fillStyle = b.text;
                ctx.fillText(`${b.label} ${pt.alt.toFixed(1)}°`, pt.x + b.r + 10, pt.y + 4);
            });
            ctx.restore();
        }

        // 6. Draw Top-Left Astrometric Overlay Summary Badge
        ctx.save();
        const badgeText = `LST ${formatLstHms(lmstDeg)} • ${visibleConstellationsCount} constellations • ${visibleStarsCount} stars • Center (${optX.toFixed(0)}, ${optY.toFixed(0)}) ${isRectilinear ? `FOV ${hfovDeg.toFixed(1)}° Az ${tiltAzDeg.toFixed(1)}°` : `R90=${r90SensorPx.toFixed(0)}px`} PA=${northPaDeg.toFixed(1)}°`;
        ctx.font = "11px monospace";
        const bw = ctx.measureText(badgeText).width + 16;
        const bx = offsetX + 10;
        const by = offsetY + 10;
        ctx.fillStyle = "rgba(2, 6, 23, 0.72)";
        ctx.strokeStyle = "rgba(56, 189, 248, 0.35)";
        ctx.lineWidth = 1;
        ctx.fillRect(bx, by, bw, 22);
        ctx.strokeRect(bx, by, bw, 22);
        ctx.fillStyle = "#e2e8f0";
        ctx.textAlign = "left";
        ctx.fillText(badgeText, bx + 8, by + 15);
        ctx.restore();
    }

    if (chkSkyOverlay) chkSkyOverlay.addEventListener("change", drawReticle);
    if (chkSkyLabels) chkSkyLabels.addEventListener("change", drawReticle);
    if (chkGridOverlay) chkGridOverlay.addEventListener("change", drawReticle);
    if (liveImg) liveImg.addEventListener("load", drawReticle);
    window.addEventListener("resize", drawReticle);
    document.addEventListener("fullscreenchange", () => setTimeout(drawReticle, 80));

    // Re-render overlay immediately when user tweaks optical/orientation inputs in Diagnostics tab
    [
        stationLatInput,
        stationLonInput,
        optCxInput,
        optCyInput,
        optR90Input,
        oriNpaInput,
        oriTiltInput,
        oriAzInput,
    ].forEach((inp) => {
        if (inp) inp.addEventListener("input", drawReticle);
    });

    // Periodically refresh sky overlay as Earth rotates (every 5 seconds)
    setInterval(drawReticle, 5000);
    setTimeout(drawReticle, 150);

    // ------------------------------------------------------------------
    // Real-Time 256-Bin Histogram Rendering
    // ------------------------------------------------------------------
    function drawHistogram(histData) {
        if (!histData || !histData.r) return;

        const w = histCanvas.width;
        const h = histCanvas.height;
        histCtx.clearRect(0, 0, w, h);

        const r = histData.r;
        const g = histData.g;
        const b = histData.b;
        const y = histData.y;

        // Find max value across all channels
        let maxVal = 1;
        for (let i = 0; i < 256; i++) {
            maxVal = Math.max(maxVal, r[i], g[i], b[i], y[i]);
        }

        // Channels definition
        const channels = [
            { data: r, color: "rgba(239, 68, 68, 0.7)", fill: "rgba(239, 68, 68, 0.15)" },
            { data: g, color: "rgba(16, 185, 129, 0.7)", fill: "rgba(16, 185, 129, 0.15)" },
            { data: b, color: "rgba(56, 189, 248, 0.7)", fill: "rgba(56, 189, 248, 0.15)" },
            { data: y, color: "rgba(255, 255, 255, 0.9)", fill: "rgba(255, 255, 255, 0.1)" },
        ];

        channels.forEach((ch) => {
            histCtx.beginPath();
            histCtx.moveTo(0, h);
            for (let i = 0; i < 256; i++) {
                const x = (i / 255) * w;
                const norm = ch.data[i] / maxVal;
                const yPos = h - norm * (h - 10);
                histCtx.lineTo(x, yPos);
            }
            histCtx.lineTo(w, h);
            histCtx.fillStyle = ch.fill;
            histCtx.fill();

            histCtx.strokeStyle = ch.color;
            histCtx.lineWidth = 1.2;
            histCtx.stroke();
        });

        // Update statistics
        if (histData.stats) {
            histMean.textContent = histData.stats.mean.toFixed(1);
            histStdDev.textContent = histData.stats.stddev.toFixed(1);
            histMedian.textContent = histData.stats.median;
            histClipLow.textContent = histData.stats.clipping_low_pct.toFixed(2) + "%";
            histClipHigh.textContent = histData.stats.clipping_high_pct.toFixed(2) + "%";
        }
        if (histData.calculation_time_ms !== undefined) {
            histCalcBadge.textContent = `${histData.calculation_time_ms.toFixed(1)} ms`;
        }
    }

    // ------------------------------------------------------------------
    // Telemetry & Status Polling / WebSocket
    // ------------------------------------------------------------------
    function syncModeButtons(modeStr) {
        if (!modeStr) return;
        const m = modeStr.toLowerCase();
        state.mode = m;
        navMode.textContent = m.toUpperCase();
        activeProfileBadge.textContent = m === "auto" ? "AUTO EPHEMERIS" : "MANUAL";
        btnModeManual.classList.toggle("active", m === "manual");
        btnModeManual.classList.toggle("btn-primary", m === "manual");
        btnModeManual.classList.toggle("btn-outline", m !== "manual");
        btnModeAuto.classList.toggle("active", m === "auto");
        btnModeAuto.classList.toggle("btn-primary", m === "auto");
        btnModeAuto.classList.toggle("btn-outline", m !== "auto");
    }

    function updateTelemetryUI(data) {
        if (!data) return;

        if (data.mode) {
            syncModeButtons(data.mode);
        }

        if (data.exposure_s !== undefined) {
            const exp = data.exposure_s;
            telemExp.textContent = exp < 0.01 ? (exp * 1000).toFixed(2) + " ms" : exp < 0.1 ? (exp * 1000).toFixed(1) + " ms" : exp.toFixed(3) + " s";
            if (state.mode === "auto") {
                rangeExposure.value = exp;
                valExposure.textContent = exp < 0.01 ? (exp * 1000).toFixed(2) + " ms" : exp < 0.1 ? (exp * 1000).toFixed(1) + " ms" : exp.toFixed(3) + " s";
            }
        }
        if (data.gain_db !== undefined) {
            telemGain.textContent = data.gain_db.toFixed(1) + " dB";
            if (state.mode === "auto") {
                rangeGain.value = data.gain_db;
                valGain.textContent = data.gain_db.toFixed(1) + " dB";
            }
        }
        if (data.star_count !== undefined) {
            telemStars.textContent = data.star_count;
        }
        if (data.cloud_cover_pct !== undefined) {
            telemClouds.textContent = data.cloud_cover_pct.toFixed(1) + "%";
        }
        if (data.sky_status) {
            telemSkyStatus.textContent = data.sky_status.toUpperCase();
        }
        if (data.fps !== undefined) {
            telemFps.textContent = data.fps.toFixed(1) + " FPS";
        }
        if (data.hw_frame_delta_ms !== undefined) {
            hudHwDelta.textContent = data.hw_frame_delta_ms.toFixed(1) + " ms";
        }
        if (data.sensor_temp_c !== undefined) {
            telemTemp.textContent = data.sensor_temp_c.toFixed(1) + " °C";
        }
        if (data.timestamp) {
            telemTimestamp.textContent = data.timestamp.split("T")[1].replace("Z", "");
        }
        if (data.histogram) {
            drawHistogram(data.histogram);
        }
        if (data.calibration) {
            updateCalibrationUI(data.calibration);
        }
    }

    function updateStatusUI(data) {
        if (!data) return;

        navStatus.textContent = (data.status || "online").toUpperCase();
        if (data.uptime_s !== undefined) {
            const h = Math.floor(data.uptime_s / 3600);
            const m = Math.floor((data.uptime_s % 3600) / 60);
            const s = Math.floor(data.uptime_s % 60);
            telemUptime.textContent = `${h}h ${m}m ${s}s`;
        }

        if (data.hardware) {
            const hw = data.hardware;
            const subtitle = document.getElementById("sensor-subtitle");
            if (subtitle && hw.sensor) {
                subtitle.textContent = `${hw.sensor} • ${hw.resolution || ""}${hw.vid_pid ? ` (${hw.vid_pid})` : ""}`;
            }
            const dayRotRow = document.getElementById("orient-rot180-day-row");
            if (dayRotRow && hw.backend) dayRotRow.style.display = hw.backend === "rts5869" ? "" : "none";
            const regBox = document.getElementById("registers-box");
            if (regBox && hw.has_registers !== undefined) regBox.style.display = hw.has_registers === false ? "none" : "";
            if (hw.limits) {
                if (rangeGain && hw.limits.max_gain_db !== undefined) {
                    rangeGain.max = String(hw.limits.max_gain_db);
                    document.querySelectorAll(".btn-preset-gain").forEach((btn) => {
                        btn.style.display = Number(btn.dataset.gain) > hw.limits.max_gain_db + 0.5 ? "none" : "";
                    });
                }
                if (rangeExposure && hw.limits.max_exposure_s !== undefined) {
                    rangeExposure.max = String(Math.min(Number(rangeExposure.max) || 15, hw.limits.max_exposure_s));
                }
            }
        }

        if (data.hardware && data.hardware.registers && data.hardware.has_registers !== false) {
            const r = data.hardware.registers;
            regVmax.textContent = r.VMAX;
            regHmax.textContent = r.HMAX;
            regShr0.textContent = r.SHR0;
            regPgc.textContent = r.PGC;
        }

        if (data.mode) {
            syncModeButtons(data.mode);
        }
        if (data.calibration) {
            updateCalibrationUI(data.calibration);
        }
        if (data.station) {
            updateStationUI(data.station);
        }
    }

    // ------------------------------------------------------------------
    // Real-Time Binary WebSocket (JPEG Frames + Telemetry/Status) & SSE Fallback
    // ------------------------------------------------------------------
    function renderBinaryFrameBlob(blob) {
        if (!liveImg || !blob) return;
        const objUrl = URL.createObjectURL(blob);
        liveImg.onload = () => {
            if (state.prevFrameObjectUrl) {
                URL.revokeObjectURL(state.prevFrameObjectUrl);
            }
            state.prevFrameObjectUrl = objUrl;
        };
        liveImg.src = objUrl;
    }

    function stopSSEFallback() {
        if (state.sseSource) {
            try {
                state.sseSource.close();
            } catch (e) {}
            state.sseSource = null;
        }
    }

    function startSSEFallback() {
        if (state.sseSource || state.wsConnected || typeof EventSource === "undefined") return;
        try {
            const es = new EventSource("/api/stream/sse");
            state.sseSource = es;
            if (hudStreamMode) {
                hudStreamMode.textContent = "SSE Push + MJPEG";
            }
            if (liveImg && !liveImg.src.includes("/api/stream/mjpeg")) {
                liveImg.src = "/api/stream/mjpeg";
            }
            es.onmessage = (ev) => {
                try {
                    const msg = JSON.parse(ev.data);
                    if (msg.telemetry) updateTelemetryUI(msg.telemetry);
                    if (msg.data) updateTelemetryUI(msg.data);
                    if (msg.status) updateStatusUI(msg.status);
                } catch (e) {}
            };
            es.onerror = () => {
                stopSSEFallback();
            };
        } catch (e) {}
    }

    const SKY_CODE_NAMES = ["clear", "mostly_clear", "scattered", "broken", "overcast"];
    const CAL_CODE_NAMES = ["idle", "running", "completed", "error"];

    function unpackAndRenderAsb1Packet(buf) {
        if (!buf || buf.byteLength < 4188) return;
        const dv = new DataView(buf);
        // Verify "ASB1" magic (0x41, 0x53, 0x42, 0x31)
        if (
            dv.getUint8(0) !== 0x41 ||
            dv.getUint8(1) !== 0x53 ||
            dv.getUint8(2) !== 0x42 ||
            dv.getUint8(3) !== 0x31
        ) {
            return;
        }

        const flags = dv.getUint8(5);
        const skyCode = dv.getUint8(6);
        const calCode = dv.getUint8(7);
        const tsUnix = dv.getFloat64(8, true);
        const exposureS = dv.getFloat32(16, true);
        const gainDb = dv.getFloat32(20, true);
        const hwDeltaMs = dv.getFloat32(24, true);
        const fps = dv.getFloat32(28, true);
        const starCount = dv.getUint32(32, true);
        const cloudCoverPct = dv.getFloat32(36, true);
        const sensorTempC = dv.getFloat32(40, true);
        const uptimeS = dv.getFloat32(44, true);

        const regVmaxVal = dv.getUint32(48, true);
        const regHmaxVal = dv.getUint32(52, true);
        const regShr0Val = dv.getUint32(56, true);
        const regPgcVal = dv.getUint32(60, true);

        const histCalcMs = dv.getFloat32(64, true);
        const histMeanVal = dv.getFloat32(68, true);
        const histStdVal = dv.getFloat32(72, true);
        const histClipLowVal = dv.getFloat32(76, true);
        const histClipHighVal = dv.getFloat32(80, true);
        const histMedianVal = dv.getUint16(84, true);
        const calReadyPacked = dv.getUint16(86, true);
        const jpegLen = dv.getUint32(88, true);

        // Zero-copy Uint32Array views over the 4x256 histogram bins (offset 92..4188)
        const allBins = new Uint32Array(buf, 92, 1024);
        const rBins = allBins.subarray(0, 256);
        const gBins = allBins.subarray(256, 512);
        const bBins = allBins.subarray(512, 768);
        const yBins = allBins.subarray(768, 1024);

        const modeStr = flags & 1 ? "auto" : "manual";
        const isoTs = new Date(tsUnix * 1000).toISOString();

        updateTelemetryUI({
            mode: modeStr,
            exposure_s: exposureS,
            gain_db: gainDb,
            hw_frame_delta_ms: hwDeltaMs,
            fps: fps,
            star_count: starCount,
            cloud_cover_pct: cloudCoverPct,
            sky_status: SKY_CODE_NAMES[skyCode] || "clear",
            sensor_temp_c: sensorTempC,
            timestamp: isoTs,
            histogram: {
                r: rBins,
                g: gBins,
                b: bBins,
                y: yBins,
                calculation_time_ms: histCalcMs,
                stats: {
                    mean: histMeanVal,
                    stddev: histStdVal,
                    median: histMedianVal,
                    clipping_low_pct: histClipLowVal,
                    clipping_high_pct: histClipHighVal,
                },
            },
        });

        updateStatusUI({
            status: flags & 8 ? "online" : "error",
            uptime_s: uptimeS,
            mode: modeStr,
            hardware: {
                registers: {
                    VMAX: regVmaxVal,
                    HMAX: regHmaxVal,
                    SHR0: regShr0Val,
                    PGC: regPgcVal,
                },
            },
            calibration: {
                state: CAL_CODE_NAMES[calCode] || "completed",
                ready_profiles_count: calReadyPacked & 0xff,
                total_profiles_count: (calReadyPacked >> 8) & 0xff,
            },
        });

        // Slice raw JPEG image bytes (offset 4188 .. 4188 + jpegLen)
        if (jpegLen > 0 && buf.byteLength >= 4188 + jpegLen) {
            const jpegView = new Uint8Array(buf, 4188, jpegLen);
            const blob = new Blob([jpegView], { type: "image/jpeg" });
            renderBinaryFrameBlob(blob);
        }
    }

    function initWebSocket() {
        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${proto}//${window.location.host}/ws/live?binary=1`;
        let ws;

        try {
            ws = new WebSocket(wsUrl);
            ws.binaryType = "arraybuffer";
            state.ws = ws;

            ws.onopen = () => {
                state.wsConnected = true;
                stopSSEFallback();
                if (hudStreamMode) {
                    hudStreamMode.textContent = "Binary WebSocket (ASB1)";
                }
            };

            ws.onmessage = (event) => {
                if (event.data instanceof ArrayBuffer) {
                    unpackAndRenderAsb1Packet(event.data);
                }
            };

            ws.onclose = () => {
                state.wsConnected = false;
                state.ws = null;
                startSSEFallback();
                setTimeout(initWebSocket, 2500);
            };

            ws.onerror = () => {
                ws.close();
            };
        } catch (e) {
            state.wsConnected = false;
            startSSEFallback();
        }
    }

    if (currentPage === "tab-live") {
        initWebSocket();
        // The binary stream carries registers only; fetch camera identity, limits and register support once
        fetch("/api/status")
            .then((res) => (res.ok ? res.json() : null))
            .then((data) => data && updateStatusUI(data))
            .catch(() => {});
    } else {
        // Other pages skip the heavy live stream; keep header status fresh with light polling
        const refreshStatus = async () => {
            try {
                const res = await fetch("/api/status");
                if (res.ok) updateStatusUI(await res.json());
            } catch (e) {
                navStatus.textContent = "OFFLINE";
            }
        };
        refreshStatus();
        setInterval(refreshStatus, 5000);
    }
    pollCalibrationStatus();
    loadStationConfig();
    if (currentPage === "tab-settings") {
        loadResolutionOptions();
        loadPerformance();
        loadWhiteBalance();
        setInterval(loadWhiteBalance, 5000);
    }

    // ------------------------------------------------------------------
    // Gallery & Events Fetcher + On-Demand Timelapse Builder
    // ------------------------------------------------------------------
    const galleryDateEl = document.getElementById("gallery-date");
    const galleryFrameCountEl = document.getElementById("gallery-frame-count");
    const btnBuildTimelapse = document.getElementById("btn-build-timelapse");
    const timelapseFpsSelect = document.getElementById("timelapse-fps-select");
    const galleryAlert = document.getElementById("gallery-alert");

    if (btnBuildTimelapse) {
        btnBuildTimelapse.addEventListener("click", async () => {
            const fps = timelapseFpsSelect ? parseFloat(timelapseFpsSelect.value) || 12 : 12;
            btnBuildTimelapse.disabled = true;
            btnBuildTimelapse.textContent = "⏳ Building MP4 timelapse...";
            showAlert(galleryAlert, `⏳ Compiling today's frames into an H.264 timelapse (${fps} FPS), keogram & startrails...`, "info", true);
            try {
                const res = await fetch(`/api/gallery/generate?fps=${encodeURIComponent(fps)}`, {
                    method: "POST",
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.gallery) {
                        renderGallery(data.gallery);
                    } else {
                        await loadGalleryAndEvents();
                    }
                    showAlert(
                        galleryAlert,
                        `✅ Timelapse created (${data.archived_frames_count || 0} frames → ${data.duration_s || 0}s @ ${data.fps || fps} FPS, ${data.size_kb || 0} KB)!`,
                        "success",
                        true
                    );
                } else {
                    showAlert(galleryAlert, `❌ Timelapse build failed: ${data.detail || "Unknown error"}`, "error", true);
                }
            } catch (err) {
                showAlert(galleryAlert, `❌ Connection error while building timelapse: ${err.message}`, "error", true);
            } finally {
                btnBuildTimelapse.disabled = false;
                btnBuildTimelapse.textContent = "🎬 Build / Update Timelapse Now";
            }
        });
    }

    if (currentPage === "tab-gallery") setTimeout(() => loadGalleryAndEvents(), 0);

    async function loadGalleryAndEvents() {
        try {
            const resGal = await fetch("/api/gallery");
            if (resGal.ok) {
                const data = await resGal.json();
                renderGallery(data);
            }

            const resEvents = await fetch("/api/events/meteors?limit=25");
            if (resEvents.ok) {
                const evData = await resEvents.json();
                renderEvents(evData);
            }
        } catch (err) {
            console.error("Failed to load gallery/events:", err);
        }
    }

    function renderGallery(data) {
        productsGrid.innerHTML = "";
        if (galleryDateEl && data && data.date) {
            galleryDateEl.textContent = `Archive: ${data.date} (UTC)`;
        }
        if (galleryFrameCountEl && data && data.archived_frames_count !== undefined) {
            galleryFrameCountEl.textContent = `${data.archived_frames_count} Frames Archived Today`;
        }

        if (!data || !data.items || data.items.length === 0) {
            productsGrid.innerHTML = "<p class='text-muted'>No products recorded yet.</p>";
            return;
        }

        const cacheBust = Date.now();
        data.items.forEach((item) => {
            const card = document.createElement("div");
            card.className = "product-card";
            const mediaUrl = `${item.url}?t=${cacheBust}`;
            const sizeBadge = item.size_kb ? `${item.size_kb.toFixed(1)} KB` : "Pending";
            const framesBadge = item.frame_count !== undefined ? `${item.frame_count} frames` : "";
            const tsFormatted = item.timestamp ? item.timestamp.replace("T", " ").slice(0, 19) + " UTC" : "";

            let mediaHtml = "";
            if (item.type === "timelapse") {
                mediaHtml = `
                    <video controls autoplay loop muted playsinline preload="metadata" src="${mediaUrl}">
                        Your browser does not support MP4 video.
                    </video>
                `;
            } else {
                mediaHtml = `
                    <a href="${mediaUrl}" target="_blank" title="Click to view full size" style="width:100%;height:100%;display:block;">
                        <img src="${mediaUrl}" alt="${item.title || item.type}" loading="lazy" />
                    </a>
                `;
            }

            card.innerHTML = `
                <div class="product-media">
                    ${mediaHtml}
                </div>
                <div class="product-info">
                    <div class="product-title">
                        <span>${item.title || item.type}</span>
                        <span class="badge badge-accent">${sizeBadge}</span>
                    </div>
                    <div class="product-meta">
                        <span>${framesBadge}${framesBadge && tsFormatted ? " • " : ""}${tsFormatted}</span>
                        <a href="${mediaUrl}" download class="btn btn-sm btn-outline" style="padding: 0.15rem 0.5rem; font-size: 0.72rem; text-decoration: none;">⬇️ Download</a>
                    </div>
                </div>
            `;
            productsGrid.appendChild(card);
        });
    }

    function renderEvents(evData) {
        eventsTableBody.innerHTML = "";
        const events = evData.events || [];
        eventCountBadge.textContent = `${evData.total_count || events.length} Events`;

        if (events.length === 0) {
            eventsTableBody.innerHTML = `<tr><td colspan="7" class="text-muted" style="text-align:center;">No meteor streaks detected yet.</td></tr>`;
            return;
        }

        events.forEach((ev) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${ev.event_id}</strong></td>
                <td>${ev.timestamp}</td>
                <td>${ev.length_px ? ev.length_px.toFixed(1) : "--"}</td>
                <td>${ev.aspect_ratio ? ev.aspect_ratio.toFixed(2) : "--"}</td>
                <td>${ev.peak_dn || "--"}</td>
                <td>${ev.bbox ? `[${ev.bbox.join(", ")}]` : "--"}</td>
                <td><a href="/api/live/snapshot.jpg" target="_blank" class="btn btn-sm btn-outline">View</a></td>
            `;
            eventsTableBody.appendChild(tr);
        });
    }
});
