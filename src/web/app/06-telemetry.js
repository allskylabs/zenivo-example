/* AllSky dashboard (part of the former app.js): Histogram, telemetry polling, WebSocket/SSE frame stream.
 * Classic script: top-level declarations are shared with the other src/web/app/*.js files; load order is fixed in index.html. */

// ------------------------------------------------------------------
// Real-Time 256-Bin Histogram Rendering
// ------------------------------------------------------------------
function drawHistogram(histData) {
    if (!histData || !histData.r) return;

    const w = histCanvas.width;
    const h = histCanvas.height;
    histCtx.clearRect(0, 0, w, h);

    // Gain/WB stretch on 8-bit data leaves empty bins between populated ones
    // (comb pattern); a small moving average shows the real distribution.
    const smooth = (a, k = 3) => a.map((_, i) => {
        let s = 0, n = 0;
        for (let j = Math.max(0, i - k); j <= Math.min(255, i + k); j++) { s += a[j]; n++; }
        return s / n;
    });
    const r = smooth(histData.r);
    const g = smooth(histData.g);
    const b = smooth(histData.b);
    const y = smooth(histData.y);

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
    if (m === "manual") state.aeLock = "both";
    else if (state.aeLock === "both") state.aeLock = null;
    const label = { both: "MANUAL", exposure: "AUTO GAIN", gain: "AUTO EXPOSURE" }[state.aeLock] || "AUTO";
    navMode.textContent = label;
    activeProfileBadge.textContent = state.aeLock ? `${label} • ${state.aeLock === "both" ? "BOTH" : state.aeLock.toUpperCase()} LOCKED` : "AUTO EPHEMERIS";
    btnLockExposure.classList.toggle("locked", isLocked("exposure"));
    btnLockGain.classList.toggle("locked", isLocked("gain"));
    btnLockExposure.textContent = isLocked("exposure") ? "Locked" : "Auto";
    btnLockGain.textContent = isLocked("gain") ? "Locked" : "Auto";
    btnLockExposure.setAttribute("aria-pressed", isLocked("exposure") ? "true" : "false");
    btnLockGain.setAttribute("aria-pressed", isLocked("gain") ? "true" : "false");
}

function syncAeLock(info) {
    state.aeLock = (info && info.lock) || null;
    // Show the held value on a locked slider (telemetry no longer moves it)
    if (isLocked("exposure") && info.exposure_s != null && document.activeElement !== rangeExposure) {
        rangeExposure.value = info.exposure_s;
        rangeExposure.dispatchEvent(new Event("input"));
    }
    if (isLocked("gain") && info.gain_db != null && document.activeElement !== rangeGain) {
        rangeGain.value = info.gain_db;
        rangeGain.dispatchEvent(new Event("input"));
    }
    state.targetLocked = info ? info.target_brightness != null : false;
    if (document.activeElement !== rangeTarget) {
        const t = state.targetLocked ? info.target_brightness : info && info.brightness;
        if (t != null) rangeTarget.value = Math.round(t);
    }
    paintTarget();
    syncModeButtons(state.aeLock === "both" ? "manual" : "auto");
}

