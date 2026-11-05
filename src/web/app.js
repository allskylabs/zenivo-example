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
    const chkAircraft = document.getElementById("chk-aircraft-overlay");
    const chkSatellite = document.getElementById("chk-satellite-overlay");
    const aircraftCanvas = document.getElementById("aircraft-canvas");
    const chkSonde = document.getElementById("chk-sonde-overlay");
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
    const btnStationSave = document.getElementById("btn-station-save");
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
    // The server does not publish its zone; day buckets are the station's local day (README rule: +07),
    // so every wall-clock display uses this ONE offset instead of the viewer's browser timezone.
    const STATION_UTC_OFFSET_MIN = 7 * 60;
    // Date whose getUTC* fields read as station wall-clock time
    function stationDate(d) {
        return new Date(d.getTime() + STATION_UTC_OFFSET_MIN * 60000);
    }
    function fmtLocalIso(iso, timeOnly) {
        const d = new Date(iso);
        if (isNaN(d)) return iso || "";
        const sd = stationDate(d);
        const p = (n) => String(n).padStart(2, "0");
        const hms = `${p(sd.getUTCHours())}:${p(sd.getUTCMinutes())}:${p(sd.getUTCSeconds())}`;
        return timeOnly ? hms : `${sd.getUTCFullYear()}-${p(sd.getUTCMonth() + 1)}-${p(sd.getUTCDate())} ${hms}`;
    }
    const tzLabel = (() => {
        const m = STATION_UTC_OFFSET_MIN;
        return `UTC${m >= 0 ? "+" : "-"}${Math.floor(Math.abs(m) / 60)}${Math.abs(m) % 60 ? ":" + String(Math.abs(m) % 60).padStart(2, "0") : ""}`;
    })();

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
            fetch("/api/obs/overlay", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ [apiKey]: chk.checked }),
            }).catch(() => {});
        });
    });

    if (chkSatellite) {
        chkSatellite.checked = !!prefs.satelliteOverlay;
        chkSatellite.addEventListener("change", () => {
            savePrefs({ satelliteOverlay: chkSatellite.checked });
            if (chkSatellite.checked) refreshSatellites(); else drawAircraft();
        });
    }

    if (chkSonde) {
        chkSonde.checked = !!prefs.sondeOverlay;
        chkSonde.addEventListener("change", () => {
            savePrefs({ sondeOverlay: chkSonde.checked });
            if (chkSonde.checked) refreshSondes(); else drawAircraft();
        });
    }

    if (chkAircraft) {
        chkAircraft.checked = !!prefs.aircraftOverlay;
        chkAircraft.addEventListener("change", () => {
            savePrefs({ aircraftOverlay: chkAircraft.checked });
            if (chkAircraft.checked) refreshAircraft(); else drawAircraft();
        });
    }

    // Initialize with synchronized OBS overlay state if available
    fetch("/api/obs/overlay")
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
    const PAGE_TITLES = {
        "tab-live": "Live Observatory",
        "tab-gallery": "Gallery & Events",
        "tab-settings": "Settings",
        "tab-calibration": "Master Dark",
        "tab-logs": "Activity Log",
        "tab-focus": "Focus",
    };
    const currentPath = window.location.pathname.replace(/\/+$/, "") || "/";
    const currentPage = PAGES[currentPath] || "tab-live";
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
            rangeExposure.dispatchEvent(new Event("change"));
        });
    });

    document.querySelectorAll(".btn-preset-gain").forEach((btn) => {
        btn.addEventListener("click", () => {
            const gain = parseFloat(btn.getAttribute("data-gain"));
            rangeGain.value = gain;
            valGain.textContent = gain.toFixed(1) + " dB";
            rangeGain.dispatchEvent(new Event("change"));
        });
    });

    // Exposure scenario chips: Moonless / Twilight / Full moon set shutter + gain and lock both; Custom leaves the sliders alone
    const presetChips = document.querySelectorAll(".chip-preset");
    const markPreset = (name) => presetChips.forEach((c) => c.classList.toggle("active", c.dataset.preset === name));
    presetChips.forEach((chip) => {
        chip.addEventListener("click", () => {
            markPreset(chip.dataset.preset);
            if (chip.dataset.preset === "custom") return;
            const clamp = (v, el) => Math.min(parseFloat(el.max), Math.max(parseFloat(el.min), v));
            rangeExposure.value = clamp(parseFloat(chip.dataset.exp), rangeExposure);
            rangeGain.value = clamp(parseFloat(chip.dataset.gain), rangeGain);
            rangeExposure.dispatchEvent(new Event("input"));
            rangeGain.dispatchEvent(new Event("input"));
            setAeLock("both");
            markPreset(chip.dataset.preset);
        });
    });
    [rangeExposure, rangeGain].forEach((el) => el.addEventListener("pointerdown", () => markPreset("custom")));
    // Collapsed exposure card on phones: the summary line (shutter and gain) stays visible
    try {
        const expCard = document.getElementById("exposure-card");
        if (expCard && window.matchMedia("(max-width: 768px)").matches) expCard.removeAttribute("open");
    } catch (e) { /* matchMedia unavailable */ }

    // Exposure / gain locks replace the Auto/Manual switch: nothing locked = Auto computes both,
    // one locked = Auto adjusts only the other, both locked = fixed (manual) exposure and gain
    const isLocked = (knob) => state.aeLock === knob || state.aeLock === "both";
    const lockName = (exp, gain) => (exp && gain ? "both" : exp ? "exposure" : gain ? "gain" : "none");
    async function setAeLock(lock) {
        const exposure_s = parseFloat(rangeExposure.value);
        const gain_db = parseFloat(rangeGain.value);
        try {
            const res = await fetch("/api/camera/ae-lock", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ lock, value: lock === "gain" ? gain_db : exposure_s, exposure_s, gain_db }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
            state.mode = data.mode;
            syncAeLock(data.ae_lock);
        } catch (err) {
            showAlert(controlAlert, `Could not change the lock: ${err.message}`, "error");
        }
    }
    btnLockExposure.addEventListener("click", () => setAeLock(lockName(!isLocked("exposure"), isLocked("gain"))));
    btnLockGain.addEventListener("click", () => setAeLock(lockName(isLocked("exposure"), !isLocked("gain"))));
    // Grabbing a slider locks it at once (telemetry stops moving it); releasing it, or picking a preset,
    // sends the lock with the chosen value
    const paintLocks = () => syncModeButtons(state.aeLock === "both" ? "manual" : "auto");
    // While a slider is held, the lock poll must not undo its local lock before the value is sent
    [rangeExposure, rangeGain, rangeTarget].forEach((el) => {
        el.addEventListener("pointerdown", () => (state.dragging = true));
        ["change", "pointerup", "pointercancel"].forEach((ev) => el.addEventListener(ev, () => (state.dragging = false)));
    });
    rangeExposure.addEventListener("input", () => {
        if (!isLocked("exposure")) { state.aeLock = lockName(true, isLocked("gain")); paintLocks(); }
    });
    rangeGain.addEventListener("input", () => {
        if (!isLocked("gain")) { state.aeLock = lockName(isLocked("exposure"), true); paintLocks(); }
    });
    rangeExposure.addEventListener("change", () => setAeLock(lockName(true, isLocked("gain"))));
    rangeGain.addEventListener("change", () => setAeLock(lockName(isLocked("exposure"), true)));

    // Target brightness: unlocked it follows the measured brightness; locked, Auto aims for it
    const paintTarget = () => {
        valTarget.textContent = rangeTarget.value;
        btnLockTarget.classList.toggle("locked", !!state.targetLocked);
        btnLockTarget.textContent = state.targetLocked ? "Locked" : "Auto";
        btnLockTarget.setAttribute("aria-pressed", state.targetLocked ? "true" : "false");
    };
    async function setTargetBrightness(value) {
        try {
            const res = await fetch("/api/camera/target-brightness", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ value }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
            syncAeLock(data.ae_lock);
        } catch (err) {
            showAlert(controlAlert, `Could not set the target brightness: ${err.message}`, "error");
        }
    }
    rangeTarget.addEventListener("input", () => {
        state.targetLocked = true;
        paintTarget();
    });
    rangeTarget.addEventListener("change", () => setTargetBrightness(parseFloat(rangeTarget.value)));
    btnLockTarget.addEventListener("click", () =>
        setTargetBrightness(state.targetLocked ? null : parseFloat(rangeTarget.value))
    );

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
                        ? `${p.raw_filename || p.filename} • ${fmtLocalIso(p.modified_at)}`
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
                            <td>${f.modified_at ? fmtLocalIso(f.modified_at) : "--"}</td>
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

        let calPollBusy = false; // don't stack requests when the server answers slower than 500 ms
        const calPollTimer = setInterval(async () => {
            if (calPollBusy) return;
            calPollBusy = true;
            try {
                await pollCalibrationStatus();
            } finally {
                calPollBusy = false;
            }
        }, 500);
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
                        if (!st) {
                            showAlert(darkAlert, "⚠️ Lost contact with the station while waiting for calibration. It may still be running; reload to check.", "error", true);
                        } else if (st.state === "completed") {
                            showAlert(darkAlert, `✅ ${st.message}`, "success", true);
                        } else if (st.state === "error") {
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

    // Save is only needed for hand edits: plate-solve and locate save on the server themselves.
    // While the form has unsaved edits, the periodic status refresh must not overwrite them.
    let stationFormDirty = false;
    function setStationFormDirty(dirty) {
        stationFormDirty = dirty;
        if (btnStationSave) btnStationSave.disabled = !dirty;
    }
    const btnStationReset = document.getElementById("btn-station-reset");
    if (btnStationReset) {
        btnStationReset.addEventListener("click", async () => {
            setStationFormDirty(false);
            await loadStationConfig();
        });
    }
    if (btnStationSave) {
        btnStationSave.closest(".card").querySelectorAll(".settings-grid input").forEach((el) =>
            el.addEventListener("input", () => setStationFormDirty(true))
        );
    }

    function updateStationUI(st) {
        if (!st) return;
        state.station = st;

        const latVal = st.latitude_deg !== undefined ? st.latitude_deg : st.latitude;
        const lonVal = st.longitude_deg !== undefined ? st.longitude_deg : st.longitude;
        const nameVal = st.location_name || st.station_name || "Hanoi, Vietnam";
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

        const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
        let tz = "";
        try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) {}
        setVal("st-f-tz", tz || "--");
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
        try {
            const res = await fetch("/api/camera/star-boost", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enabled: starBoostEnabled.checked, bin: Number(starBoostBin.value) }),
            });
            const data = await res.json();
            if (!res.ok || !data.success) throw new Error(data.detail || "Unknown error");
            starBoostEnabled.checked = !!data.enabled;
            starBoostBin.value = String(data.bin);
        } catch (err) {
            showAlert(stationAlert, `❌ Failed to set star boost: ${err.message}`, "error", true);
            loadStarBoost();
        } finally {
            starBoostEnabled.disabled = starBoostBin.disabled = false;
        }
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
                                setStationFormDirty(false);
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
                    setStationFormDirty(false);
                    updateStationUI(st);
                    showAlert(stationAlert, `✅ ${data.message || "Configuration and manual adjustments saved!"}`, "success", true);
                } else {
                    showAlert(stationAlert, `❌ Failed to save configuration: ${data.detail || "Unknown error"}`, "error", true);
                }
            } catch (err) {
                showAlert(stationAlert, `❌ Network error: ${err.message}`, "error", true);
            } finally {
                btnStationSave.disabled = !stationFormDirty;
            }
        });
    }

    // Button 3: Capture & Plate-Solve via https://hoan.uk/astronomy/plate-solving (Only when user clicks!)
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
                const res = await fetch("/api/station/sky-mask");
                if (res.ok) {
                    const data = await res.json();
                    mask.sky = data.sky || [];
                    mask.obstruction = data.obstruction || [];
                    current = { kind: "sky", pts: [] };
                    drawMask();
                }
            } catch (e) {}
        };
        maskEditor.addEventListener("toggle", () => { if (maskEditor.open) loadMask(); });
        // <details open> fires its toggle event before this script runs, so load once now as well
        if (maskEditor.open) loadMask();
        maskImg.addEventListener("load", () => { maskRetries = 0; drawMask(); });
        window.addEventListener("resize", rafDebounced(drawMask));
        maskCanvas.addEventListener("click", (ev) => {
            const r = maskCanvas.getBoundingClientRect();
            const x = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
            const y = Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height));
            current.pts.push([Math.round(x * 10000) / 10000, Math.round(y * 10000) / 10000]);
            drawMask();
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
                showAlert(maskAlert, `✅ Sky mask saved: ${data.sky.length} sky, ${data.obstruction.length} obstruction polygon(s)`, "success");
            } catch (err) {
                showAlert(maskAlert, `❌ Could not save the sky mask: ${err.message}`, "error", true);
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
                const res = await fetch("/api/station/sky-mask");
                if (res.ok) {
                    const d = await res.json();
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
        loadAlignFrame();
        loadAlignMask();
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
        const lat = stationLatInput && !isNaN(parseFloat(stationLatInput.value)) ? parseFloat(stationLatInput.value) : 21.0285;
        const lon = stationLonInput && !isNaN(parseFloat(stationLonInput.value)) ? parseFloat(stationLonInput.value) : 105.8542;
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
    loadPlanets();
    setInterval(loadPlanets, 600000);
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
        if (typeof drawAircraft === "function") drawAircraft();

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
        // The canvas sits in the CSS-zoomed stage: 1 screen px = u canvas px (keeps strokes and text screen-sized)
        const u = 1 / Math.max(1, view.zoom);
        const occupiedBoxes = [];

        // Collision test + register for a label box given by its top-left corner (canvas px)
        function placeBox(x, y, bw, bh, pad = 2 * u) {
            const box = { x1: x - pad, y1: y - pad, x2: x + bw + pad, y2: y + bh + pad };
            if (box.x1 < offsetX + 4 * u || box.x2 > offsetX + renderW - 4 * u || box.y1 < offsetY + 4 * u || box.y2 > offsetY + renderH - 4 * u) {
                return false;
            }
            for (let i = 0; i < occupiedBoxes.length; i++) {
                const b = occupiedBoxes[i];
                if (!(box.x2 < b.x1 || box.x1 > b.x2 || box.y2 < b.y1 || box.y1 > b.y2)) return false;
            }
            occupiedBoxes.push(box);
            return true;
        }
        // Reserve the top-left summary badge so labels avoid it
        occupiedBoxes.push({ x1: offsetX, y1: offsetY, x2: offsetX + 560 * u, y2: offsetY + 36 * u });

        const roundedRect = (x, y, rw, rh, r) => {
            ctx.beginPath();
            ctx.moveTo(x + r, y);
            ctx.arcTo(x + rw, y, x + rw, y + rh, r);
            ctx.arcTo(x + rw, y + rh, x, y + rh, r);
            ctx.arcTo(x, y + rh, x, y, r);
            ctx.arcTo(x, y, x + rw, y, r);
            ctx.closePath();
        };

        // Bright-star lookup so stick-figure vertices know their magnitude (line weight, reticle rim gap)
        const starVertices = new Map();
        const vertexKey = (ra, dec) => `${Math.round(ra * 10)},${Math.round(dec * 10)}`;
        (catalog.stars || []).forEach((s) => {
            const k = vertexKey(s.ra, s.dec);
            if (!starVertices.has(k)) starVertices.set(k, s);
        });
        const findVertexStar = (ra, dec) => {
            const r0 = Math.round(ra * 10), d0 = Math.round(dec * 10);
            for (let dr = -2; dr <= 2; dr++) {
                for (let dd = -2; dd <= 2; dd++) {
                    const s = starVertices.get(`${r0 + dr},${d0 + dd}`);
                    if (s && Math.abs(s.ra - ra) < 0.25 && Math.abs(s.dec - dec) < 0.25) return s;
                }
            }
            return null;
        };
        const starWeight = (mag) => Math.max(0.24, Math.min(1.0, (5.2 - mag) / 4.5));
        const reticleRadius = (mag) => (Math.max(1.4, Math.min(4.8, 3.8 - mag * 0.65)) + 4) * u;

        // Bi-tapered, magnitude-weighted constellation filament (after the hoan.uk plate-solving viewer):
        // anchored at each star's reticle rim, a soft bloom, a gradient body and a fine core, screen-blended
        const lw = 1.6;
        function drawFilament(v1, v2) {
            const dx = v2.x - v1.x;
            const dy = v2.y - v1.y;
            const segLen = Math.hypot(dx, dy);
            if (segLen / u < 7) return;
            const ux = dx / segLen, uy = dy / segLen;
            const nx = -uy, ny = ux;
            const b1 = starWeight(v1.mag), b2 = starWeight(v2.mag);
            const bAvg = Math.sqrt(b1 * b2);
            const g1 = Math.min(segLen * 0.26, v1.rRing + 0.15 * u);
            const g2 = Math.min(segLen * 0.26, v2.rRing + 0.15 * u);
            const x1 = v1.x + ux * g1, y1 = v1.y + uy * g1;
            const x2 = v2.x - ux * g2, y2 = v2.y - uy * g2;
            const effLen = Math.hypot(x2 - x1, y2 - y1);
            const effScreen = effLen / u;
            if (effLen <= 1e-2 || effScreen < 2.5) return;

            // Short spans stay fine, long spans stay strong but slightly softer
            const distW = effScreen < 36 ? 0.68 + (effScreen / 36) * 0.32 : effScreen > 180 ? Math.max(0.88, 1.0 - (effScreen - 180) / 1100) : 1.0;
            const distA = effScreen > 160 ? Math.max(0.84, 1.0 - (effScreen - 160) / 900) : 1.0;
            const taperLen = Math.min(effLen * 0.28, Math.max(4.5, Math.min(17.0, Math.sqrt(effScreen) * 1.12)) * u);
            const tStop = Math.max(0.04, Math.min(0.24, taperLen / effLen));
            const tx1 = x1 + ux * taperLen, ty1 = y1 + uy * taperLen;
            const tx2 = x2 - ux * taperLen, ty2 = y2 - uy * taperLen;
            const midX = (x1 + x2) * 0.5, midY = (y1 + y2) * 0.5;

            const ribbon = (tipW1, bodyW1, midW, bodyW2, tipW2) => {
                ctx.beginPath();
                ctx.moveTo(x1 + nx * tipW1, y1 + ny * tipW1);
                ctx.quadraticCurveTo(tx1 + nx * bodyW1, ty1 + ny * bodyW1, midX + nx * midW, midY + ny * midW);
                ctx.quadraticCurveTo(tx2 + nx * bodyW2, ty2 + ny * bodyW2, x2 + nx * tipW2, y2 + ny * tipW2);
                ctx.lineTo(x2 - nx * tipW2, y2 - ny * tipW2);
                ctx.quadraticCurveTo(tx2 - nx * bodyW2, ty2 - ny * bodyW2, midX - nx * midW, midY - ny * midW);
                ctx.quadraticCurveTo(tx1 - nx * bodyW1, ty1 - ny * bodyW1, x1 - nx * tipW1, y1 - ny * tipW1);
                ctx.closePath();
            };

            const wBase = distW * lw * u;
            const tipW1 = (0.14 + 0.12 * b1) * wBase, tipW2 = (0.14 + 0.12 * b2) * wBase;
            const bodyW1 = (0.30 + 0.34 * b1) * wBase, bodyW2 = (0.30 + 0.34 * b2) * wBase;
            const midW = (0.34 + 0.36 * bAvg) * wBase * (0.88 + 0.12 * distA);
            const aTip1 = Math.min(0.68, (0.20 + 0.24 * b1) * distA);
            const aShld1 = Math.min(0.76, (0.38 + 0.28 * b1) * distA);
            const aMid = Math.min(0.80, (0.42 + 0.28 * bAvg) * distA);
            const aShld2 = Math.min(0.76, (0.38 + 0.28 * b2) * distA);
            const aTip2 = Math.min(0.68, (0.20 + 0.24 * b2) * distA);

            ctx.save();
            ctx.globalCompositeOperation = "screen";
            // 1) soft bloom
            const glow = ctx.createLinearGradient(x1, y1, x2, y2);
            glow.addColorStop(0, `rgba(56, 189, 248, ${(aTip1 * 0.16).toFixed(3)})`);
            glow.addColorStop(tStop, `rgba(56, 189, 248, ${(aShld1 * 0.22).toFixed(3)})`);
            glow.addColorStop(0.5, `rgba(125, 211, 252, ${(aMid * 0.25).toFixed(3)})`);
            glow.addColorStop(1 - tStop, `rgba(56, 189, 248, ${(aShld2 * 0.22).toFixed(3)})`);
            glow.addColorStop(1, `rgba(56, 189, 248, ${(aTip2 * 0.16).toFixed(3)})`);
            ribbon(tipW1 * 2.15, bodyW1 * 2.25, midW * 2.35, bodyW2 * 2.25, tipW2 * 2.15);
            ctx.fillStyle = glow;
            ctx.fill();
            // 2) tapered body
            const body = ctx.createLinearGradient(x1, y1, x2, y2);
            body.addColorStop(0, `rgba(56, 189, 248, ${aTip1.toFixed(3)})`);
            body.addColorStop(tStop, `rgba(125, 211, 252, ${aShld1.toFixed(3)})`);
            body.addColorStop(0.5, `rgba(186, 230, 253, ${aMid.toFixed(3)})`);
            body.addColorStop(1 - tStop, `rgba(125, 211, 252, ${aShld2.toFixed(3)})`);
            body.addColorStop(1, `rgba(56, 189, 248, ${aTip2.toFixed(3)})`);
            ribbon(tipW1, bodyW1, midW, bodyW2, tipW2);
            ctx.fillStyle = body;
            ctx.fill();
            // 3) fine continuous core reaching both rims
            ctx.strokeStyle = body;
            ctx.lineWidth = (0.34 + 0.28 * bAvg) * wBase;
            ctx.lineCap = "round";
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.stroke();
            // 4) contact glint on prominent rims
            if (effScreen >= 24) {
                [[b1, x1, y1], [b2, x2, y2]].forEach(([b, gx, gy]) => {
                    if (b < 0.42) return;
                    ctx.fillStyle = `rgba(186, 230, 253, ${(0.28 + 0.26 * b).toFixed(3)})`;
                    ctx.beginPath();
                    ctx.arc(gx, gy, (0.36 + 0.24 * b) * wBase, 0, Math.PI * 2);
                    ctx.fill();
                });
            }
            ctx.restore();
        }

        let visibleConstellationsCount = 0;
        let visibleStarsCount = 0;

        // 2. IAU constellation stick figures (one filament per unique edge) & visible constellation centroids
        const visibleConstellationLabels = [];
        if ((showSky || showLabels) && Array.isArray(catalog.constellations)) {
            const maxSegLen = isRectilinear ? renderW * (isFisheyePointing ? 0.35 : 0.6) : r90Canvas * 0.65;
            const drawnEdges = new Set();
            const resolveVertex = (ra, dec) => {
                const p = projectRaDecToCanvas(ra, dec);
                const star = findVertexStar(ra, dec);
                const mag = star ? Number(star.mag) : 3.8;
                p.mag = mag;
                p.rRing = star && mag <= 3.0 ? reticleRadius(mag) : 2.8 * u;
                return p;
            };

            catalog.constellations.forEach((con) => {
                let sumX = 0;
                let sumY = 0;
                let countPts = 0;
                let anySegmentVisible = false;

                if (Array.isArray(con.lines)) {
                    con.lines.forEach((seg) => {
                        if (!Array.isArray(seg) || seg.length < 2) return;
                        const p1 = resolveVertex(seg[0][0], seg[0][1]);
                        const p2 = resolveVertex(seg[1][0], seg[1][1]);
                        if (p1.alt >= 0.0 && p2.alt >= 0.0 && (p1.visible || p2.visible)) {
                            const segDist = Math.hypot(p2.x - p1.x, p2.y - p1.y);
                            if (segDist <= maxSegLen) {
                                anySegmentVisible = true;
                                const k1 = `${Math.round(p1.x * 2)},${Math.round(p1.y * 2)}`;
                                const k2 = `${Math.round(p2.x * 2)},${Math.round(p2.y * 2)}`;
                                const eKey = k1 < k2 ? `${k1}|${k2}` : `${k2}|${k1}`;
                                if (showSky && !drawnEdges.has(eKey)) {
                                    drawnEdges.add(eKey);
                                    drawFilament(p1, p2);
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
        }

        // 3. Messier deep-sky objects: screen-blended diamond with a soft halo
        const visibleMessier = [];
        if ((showSky || showLabels) && Array.isArray(catalog.messier)) {
            ctx.save();
            ctx.globalCompositeOperation = "screen";
            catalog.messier.forEach((dso) => {
                const pt = projectRaDecToCanvas(dso.ra, dso.dec);
                if (!pt.visible || pt.alt < 5.0) return;
                visibleMessier.push({ ...dso, x: pt.x, y: pt.y, alt: pt.alt });
                if (showSky) {
                    const r = 4.5 * u;
                    const diamond = () => {
                        ctx.beginPath();
                        ctx.moveTo(pt.x, pt.y - r);
                        ctx.lineTo(pt.x + r, pt.y);
                        ctx.lineTo(pt.x, pt.y + r);
                        ctx.lineTo(pt.x - r, pt.y);
                        ctx.closePath();
                    };
                    ctx.strokeStyle = "rgba(253, 224, 71, 0.14)";
                    ctx.lineWidth = 2.6 * u;
                    diamond();
                    ctx.stroke();
                    ctx.strokeStyle = "rgba(253, 224, 71, 0.62)";
                    ctx.lineWidth = 0.8 * u;
                    diamond();
                    ctx.stroke();
                }
            });
            ctx.restore();
        }

        // 4. Bright stars: four-arc reticles (open, so the real star stays visible), gold + ticks for the brightest
        const visibleBrightStars = [];
        if ((showSky || showLabels) && Array.isArray(catalog.stars)) {
            ctx.save();
            ctx.globalCompositeOperation = "screen";
            const gapRad = 0.28;
            const arcs = (x, y, r) => {
                for (let q = 0; q < 4; q++) {
                    ctx.beginPath();
                    ctx.arc(x, y, r, q * (Math.PI / 2) + gapRad, (q + 1) * (Math.PI / 2) - gapRad);
                    ctx.stroke();
                }
            };
            catalog.stars.concat(window.ALLSKY_PLANETS || []).forEach((star) => {
                const pt = projectRaDecToCanvas(star.ra, star.dec);
                if (!pt.visible || pt.alt < 1.0) return;
                visibleStarsCount++;

                const mag = Number(star.mag !== undefined ? star.mag : 3.0);
                const rRing = reticleRadius(mag);
                const prominent = mag <= 1.5;
                const hasTicks = mag <= 1.0;

                if (showSky && mag <= 3.0) {
                    const b = Math.max(0.32, starWeight(mag));
                    ctx.strokeStyle = prominent ? "rgba(253, 224, 71, 0.12)" : "rgba(56, 189, 248, 0.10)";
                    ctx.lineWidth = (prominent ? 1.85 : 1.45) * lw * u;
                    arcs(pt.x, pt.y, rRing);
                    ctx.strokeStyle = prominent
                        ? `rgba(253, 224, 71, ${(0.40 + 0.18 * b).toFixed(2)})`
                        : `rgba(125, 211, 252, ${(0.28 + 0.22 * b).toFixed(2)})`;
                    ctx.lineWidth = (prominent ? 0.68 + 0.24 * b : 0.48 + 0.22 * b) * lw * u;
                    arcs(pt.x, pt.y, rRing);
                    if (hasTicks) {
                        const tIn = rRing + 1.2 * u, tOut = rRing + 3.6 * u;
                        ctx.beginPath();
                        ctx.moveTo(pt.x - tOut, pt.y); ctx.lineTo(pt.x - tIn, pt.y);
                        ctx.moveTo(pt.x + tIn, pt.y); ctx.lineTo(pt.x + tOut, pt.y);
                        ctx.moveTo(pt.x, pt.y - tOut); ctx.lineTo(pt.x, pt.y - tIn);
                        ctx.moveTo(pt.x, pt.y + tIn); ctx.lineTo(pt.x, pt.y + tOut);
                        ctx.stroke();
                    }
                }

                if (star.name && mag <= 2.55) {
                    visibleBrightStars.push({
                        name: star.name,
                        bayer: star.bayer || "",
                        mag: mag,
                        x: pt.x,
                        y: pt.y,
                        rRing: rRing,
                        prominent: prominent,
                        hasTicks: hasTicks,
                    });
                }
            });
            ctx.restore();
        }

        // 5. Labels with collision avoidance (constellation pill badges, star names with leader filaments, DSOs)
        if (showLabels) {
            ctx.save();
            ctx.textBaseline = "middle";

            // 5a. Bright star names first (sorted brightest first): try 8 positions just outside the reticle
            visibleBrightStars.sort((a, b) => a.mag - b.mag);
            visibleBrightStars.forEach((s) => {
                const fs = (s.prominent ? 11.2 : 10.0) * u;
                ctx.font = `${s.prominent ? 600 : 500} ${fs}px Inter, system-ui, sans-serif`;
                const tw = ctx.measureText(s.name).width;
                const th = 14 * u;
                const rim = s.rRing + (s.hasTicks ? 4.2 : 1.0) * u;
                const dist = rim + 6.5 * u;
                const diag = rim * 0.72 + 6.0 * u;
                const cands = [
                    { bx: s.x + dist, by: s.y - th / 2, tx: s.x + dist, ty: s.y, align: "left", ux: 1, uy: 0 },
                    { bx: s.x + diag, by: s.y - diag - th + 2 * u, tx: s.x + diag, ty: s.y - diag - th / 2 + 2 * u, align: "left", ux: 0.707, uy: -0.707 },
                    { bx: s.x + diag, by: s.y + diag - 2 * u, tx: s.x + diag, ty: s.y + diag + th / 2 - 2 * u, align: "left", ux: 0.707, uy: 0.707 },
                    { bx: s.x - dist - tw, by: s.y - th / 2, tx: s.x - dist, ty: s.y, align: "right", ux: -1, uy: 0 },
                    { bx: s.x - diag - tw, by: s.y - diag - th + 2 * u, tx: s.x - diag, ty: s.y - diag - th / 2 + 2 * u, align: "right", ux: -0.707, uy: -0.707 },
                    { bx: s.x - diag - tw, by: s.y + diag - 2 * u, tx: s.x - diag, ty: s.y + diag + th / 2 - 2 * u, align: "right", ux: -0.707, uy: 0.707 },
                    { bx: s.x - tw / 2, by: s.y - dist - th, tx: s.x, ty: s.y - dist - th / 2, align: "center", ux: 0, uy: -1 },
                    { bx: s.x - tw / 2, by: s.y + dist, tx: s.x, ty: s.y + dist + th / 2, align: "center", ux: 0, uy: 1 },
                ];
                for (const c of cands) {
                    if (!placeBox(c.bx, c.by, tw, th)) continue;
                    // Subtle leader filament from just outside the reticle rim toward the label
                    ctx.save();
                    ctx.globalCompositeOperation = "screen";
                    ctx.strokeStyle = s.prominent ? "rgba(253, 224, 71, 0.28)" : "rgba(125, 211, 252, 0.25)";
                    ctx.lineWidth = 0.65 * u;
                    ctx.beginPath();
                    ctx.moveTo(s.x + c.ux * (rim + 1.0 * u), s.y + c.uy * (rim + 1.0 * u));
                    ctx.lineTo(s.x + c.ux * (rim + 4.5 * u), s.y + c.uy * (rim + 4.5 * u));
                    ctx.stroke();
                    ctx.restore();
                    ctx.fillStyle = s.prominent ? "rgba(254, 243, 199, 0.88)" : "rgba(224, 242, 254, 0.80)";
                    ctx.shadowColor = "rgba(2, 6, 23, 0.72)";
                    ctx.shadowBlur = 4;
                    ctx.textAlign = c.align;
                    ctx.fillText(s.name, c.tx, c.ty);
                    ctx.shadowBlur = 0;
                    break;
                }
            });

            // 5b. Constellation names as translucent pill badges (highest altitude first), nudged around the centroid
            visibleConstellationLabels.sort((a, b) => b.alt - a.alt);
            visibleConstellationLabels.forEach((con) => {
                const labelText = con.common ? `${con.name.toUpperCase()} · ${con.common}` : con.name.toUpperCase();
                const fs = 10.5 * u;
                ctx.font = `600 ${fs}px Inter, system-ui, sans-serif`;
                const tw = ctx.measureText(labelText).width;
                const padX = 7 * u;
                const bw = tw + padX * 2;
                const bh = 18 * u;
                const offs = [[0, 0], [0, -22], [0, 22], [-40, 0], [40, 0], [0, -44], [0, 44]];
                for (const [ox, oy] of offs) {
                    const bx = con.x - bw / 2 + ox * u;
                    const by = con.y - bh / 2 + oy * u;
                    if (!placeBox(bx, by, bw, bh)) continue;
                    roundedRect(bx, by, bw, bh, 5 * u);
                    ctx.fillStyle = "rgba(15, 23, 42, 0.46)";
                    ctx.fill();
                    ctx.strokeStyle = "rgba(56, 189, 248, 0.30)";
                    ctx.lineWidth = 0.75 * u;
                    ctx.stroke();
                    ctx.fillStyle = "rgba(186, 230, 253, 0.88)";
                    ctx.textAlign = "center";
                    ctx.shadowColor = "rgba(2, 6, 23, 0.75)";
                    ctx.shadowBlur = 3;
                    ctx.fillText(labelText, bx + bw / 2, by + bh / 2 + 0.5 * u);
                    ctx.shadowBlur = 0;
                    break;
                }
            });

            // 5c. Messier labels (M45, M42, M31, M44, ...)
            ctx.font = `600 ${9.5 * u}px Inter, system-ui, sans-serif`;
            ctx.textAlign = "left";
            ctx.shadowColor = "rgba(2, 6, 23, 0.72)";
            visibleMessier.forEach((dso) => {
                const labelText = `${dso.id} ${dso.name}`;
                const tw = ctx.measureText(labelText).width;
                const th = 12 * u;
                const lx = dso.x + 8 * u;
                if (placeBox(lx, dso.y - th / 2, tw, th)) {
                    ctx.fillStyle = "rgba(254, 240, 138, 0.85)";
                    ctx.shadowBlur = 3;
                    ctx.fillText(labelText, lx, dso.y);
                    ctx.shadowBlur = 0;
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

        // 6. Draw Top-Left Astrometric Overlay Summary Badge (not on a phone: it would cover the picture)
        if (viewportContainer && viewportContainer.clientWidth < 600) return;
        ctx.save();
        const badgeText = `LST ${formatLstHms(lmstDeg)} • ${visibleConstellationsCount} constellations • ${visibleStarsCount} stars • Center (${optX.toFixed(0)}, ${optY.toFixed(0)}) ${isRectilinear ? `FOV ${hfovDeg.toFixed(1)}° Az ${tiltAzDeg.toFixed(1)}°` : `R90=${r90SensorPx.toFixed(0)}px`} PA=${northPaDeg.toFixed(1)}°`;
        ctx.font = "11px monospace";
        // Below the capture info (time / exposure) rather than under it; two lines when the picture is too narrow for one
        const infoBottom = liveFrameInfo && !liveFrameInfo.hidden ? liveFrameInfo.offsetTop + liveFrameInfo.offsetHeight + 6 : 0;
        const cut = badgeText.indexOf(" • Center");
        const oneLine = ctx.measureText(badgeText).width + 16 <= renderW - 20 || cut < 0;
        const lines = oneLine ? [badgeText] : [badgeText.slice(0, cut), badgeText.slice(cut + 3)];
        const bw = Math.max(...lines.map((t) => ctx.measureText(t).width)) + 16;
        const bh = 8 + 14 * lines.length;
        const bx = offsetX + 10;
        const by = Math.max(offsetY + 10, infoBottom);
        ctx.fillStyle = "rgba(2, 6, 23, 0.72)";
        ctx.strokeStyle = "rgba(56, 189, 248, 0.35)";
        ctx.lineWidth = 1;
        ctx.fillRect(bx, by, bw, bh);
        ctx.strokeRect(bx, by, bw, bh);
        ctx.fillStyle = "#e2e8f0";
        ctx.textAlign = "left";
        lines.forEach((t, i) => ctx.fillText(t, bx + 8, by + 15 + 14 * i));
        ctx.restore();
    }


    // ---- ADS-B aircraft overlay: marks planes that fall inside the camera's field of view ----
    let aircraftList = [];
    let aircraftFetchedAt = 0;
    let aircraftT = 0;       // server time of the positions in aircraftList
    let liveFrameT = null;   // server time the displayed frame was taken (mid-exposure)
    let aircraftDrawQueued = false;
    function scheduleAircraftDraw() {
        if (aircraftDrawQueued) return;
        aircraftDrawQueued = true;
        requestAnimationFrame(() => { aircraftDrawQueued = false; drawAircraft(); });
    }
    function refreshAircraft() {
        if (!chkAircraft || !chkAircraft.checked || document.hidden) return;
        fetch("/api/adsb/aircraft?limit=60")
            .then((r) => r.json())
            .then((d) => {
                aircraftList = (d && d.aircraft) || [];
                aircraftFetchedAt = Date.now();
                aircraftT = (d && d.t) || 0;
                drawAircraft();
            })
            .catch(() => {});
    }

    // Radiosondes (SondeHub weather balloons)
    let sondeList = [];
    let sondeT = 0;
    let sondeFetchedAt = 0;
    function refreshSondes() {
        if (!chkSonde || !chkSonde.checked || document.hidden) return;
        fetch("/api/radiosondes")
            .then((r) => r.json())
            .then((d) => {
                sondeList = (d && d.sondes) || [];
                sondeT = (d && d.t) || 0;
                sondeFetchedAt = Date.now();
                drawAircraft();
            })
            .catch(() => {});
    }
    if (chkSonde) {
        setInterval(refreshSondes, 3000);
        setTimeout(refreshSondes, 600);
    }

    // Large satellites (space stations, bright satellites): only those lit by the sun under a dark sky are drawn
    let satelliteList = [];
    function refreshSatellites() {
        if (!chkSatellite || !chkSatellite.checked || document.hidden) return;
        fetch("/api/satellites")
            .then((r) => r.json())
            .then((d) => {
                satelliteList = (d && d.satellites) || [];
                drawAircraft();
            })
            .catch(() => {});
    }

    function drawAircraft() {
        if (!aircraftCanvas || !viewportContainer) return;
        const w = viewportContainer.clientWidth;
        const h = viewportContainer.clientHeight;
        if (w <= 10 || h <= 10) return;
        const pxRatio = Math.min(4, (window.devicePixelRatio || 1) * view.zoom);
        aircraftCanvas.width = Math.round(w * pxRatio);
        aircraftCanvas.height = Math.round(h * pxRatio);
        const ctx = aircraftCanvas.getContext("2d");
        ctx.setTransform(pxRatio, 0, 0, pxRatio, 0, 0);
        ctx.clearRect(0, 0, w, h);
        const showAircraft = !!(chkAircraft && chkAircraft.checked);
        const showSat = !!(chkSatellite && chkSatellite.checked);
        const showSonde = !!(chkSonde && chkSonde.checked);
        if (!showAircraft && !showSat && !showSonde) return;

        const st = state.station || {};
        const sensor = st.camera_sensor || {};
        const sensorW = Number(sensor.width_px || 2592);
        const sensorH = Number(sensor.height_px || 1944);
        const containerAspect = w / Math.max(1, h);
        const sensorAspect = sensorW / Math.max(1, sensorH);
        let renderW = w, renderH = h, offsetX = 0, offsetY = 0;
        if (containerAspect > sensorAspect) {
            renderW = h * sensorAspect;
            offsetX = (w - renderW) / 2.0;
        } else {
            renderH = w / sensorAspect;
            offsetY = (h - renderH) / 2.0;
        }
        const tf = st.image_transform || {};
        const rot180 = Number(tf.rotate_deg || 0) % 360 === 180;
        const mirrorX = !!tf.flip_horizontal !== rot180;
        const mirrorY = !!tf.flip_vertical !== rot180;

        ctx.save();
        ctx.beginPath();
        ctx.rect(offsetX, offsetY, renderW, renderH);
        ctx.clip();
        ctx.font = "11px monospace";
        ctx.textBaseline = "middle";
        // Marks sit where the plane was when the shown frame was taken: the fetched position slid along its own frame velocity
        const age = (Date.now() - aircraftFetchedAt) / 1000.0;
        const shift = aircraftT && liveFrameT != null ? liveFrameT - aircraftT : age;
        const lag = Math.max(-5, Math.min(20, shift));
        if (showSat) {
            satelliteList.forEach((sat) => {
                if (!sat.frame_xy || !sat.in_view || !sat.visible) return;
                let fx = sat.frame_xy[0], fy = sat.frame_xy[1];
                if (mirrorX) fx = 1 - fx;
                if (mirrorY) fy = 1 - fy;
                const x = offsetX + fx * renderW;
                const y = offsetY + fy * renderH;
                ctx.strokeStyle = "rgba(34, 211, 238, 0.95)";
                ctx.fillStyle = "rgba(34, 211, 238, 0.95)";
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.moveTo(x, y - 12); ctx.lineTo(x + 12, y); ctx.lineTo(x, y + 12); ctx.lineTo(x - 12, y); ctx.closePath();
                ctx.save();
                ctx.globalAlpha = 0.6;
                ctx.stroke();
                ctx.restore();
                const txt = `${sat.name} ${sat.height_km}km`;
                ctx.shadowColor = "rgba(2, 6, 23, 0.95)";
                ctx.shadowBlur = 4;
                ctx.textAlign = x > offsetX + renderW * 0.75 ? "right" : "left";
                ctx.fillText(txt, x + (ctx.textAlign === "left" ? 16 : -16), y);
                ctx.shadowBlur = 0;
            });
        }
        if (showSonde) {
            const sLag = Math.max(-5, Math.min(20, sondeT && liveFrameT != null ? liveFrameT - sondeT : (Date.now() - sondeFetchedAt) / 1000.0));
            sondeList.forEach((sd) => {
                if (!sd.frame_xy || !sd.in_view) return;
                let fx = sd.frame_xy[0], fy = sd.frame_xy[1];
                if (sd.frame_v) { fx += sd.frame_v[0] * sLag; fy += sd.frame_v[1] * sLag; }
                if (mirrorX) fx = 1 - fx;
                if (mirrorY) fy = 1 - fy;
                const x = offsetX + fx * renderW;
                const y = offsetY + fy * renderH;
                ctx.strokeStyle = "rgba(244, 114, 182, 0.95)";
                ctx.fillStyle = "rgba(244, 114, 182, 0.95)";
                ctx.lineWidth = 1.5;
                ctx.save();
                ctx.globalAlpha = 0.6;
                ctx.beginPath();
                ctx.rect(x - 11, y - 11, 22, 22);
                ctx.moveTo(x - 4, y - 4); ctx.lineTo(x + 4, y + 4); ctx.moveTo(x + 4, y - 4); ctx.lineTo(x - 4, y + 4);
                ctx.stroke();
                ctx.restore();
                const txt = `${sd.serial} ${(sd.altitude_m / 1000).toFixed(1)}km ${sd.distance_km}km`;
                ctx.shadowColor = "rgba(2, 6, 23, 0.95)";
                ctx.shadowBlur = 4;
                ctx.textAlign = x > offsetX + renderW * 0.75 ? "right" : "left";
                ctx.fillText(txt, x + (ctx.textAlign === "left" ? 16 : -16), y);
                ctx.shadowBlur = 0;
            });
        }
        if (showAircraft) aircraftList.forEach((a) => {
            if (!a.frame_xy) return;
            let fx = a.frame_xy[0], fy = a.frame_xy[1];
            if (a.frame_v) { fx += a.frame_v[0] * lag; fy += a.frame_v[1] * lag; }
            if (mirrorX) fx = 1 - fx;
            if (mirrorY) fy = 1 - fy;
            const x = offsetX + fx * renderW;
            const y = offsetY + fy * renderH;
            ctx.strokeStyle = "rgba(251, 191, 36, 0.95)";
            ctx.fillStyle = "rgba(251, 191, 36, 0.95)";
            ctx.lineWidth = 1.5;
            // The circle is a margin for the timing error (~1.5 s of travel): bigger for a plane that crosses the frame faster
            const rad = a.frame_v ? Math.max(16, Math.min(44, 14 + Math.hypot(a.frame_v[0] * renderW, a.frame_v[1] * renderH) * 1.5)) : 16;
            ctx.beginPath();
            ctx.arc(x, y, rad, 0, Math.PI * 2);
            ctx.save();
            ctx.globalAlpha = 0.6;
            ctx.stroke();
            ctx.restore();
            const name = a.callsign || a.icao || "?";
            const alt = a.altitude_m != null ? `FL${String(Math.round(a.altitude_m / 30.48)).padStart(3, "0")}` : "";
            const dist = a.distance_km != null ? `${a.distance_km}km` : "";
            const txt = [name, alt, dist].filter(Boolean).join(" ");
            ctx.shadowColor = "rgba(2, 6, 23, 0.95)";
            ctx.shadowBlur = 4;
            ctx.textAlign = x > offsetX + renderW * 0.75 ? "right" : "left";
            ctx.fillText(txt, x + (ctx.textAlign === "left" ? rad + 3 : -(rad + 3)), y);
            ctx.shadowBlur = 0;
        });
        ctx.restore();
    }

    if (chkAircraft) {
        setInterval(refreshAircraft, 1500);
        window.addEventListener("resize", drawAircraft);
        setTimeout(refreshAircraft, 400);
    }
    if (chkSatellite) {
        setInterval(refreshSatellites, 1500)  /* LEO satellites move up to ~1 deg/s */;
        setTimeout(refreshSatellites, 600);
    }

    if (chkSkyOverlay) chkSkyOverlay.addEventListener("change", drawReticle);
    if (chkSkyLabels) chkSkyLabels.addEventListener("change", drawReticle);
    if (chkGridOverlay) chkGridOverlay.addEventListener("change", drawReticle);
    // Match the viewport box to the camera's real aspect ratio (4:3 IMX335, 16:9 IMX708, ...)
    function fitViewportToFrame() {
        if (!liveImg || !viewportContainer || !liveImg.naturalWidth || !liveImg.naturalHeight) return;
        const ratio = `${liveImg.naturalWidth} / ${liveImg.naturalHeight}`;
        // The server pre-sizes the box (data-frame-ratio); only touch it if the camera's ratio differs
        const [pw, ph] = (viewportContainer.dataset.frameRatio || "").split("/").map(Number);
        if (pw && ph && Math.abs(pw / ph - liveImg.naturalWidth / liveImg.naturalHeight) < 0.01) return;
        viewportContainer.dataset.frameRatio = ratio;
        viewportContainer.style.aspectRatio = ratio;
        applyView(false);
    }
    if (liveImg) liveImg.addEventListener("load", fitViewportToFrame);
    fitViewportToFrame();
    if (liveImg) liveImg.addEventListener("load", drawReticle);
    window.addEventListener("resize", rafDebounced(drawReticle));
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
    setInterval(() => {
        if (document.hidden) return;
        const anyOverlay =
            (chkSkyOverlay ? chkSkyOverlay.checked : true) ||
            (chkSkyLabels ? chkSkyLabels.checked : true) ||
            (chkGridOverlay ? chkGridOverlay.checked : false);
        if (!anyOverlay) return;
        drawReticle();
    }, 5000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) drawReticle(); });
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
            telemSkyStatus.textContent = data.sky_status.toUpperCase();
        }
        if (data.fps !== undefined) {
            telemFps.textContent = data.fps.toFixed(1) + " fps";
            const nf = document.getElementById("nav-fps");
            if (nf) nf.textContent = ` · ${data.fps.toFixed(1)} fps`;
        }
        if (data.hw_frame_delta_ms !== undefined) {
            hudHwDelta.textContent = data.hw_frame_delta_ms.toFixed(1) + " ms";
        }
        if (data.sensor_temp_c !== undefined) {
            telemTemp.textContent = data.sensor_temp_c.toFixed(1) + " °C";
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
    pollCalibrationStatus();
    loadStationConfig();
    // A plate-solve run from another tab changes the lens/pointing the sky overlay uses: pick it up
    setInterval(() => { if (!document.hidden) loadStationConfig(); }, 60000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) loadStationConfig(); });
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

    // ------------------------------------------------------------------
    // Cloud Cover History chart (inline SVG, refreshed every 5 min)
    // ------------------------------------------------------------------
    const cloudChart = document.getElementById("cloud-chart");
    if (cloudChart && currentPage === "tab-live") {
        const SVG_NS = "http://www.w3.org/2000/svg";
        const cloudTip = document.getElementById("cloud-tooltip");
        const cloudEmpty = document.getElementById("cloud-empty");
        const cloudWrap = document.getElementById("cloud-chart-wrap");
        const rangeBtns = document.querySelectorAll(".cloud-ranges .btn-range");
        let cloudHours = 24;
        try {
            cloudHours = Number(localStorage.getItem("cloudHours")) || 24;
        } catch (e) {
            /* storage unavailable */
        }
        let cloudData = null;
        const mainStore = { geom: null };
        const fvStore = { geom: null };
        let cloudEvents = [];   // brief sky events inside the charted range
        let viewTs = null;      // moment shown in the frame viewer (marked on the chart)

        const svgEl = (tag, attrs, parent) => {
            const el = document.createElementNS(SVG_NS, tag);
            Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
            if (parent) parent.appendChild(el);
            return el;
        };
        const fmtTime = (t, withDate) => {
            const d = stationDate(new Date(t * 1000));
            const p = (n) => String(n).padStart(2, "0");
            const hm = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
            const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getUTCMonth()];
            return withDate ? `${p(d.getUTCDate())} ${mon} ${hm}` : hm;
        };

        function renderChart(svg, store) {
            if (!cloudData) return;
            const box = svg.getBoundingClientRect();
            const W = Math.max(280, Math.round(box.width));
            const H = Math.round(box.height) || 260;
            const LANE = 16;   // event ticks sit in a strip under the plot, above the time labels
            const m = { l: 40, r: 12, t: 10, b: 24 + LANE };
            const pw = W - m.l - m.r;
            const ph = H - m.t - m.b;
            const { start, end } = cloudData;
            const pts = cloudData.points.filter((p) => p.cc !== null && p.cc !== undefined);
            const x = (t) => m.l + ((t - start) / (end - start)) * pw;
            const y = (v) => m.t + (1 - v / 100) * ph;
            svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
            svg.replaceChildren();
            if (svg === cloudChart) cloudEmpty.classList.toggle("hidden", pts.length > 0);

            // Night bands from consecutive night samples
            const all = cloudData.points;
            const step = Math.max(cloudData.interval_s, (end - start) / 600);
            let runStart = null;
            all.forEach((p, i) => {
                const night = p.m === "night";
                if (night && runStart === null) runStart = p.t - step / 2;
                const next = all[i + 1];
                const endsHere = night && (!next || next.m !== "night" || next.t - p.t > step * 4);
                if (endsHere) {
                    const x0 = Math.max(m.l, x(runStart));
                    const x1 = Math.min(m.l + pw, x(p.t + step / 2));
                    if (x1 > x0) svgEl("rect", { class: "night-band", x: x0, y: m.t, width: x1 - x0, height: ph }, svg);
                    runStart = null;
                }
            });

            // Grid + y labels
            [0, 25, 50, 75, 100].forEach((v) => {
                svgEl("line", { class: "grid", x1: m.l, x2: m.l + pw, y1: y(v), y2: y(v) }, svg);
                const tx = svgEl("text", { class: "axis-label", x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, svg);
                tx.textContent = `${v}%`;
            });
            // X ticks at round local hours / days
            const span = end - start;
            const tickH = span <= 6 * 3600 ? 1 : span <= 26 * 3600 ? 3 : span <= 80 * 3600 ? 12 : span <= 170 * 3600 ? 24 : 120;
            const tickS = tickH * 3600;
            const tzOff = -STATION_UTC_OFFSET_MIN * 60;
            let t0 = Math.ceil((start - tzOff) / tickS) * tickS + tzOff;
            const maxTicks = Math.max(2, Math.floor(pw / 70));
            const stride = Math.max(1, Math.ceil(span / tickS / maxTicks));
            for (let t = t0, k = 0; t <= end; t += tickS, k++) {
                if (k % stride) continue;
                const tx = svgEl("text", { class: "axis-label", x: x(t), y: H - 6, "text-anchor": "middle" }, svg);
                tx.textContent = tickH >= 24 ? fmtTime(t, true).split(" ").slice(0, 2).join(" ") : fmtTime(t, false);
            }

            // Line segments, broken over gaps (camera off, overexposed frames)
            const gap = step * 3.5;
            const segs = [];
            pts.forEach((p, i) => {
                if (i === 0 || p.t - pts[i - 1].t > gap) segs.push([]);
                segs[segs.length - 1].push(p);
            });
            segs.forEach((seg) => {
                const line = seg.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.cc).toFixed(1)}`).join("");
                if (seg.length > 1) {
                    svgEl("path", { class: "cc-area", d: `${line}L${x(seg[seg.length - 1].t).toFixed(1)},${y(0)}L${x(seg[0].t).toFixed(1)},${y(0)}Z` }, svg);
                    svgEl("path", { class: "cc-line", d: line }, svg);
                } else {
                    svgEl("circle", { class: "hover-dot", cx: x(seg[0].t), cy: y(seg[0].cc), r: 3 }, svg);
                }
            });
            // Low-confidence samples as hollow markers (only when sparse enough to read)
            if (pts.length <= 300) {
                pts.filter((p) => (p.conf ?? 1) < 0.5).forEach((p) => svgEl("circle", { class: "cc-low", cx: x(p.t), cy: y(p.cc), r: 3.5 }, svg));
            }
            // Event lane: one tick per meteor / aircraft / satellite / flash, drawn on top so it stays clickable
            const laneY = m.t + ph + 3;
            svgEl("rect", { class: "ev-lane", x: m.l, y: laneY, width: pw, height: LANE - 3 }, svg);
            const evs = cloudEvents.filter((e) => e.start_ts >= start && e.start_ts <= end);
            evs.forEach((e) => svgEl("rect", { class: `ev-tick ev-${e.type}`, x: (x(e.start_ts) - 1).toFixed(1), y: laneY + 1, width: 2, height: LANE - 5, rx: 0.5 }, svg));
            store.geom = { pts, x, y, m, pw, ph, W, H, laneY, LANE, evs, start, end };
            setMark(svg, store);
        }

        // The viewed moment as a dashed line on a chart (moved without redrawing the whole chart)
        function setMark(svg, store) {
            svg.querySelectorAll(".cc-mark").forEach((el) => el.remove());
            const g = store.geom;
            if (!g || viewTs === null || viewTs < g.start || viewTs > g.end) return;
            svgEl("line", { class: "cc-mark", x1: g.x(viewTs), x2: g.x(viewTs), y1: g.m.t, y2: g.laneY + g.LANE - 3 }, svg);
        }
        function renderCloudChart() {
            renderChart(cloudChart, mainStore);
            if (fv && !fv.classList.contains("hidden")) renderChart(fvChart, fvStore);
        }

        const SNAP_PX = 8;   // an event this close to the pointer (horizontally) is snapped to
        // Chart x position of a pointer, in the svg's own units, and the event (if any) close enough to snap to
        function pointerAt(svg, store, e) {
            const g = store.geom;
            if (!g) return null;
            const rect = svg.getBoundingClientRect();
            const px = ((e.clientX - rect.left) / rect.width) * g.W;
            const py = ((e.clientY - rect.top) / rect.height) * g.H;
            if (px < g.m.l - 4 || px > g.m.l + g.pw + 4) return null;
            let snap = null;
            g.evs.forEach((ev) => {
                const d = Math.abs(g.x(ev.start_ts) - px);
                if (d <= SNAP_PX && (!snap || d < snap[0])) snap = [d, ev];
            });
            return { g, rect, px, py, ev: snap && snap[1], t: cloudData.start + ((px - g.m.l) / g.pw) * (cloudData.end - cloudData.start) };
        }

        function showCloudTip(svg, store, tip, e) {
            const at = pointerAt(svg, store, e);
            svg.querySelectorAll(".crosshair, .hover-sel").forEach((el) => el.remove());
            if (!at || !at.g.pts.length) { tip.classList.add("hidden"); return; }
            const { g, rect, px } = at;
            svg.style.cursor = "pointer";
            if (at.ev) {   // snapped onto an event
                const cx = g.x(at.ev.start_ts);
                svgEl("line", { class: "crosshair", x1: cx, x2: cx, y1: g.m.t, y2: g.laneY + g.LANE - 3 }, svg);
                tip.innerHTML = `<strong>${EV_NAMES[at.ev.type] || at.ev.type}${at.ev.label ? " " + at.ev.label : ""}</strong><br>${fmtTime(at.ev.start_ts, true)} ${tzLabel}<br>click to view`;
                placeTip(tip, rect, cx, g.W);
                return;
            }
            let best = g.pts[0];
            g.pts.forEach((p) => {
                if (Math.abs(g.x(p.t) - px) < Math.abs(g.x(best.t) - px)) best = p;
            });
            const cx = g.x(best.t);
            svgEl("line", { class: "crosshair", x1: cx, x2: cx, y1: g.m.t, y2: g.m.t + g.ph }, svg);
            svgEl("circle", { class: "hover-dot hover-sel", cx, cy: g.y(best.cc), r: 5 }, svg);
            const mode = best.m === "night" ? "Night" : best.m === "day" ? "Day" : "";
            const extra = best.m === "night" ? ` · ${best.stars} stars` : "";
            const conf = (best.conf ?? 1) < 0.5 ? " · low confidence" : "";
            const avg = best.n > 1 ? ` · avg of ${best.n}` : "";
            tip.innerHTML = `<strong>${best.cc.toFixed(0)}%</strong> cloud<br>${fmtTime(best.t, true)} ${tzLabel}<br>${mode}${extra}${conf}${avg}`;
            placeTip(tip, rect, cx, g.W);
        }
        function placeTip(tip, rect, cx, W) {
            tip.classList.remove("hidden");
            const left = (cx / W) * rect.width;
            const tipW = tip.offsetWidth;
            tip.style.left = `${Math.min(rect.width - tipW - 4, Math.max(4, left + (left > rect.width / 2 ? -tipW - 12 : 12)))}px`;
        }

        // Hover shows the cloud value (or the event it snaps to); a click opens the frame at that moment / event
        function bindChart(svg, store, tip) {
            let downAt = null;
            svg.addEventListener("pointermove", (e) => showCloudTip(svg, store, tip, e));
            svg.addEventListener("pointerleave", () => {
                tip.classList.add("hidden");
                svg.querySelectorAll(".crosshair, .hover-sel").forEach((el) => el.remove());
            });
            svg.addEventListener("pointerdown", (e) => { downAt = [e.clientX, e.clientY]; });
            svg.addEventListener("pointerup", (e) => {
                if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 6) return;
                const at = pointerAt(svg, store, e);
                if (!at || at.py > at.g.laneY + at.g.LANE) return;
                if (at.ev) openFrameAt(at.ev.start_ts, at.ev);
                else openFrameAt(at.t, null);
            });
        }

        // ---- Frame viewer: the same chart with its events, plus play / pause / prev / next through the frames ----
        const fv = document.getElementById("frame-viewer");
        const fvImg = document.getElementById("fv-img");
        const fvMsg = document.getElementById("fv-msg");
        const fvChart = document.getElementById("fv-chart");
        const fvPlay = document.getElementById("fv-play");
        const fvSpeed = document.getElementById("fv-speed");
        let fvEvent = null;     // event the viewer was opened on (or is passing through while playing)
        let fvFrames = [];      // archived frames around the viewed moment, oldest first
        let fvIdx = 0;
        let fvTimer = null;
        let fvReq = 0;
        const EV_NAMES = { meteor: "Meteor", plane: "Aircraft", satellite: "Satellite", flash: "Flash" };
        const SPAN_BEFORE = 20 * 60, SPAN_AFTER = 60 * 60;

        const eventAt = (ts) => cloudEvents.find((e) => ts >= e.start_ts - 4 && ts <= Math.max(e.end_ts || 0, e.start_ts) + 4) || null;

        async function loadSpan(ts) {
            const r = await fetch(`/api/frames/span?start=${Math.floor(ts - SPAN_BEFORE)}&end=${Math.ceil(ts + SPAN_AFTER)}&max=900`);
            if (!r.ok) throw new Error("span");
            return (await r.json()).frames || [];
        }
        const nearestIdx = (frames, ts) => frames.reduce((bi, f, i) => (Math.abs(f.ts - ts) < Math.abs(frames[bi].ts - ts) ? i : bi), 0);

        function showFrame(i) {
            fvIdx = Math.max(0, Math.min(fvFrames.length - 1, i));
            const f = fvFrames[fvIdx];
            if (!f) return;
            viewTs = f.ts;
            fvImg.src = f.url;
            fvImg.classList.remove("hidden");
            fvMsg.classList.add("hidden");
            for (let k = 1; k <= 3 && fvIdx + k < fvFrames.length; k++) new Image().src = fvFrames[fvIdx + k].url;   // preload what plays next
            const ev = eventAt(f.ts);
            if (ev) fvEvent = ev; else if (fvEvent && (f.ts < fvEvent.start_ts - 4 || f.ts > Math.max(fvEvent.end_ts || 0, fvEvent.start_ts) + 60)) fvEvent = null;
            const near = cloudData && cloudData.points.length
                ? cloudData.points.reduce((a, b) => (Math.abs(b.t - f.ts) < Math.abs(a.t - f.ts) ? b : a)) : null;
            const cc = near && near.cc !== null && near.cc !== undefined && Math.abs(near.t - f.ts) < 1800 ? `${near.cc.toFixed(0)}% cloud` : "";
            document.getElementById("fv-title").textContent = ev
                ? `${EV_NAMES[ev.type] || ev.type}${ev.label ? " " + ev.label : ""} · ${fmtTime(ev.start_ts, true)} ${tzLabel}`
                : `Frame · ${fmtTime(f.ts, true)} ${tzLabel}`;
            const sec = (n) => String(n).padStart(2, "0");
            const d = stationDate(new Date(f.ts * 1000));
            document.getElementById("fv-meta").innerHTML =
                `<span><strong>${fmtTime(f.ts, true)}:${sec(d.getUTCSeconds())}</strong> ${tzLabel}</span>` +
                `<span>frame ${fvIdx + 1} / ${fvFrames.length}</span>${cc ? `<span>${cc}</span>` : ""}` +
                (ev && ev.end_ts ? `<span>event lasts ${Math.max(1, Math.round(ev.end_ts - ev.start_ts))} s</span>` : "");
            document.getElementById("fv-open").href = f.url;
            const crop = document.getElementById("fv-crop");
            const cropUrl = ev && (ev.sky_url || ev.thumbnail_url);
            crop.classList.toggle("hidden", !cropUrl);
            if (cropUrl) crop.href = cropUrl;
            setMark(cloudChart, mainStore);
            setMark(fvChart, fvStore);
            drawBoxes();
            markEventList(true);
        }

        const EV_COLORS = { meteor: "#f472b6", plane: "#fbbf24", satellite: "#34d399", flash: "#f87171" };
        const fvBoxes = document.getElementById("fv-boxes");
        // Where an event is at a moment: moving dots are interpolated along their path (line is the first -> last point,
        // normalised to the frame); a streak is drawn as the whole line
        // Archive frames are 30 s apart and their file times do not line up exactly with the detector's clock, so a
        // dot's place is found in the pixels: brighter than in both neighbouring frames, close to the event's path.
        const GW = 648, GH = 365;
        const grayCache = new Map();
        function grayOf(url) {
            if (!grayCache.has(url)) {
                if (grayCache.size > 12) grayCache.delete(grayCache.keys().next().value);
                grayCache.set(url, new Promise((resolve) => {
                    const im = new Image();
                    im.onload = () => {
                        const c = document.createElement("canvas");
                        c.width = GW; c.height = GH;
                        const g = c.getContext("2d", { willReadFrequently: true });
                        g.drawImage(im, 0, 0, GW, GH);
                        const d = g.getImageData(0, 0, GW, GH).data, out = new Float32Array(GW * GH);
                        for (let i = 0; i < out.length; i++) out[i] = d[i * 4] * 0.3 + d[i * 4 + 1] * 0.59 + d[i * 4 + 2] * 0.11;
                        resolve(out);
                    };
                    im.onerror = () => resolve(null);
                    im.src = url;
                }));
            }
            return grayCache.get(url);
        }
        async function locateDot(e, i) {
            const cur = await grayOf(fvFrames[i].url);
            if (!cur) return null;
            const nb = (await Promise.all([i - 1, i + 1].filter((j) => j >= 0 && j < fvFrames.length).map((j) => grayOf(fvFrames[j].url)))).filter(Boolean);
            if (!nb.length) return null;
            const [x0, y0, x1, y1] = [e.line[0] * GW, e.line[1] * GH, e.line[2] * GW, e.line[3] * GH];
            const L2 = (x1 - x0) ** 2 + (y1 - y0) ** 2 || 1, R = 14;
            const dur = Math.max(1, (e.end_ts || e.start_ts) - e.start_ts);
            const u0 = (fvFrames[i].ts - e.start_ts) / dur, uw = 35 / dur;   // the clocks differ by seconds, not minutes
            let best = 0, bx = -1, by = -1;
            for (let y = 1; y < GH - 1; y++) {
                for (let x = 1; x < GW - 1; x++) {
                    const t = ((x - x0) * (x1 - x0) + (y - y0) * (y1 - y0)) / L2;
                    if (t < -0.06 || t > 1.06 || t < u0 - uw || t > u0 + uw) continue;
                    const px = x0 + t * (x1 - x0) - x, py = y0 + t * (y1 - y0) - y;
                    if (px * px + py * py > R * R) continue;
                    const k = y * GW + x;
                    let m = 0;
                    for (const n of nb) m = Math.max(m, n[k], n[k - 1], n[k + 1], n[k - GW], n[k + GW]);
                    const v = cur[k] - m;
                    if (v > best) { best = v; bx = x; by = y; }
                }
            }
            return best >= 10 ? [bx / GW, by / GH] : null;
        }

        let boxReq = 0;
        async function drawBoxes() {
            const req = ++boxReq;
            const f = fvFrames[fvIdx], i = fvIdx;
            fvBoxes.innerHTML = "";
            if (!f || f.baked || !fvImg.naturalWidth) return;   // baked: the event's own image already has the box drawn in
            const wrap = fvImg.parentElement.getBoundingClientRect(), im = fvImg.getBoundingClientRect();
            const W = fvImg.naturalWidth, H = fvImg.naturalHeight;
            const k = Math.min(im.width / W, im.height / H);
            const w = W * k, h = H * k;   // object-fit: contain
            Object.assign(fvBoxes.style, { left: `${im.left - wrap.left + (im.width - w) / 2}px`, top: `${im.top - wrap.top + (im.height - h) / 2}px`, width: `${w}px`, height: `${h}px` });
            fvBoxes.setAttribute("viewBox", `0 0 ${W} ${H}`);
            const active = cloudEvents.filter((e) => e.line && f.ts >= e.start_ts - 30 && f.ts <= Math.max(e.end_ts || 0, e.start_ts) + 30);
            const found = await Promise.all(active.map((e) => (e.moving_dot ? locateDot(e, i) : null)));
            if (req !== boxReq) return;
            active.forEach((e, n) => {
                const [x0, y0, x1, y1] = e.line, c = EV_COLORS[e.type] || "#fff";
                const dur = (e.end_ts || e.start_ts) - e.start_ts;
                const name = `${EV_NAMES[e.type] || e.type}${e.label ? " " + e.label : ""}`;
                const inside = f.ts >= e.start_ts - 4 && f.ts <= (e.end_ts || e.start_ts) + 4;
                let bx, by, bw, bh, dim = false;
                if (e.moving_dot && dur > 0) {
                    svgEl("line", { x1: x0 * W, y1: y0 * H, x2: x1 * W, y2: y1 * H, stroke: c, "stroke-width": 1, "stroke-dasharray": "5 4", opacity: 0.45, "vector-effect": "non-scaling-stroke" }, fvBoxes);
                    let pos = found[n];
                    if (!pos) {   // dot not visible in this frame (strobe off / too faint): fall back to the timed position
                        if (!inside) return;
                        const u = Math.max(0, Math.min(1, (f.ts - e.start_ts) / dur));
                        pos = [x0 + (x1 - x0) * u, y0 + (y1 - y0) * u];
                        dim = true;
                    }
                    const r = 0.02 * W;
                    bx = pos[0] * W - r; by = pos[1] * H - r; bw = bh = 2 * r;
                } else {
                    if (!inside) return;
                    const pad = 0.015 * W;
                    bx = Math.min(x0, x1) * W - pad; by = Math.min(y0, y1) * H - pad;
                    bw = Math.abs(x1 - x0) * W + 2 * pad; bh = Math.abs(y1 - y0) * H + 2 * pad;
                }
                svgEl("rect", { x: bx, y: by, width: bw, height: bh, stroke: c, rx: 3, "stroke-dasharray": dim ? "4 3" : "none", opacity: dim ? 0.6 : 1 }, fvBoxes);
                const t = svgEl("text", { x: bx, y: Math.max(14, by - 6), fill: c, "font-size": 0.015 * W, opacity: dim ? 0.6 : 1 }, fvBoxes);
                t.textContent = name;
            });
        }

        // The night's events beside the viewer (same list as "Recent events"); the one in view is highlighted
        const fvList = document.getElementById("fv-events");
        function renderEventList() {
            const evs = cloudEvents.slice().sort((a, b) => b.start_ts - a.start_ts);
            fvList.innerHTML = evs.map((e) => {
                const bits = [e.flight ? `✈ ${escapeHtml(e.flight)}` : "", e.length_px ? `${Math.round(e.length_px)} px` : "",
                    e.frames > 1 ? `${e.frames} frames` : "", e.peak_dn ? `peak +${e.peak_dn} DN` : ""].filter(Boolean).join(" · ");
                return `<li data-ts="${e.start_ts}" data-id="${escapeHtml(e.event_id || "")}" style="--pill:${EV_COLORS[e.type] || "#fff"}"><div class="ev-type">${EV_NAMES[e.type] || e.type}</div>` +
                    `<div class="ev-time">${fmtTime(e.start_ts, true)}</div><div class="ev-detail">${bits}</div></li>`;
            }).join("");
            markEventList(false);
        }
        function markEventList(scroll) {
            let on = null;
            fvList.querySelectorAll("li").forEach((li) => {
                const hit = fvEvent && li.dataset.id === (fvEvent.event_id || "");
                li.classList.toggle("active", !!hit);
                if (hit) on = li;
            });
            if (on && scroll) on.scrollIntoView({ block: "nearest" });
        }
        fvList.addEventListener("click", (e) => {
            const li = e.target.closest("li[data-ts]");
            if (!li) return;
            const ev = cloudEvents.find((x) => (x.event_id || "") === li.dataset.id);
            openFrameAt(Number(li.dataset.ts), ev || null);
        });
        window.openFrameViewerAt = (ts, evId) => openFrameAt(ts, cloudEvents.find((x) => x.event_id === evId) || null);
        fvImg.addEventListener("load", drawBoxes);
        window.addEventListener("resize", drawBoxes);

        // Jump to a moment: (re)load the frames around it unless it is already inside the loaded window
        async function openFrameAt(ts, ev) {
            stopPlay();
            if (ev !== undefined) fvEvent = ev;
            fv.classList.remove("hidden");
            renderChart(fvChart, fvStore);
            renderEventList();
            const req = ++fvReq;
            const inside = fvFrames.length && ts >= fvFrames[0].ts - 30 && ts <= fvFrames[fvFrames.length - 1].ts + 30;
            if (!inside) {
                fvMsg.textContent = "Loading…";
                fvMsg.classList.remove("hidden");
                try {
                    const frames = await loadSpan(ts);
                    if (req !== fvReq) return;
                    fvFrames = frames;
                } catch (e) {
                    fvFrames = [];
                }
                if (req !== fvReq) return;
            }
            if (!fvFrames.length) {
                fvImg.classList.add("hidden");
                fvMsg.textContent = "No archived frame near that time (frames are kept for a limited number of days).";
                fvMsg.classList.remove("hidden");
                return;
            }
            // The archive keeps one frame per 30 s, so a short event (a meteor) usually falls between two of them. The
            // event's own whole-sky image (taken from the live stream at that moment) is slotted in as a frame so the viewer shows it.
            if (ev && ev.sky_url && !fvFrames.some((f) => f.ev_id === ev.event_id)) {
                const near = fvFrames[nearestIdx(fvFrames, ev.start_ts)];
                if (!near || Math.abs(near.ts - ev.start_ts) > 4) {
                    const at = fvFrames.findIndex((f) => f.ts > ev.start_ts);
                    fvFrames.splice(at < 0 ? fvFrames.length : at, 0, { ts: ev.start_ts, url: ev.sky_url, ev_id: ev.event_id, baked: true });
                }
            }
            showFrame(nearestIdx(fvFrames, ts));
        }

        // Step one frame; at the edge of the loaded window load the next stretch and carry on
        async function stepFrame(dir) {
            const j = fvIdx + dir;
            if (j >= 0 && j < fvFrames.length) { showFrame(j); return true; }
            if (!fvFrames.length) return false;
            const edge = fvFrames[fvIdx].ts, req = ++fvReq;
            try {
                const frames = await loadSpan(dir > 0 ? edge + SPAN_AFTER / 2 : edge - SPAN_AFTER / 2);
                if (req !== fvReq || !frames.length) return false;
                const k = nearestIdx(frames, edge);
                const next = k + dir;
                if (next < 0 || next >= frames.length) return false;
                fvFrames = frames;
                showFrame(next);
                return true;
            } catch (e) {
                return false;
            }
        }

        function stopPlay() {
            if (fvTimer) clearInterval(fvTimer);
            fvTimer = null;
            fvPlay.textContent = "▶ Play";
            fvPlay.classList.remove("active");
        }
        function startPlay() {
            if (!fvFrames.length) return;
            stopPlay();
            fvPlay.textContent = "⏸ Pause";
            fvPlay.classList.add("active");
            let busy = false;
            fvTimer = setInterval(async () => {
                if (busy) return;
                busy = true;
                if (!(await stepFrame(1))) stopPlay();   // reached the newest frame
                busy = false;
            }, 1000 / Number(fvSpeed.value));
        }
        function jumpEvent(dir) {
            if (viewTs === null) return;
            const evs = cloudEvents.slice().sort((a, b) => a.start_ts - b.start_ts);
            const ev = dir > 0 ? evs.find((e) => e.start_ts > viewTs + 2) : evs.slice().reverse().find((e) => e.start_ts < viewTs - 2);
            if (ev) openFrameAt(ev.start_ts, ev);
        }
        function closeFrameViewer() {
            fvReq++;
            stopPlay();
            fv.classList.add("hidden");
            viewTs = null;
            fvEvent = null;
            fvFrames = [];
            renderCloudChart();
        }
        document.getElementById("fv-close").addEventListener("click", closeFrameViewer);
        fv.addEventListener("click", (e) => { if (e.target === fv) closeFrameViewer(); });
        fvPlay.addEventListener("click", () => (fvTimer ? stopPlay() : startPlay()));
        fvSpeed.addEventListener("change", () => { if (fvTimer) startPlay(); });
        document.getElementById("fv-prev").addEventListener("click", () => { stopPlay(); stepFrame(-1); });
        document.getElementById("fv-next").addEventListener("click", () => { stopPlay(); stepFrame(1); });
        document.getElementById("fv-prev-ev").addEventListener("click", () => jumpEvent(-1));
        document.getElementById("fv-next-ev").addEventListener("click", () => jumpEvent(1));
        document.addEventListener("keydown", (e) => {
            if (fv.classList.contains("hidden")) return;
            if (e.key === "Escape") closeFrameViewer();
            else if (e.key === " ") { e.preventDefault(); fvTimer ? stopPlay() : startPlay(); }
            else if (e.key === "ArrowLeft") { stopPlay(); stepFrame(-1); }
            else if (e.key === "ArrowRight") { stopPlay(); stepFrame(1); }
        });
        bindChart(cloudChart, mainStore, cloudTip);
        bindChart(fvChart, fvStore, document.getElementById("fv-tooltip"));

        async function loadCloudHistory() {
            try {
                const maxPoints = Math.min(1200, Math.max(100, Math.round(cloudWrap.clientWidth / 2)));
                const res = await fetch(`/api/sky/clouds/history?hours=${cloudHours}&max_points=${maxPoints}`);
                if (!res.ok) return;
                cloudData = await res.json();
                const sm = cloudData.summary || {};
                const pct = (v) => (v === null || v === undefined ? "--" : `${Number(v).toFixed(0)}%`);
                document.getElementById("cloud-mean").textContent = pct(sm.mean);
                document.getElementById("cloud-clear").textContent = pct(sm.clear_pct);
                document.getElementById("cloud-range").textContent = sm.min === null || sm.min === undefined ? "--" : `${pct(sm.min)} – ${pct(sm.max)}`;
                document.getElementById("cloud-samples").textContent = sm.samples ?? "--";
                const avgEl = document.getElementById("cloud-avg");
                if (avgEl) avgEl.textContent = pct(sm.mean);
                const avgLbl = document.getElementById("cloud-avg-label");
                if (avgLbl) { const act = document.querySelector(".cloud-ranges .btn-range.active"); avgLbl.textContent = act ? act.textContent.trim() : "24h"; }
                try {
                    const er = await fetch(`/api/events?since=${cloudData.start}&until=${cloudData.end}&limit=3000&brief=true`);
                    cloudEvents = er.ok ? ((await er.json()).events || []).filter((e) => e.type !== "clouds") : [];
                } catch (e) {
                    cloudEvents = [];
                }
                renderCloudChart();
            } catch (e) {
                /* keep the previous chart */
            }
        }
        rangeBtns.forEach((btn) => {
            btn.classList.toggle("active", Number(btn.dataset.hours) === cloudHours);
            btn.addEventListener("click", () => {
                cloudHours = Number(btn.dataset.hours);
                rangeBtns.forEach((b) => b.classList.toggle("active", b === btn));
                try {
                    localStorage.setItem("cloudHours", String(cloudHours));
                } catch (e) {
                    /* storage unavailable */
                }
                loadCloudHistory();
            });
        });
        let resizeTimer = null;
        window.addEventListener("resize", () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(renderCloudChart, 150);
        });
        loadCloudHistory();
        setInterval(() => {
            if (document.hidden) return;
            loadCloudHistory();
        }, 300000);
        document.addEventListener("visibilitychange", () => { if (!document.hidden) loadCloudHistory(); });
    }

    // ------------------------------------------------------------------
    // Cloud cover settings (sky region + learned reference)
    // ------------------------------------------------------------------
    const roiInputs = document.querySelectorAll("input[data-roi]");
    const cloudRefNote = document.getElementById("cloud-ref-note");
    const btnCloudRefReset = document.getElementById("btn-cloud-ref-reset");

    function syncCloudConfigUI(cfg) {
        if (!cfg || !cfg.sky_roi) return;
        roiInputs.forEach((inp) => {
            if (document.activeElement !== inp) inp.value = Math.round(cfg.sky_roi[Number(inp.dataset.roi)] * 100);
        });
        if (cloudRefNote && cfg.reference) {
            const r = cfg.reference;
            cloudRefNote.textContent = `Clear-sky template learned from ${r.night_samples} dark-night samples${r.learned ? "" : " (still learning)"}.`;
        }
    }

    async function postCloudConfig(body) {
        try {
            const res = await fetch("/api/sky/clouds/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            const data = await res.json();
            if (!res.ok || !data.success) throw new Error(JSON.stringify(data.detail || "Unknown error"));
            syncCloudConfigUI(data);
            return true;
        } catch (err) {
            showAlert(stationAlert, `❌ Failed to save cloud settings: ${err.message}`, "error", true);
            return false;
        }
    }

    if (roiInputs.length && currentPage === "tab-settings") {
        fetch("/api/sky/clouds/config")
            .then((res) => (res.ok ? res.json() : null))
            .then(syncCloudConfigUI)
            .catch(() => {});
        roiInputs.forEach((inp) =>
            inp.addEventListener("change", () => {
                const roi = [0, 0, 1, 1];
                roiInputs.forEach((el) => (roi[Number(el.dataset.roi)] = Number(el.value) / 100));
                if (roi.every(Number.isFinite)) postCloudConfig({ sky_roi: roi });
            })
        );
        if (btnCloudRefReset) {
            btnCloudRefReset.addEventListener("click", async () => {
                if (!confirm("Forget the learned clear-sky reference? Cloud cover is less accurate until it relearns (a few nights).")) return;
                if (await postCloudConfig({ reset_reference: true })) showAlert(stationAlert, "✅ Clear-sky reference reset.", "success", true);
            });
        }
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

    // Gallery top tabs: Products | By day | Events (same data, different views)
    const galleryContainer = document.getElementById("gallery-container");
    const galleryTabs = document.getElementById("gallery-tabs");
    const galleryDayTitle = document.getElementById("gallery-day-title");
    function setGalleryMode(mode) {
        if (!galleryContainer || !galleryTabs) return;
        galleryContainer.classList.remove("gal-mode-products", "gal-mode-day", "gal-mode-events");
        galleryContainer.classList.add(`gal-mode-${mode}`);
        galleryTabs.querySelectorAll(".gal-tab").forEach((b) => {
            b.classList.toggle("active", b.dataset.gmode === mode);
            b.setAttribute("aria-selected", b.dataset.gmode === mode ? "true" : "false");
        });
        if (galleryDayTitle) galleryDayTitle.textContent = mode === "events" ? "Events" : "By Day";
        try { localStorage.setItem("galleryMode", mode); } catch (e) { /* ignore */ }
    }
    if (galleryTabs) {
        galleryTabs.addEventListener("click", (e) => {
            const b = e.target.closest(".gal-tab");
            if (b) setGalleryMode(b.dataset.gmode);
        });
        let savedMode = "products";
        try { savedMode = localStorage.getItem("galleryMode") || "products"; } catch (e) { /* ignore */ }
        setGalleryMode(["products", "day", "events"].includes(savedMode) ? savedMode : "products");
    }

    // Timelapse builder: custom date range -> POST /api/timelapse/build
    const tlBuild = document.getElementById("tl-build");
    if (tlBuild) {
        const tlFrom = document.getElementById("tl-from");
        const tlTo = document.getElementById("tl-to");
        const tlResult = document.getElementById("tl-result");
        const todayKey = dayKeyOf(new Date());
        tlTo.value = todayKey;
        tlFrom.value = dayKeyOf(new Date(Date.now() - 86400000));
        tlBuild.addEventListener("click", async () => {
            const q = new URLSearchParams({
                date_from: tlFrom.value,
                date_to: tlTo.value,
                fps: document.getElementById("tl-fps").value || "24",
                width: document.getElementById("tl-res").value,
                overlay: document.getElementById("tl-overlay").checked ? "true" : "false",
            });
            tlBuild.disabled = true;
            tlBuild.textContent = "Building...";
            tlResult.classList.remove("hidden");
            tlResult.textContent = "Encoding frames, this can take a minute.";
            try {
                const res = await fetch(`/api/timelapse/build?${q}`, { method: "POST" });
                const d = await res.json();
                if (res.ok) {
                    const url = `${d.timelapse_url}?t=${Date.now()}`;
                    tlResult.innerHTML = `${d.video_frames_count} frames, ${d.duration_s}s @ ${d.fps} fps, ${formatSize(d.size_kb)} · <a href="${escapeHtml(url)}" download>Download</a><video controls loop muted playsinline src="${escapeHtml(url)}"></video>`;
                } else {
                    tlResult.textContent = `Build failed: ${d.detail || "unknown error"}`;
                }
            } catch (err) {
                tlResult.textContent = `Connection error: ${err.message}`;
            } finally {
                tlBuild.disabled = false;
                tlBuild.textContent = "Build timelapse";
            }
        });
    }

    async function loadGalleryAndEvents() {
        try {
            const resGal = await fetch("/api/gallery");
            if (resGal.ok) {
                const data = await resGal.json();
                renderGallery(data);
            }

            await loadEvents();
        } catch (err) {
            console.error("Failed to load gallery/events:", err);
        }
    }

    function renderGallery(data) {
        productsGrid.innerHTML = "";
        if (galleryDateEl && data && data.date) {
            galleryDateEl.textContent = `Archive: ${data.date} (${tzLabel})`;
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
            const nRejected = item.rejected ? Object.values(item.rejected).reduce((a, b) => a + b, 0) : 0;
            const framesBadge = item.frame_count !== undefined
                ? `${item.frame_count} frames` + (nRejected ? ` (${nRejected} rejected)` : "")
                : "";
            const tsFormatted = item.timestamp ? fmtLocalIso(item.timestamp) : "";

            let mediaHtml = "";
            if (item.type === "timelapse") {
                mediaHtml = `
                    <video controls autoplay loop muted playsinline preload="metadata" src="${escapeHtml(mediaUrl)}">
                        Your browser does not support MP4 video.
                    </video>
                `;
            } else {
                mediaHtml = `
                    <a href="${mediaUrl}" data-viewer="${escapeHtml(item.title || item.type)}" title="Click to view full size" style="width:100%;height:100%;display:block;">
                        <img src="${escapeHtml(mediaUrl)}" alt="${escapeHtml(item.title || item.type)}" loading="lazy" />
                    </a>
                `;
            }

            card.innerHTML = `
                <div class="product-media">
                    ${mediaHtml}
                </div>
                <div class="product-info">
                    <div class="product-title">
                        <span>${escapeHtml(item.title || item.type)}</span>
                        <span class="badge badge-accent">${sizeBadge}</span>
                    </div>
                    <div class="product-meta">
                        <span>${framesBadge}${framesBadge && tsFormatted ? " • " : ""}${tsFormatted}</span>
                        <a href="${escapeHtml(mediaUrl)}" download class="btn btn-sm btn-outline" style="padding: 0.15rem 0.5rem; font-size: 0.75rem; text-decoration: none;">⬇️ Download</a>
                    </div>
                </div>
            `;
            productsGrid.appendChild(card);
            if (item.type === "timelapse") attachVideoInfo(card.querySelector("video"), item.info_url);
        });
        galleryHistory = data.history || [];
        renderDays();
    }

    function formatSize(kb) {
        return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb.toFixed(0)} KB`;
    }

    // Gallery "By Day": one collapsible block per local day (newest first) holding that day's
    // timelapse / keogram / startrails and its sky events. Today's live products stay in the grid above.
    let galleryHistory = [];
    let eventsMeta = { total_count: 0, enabled: {} };

    function dayKeyOf(date) {
        const p = (n) => String(n).padStart(2, "0");
        const sd = stationDate(date);
        return `${sd.getUTCFullYear()}-${p(sd.getUTCMonth() + 1)}-${p(sd.getUTCDate())}`;
    }

    function renderDays() {
        if (!dayList) return;
        const todayKey = dayKeyOf(new Date());
        const byDay = new Map();
        const ensure = (key) => {
            if (!byDay.has(key)) byDay.set(key, { date: key, product: null, events: [] });
            return byDay.get(key);
        };
        ensure(todayKey);
        galleryHistory.forEach((d) => { ensure(d.date).product = d; });
        tableEvents.forEach((ev, i) => ensure(dayKeyOf(new Date(ev.timestamp))).events.push(i));

        const days = [...byDay.values()].sort((a, b) => (a.date < b.date ? 1 : -1));
        if (dayCountBadge) dayCountBadge.textContent = `${days.length} ${days.length === 1 ? "Day" : "Days"}`;

        let notice = "";
        if (tableEvents.length === 0) {
            const on = Object.entries(eventsMeta.enabled || {}).filter(([, v]) => v).map(([k]) => EVENT_LABELS[k] || k);
            notice = `No events yet. Detecting: ${on.join(", ") || "nothing (turn types on in Settings)"}.`;
        } else if (eventsMeta.total_count > tableEvents.length) {
            notice = `Showing the newest ${tableEvents.length} of ${eventsMeta.total_count} events.`;
        }
        dayList.innerHTML = (notice ? `<p class="text-muted day-notice">${notice}</p>` : "")
            + days.map((day, n) => dayBlockHtml(day, todayKey, n === 0)).join("");
        dayList.querySelectorAll("video[data-info]").forEach((v) => attachVideoInfo(v, v.dataset.info));
    }

    function dayBlockHtml(day, todayKey, first) {
        const isToday = day.date === todayKey;
        const p = day.product || {};
        const evs = day.events;
        const tl = p.timelapse;

        let products = "";
        if (!isToday) {
            const tiles = [];
            if (tl) tiles.push(`<video controls loop muted playsinline preload="metadata" src="${escapeHtml(tl.url)}#t=0.5" data-info="${escapeHtml(tl.url.replace(/\.mp4$/, ".json"))}"></video>`);
            [["keogram", "Keogram"], ["startrails", "Startrails"]].forEach(([k, label]) => {
                if (p[k]) tiles.push(`<a class="day-tile" href="${escapeHtml(p[k].url)}" data-viewer="${label} — ${escapeHtml(day.date)}" title="View ${label}"><img src="${escapeHtml(p[k].url)}" alt="${label} ${escapeHtml(day.date)}" loading="lazy" /><span>${label}</span></a>`);
            });
            if (tiles.length) {
                products = `<div class="day-products">${tiles.join("")}</div>`;
                if (tl) products = `<div class="day-actions"><a href="${escapeHtml(tl.url)}" download class="btn btn-sm btn-outline">⬇️ Video · ${formatSize(tl.size_kb)}</a></div>` + products;
            }
        }

        const eventsHtml = evs.length
            ? `<div class="events-table-wrapper"><table class="events-table">
                    <thead><tr><th>Image</th><th>Type</th><th>Time</th><th>Details</th><th>Whole sky</th></tr></thead>
                    <tbody>${evs.map((i) => eventRowHtml(tableEvents[i], i)).join("")}</tbody>
                </table></div>`
            : `<p class="text-muted day-empty">No sky events.</p>`;

        return `
            <details class="day-block" ${first ? "open" : ""}>
                <summary class="day-summary">
                    <span class="day-date">${escapeHtml(day.date)}</span>
                    ${isToday ? `<span class="badge badge-accent">Today</span>` : ""}
                    <span class="badge">${evs.length} ${evs.length === 1 ? "event" : "events"}</span>
                    ${tl ? `<span class="text-muted day-meta">${tl.size_kb ? formatSize(tl.size_kb) + " video" : ""}</span>` : ""}
                </summary>
                <div class="day-body">${products}${eventsHtml}</div>
            </details>`;
    }

    // In-page image viewer for keograms / startrails (Esc, click or ✕ to close)
    const viewer = document.createElement("div");
    viewer.className = "image-viewer hidden";
    viewer.innerHTML = `
        <button class="image-viewer-close" title="Close (Esc)">✕</button>
        <img alt="" />
        <div class="image-viewer-caption"></div>
    `;
    document.body.appendChild(viewer);
    const viewerImg = viewer.querySelector("img");
    const viewerCaption = viewer.querySelector(".image-viewer-caption");

    function closeViewer() {
        viewer.classList.add("hidden");
        viewerImg.removeAttribute("src");
    }

    document.addEventListener("click", (e) => {
        const link = e.target.closest("a[data-viewer]");
        if (link) {
            if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return; // keep open-in-new-tab
            e.preventDefault();
            viewerImg.src = link.getAttribute("href");
            viewerCaption.textContent = link.dataset.viewer;
            viewer.classList.remove("hidden");
        } else if (!viewer.classList.contains("hidden") && viewer.contains(e.target)) {
            closeViewer();
        }
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !viewer.classList.contains("hidden")) closeViewer();
    });

    const EVENT_LABELS = {
        meteor: "☄️ Meteor", plane: "✈️ Aircraft", satellite: "🛰️ Satellite", clouds: "☁️ Clouds", flash: "⚡ Flash",
    };

    function eventDetails(ev) {
        if (ev.type === "clouds") return `Sky turned ${escapeHtml(ev.state)} (${Number(ev.cloud_cover_pct).toFixed(0)}% cloud)`;
        if (ev.type === "flash") return `Sky brightness ${escapeHtml(ev.sky_mean_before)} → ${escapeHtml(ev.sky_mean)}`;
        const parts = [`${Number(ev.length_px || 0).toFixed(0)} px`];
        if (ev.frames > 1) parts.push(`${ev.frames} frames`);
        if (ev.dashes > 1) parts.push(`${ev.dashes} dashes`);
        if (ev.peak_dn) parts.push(`peak +${ev.peak_dn} DN`);
        return parts.join(" · ");
    }

    function escapeHtml(v) {
        return String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    // Flight named by ADS-B (callsign, altitude, distance), linked to a public flight tracker
    function flightHtml(ev) {
        const fl = ev.flight;
        if (!fl) return "";
        const name = escapeHtml(fl.callsign || fl.icao);
        const url = `https://globe.adsbexchange.com/?icao=${encodeURIComponent(String(fl.icao).toLowerCase())}`;
        return `<a class="flight-tag" href="${url}" target="_blank" rel="noopener" title="ICAO ${escapeHtml(fl.icao)} · az ${escapeHtml(fl.az_deg)}° alt ${escapeHtml(fl.alt_deg)}° · match ${escapeHtml(fl.match_px)} px">✈ ${name}</a>`
            + ` <span class="flight-meta">${escapeHtml(Number(fl.altitude_m).toLocaleString())} m · ${escapeHtml(fl.distance_km)} km</span>`;
    }

    const EVENT_COLORS = { meteor: "#f59e0b", plane: "#38bdf8", satellite: "#a78bfa", clouds: "#94a3b8", flash: "#facc15" };

    function eventDuration(ev) {
        const s = Number(ev.end_ts) - Number(ev.start_ts);
        return Number.isFinite(s) && s > 0.5 ? s : 0;
    }

    function fmtSeconds(s) {
        return s >= 90 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${s.toFixed(s < 10 ? 1 : 0)} s`;
    }

    // One sentence a non-astronomer understands
    function eventSummary(ev) {
        const frames = Number(ev.frames || 1);
        const dur = eventDuration(ev);
        const fl = ev.flight;
        switch (ev.type) {
            case "meteor":
                return "A bright streak appeared in a single frame and was gone in the next: the signature of a meteor (shooting star). "
                    + "Car lights, lit windows and glints on the lens can look similar, so check the close-up.";
            case "plane": {
                let t = ev.moving_dot
                    ? `A light moved steadily across the sky for ${frames} frames${dur ? ` (${fmtSeconds(dur)})` : ""}: an aircraft.`
                    : frames > 1 ? `A streak continued along the same line over ${frames} frames: an aircraft.`
                        : "A dashed streak (blinking navigation lights): an aircraft.";
                if (ev.dashes > 1 && ev.moving_dot) t += " Its lights blinked on the way.";
                if (fl) t += ` Identified from ADS-B as flight ${fl.callsign || fl.icao}, ${Number(fl.altitude_m).toLocaleString()} m up and ${fl.distance_km} km away.`;
                if (ev.reclassified_from) t += ` (First detected as a ${ev.reclassified_from}.)`;
                return t;
            }
            case "satellite":
                return `A faint, steady light crossed the sky over ${frames} frames${dur ? ` (${fmtSeconds(dur)})` : ""} without blinking: most likely a satellite catching sunlight.`;
            case "flash":
                return `The whole sky brightened at once (${ev.sky_mean_before} → ${ev.sky_mean}): lightning, or a light shining on the camera.`;
            case "clouds":
                return ev.state === "cloudy"
                    ? `Clouds moved in: ${Number(ev.cloud_cover_pct).toFixed(0)}% of the sky is now covered.`
                    : `The sky cleared: cloud cover dropped to ${Number(ev.cloud_cover_pct).toFixed(0)}%.`;
            default:
                return "";
        }
    }

    function eventFacts(ev) {
        const facts = [];
        const dur = eventDuration(ev);
        if (ev.type === "clouds") {
            facts.push(["Cloud cover", `${Number(ev.cloud_cover_pct).toFixed(0)}%`]);
        } else if (ev.type === "flash") {
            facts.push(["Sky brightness", `${ev.sky_mean_before} → ${ev.sky_mean}`]);
        } else {
            facts.push(["Seen in", `${ev.frames || 1} frame${(ev.frames || 1) > 1 ? "s" : ""}`]);
            if (dur) facts.push(["Duration", fmtSeconds(dur)]);
            if (ev.length_px) facts.push([ev.moving_dot ? "Path length" : "Streak length", `${Number(ev.length_px).toFixed(0)} px`]);
            if (ev.speed_px_s) facts.push(["Speed", `${Number(ev.speed_px_s).toFixed(0)} px/s`]);
            if (ev.peak_dn) facts.push(["Brightness", `+${ev.peak_dn} above the sky`]);
            if (ev.dashes > 1) facts.push([ev.moving_dot ? "Blinks" : "Dashes", String(ev.dashes)]);
        }
        if (ev.sky_from && ev.sky_to) {
            const d = (p) => `${COMPASS[Math.round(((p.az_deg % 360) + 360) % 360 / 45) % 8]} ${Math.round(p.alt_deg)}° up`;
            facts.push(["Where in the sky", ev.frames > 1 ? `${d(ev.sky_from)} → ${d(ev.sky_to)}` : d(ev.sky_from)]);
        }
        const fl = ev.flight;
        if (fl) {
            facts.push(["Flight", flightHtml(ev), true]); // true = already-escaped HTML
            facts.push(["Altitude", `${Number(fl.altitude_m).toLocaleString()} m`]);
            if (fl.speed_kmh != null) facts.push(["Ground speed", `${fl.speed_kmh} km/h`]);
            if (fl.heading_deg != null) facts.push(["Heading", `${fl.heading_deg}° (${COMPASS[Math.round(fl.heading_deg / 45) % 8]})`]);
            if (fl.climb_m_min != null && Math.abs(fl.climb_m_min) > 60) facts.push([fl.climb_m_min > 0 ? "Climbing" : "Descending", `${Math.abs(fl.climb_m_min)} m/min`]);
            facts.push(["Distance", `${fl.distance_km} km`]);
            facts.push(["Direction", `az ${fl.az_deg}°, ${fl.alt_deg}° above horizon`]);
        }
        return facts;
    }

    const eventViewer = document.createElement("div");
    eventViewer.className = "event-viewer hidden";
    eventViewer.setAttribute("role", "dialog");
    eventViewer.setAttribute("aria-modal", "true");
    eventViewer.innerHTML = `
        <div class="ev-panel">
            <header class="ev-head">
                <span class="ev-pill"></span>
                <div class="ev-when"><strong></strong><span></span></div>
                <div class="ev-nav">
                    <button type="button" class="ev-prev" title="Newer event (←)">‹</button>
                    <span class="ev-pos"></span>
                    <button type="button" class="ev-next" title="Older event (→)">›</button>
                    <button type="button" class="ev-close" title="Close (Esc)">✕</button>
                </div>
            </header>
            <div class="ev-images">
                <figure class="ev-fig ev-fig-crop"><img alt="Close-up" /><figcaption>Close-up</figcaption></figure>
                <figure class="ev-fig ev-fig-sky"><img alt="Whole sky" /><figcaption>Whole sky, event boxed</figcaption></figure>
            </div>
            <p class="ev-summary"></p>
            <dl class="ev-facts"></dl>
            <div class="ev-actions">
                <a class="btn btn-sm btn-outline ev-open-crop" target="_blank" rel="noopener">Open close-up ↗</a>
                <a class="btn btn-sm btn-outline ev-open-sky" target="_blank" rel="noopener">Open whole sky ↗</a>
            </div>
        </div>`;
    document.body.appendChild(eventViewer);
    const evQ = (sel) => eventViewer.querySelector(sel);
    let evList = [];
    let evIndex = 0;

    function showEvent(i) {
        if (!evList.length) return;
        evIndex = Math.max(0, Math.min(evList.length - 1, i));
        const ev = evList[evIndex];
        const when = new Date(ev.timestamp);
        const pill = evQ(".ev-pill");
        pill.textContent = EVENT_LABELS[ev.type] || ev.type;
        pill.style.setProperty("--pill", EVENT_COLORS[ev.type] || "var(--accent)");
        evQ(".ev-when strong").textContent = `${fmtLocalIso(ev.timestamp)} ${tzLabel}`;
        evQ(".ev-when span").textContent = timeAgo(when);
        evQ(".ev-pos").textContent = `${evIndex + 1} / ${evList.length}`;
        evQ(".ev-prev").disabled = evIndex === 0;
        evQ(".ev-next").disabled = evIndex === evList.length - 1;
        const crop = ev.sky_url && ev.thumbnail_url !== ev.sky_url ? ev.thumbnail_url : null;
        const figCrop = evQ(".ev-fig-crop"), figSky = evQ(".ev-fig-sky");
        figCrop.hidden = !crop;
        figSky.hidden = !ev.sky_url;
        if (crop) figCrop.querySelector("img").src = crop;
        if (ev.sky_url) figSky.querySelector("img").src = ev.sky_url;
        figSky.querySelector("figcaption").textContent = ev.moving_dot ? "Whole sky, path marked" : (crop ? "Whole sky, event boxed" : "Whole sky");
        evQ(".ev-images").classList.toggle("single", !crop || !ev.sky_url);
        evQ(".ev-summary").textContent = eventSummary(ev);
        evQ(".ev-facts").innerHTML = eventFacts(ev).map(([k, v, isHtml]) => `<div><dt>${escapeHtml(k)}</dt><dd>${isHtml ? v : escapeHtml(v)}</dd></div>`).join("");
        const oc = evQ(".ev-open-crop"), os = evQ(".ev-open-sky");
        oc.hidden = !crop; os.hidden = !ev.sky_url;
        if (crop) oc.href = crop;
        if (ev.sky_url) os.href = ev.sky_url;
    }

    function openEventViewer(list, i) {
        evList = list || [];
        if (!evList.length) return;
        showEvent(i);
        eventViewer.classList.remove("hidden");
        document.body.classList.add("no-scroll");
        evQ(".ev-close").focus({ preventScroll: true });
    }

    function closeEventViewer() {
        eventViewer.classList.add("hidden");
        document.body.classList.remove("no-scroll");
        eventViewer.querySelectorAll("img").forEach((im) => im.removeAttribute("src"));
    }

    eventViewer.addEventListener("click", (e) => {
        if (e.target === eventViewer || e.target.closest(".ev-close")) closeEventViewer();
        else if (e.target.closest(".ev-prev")) showEvent(evIndex - 1);
        else if (e.target.closest(".ev-next")) showEvent(evIndex + 1);
    });
    document.addEventListener("keydown", (e) => {
        if (eventViewer.classList.contains("hidden")) return;
        if (e.key === "Escape") closeEventViewer();
        else if (e.key === "ArrowLeft") showEvent(evIndex - 1);
        else if (e.key === "ArrowRight") showEvent(evIndex + 1);
    });
    // Swipe left/right on phones
    let evTouchX = null;
    eventViewer.addEventListener("touchstart", (e) => { evTouchX = e.touches[0].clientX; }, { passive: true });
    eventViewer.addEventListener("touchend", (e) => {
        if (evTouchX === null) return;
        const dx = e.changedTouches[0].clientX - evTouchX;
        evTouchX = null;
        if (Math.abs(dx) > 60) showEvent(evIndex + (dx < 0 ? 1 : -1));
    });

    let tableEvents = [];
    let liveEventsData = [];

    function eventRowHtml(ev, i) {
        const when = new Date(ev.timestamp);
        const thumb = ev.thumbnail_url
            ? `<img class="event-thumb" src="${escapeHtml(ev.thumbnail_url)}" alt="${escapeHtml(ev.type)}" loading="lazy" />`
            : "--";
        return `
            <tr class="event-row" data-event-idx="${i}" title="Click for details">
                <td>${thumb}</td>
                <td><span class="ev-pill" style="--pill:${EVENT_COLORS[ev.type] || "var(--accent)"}">${escapeHtml(EVENT_LABELS[ev.type] || ev.type)}</span></td>
                <td title="${escapeHtml(ev.timestamp)}">${fmtLocalIso(ev.timestamp, true)} ${tzLabel}<br><span class="text-muted">${timeAgo(when)}</span></td>
                <td>${eventDetails(ev)}${ev.flight ? `<br>${flightHtml(ev)}` : ""}</td>
                <td><button type="button" class="btn btn-sm btn-outline">Details</button></td>
            </tr>`;
    }

    function renderEvents(evData) {
        tableEvents = evData.events || [];
        eventsMeta = { total_count: evData.total_count || 0, enabled: evData.enabled || {} };
        renderDays();
    }

    dayList.addEventListener("click", (e) => {
        if (e.target.closest("a")) return; // flight link
        const tr = e.target.closest("tr[data-event-idx]");
        if (tr) openEventViewer(tableEvents, Number(tr.dataset.eventIdx));
    });

    let eventsFilterType = "";
    const eventsFilter = document.getElementById("events-filter");
    if (eventsFilter) {
        eventsFilter.addEventListener("click", async (e) => {
            const btn = e.target.closest("button[data-type]");
            if (!btn) return;
            eventsFilterType = btn.dataset.type;
            eventsFilter.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
            await loadEvents();
        });
    }

    async function loadEvents() {
        const q = eventsFilterType ? `&type=${eventsFilterType}` : "";
        const res = await fetch(`/api/events?limit=100${q}`); // API max; By Day groups these by local date
        if (res.ok) renderEvents(await res.json());
    }

    // Live page: feed of recent sky events on the left, refreshed every 20 s
    const liveEventsList = document.getElementById("live-events-list");
    const liveEventsStatus = document.getElementById("live-events-status");
    const liveEventsCount = document.getElementById("live-events-count");
    const liveEventsSeen = new Set();
    let liveEventsFirst = true;

    function timeAgo(date) {
        const s = Math.max(0, (Date.now() - date.getTime()) / 1000);
        if (s < 60) return "just now";
        if (s < 3600) return `${Math.floor(s / 60)} min ago`;
        if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
        return date.toLocaleDateString();
    }

    function renderLiveEvents(evData) {
        const events = evData.events || [];
        // "Tonight": since noon today, or since noon yesterday before noon
        const since = new Date();
        if (since.getHours() < 12) since.setDate(since.getDate() - 1);
        since.setHours(12, 0, 0, 0);
        const tonight = events.filter((ev) => new Date(ev.timestamp) >= since).length;
        liveEventsCount.textContent = `${tonight} tonight`;

        const on = Object.entries(evData.enabled || {}).filter(([, v]) => v).map(([k]) => EVENT_LABELS[k] || k);
        const night = evData.night;
        liveEventsStatus.innerHTML = on.length
            ? `<span class="dot ${night ? "on" : ""}"></span>${night ? "Watching" : "Paused until dark"} · ${on.map((l) => l.split(" ")[0]).join(" ")}`
            : `<span class="dot"></span>Detection off (Settings → Event detection)`;

        if (!events.length) {
            liveEventsList.innerHTML = `<li class="live-events-empty">No events yet.<br>Meteors and aircraft show up here as soon as they are detected.</li>`;
            return;
        }
        liveEventsList.innerHTML = "";
        liveEventsData = events;
        events.forEach((ev, i) => {
            const when = new Date(ev.timestamp);
            const li = document.createElement("li");
            li.className = "live-event" + (!liveEventsFirst && !liveEventsSeen.has(ev.event_id) ? " fresh" : "");
            li.dataset.eventIdx = i;
            li.tabIndex = 0;
            li.setAttribute("role", "button");
            li.title = "Click for details";
            li.style.setProperty("--pill", EVENT_COLORS[ev.type] || "var(--accent)");
            liveEventsSeen.add(ev.event_id);
            const label = EVENT_LABELS[ev.type] || ev.type;
            const thumb = ev.thumbnail_url
                ? `<img src="${escapeHtml(ev.thumbnail_url)}" alt="${escapeHtml(ev.type)}" loading="lazy" />`
                : `<span class="no-thumb"></span>`;
            li.innerHTML = `${thumb}<div>
                <div class="ev-type">${escapeHtml(label)}</div>
                <div class="ev-time" title="${fmtLocalIso(ev.timestamp)} ${tzLabel}">${fmtLocalIso(ev.timestamp, true)} · ${timeAgo(when)}</div>
                <div class="ev-detail">${ev.flight ? flightHtml(ev) : eventDetails(ev)}</div></div>`;
            liveEventsList.appendChild(li);
        });
        liveEventsFirst = false;
    }

    if (liveEventsList) {
        const openLive = (e) => {
            if (e.target.closest("a")) return;
            const li = e.target.closest("li[data-event-idx]");
            if (!li) return;
            const ev = liveEventsData[Number(li.dataset.eventIdx)];
            if (window.openFrameViewerAt && ev && ev.start_ts) window.openFrameViewerAt(ev.start_ts, ev.event_id);
            else openEventViewer(liveEventsData, Number(li.dataset.eventIdx));
        };
        liveEventsList.addEventListener("click", openLive);
        liveEventsList.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openLive(e); } });
    }

    async function loadLiveEvents() {
        try {
            const [evRes, cfgRes] = await Promise.all([fetch("/api/events?limit=40"), fetch("/api/events/config")]);
            if (!evRes.ok) return;
            const data = await evRes.json();
            if (cfgRes.ok) {
                const cfg = await cfgRes.json();
                data.enabled = cfg.enabled;
                data.night = cfg.night;
            }
            renderLiveEvents(data);
        } catch (err) {
            liveEventsStatus.textContent = "Events unavailable";
        }
    }

    // Live page: aircraft heard by the ADS-B receiver, nearest in-view first, refreshed every 10 s
    const flightsCard = document.getElementById("flights-card");
    const flightsList = document.getElementById("flights-list");
    const flightsStatus = document.getElementById("flights-status");
    const flightsCount = document.getElementById("flights-count");
    const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    const compass = (az) => COMPASS[Math.round(((az % 360) + 360) % 360 / 45) % 8];

    function renderFlights(d) {
        if (!d || !d.enabled) { flightsCard.hidden = true; return; }
        flightsCard.hidden = false;
        const list = d.aircraft || [];
        flightsCount.textContent = `${d.in_view || 0} in view · ${d.total || 0} heard`;
        flightsStatus.textContent = d.connected ? "Live from the ADS-B receiver" : "Receiver not connected";
        if (!list.length) {
            flightsList.innerHTML = `<li class="live-events-empty">No aircraft heard right now.</li>`;
            return;
        }
        flightsList.innerHTML = list.map((a) => {
            const name = escapeHtml(a.callsign || a.icao);
            const url = `https://globe.adsbexchange.com/?icao=${encodeURIComponent(String(a.icao).toLowerCase())}`;
            const climb = a.climb_m_min == null ? "" : a.climb_m_min > 60 ? " ↗" : a.climb_m_min < -60 ? " ↘" : "";
            const where = a.in_view ? `<span class="fl-inview">● in view</span>`
                : a.above_horizon ? `${compass(a.az_deg)} ${Math.round(a.alt_deg)}° up` : "below horizon";
            const hdg = a.heading_deg == null ? "" : `<span class="fl-hdg" style="transform:rotate(${a.heading_deg}deg)" title="Heading ${a.heading_deg}°">↑</span>`;
            return `<li class="flight${a.in_view ? " in-view" : ""}">
                <div class="fl-top"><a href="${url}" target="_blank" rel="noopener" class="flight-tag">✈ ${name}</a>${hdg}<span class="fl-where">${where}</span></div>
                <div class="fl-meta">
                    <span title="Altitude">${escapeHtml(Number(a.altitude_m).toLocaleString())} m${climb}</span>
                    <span title="Ground speed">${a.speed_kmh != null ? `${escapeHtml(a.speed_kmh)} km/h` : "-- km/h"}</span>
                    <span title="Distance from the camera">${escapeHtml(a.distance_km)} km</span>
                </div></li>`;
        }).join("");
    }

    async function loadFlights() {
        try {
            const res = await fetch("/api/adsb/aircraft?limit=12");
            if (res.ok) renderFlights(await res.json());
        } catch (err) { /* optional feature */ }
    }

    // Live page: radiosondes (weather balloons) near the station, refreshed every 20 s
    const sondesCard = document.getElementById("sondes-card");
    function renderSondes(d) {
        const list = (d && d.sondes) || [];
        if (!d || (!list.length && d.error)) { sondesCard.hidden = true; return; }
        sondesCard.hidden = false;
        document.getElementById("sondes-count").textContent = `${d.in_view || 0} in view · ${d.total || 0} heard`;
        document.getElementById("sondes-status").textContent = "Live from SondeHub (within 300 km)";
        const ul = document.getElementById("sondes-list");
        if (!list.length) { ul.innerHTML = `<li class="live-events-empty">No radiosonde flying nearby right now.</li>`; return; }
        ul.innerHTML = list.map((a) => {
            const where = a.in_view ? `<span class="fl-inview">● in view</span>`
                : a.above_horizon ? `${compass(a.az_deg)} ${Math.round(a.alt_deg)}° up` : "below horizon";
            const dir = a.climb_m_s > 1 ? " ↗" : a.climb_m_s < -1 ? " ↘" : "";
            return `<li class="flight${a.in_view ? " in-view" : ""}">
                <div class="fl-top"><a href="https://sondehub.org/${encodeURIComponent(a.serial)}" target="_blank" rel="noopener" class="flight-tag">🎈 ${escapeHtml(a.serial)}</a><span class="fl-where">${where}</span></div>
                <div class="fl-meta">
                    <span title="Altitude">${escapeHtml(Number(a.altitude_m).toLocaleString())} m${dir}</span>
                    <span title="${escapeHtml(a.type || "")}">${a.freq_mhz ? escapeHtml(a.freq_mhz) + " MHz" : escapeHtml(a.type || "")}</span>
                    <span title="Distance from the camera">${escapeHtml(a.distance_km)} km</span>
                </div></li>`;
        }).join("");
    }
    async function loadSondes() {
        try {
            const res = await fetch("/api/radiosondes?limit=8");
            if (res.ok) renderSondes(await res.json());
        } catch (err) { /* optional feature */ }
    }
    if (currentPage === "tab-live" && sondesCard) {
        loadSondes();
        setInterval(() => { if (!document.hidden) loadSondes(); }, 20000);
    }

    if (currentPage === "tab-live" && flightsCard) {
        loadFlights();
        setInterval(() => { if (!document.hidden) loadFlights(); }, 10000);
    }

    if (currentPage === "tab-live" && liveEventsList) {
        loadLiveEvents();
        setInterval(() => { if (!document.hidden) loadLiveEvents(); }, 20000);
        document.addEventListener("visibilitychange", () => { if (!document.hidden) loadLiveEvents(); });
    }

    // Settings → Event detection
    const eventToggles = [...document.querySelectorAll("input[data-event]")];

    function syncEventConfigUI(data) {
        if (!data || !data.enabled) return;
        eventToggles.forEach((el) => { el.checked = !!data.enabled[el.dataset.event]; });
        const st = document.getElementById("evt-status");
        if (st) {
            st.textContent = data.night
                ? `Watching now: ${data.frames_compared} of ${data.frames_seen} frames compared tonight.`
                : "Daytime: streak detection is idle.";
        }
    }

    async function loadEventConfig() {
        if (!eventToggles.length) return;
        try {
            const res = await fetch("/api/events/config");
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            syncEventConfigUI(await res.json());
        } catch (err) {
            showAlert(stationAlert, `❌ Could not load event detection settings: ${err.message}`, "error", true);
        }
    }

    eventToggles.forEach((el) => {
        el.addEventListener("change", async () => {
            try {
                const res = await fetch("/api/events/config", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ [el.dataset.event]: el.checked }),
                });
                const data = await res.json();
                if (!res.ok || !data.success) throw new Error(JSON.stringify(data.detail || "Unknown error"));
                syncEventConfigUI(data);
            } catch (err) {
                showAlert(stationAlert, `❌ Failed to save event detection: ${err.message}`, "error", true);
                loadEventConfig();
            }
        });
    });
    if (currentPage === "tab-settings") loadEventConfig();

    // Settings → ADS-B flight matching
    const adsbHost = document.getElementById("adsb-host");
    const adsbPort = document.getElementById("adsb-port");
    const adsbEnabled = document.getElementById("adsb-enabled");
    const adsbStatus = document.getElementById("adsb-status");

    function syncAdsbUI(d) {
        if (!d || !adsbHost) return;
        adsbHost.value = d.host || "";
        adsbPort.value = d.port || 30003;
        adsbEnabled.checked = !!d.enabled;
        const st = d.status;
        let txt = !d.enabled ? "Off." : !st ? "Starting…"
            : st.connected ? `Connected: ${st.with_position} aircraft with position.`
            : st.last_error ? `Not connected: ${st.last_error}` : "Idle (reads the feed only at night with aircraft detection on).";
        if (d.enabled && !d.pointing_solved) txt += " Needs a star-solved camera pointing (plate solve) to match flights.";
        adsbStatus.textContent = txt;
    }

    async function loadAdsb() {
        try {
            const res = await fetch("/api/adsb");
            if (res.ok) syncAdsbUI(await res.json());
        } catch (err) { /* optional feature */ }
    }

    const adsbSave = document.getElementById("btn-adsb-save");
    if (adsbSave) {
        adsbSave.addEventListener("click", async () => {
            try {
                const res = await fetch("/api/adsb", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ host: adsbHost.value.trim(), port: Number(adsbPort.value) || 30003, enabled: adsbEnabled.checked }),
                });
                const data = await res.json();
                if (!res.ok || !data.success) throw new Error(JSON.stringify(data.detail || "Unknown error"));
                syncAdsbUI(data);
                setTimeout(loadAdsb, 3000);
            } catch (err) {
                showAlert(stationAlert, `❌ Failed to save ADS-B settings: ${err.message}`, "error", true);
            }
        });
    }
    if (currentPage === "tab-settings") loadAdsb();
});