function updateTelemetryUI(data) {
    if (!data) return;

    if (data.ae_lock !== undefined) syncAeLock(data.ae_lock);
    if (data.mode) {
        syncModeButtons(data.mode);
    }

    if (data.exposure_s !== undefined) {
        const exp = data.exposure_s;
        telemExp.textContent = exp < 0.01 ? (exp * 1000).toFixed(2) + " ms" : exp < 0.1 ? (exp * 1000).toFixed(1) + " ms" : exp.toFixed(3) + " s";
        if (!isLocked("exposure") && document.activeElement !== rangeExposure) {
            rangeExposure.value = exp;
            valExposure.textContent = exp < 0.01 ? (exp * 1000).toFixed(2) + " ms" : exp < 0.1 ? (exp * 1000).toFixed(1) + " ms" : exp.toFixed(3) + " s";
        }
    }
    if (data.gain_db !== undefined) {
        telemGain.textContent = data.gain_db.toFixed(1) + " dB";
        if (!isLocked("gain") && document.activeElement !== rangeGain) {
            rangeGain.value = data.gain_db;
            valGain.textContent = data.gain_db.toFixed(1) + " dB";
        }
    }
    if (data.exposure_s !== undefined && data.gain_db !== undefined) {
        const vp = document.getElementById("vp-exp-gain");
        if (vp) {
            const e = data.exposure_s;
            vp.textContent = `${e < 0.1 ? (e * 1000).toFixed(e < 0.01 ? 2 : 1) : (e * 1000).toFixed(0)} ms · ${data.gain_db.toFixed(1)} dB`;
        }
    }
    if (data.star_count !== undefined) {
        telemStars.textContent = data.star_count;
    }
    if (data.cloud_cover_pct !== undefined) {
        telemClouds.textContent = data.cloud_cover_pct.toFixed(1) + "%";
        const cn = document.getElementById("cloud-now");
        if (cn) cn.textContent = data.cloud_cover_pct.toFixed(0) + "%";
    }
    if (data.sky_status) {
        telemSkyStatus.textContent = rainNow ? "RAINING" : data.sky_status.toUpperCase();
        telemSkyStatus.title = rainNow ? "Drops on the dome: daytime object tracking is paused" : "";
    }
    if (data.fps !== undefined) {
        // Rate at which new pictures really arrive (not the sensor's streaming rate)
        const fpsTxt = !(data.fps > 0) ? "--" : data.fps >= 1 ? data.fps.toFixed(1) + " fps" : (1 / data.fps).toFixed(1) + " s/frame";
        telemFps.textContent = fpsTxt;
        const nf = document.getElementById("nav-fps");
        if (nf) nf.textContent = data.fps > 0 ? ` · ${fpsTxt}` : "";
    }
    if (data.hw_frame_delta_ms !== undefined) {
        hudHwDelta.textContent = data.hw_frame_delta_ms > 0 ? "sensor " + data.hw_frame_delta_ms.toFixed(1) + " ms" : "";
    }
    if (data.sensor_temp_c !== undefined) {
        telemTemp.textContent = Number.isFinite(data.sensor_temp_c) ? data.sensor_temp_c.toFixed(1) + " °C" : "n/a";
    }
    if (data.timestamp) {
        telemTimestamp.textContent = fmtLocalIso(data.timestamp, true);
    }
    if (data.capture_time) {
        setFrameInfo(liveFrameInfo, data.capture_time, data.exposure_s, data.gain_db, data.brightness);
        // The instant the picture shows: the middle of its exposure (capture_time is when the exposure ended)
        const ft = Date.parse(data.capture_time) / 1000 - (typeof data.exposure_s === "number" ? data.exposure_s / 2 : 0);
        if (isFinite(ft) && ft !== liveFrameT) { liveFrameT = ft; scheduleAircraftDraw(); }
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

    { const s = String(data.status || "online"); navStatus.textContent = s.charAt(0).toUpperCase() + s.slice(1).toLowerCase(); }
    if (data.uptime_s !== undefined) {
        const h = Math.floor(data.uptime_s / 3600);
        const m = Math.floor((data.uptime_s % 3600) / 60);
        const s = Math.floor(data.uptime_s % 60);
        telemUptime.textContent = `${h}h ${m}m ${s}s`;
    }

    if (data.hardware) {
        const hw = data.hardware;
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
                rangeExposure.max = String(Math.min(60, hw.limits.max_exposure_s));
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

    if (data.ae_lock !== undefined) syncAeLock(data.ae_lock);
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
    // Revoke the previous frame's URL before swapping; no per-frame onload handler is kept
    if (state.prevFrameObjectUrl) {
        URL.revokeObjectURL(state.prevFrameObjectUrl);
    }
    state.prevFrameObjectUrl = objUrl;
    liveImg.onload = null;
    // An aborted/failed load must not leave the URL alive
    liveImg.onerror = () => {
        if (state.prevFrameObjectUrl === objUrl) {
            URL.revokeObjectURL(objUrl);
            state.prevFrameObjectUrl = null;
        }
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
                if (msg.type === "obs_overlay" && msg.settings) {
                    if (chkSkyOverlay && typeof msg.settings.sky_overlay === "boolean") chkSkyOverlay.checked = msg.settings.sky_overlay;
                    if (chkSkyLabels && typeof msg.settings.sky_labels === "boolean") chkSkyLabels.checked = msg.settings.sky_labels;
                    if (chkGridOverlay && typeof msg.settings.grid_overlay === "boolean") chkGridOverlay.checked = msg.settings.grid_overlay;
                    drawReticle();
                }
            } catch (e) {}
        };
        es.onerror = () => {
            stopSSEFallback();
        };
    } catch (e) {}
}

const SKY_CODE_NAMES = ["clear", "mostly_clear", "scattered", "broken", "overcast", "unknown", "overexposed", "disabled"];
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
        capture_time: jpegLen > 0 ? isoTs : undefined,
        brightness: histMeanVal,
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
    // Hidden tab: stream is paused; an already-open socket (or pending reconnect) is not duplicated
    if (state.streamPaused || state.ws) return;
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
            } else if (typeof event.data === "string") {
                // Lock state / brightness, pushed by the server only when it changes
                try {
                    const msg = JSON.parse(event.data);
                    if (msg.type === "ae_lock" && !state.dragging) {
                        syncModeButtons(msg.mode);
                        syncAeLock(msg.ae_lock);
                    } else if (msg.type === "obs_overlay" && msg.settings) {
                        if (chkSkyOverlay && typeof msg.settings.sky_overlay === "boolean") chkSkyOverlay.checked = msg.settings.sky_overlay;
                        if (chkSkyLabels && typeof msg.settings.sky_labels === "boolean") chkSkyLabels.checked = msg.settings.sky_labels;
                        if (chkGridOverlay && typeof msg.settings.grid_overlay === "boolean") chkGridOverlay.checked = msg.settings.grid_overlay;
                        drawReticle();
                    }
                } catch (e) {}
            }
        };

        ws.onclose = () => {
            state.wsConnected = false;
            state.ws = null;
            if (state.streamPaused) return; // closed on purpose while the tab is hidden
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
    // Hidden tab: drop the live stream (WS / SSE / MJPEG) and reopen it when the tab is visible again
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            state.streamPaused = true;
            stopSSEFallback();
            if (state.ws) {
                try {
                    state.ws.close();
                } catch (e) {}
            }
            if (liveImg && liveImg.src.includes("/api/stream/mjpeg")) liveImg.removeAttribute("src");
        } else if (state.streamPaused) {
            state.streamPaused = false;
            initWebSocket();
        }
    });
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
            navStatus.textContent = "Offline";
        }
    };
    refreshStatus();
    setInterval(() => {
        if (document.hidden) return;
        refreshStatus();
    }, 5000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshStatus(); });
}
// Calibration state is shown on the calibration page only (the status pill on other pages comes from /api/status)
if (currentPage === "tab-calibration") pollCalibrationStatus();
// Station config feeds the sky overlay / astro tiles (live) and the station form (settings); other pages get the station name from /api/status
if (currentPage === "tab-live" || currentPage === "tab-settings") {
    loadStationConfig();
    // A plate-solve run from another tab changes the lens/pointing the sky overlay uses: pick it up
    setInterval(() => { if (!document.hidden) loadStationConfig(); }, 60000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) loadStationConfig(); });
}
if (currentPage === "tab-settings") {
    loadResolutionOptions();
    loadStarBoost();
    loadPerformance();
    loadWhiteBalance();
    setInterval(() => {
        if (document.hidden) return;
        loadWhiteBalance();
    }, 5000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) loadWhiteBalance(); });
}
