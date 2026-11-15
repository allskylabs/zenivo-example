/**
 * Focus assistant page (/focus).
 * Streams a native-resolution crop from /api/focus/frame as fast as the camera delivers frames
 * (long-poll: each request waits for the next capture), draws it zoomed without smoothing,
 * and plots the focus score (HFR for stars/lights, edge contrast for daytime) against its best value.
 */
(function () {
    "use strict";

    const onFocusPage = (window.location.pathname.replace(/\/+$/, "") || "/") === "/focus";

    const esc = (v) => String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    // Same rule as app.js (fmtLocalIso + tzLabel): local wall-clock time with a zone label, "YYYY-MM-DD HH:MM[:SS] UTC+7"
    function fmtWhen(t, timeOnly, noSec) {
        const d = new Date(t);
        if (isNaN(d)) return "—";
        const p = (n) => String(n).padStart(2, "0");
        const hm = `${p(d.getHours())}:${p(d.getMinutes())}` + (noSec ? "" : `:${p(d.getSeconds())}`);
        const m = -d.getTimezoneOffset(), a = Math.abs(m);
        const tz = `UTC${m >= 0 ? "+" : "-"}${Math.floor(a / 60)}${a % 60 ? ":" + String(a % 60).padStart(2, "0") : ""}`;
        return `${timeOnly ? "" : `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `}${hm} ${tz}`;
    }

    // Saved focus results (station config focus_history), shown on the focus page and in Settings
    function fmtRecScore(r) {
        if (r.score === null || r.score === undefined) return "—";
        return r.unit === "px HFR" ? `${r.score.toFixed(2)} px` : r.score.toPrecision(4);
    }
    function fmtRecLens(r) {
        if (r.lens_position === null || r.lens_position === undefined) return "—";
        return `${r.lens_position.toFixed(3)} D` + (r.lens_distance_m ? ` (≈${r.lens_distance_m} m)` : " (∞)");
    }
    function fmtExp(e) {
        if (!e) return "—";
        return e < 0.1 ? `${(e * 1000).toFixed(1)} ms` : `${e.toFixed(2)} s`;
    }
    function renderHistory(el, records, onUse) {
        if (!el) return;
        if (!records || !records.length) { el.innerHTML = '<span class="text-muted">No focus saved yet.</span>'; return; }
        const rows = records.slice().reverse().map((r, i) => {
            const idx = records.length - 1 - i;
            const near = r.near || (r.lens_position || 0) > 1;
            return `<tr class="${near ? "near" : i === 0 ? "latest" : ""}" title="${near ? "Focused nearer than 1 m: not the sky" : ""}">
                <td>${fmtWhen(r.finished_at, false, true)}</td>
                <td>${esc(r.source)}</td><td>${esc(r.method || "—")}</td>
                <td class="num">${fmtRecScore(r)}</td><td class="num">${fmtRecLens(r)}</td>
                <td class="num">${r.sensor_temp_c ?? "—"}${r.sensor_temp_c != null ? " °C" : ""}</td>
                <td class="num">${fmtExp(r.exposure_s)} · ${(r.gain_db ?? 0).toFixed(0)} dB</td>
                <td class="num">${r.roi ? `${r.roi.size}px @ ${(r.roi.cx * 100).toFixed(0)},${(r.roi.cy * 100).toFixed(0)}%` : "—"}</td>
                <td>${near ? '<span class="text-warning">near</span>' : onUse ? `<button class="btn btn-sm btn-outline" data-use="${idx - records.length}" title="Reuse this lens position, zoom region, measure and exposure">Use</button>` : ""}</td>
            </tr>`;
        }).join("");
        el.innerHTML = `<table><thead><tr><th>When</th><th>How</th><th>Measure</th><th>Score</th><th>Lens</th><th>Temp</th><th>Exposure</th><th>Zoom</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
        if (onUse) el.querySelectorAll("[data-use]").forEach((b) => b.addEventListener("click", () => onUse(parseInt(b.dataset.use, 10))));
    }
    async function useRecord(index) {
        const r = await fetch(`/api/focus/apply-record?index=${index}`, { method: "POST" });
        if (!r.ok) { alert(`Use saved focus: HTTP ${r.status}`); return null; }
        return r.json();
    }

    if (!onFocusPage) {
        // One in-flight GET /api/focus shared by the history list and the banner check (both run at load)
        let focusInflight = null;
        const getFocusState = () => {
            if (!focusInflight) {
                // Focus state is admin-only: guests do not ask (the server would answer 401)
                focusInflight = (window.zenivoAuth && window.zenivoAuth.admin
                    ? fetch("/api/focus", { cache: "no-store" }).then((res) => (res.ok ? res.json() : null))
                    : Promise.resolve(null))
                    .finally(() => { focusInflight = null; });
            }
            return focusInflight;
        };
        const settingsHist = document.getElementById("settings-focus-history");
        if (settingsHist && (window.location.pathname.replace(/\/+$/, "") || "/") === "/settings") {
            const load = async () => {
                try {
                    const s = await getFocusState();
                    if (!s) return;
                    renderHistory(settingsHist, s.history_records, async (i) => { if (await useRecord(i)) load(); });
                } catch (e) { /* offline */ }
            };
            load();
        }
        // Other pages: warn while focus mode holds the exposure (auto-exposure and archiving are paused)
        const bar = document.createElement("div");
        bar.className = "focus-banner";
        bar.hidden = true;
        bar.innerHTML = 'Focus mode is on: auto-exposure and archiving are paused. <a href="/focus">Open focus</a> <button class="btn btn-sm btn-outline">Stop focus mode</button>';
        const nav = document.querySelector(".app-main > .navbar") || document.querySelector(".tab-bar");
        if (nav) nav.after(bar);
        bar.querySelector("button").addEventListener("click", async () => {
            const btn = bar.querySelector("button");
            try {
                const res = await fetch("/api/focus/stop", { method: "POST" });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                bar.hidden = true;
            } catch (e) {
                btn.textContent = `Stop failed (${e.message}), retry`;
                setTimeout(() => { btn.textContent = "Stop focus mode"; }, 5000);
            }
        });
        const check = async () => {
            try {
                const s = await getFocusState();
                if (!s) return;
                if (typeof s.active !== "boolean") return;
                bar.hidden = !s.active;
            } catch (e) { /* offline: keep the last state */ }
        };
        check();
        setInterval(check, 20000);
        return;
    }

    const $ = (id) => document.getElementById(id);
    const setTxt = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
    const canvas = $("focus-zoom-canvas");
    const ctx = canvas.getContext("2d");
    const wrap = $("focus-zoom-wrap");
    const emptyMsg = $("focus-zoom-empty");
    const lensChart = $("focus-lens-chart");
    const lensChartCtx = lensChart ? lensChart.getContext("2d") : null;
    const timeChart = $("focus-time-chart");
    const timeChartCtx = timeChart ? timeChart.getContext("2d") : null;
    const toggleBtn = $("focus-toggle");
    const badge = $("focus-badge");
    const overviewImg = $("focus-overview");
    const overviewWrap = $("focus-overview-wrap");
    const roiBox = $("focus-roi-box");
    const roiSizeSel = $("focus-roi-size");
    const keepBtn = $("focus-keep");
    const stretchChk = $("focus-stretch");
    const markersChk = $("focus-markers");
    const crossChk = $("focus-crosshair");

    const METHOD_LABEL = {
        stars: "Stars · Star size HFR",
        lights: "Lights · Light-spot size HFR",
        contrast: "Day · Edge contrast",
    };
    const HISTORY_MAX = 240;

    const st = {
        active: false,
        running: false,
        lastTs: 0,
        cx: 0.5,
        cy: 0.5,
        roiMode: "auto",
        roiSize: 256,
        method: "auto",
        frameW: 0,
        frameH: 0,
        history: [],      // {t, score (raw), sm (running average from the server)}
        lensHistory: [],  // [{ pos, score, hfr, fwhm, contrast, method, samples, t }]
        lensPos: 0.0,
        motorized: false,
        chartMetric: "starsize", // "starsize" (HFR px) or "sharpness" (edge contrast / score)
        userPickedChartMetric: false,
        bestLensPos: null,
        bestLensVal: null,
        curveFit: null,
        hoverPoint: null,
        chartPad: { l: 64, r: 24, t: 24, b: 38 },
        chartXMin: 0.0,
        chartXMax: 1.0,
        chartYMin: 0.0,
        chartYMax: 10.0,
        family: null,
        lastResult: null,
        bitmap: null,
        overviewTimer: null,
        fpsTimes: [],
        actionSeq: 0,     // bumped by every POST; a poll reply from before it is stale
        overviewBusyAt: 0,
    };

    function familyOf(method) {
        return method === "contrast" ? "contrast" : "hfr";
    }

    function syncChartMetric(method) {
        const want = method === "contrast" ? "sharpness" : (method === "stars" || method === "lights") ? "starsize" : null;
        if (!want) return;
        st.chartMetric = want;
        document.querySelectorAll("#focus-chart-metric button").forEach((btn) =>
            btn.classList.toggle("active", btn.dataset.chartMetric === want)
        );
    }

    async function api(path, opts = {}) {
        const init = { method: opts.method || "GET", headers: {} };
        if (init.method !== "GET") st.actionSeq++;
        if (opts.body !== undefined) {
            init.headers["Content-Type"] = "application/json";
            init.body = JSON.stringify(opts.body);
        }
        const r = await fetch(path, init);
        if (!r.ok) {
            let detail = "";
            try { detail = (await r.json()).detail || ""; } catch (e) { /* not JSON */ }
            throw new Error(detail ? `${detail} (HTTP ${r.status})` : `${path}: HTTP ${r.status}`);
        }
        return r.json();
    }

    function syncRoiModeUi() {
        document.querySelectorAll("#focus-roi-mode button").forEach((b) =>
            b.classList.toggle("active", b.dataset.roiMode === st.roiMode)
        );
    }

    function applyState(s) {
        st.active = !!s.active;
        st.cx = s.cx;
        st.cy = s.cy;
        if (s.roi_mode) st.roiMode = s.roi_mode;
        st.roiSize = s.roi_size;
        st.method = s.method;
        if (s.frame_width) { st.frameW = s.frame_width; st.frameH = s.frame_height; }
        roiSizeSel.value = String(s.roi_size);
        document.querySelectorAll("#focus-method button").forEach((b) => b.classList.toggle("active", b.dataset.method === s.method));
        syncRoiModeUi();
        if (s.method === "stars" || s.method === "lights") {
            syncChartMetric("stars");
        } else if (s.method === "contrast" || s.best_method === "contrast" || (s.method === "auto" && s.exposure_s > 0 && s.exposure_s < 0.05)) {
            syncChartMetric("contrast");
        } else if (s.best_method === "stars" || s.best_method === "lights") {
            syncChartMetric("stars");
        }
        if (st.history.length === 0 && Array.isArray(s.history) && s.history.length > 0) {
            const effMethod = s.best_method || (s.method === "auto" ? (s.exposure_s > 0 && s.exposure_s < 0.05 ? "contrast" : "stars") : s.method);
            st.family = familyOf(effMethod);
            st.history = s.history
                .filter((p) => p && p.score !== null && p.score !== undefined)
                .map((p) => ({ t: p.t, score: p.score, sm: p.smoothed ?? p.score }));
            if (!st.lastResult && st.history.length > 0) {
                const lastP = st.history[st.history.length - 1];
                st.lastResult = {
                    method: effMethod,
                    lower_is_better: effMethod !== "contrast",
                    score: lastP.score,
                    smoothed: lastP.sm,
                    best: s.best,
                    smooth: s.smooth || 5,
                };
            }
        }
        if (s.smooth) document.querySelectorAll("#focus-smooth button").forEach((b) => b.classList.toggle("active", +b.dataset.smooth === s.smooth));
        if (s.exposure_s !== undefined && document.activeElement !== $("focus-exp")) $("focus-exp").value = +s.exposure_s.toPrecision(4);
        if (s.gain_db !== undefined && document.activeElement !== $("focus-gain")) $("focus-gain").value = +s.gain_db.toFixed(1);
        toggleBtn.textContent = st.active ? "Stop" : "Start focus mode";
        $("focus-finish").hidden = !st.active;
        renderSaved(s);
        toggleBtn.classList.toggle("btn-danger", st.active);
        badge.textContent = st.active ? "LIVE" : "OFF";
        badge.classList.toggle("on", st.active);
        showExpiry(s.expires_in_s);
        if (st.active && !st.running) {
            // Focus mode turned on elsewhere (AF sweep, another tab): stream it here too
            st.lastTs = 0;
            frameLoop();
            startOverviewTimer();
        }
        if (s.lens) applyLens(s.lens);
        emptyMsg.hidden = st.active && !!st.bitmap;
        if (st.active && !st.bitmap) {
            emptyMsg.innerHTML = "<span>Waiting for the first frame from the camera…</span>";
            clearTimeout(st.waitTimer);
            st.waitTimer = setTimeout(() => {
                if (st.active && !st.bitmap) emptyMsg.innerHTML =
                    "<span>The camera has not sent a frame for 15 s. It is re-opened automatically after a stall; " +
                    "if this stays, check the Activity Log.</span>";
            }, 15000);
        } else if (!st.active) {
            emptyMsg.innerHTML = "<span>Press <strong>Start focus mode</strong> to stream a native-resolution crop.</span>";
        }
        placeRoiBox();
        drawChart();
    }

    // ------------------------------------------------------------------
    // Frame loop
    // ------------------------------------------------------------------
    async function frameLoop() {
        if (st.running) return;
        st.running = true;
        while (st.active) {
            if (document.hidden) {
                // A background tab must not keep focus mode alive: the server ends it after 60 s without polls
                await new Promise((ok) => document.addEventListener("visibilitychange", ok, { once: true }));
                continue;
            }
            try {
                const url = `/api/focus/frame?since=${st.lastTs}&stretch=${stretchChk.checked ? 1 : 0}`;
                const r = await fetch(url, { cache: "no-store" });
                if (r.status === 409) { st.active = false; applyState(await api("/api/focus")); break; }
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                const res = JSON.parse(r.headers.get("X-Focus") || "{}");
                const blob = await r.blob();
                const bmp = await createImageBitmap(blob);
                if (st.bitmap && st.bitmap.close) st.bitmap.close();
                st.bitmap = bmp;
                if (res.frame_ts === st.lastTs) continue;
                st.lastTs = res.frame_ts || st.lastTs;
                onResult(res);
            } catch (e) {
                $("focus-statline").textContent = `Waiting for camera… (${e.message})`;
                await new Promise((ok) => setTimeout(ok, 1000));
            }
        }
        st.running = false;
    }

    function renderSaved(s) {
        const box = $("focus-saved-box") || document.createElement("div");
        const r = s.last_record;
        $("focus-exp-summary").textContent = s.exposure_s ? `· now ${fmtExp(s.exposure_s)}, ${(s.gain_db || 0).toFixed(0)} dB` : "";
        const hist = s.history_records || [];
        $("focus-history-count").textContent = hist.length ? `· ${hist.length}` : "";
        renderHistory($("focus-history"), hist, async (i) => {
            const ns = await useRecord(i);
            if (ns) { st.history = []; st.lastResult = null; applyState(ns); }
        });
        if (!r) { box.innerHTML = '<span class="text-muted">No saved focus yet. Focus, then <b>Finish &amp; save</b> (a successful sweep saves by itself).</span>'; return; }
        const when = new Date(r.finished_at);
        const fresh = Date.now() - when.getTime() < 10 * 60 * 1000 && !s.active;
        box.innerHTML =
            (fresh ? '<div class="ok">✓ Focus saved to Settings, focus mode off</div>' : "") +
            `<div>Last saved <b>${fmtWhen(r.finished_at)}</b> (${esc(r.source)})</div>` +
            `<div>${esc(r.method || "—")} <b>${fmtRecScore(r)}</b>${r.fwhm_px ? ` · FWHM ${r.fwhm_px} px` : ""}</div>` +
            (r.lens_position != null ? `<div>Lens <b>${fmtRecLens(r)}</b>${r.lens_mode ? ` · ${r.lens_mode}` : ""}</div>` : "") +
            `<div>${r.sensor_temp_c != null ? `${r.sensor_temp_c} °C · ` : ""}${fmtExp(r.exposure_s)}, ${(r.gain_db ?? 0).toFixed(0)} dB</div>`;
    }

    function showExpiry(sec) {
        if (sec === undefined || sec === null || !st.active) { badge.textContent = st.active ? "LIVE" : "OFF"; keepBtn.hidden = true; return; }
        const m = Math.floor(sec / 60), ss = String(Math.floor(sec % 60)).padStart(2, "0");
        badge.textContent = `LIVE · ${m}:${ss}`;
        badge.title = "Focus mode ends by itself after 15 min without a click or key press here";
        keepBtn.hidden = sec > 120;
    }

    function onResult(res) {
        st.lastResult = res;
        showExpiry(res.expires_in_s);
        st.frameW = res.frame_width;
        st.frameH = res.frame_height;
        if (res.method && st.method !== "auto" && res.method !== st.method) {
            st.method = res.method;
            document.querySelectorAll("#focus-method button").forEach((b) =>
                b.classList.toggle("active", b.dataset.method === st.method)
            );
        }
        if (res.method && !st.userPickedChartMetric) {
            syncChartMetric(res.method);
        }
        const livePos = res.lens_target ?? res.lens_position;
        if (livePos !== undefined && livePos !== null && !sliderDragging) {
            st.lensPos = livePos;
            const posEl = $("focus-lens-pos");
            if (posEl) posEl.textContent = `${Number(livePos).toFixed(3)} D`;
            const distEl = $("focus-lens-dist");
            if (distEl) {
                const lag = res.lens_target != null && res.lens_position != null && Math.abs(res.lens_target - res.lens_position) > 0.003;
                const distStr = fmtDist(livePos);
                const lagStr = lag ? ` · adjusting ${Number(res.lens_position).toFixed(3)} D` : "";
                distEl.textContent = distStr ? `(${distStr}${lagStr})` : lagStr;
                distEl.classList.toggle("text-warning", livePos > 1);
            }
            if (lensSlider) lensSlider.value = livePos;
        }
        if (res.sweep) {
            const sw = res.sweep;
            const running = sw.state === "running";
            const cancelBtn = $("focus-lens-cancel");
            const progWrap = $("focus-lens-progress-wrap");
            const progBar = $("focus-lens-progress");
            const msgEl = $("focus-lens-msg");
            if (cancelBtn) cancelBtn.hidden = !running;
            if (progWrap) progWrap.hidden = !running;
            if (progBar) progBar.style.width = Math.round((sw.progress || 0) * 100) + "%";
            if (msgEl) {
                msgEl.textContent = running
                    ? `${sw.mode === "camera" ? "Camera AF" : "Sweep"} · ${sw.phase || ""}${sw.method ? " · " + sw.method : ""} · ${sw.steps ?? 0} steps`
                    : `${sw.mode === "camera" ? "Camera AF" : "Sweep"} ${sw.state}: ${sw.message || ""}`;
                msgEl.classList.toggle("text-warning", sw.state === "rejected" || sw.state === "failed");
            }
            if (running && !lensSweepRunning) {
                lensSweepRunning = true;
                setLensPoll(700);
            }
        }
        const fam = familyOf(res.method);
        if (fam !== st.family) {
            st.history = [];
            st.lensHistory = st.lensHistory.filter((p) => familyOf(p.method) === fam);
            st.family = fam;
            if (!st.userPickedChartMetric) syncChartMetric(res.method);
        }
        if (res.score !== null && res.score !== undefined) {
            st.history.push({ t: res.frame_ts, score: res.score, sm: res.smoothed ?? res.score });
            if (st.history.length > HISTORY_MAX) st.history.shift();
            recordLensMeasurement(res);
        }
        const now = performance.now();
        st.fpsTimes.push(now);
        while (st.fpsTimes.length > 8) st.fpsTimes.shift();
        drawZoom();
        updateReadout(res);
        drawChart();
        placeRoiBox();
        emptyMsg.hidden = true;
    }

    // ------------------------------------------------------------------
    // Rendering
    // ------------------------------------------------------------------
    function drawZoom() {
        const bmp = st.bitmap;
        if (!bmp) return;
        const dpr = window.devicePixelRatio || 1;
        // Largest square that fits the zoom area (which fills what the curve below leaves free)
        const side = Math.max(120, Math.min(wrap.clientWidth, wrap.clientHeight));
        canvas.style.width = side + "px";
        canvas.style.height = side + "px";
        if (canvas.width !== Math.round(side * dpr)) {
            canvas.width = Math.round(side * dpr);
            canvas.height = Math.round(side * dpr);
        }
        ctx.imageSmoothingEnabled = false;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
        const scale = canvas.width / bmp.width;
        $("focus-zoom-label").textContent = `· ${bmp.width}×${bmp.height} native px · ${(side / bmp.width).toFixed(1)}× on screen`;

        const res = st.lastResult;
        if (markersChk.checked && res && res.sources) {
            ctx.lineWidth = Math.max(1, dpr);
            ctx.font = `${11 * dpr}px ui-monospace, monospace`;
            res.sources.forEach((s, i) => {
                const lx = (s.x - res.roi[0] + 0.5) * scale;
                const ly = (s.y - res.roi[1] + 0.5) * scale;
                const rad = Math.max(6 * dpr, s.hfr * 2.5 * scale);
                ctx.strokeStyle = s.saturated ? "rgba(245,158,11,0.9)" : i === 0 ? "rgba(16,185,129,0.95)" : "rgba(56,189,248,0.8)";
                ctx.beginPath();
                ctx.arc(lx, ly, rad, 0, Math.PI * 2);
                ctx.stroke();
                ctx.fillStyle = ctx.strokeStyle;
                ctx.fillText(s.hfr.toFixed(2), lx + rad + 3 * dpr, ly - 3 * dpr);
            });
        }
        if (crossChk.checked) {
            ctx.strokeStyle = "rgba(239,68,68,0.6)";
            ctx.lineWidth = dpr;
            ctx.beginPath();
            ctx.moveTo(canvas.width / 2, 0); ctx.lineTo(canvas.width / 2, canvas.height);
            ctx.moveTo(0, canvas.height / 2); ctx.lineTo(canvas.width, canvas.height / 2);
            ctx.stroke();
        }
    }

    function fmtScore(res, v) {
        if (v === null || v === undefined) return "—";
        return res.method === "contrast" ? v.toFixed(v >= 10 ? 2 : 4) : v.toFixed(2) + " px";
    }

    function updateReadout(res) {
        const shown = res.smoothed ?? res.score;
        $("focus-score-label").textContent = METHOD_LABEL[res.method] || res.method;
        $("focus-score-value").textContent = fmtScore(res, shown);
        let sub = "";
        const avg = res.smooth > 1 ? `avg of ${res.smooth} frames · this frame ${fmtScore(res, res.score)}` : "single frame";
        if (res.method === "contrast") sub = `${avg} · higher is sharper`;
        else if (res.score !== null && res.score !== undefined) sub = `${avg} · FWHM ≈ ${res.fwhm} px · ${res.n_sources} source${res.n_sources === 1 ? "" : "s"} · lower is sharper`;
        else sub = "no point sources in the zoom region — move it or use Find star";
        $("focus-score-sub").textContent = sub;
        const q = res.quality_pct;
        $("focus-quality").textContent = q === null || q === undefined ? "—" : q.toFixed(1) + " %";
        $("focus-best").textContent = fmtScore(res, res.best);
        const fill = $("focus-meter-fill");
        // The bar spans 80-100 % of best: the last few percent are what fine focusing is about
        fill.style.width = Math.max(0, Math.min(100, ((q || 0) - 80) * 5)) + "%";
        fill.className = "focus-meter-fill " + (q >= 97 ? "good" : q >= 85 ? "near" : "far");

        // Trend: averaged score now against the average one window ago, ignoring changes within the noise
        const h = st.history;
        const n = Math.max(3, res.smooth || 1);
        let trend = "";
        if (h.length > n) {
            const now = h[h.length - 1].sm, before = h[h.length - 1 - n].sm;
            const lower = res.method !== "contrast";
            const rel = (100 * (now - before)) / Math.max(1e-9, Math.abs(before));
            const thr = Math.max(0.5, 1.5 * (res.noise_pct || 0) / Math.sqrt(n));
            if (Math.abs(rel) > thr) trend = (lower ? rel < 0 : rel > 0) ? "▲ sharper" : "▼ softer";
            else trend = "■ steady";
        }
        const tr = $("focus-trend");
        tr.textContent = trend;
        tr.className = "focus-trend " + (trend.startsWith("▲") ? "up" : trend.startsWith("▼") ? "down" : "");

        const times = st.fpsTimes;
        const fps = times.length > 1 ? (times.length - 1) / ((times[times.length - 1] - times[0]) / 1000) : 0;
        const exp = res.exposure_s < 0.1 ? (res.exposure_s * 1000).toFixed(2) + " ms" : res.exposure_s.toFixed(3) + " s";
        $("focus-statline").textContent =
            `${fps ? fps.toFixed(1) + " fps" : "…"} · ${exp} · ${res.gain_db.toFixed(1)} dB · ROI ${res.roi[2]} px`;
        $("focus-statline").title = `${res.capture_path || ""} · ROI at (${res.roi[0]}, ${res.roi[1]}) · ` +
            `background ${res.background} · noise ${res.noise} · measured in ${res.calc_ms} ms`;
        updateStats(res);
    }

    function updateStats(res) {
        if (st.showSweep && st.sweep) return;  // the sweep chart fills the stats row itself
        const vals = st.history.map((p) => p.sm);
        const f = (v) => (res ? fmtScore(res, v) : "—");
        $("focus-stat-noise").textContent = res && res.noise_pct != null ? `±${res.noise_pct.toFixed(1)} %` : "—";
        setTxt("focus-stat-now", res ? f(res.smoothed ?? res.score) : "—");
        setTxt("focus-stat-best", res ? f(res.best) : "—");
        $("focus-stat-min").textContent = vals.length ? f(Math.min(...vals)) : "—";
        $("focus-stat-max").textContent = vals.length ? f(Math.max(...vals)) : "—";
        $("focus-stat-n").textContent = String(vals.length);
    }

    function fitQuadratic(pts, metricKey, lowerIsBetter) {
        const rawValid = pts.filter((p) => p[metricKey] !== null && p[metricKey] !== undefined && isFinite(p[metricKey]));
        if (rawValid.length < 4) return null;

        // Reject isolated single-point outliers (e.g. a single in-flight frame far from its neighbors)
        const valid = rawValid.filter((p, idx) => {
            if (rawValid.length < 6) return true;
            const neigh = [];
            for (let j = Math.max(0, idx - 2); j <= Math.min(rawValid.length - 1, idx + 2); j++) {
                if (j !== idx) neigh.push(rawValid[j][metricKey]);
            }
            if (neigh.length < 2) return true;
            neigh.sort((a, b) => a - b);
            const med = neigh[Math.floor(neigh.length / 2)];
            const allSorted = rawValid.map((q) => q[metricKey]).sort((a, b) => a - b);
            const iqr = Math.max(1e-6, allSorted[Math.floor(allSorted.length * 0.75)] - allSorted[Math.floor(allSorted.length * 0.25)]);
            return Math.abs(p[metricKey] - med) <= Math.max(iqr * 2.2, Math.abs(med) * 0.18);
        });
        if (valid.length < 4) return null;

        const xsAll = valid.map((p) => p.pos);
        const ysAll = valid.map((p) => p[metricKey]);
        const xMin = Math.min(...xsAll), xMax = Math.max(...xsAll);
        if (xMax - xMin < 0.015) return null;

        // Find the robust peak index using a 3-point median window
        let ib = 0;
        let bestMed = lowerIsBetter ? Infinity : -Infinity;
        for (let i = 0; i < valid.length; i++) {
            const w = ysAll.slice(Math.max(0, i - 1), Math.min(valid.length, i + 2)).slice().sort((a, b) => a - b);
            const med = w[Math.floor(w.length / 2)];
            if (lowerIsBetter ? med < bestMed : med > bestMed) {
                bestMed = med;
                ib = i;
            }
        }

        // Fit the parabola over the full arch around the peak (±0.60 D, or at least ±5 points)
        const x0 = valid[ib].pos;
        const halfSpan = Math.max(0.60, (xMax - xMin) * 0.55);
        let sub = valid.filter((p) => Math.abs(p.pos - x0) <= halfSpan);
        if (sub.length < Math.min(valid.length, 6)) {
            const w0 = Math.max(0, ib - 5);
            const w1 = Math.min(valid.length, ib + 6);
            sub = valid.slice(w0, w1);
        }
        const wMin = sub[0].pos, wMax = sub[sub.length - 1].pos;
        if (wMax - wMin < 0.01) return null;

        // Center x at x0 = valid[ib].pos for numerical stability: y(x) = a2*(x-x0)^2 + a1*(x-x0) + a0
        const n = sub.length;
        let sx = 0, sx2 = 0, sx3 = 0, sx4 = 0;
        let sy = 0, sxy = 0, sx2y = 0;
        for (let i = 0; i < n; i++) {
            const dx = sub[i].pos - x0;
            const y = sub[i][metricKey];
            const dx2 = dx * dx;
            sx += dx; sx2 += dx2; sx3 += dx2 * dx; sx4 += dx2 * dx2;
            sy += y; sxy += dx * y; sx2y += dx2 * y;
        }
        // Columns: [0] = a2 (dx^2), [1] = a1 (dx), [2] = a0 (1), [3] = RHS
        const m = [
            [sx4, sx3, sx2, sx2y],
            [sx3, sx2, sx, sxy],
            [sx2, sx, n, sy]
        ];
        for (let i = 0; i < 3; i++) {
            let maxRow = i;
            for (let k = i + 1; k < 3; k++) {
                if (Math.abs(m[k][i]) > Math.abs(m[maxRow][i])) maxRow = k;
            }
            const tmp = m[i]; m[i] = m[maxRow]; m[maxRow] = tmp;
            if (Math.abs(m[i][i]) < 1e-12) return null;
            for (let k = i + 1; k < 3; k++) {
                const factor = m[k][i] / m[i][i];
                for (let j = i; j <= 3; j++) m[k][j] -= factor * m[i][j];
            }
        }
        const a0 = m[2][3] / m[2][2];
        const a1 = (m[1][3] - m[1][2] * a0) / m[1][1];
        const a2 = (m[0][3] - m[0][2] * a0 - m[0][1] * a1) / m[0][0];
        if (!isFinite(a2) || !isFinite(a1) || !isFinite(a0) || Math.abs(a2) < 1e-9) return null;

        const vertex = x0 - a1 / (2 * a2);
        const dv = vertex - x0;
        const yVertex = a2 * dv * dv + a1 * dv + a0;
        const a = a2;
        const b = a1 - 2 * a2 * x0;
        const c = a2 * x0 * x0 - a1 * x0 + a0;
        const hasTurnedOver = ib > 0 && ib < valid.length - 1;
        const expectedSign = hasTurnedOver && (lowerIsBetter ? a2 > 0 : a2 < 0)
            && vertex >= wMin - 0.1 && vertex <= wMax + 0.1;
        return { a, b, c, a2, a1, a0, x0, vertex, yVertex, xMin, xMax, wMin, wMax, expectedSign };
    }

    function recordLensMeasurement(res) {
        if (!res) return;
        const pos = res.lens_position !== undefined && res.lens_position !== null ? res.lens_position : (st.lensPos || 0.0);
        st.lensPos = pos;
        if (lensSweepRunning || (st.showSweep && st.sweep && (st.sweep.state === "done" || st.sweep.state === "rejected"))) {
            updateCurveSummary();
            return;  // Keep the sweep curve clean without in-flight or post-sweep single-frame outliers
        }
        const method = res.method || "auto";
        const score = res.score;
        if (score === null || score === undefined) return;
        const hfr = res.hfr ?? (method !== "contrast" ? score : null);
        const fwhm = res.fwhm;
        const contrast = res.contrast ?? (method === "contrast" ? score : null);

        const match = st.lensHistory.find((p) => Math.abs(p.pos - pos) <= 0.0035);
        if (match) {
            match.samples = (match.samples || 1) + 1;
            match.score = match.score * 0.7 + score * 0.3;
            if (hfr != null) match.hfr = (match.hfr != null ? match.hfr * 0.7 + hfr * 0.3 : hfr);
            if (fwhm != null) match.fwhm = (match.fwhm != null ? match.fwhm * 0.7 + fwhm * 0.3 : fwhm);
            if (contrast != null) match.contrast = (match.contrast != null ? match.contrast * 0.7 + contrast * 0.3 : contrast);
            match.t = res.frame_ts;
        } else {
            st.lensHistory.push({
                pos: Number(pos.toFixed(4)),
                score,
                hfr,
                fwhm,
                contrast,
                method,
                samples: 1,
                t: res.frame_ts
            });
            st.lensHistory.sort((a, b) => a.pos - b.pos);
            if (st.lensHistory.length > 250) st.lensHistory.shift();
        }
        updateCurveSummary();
    }

    function updateCurveSummary() {
        setTxt("focus-curve-cur-pos", `${(st.lensPos || 0).toFixed(3)} D`);
        const count = st.lensHistory.length;
        setTxt("focus-curve-points-count", String(count));
        const goBest = $("focus-go-best");
        const bestWrap = $("focus-curve-best-wrap");
        if (st.bestLensPos != null) {
            const valStr = st.bestLensVal != null
                ? (st.chartMetric === "starsize" ? `${st.bestLensVal.toFixed(2)} px` : st.bestLensVal.toFixed(1))
                : "";
            setTxt("focus-curve-best-info", `${st.bestLensPos.toFixed(3)} D${valStr ? " (" + valStr + ")" : ""}`);
            setTxt("focus-best-pos-val", `${st.bestLensPos.toFixed(3)} D`);
            if (goBest) goBest.hidden = false;
            if (bestWrap) bestWrap.hidden = false;
        } else {
            setTxt("focus-curve-best-info", "—");
            if (goBest) goBest.hidden = true;
        }
    }

    function drawChart() {
        drawLensChart();
        drawTimeStreamChart();
    }
    const timeDetails = document.getElementById("focus-time-details");
    if (timeDetails) timeDetails.addEventListener("toggle", () => requestAnimationFrame(drawChart));

    function drawLensChart() {
        if (!lensChart || !lensChartCtx) return;
        const dpr = window.devicePixelRatio || 1;
        const w = lensChart.clientWidth, h = lensChart.clientHeight;
        if (!w || !h) return;  // hidden (collapsed or other tab): nothing to draw
        if (lensChart.width !== Math.round(w * dpr) || lensChart.height !== Math.round(h * dpr)) {
            lensChart.width = Math.round(w * dpr);
            lensChart.height = Math.round(h * dpr);
        }
        const c = lensChartCtx;
        c.setTransform(dpr, 0, 0, dpr, 0, 0);
        c.clearRect(0, 0, w, h);

        const isStarSize = st.chartMetric === "starsize";
        const titleEl = $("focus-lens-chart-title");
        if (titleEl) {
            titleEl.textContent = isStarSize
                ? "Lens position vs Star size (HFR)"
                : "Lens position vs Sharpness";
        }
        drawLensPositionChart(c, w, h);
    }

    function drawTimeStreamChart() {
        if (!timeChart || !timeChartCtx) return;
        const dpr = window.devicePixelRatio || 1;
        const w = timeChart.clientWidth, h = timeChart.clientHeight;
        if (!w || !h) return;  // collapsed: skip the per-frame redraw entirely
        if (timeChart.width !== Math.round(w * dpr) || timeChart.height !== Math.round(h * dpr)) {
            timeChart.width = Math.round(w * dpr);
            timeChart.height = Math.round(h * dpr);
        }
        const c = timeChartCtx;
        c.setTransform(dpr, 0, 0, dpr, 0, 0);
        c.clearRect(0, 0, w, h);

        drawTimeChart(c, w, h);
    }

    function syncSweepPointsToLensHistory(sw) {
        if (!sw || !sw.points) return;
        if (sw.started_at && sw.started_at !== st.lastSweepStartedAt) {
            st.lastSweepStartedAt = sw.started_at;
            st.lensHistory = [];
        }
        const swMethod = sw.method || "auto";
        sw.points.forEach((sp) => {
            if (sp.score == null || !isFinite(sp.score)) return;
            const pos = Number(sp.pos.toFixed(4));
            const match = st.lensHistory.find((p) => Math.abs(p.pos - pos) <= 0.0035);
            if (match) {
                match.score = sp.score;
                match.hfr = swMethod !== "contrast" ? sp.score : null;
                match.contrast = swMethod === "contrast" ? sp.score : null;
                match.method = swMethod;
                match.phase = sp.phase;
            } else {
                st.lensHistory.push({
                    pos,
                    score: sp.score,
                    hfr: swMethod !== "contrast" ? sp.score : null,
                    contrast: swMethod === "contrast" ? sp.score : null,
                    method: swMethod,
                    samples: 1,
                    phase: sp.phase,
                    t: Date.now() / 1000
                });
            }
        });
        st.lensHistory.sort((a, b) => a.pos - b.pos);
    }

    function drawLensPositionChart(c, w, h) {
        if (st.sweep && st.sweep.points) {
            syncSweepPointsToLensHistory(st.sweep);
        }
        let pts = st.lensHistory.slice();

        const isStarSize = st.chartMetric === "starsize";
        const hasHfr = pts.some((p) => p.hfr != null);
        const hasContrast = pts.some((p) => p.contrast != null);
        const resIsContrast = st.lastResult && st.lastResult.method === "contrast";
        const useHfr = isStarSize && !resIsContrast && (hasHfr || (!hasContrast && pts.length === 0));
        const metricKey = useHfr ? "hfr" : (hasContrast || resIsContrast ? "contrast" : "score");
        const lowerIsBetter = useHfr || Boolean(st.lastResult && st.lastResult.lower_is_better && !resIsContrast);

        pts = pts.filter((p) => {
            const v = p[metricKey] ?? p.score;
            return v != null && isFinite(v);
        });

        const pad = st.chartPad;
        const curPos = st.lensPos ?? 0.0;

        // When focusing a manual lens by turning the physical ring without moving the Dioptre slider
        // (only 0 or 1 lens position recorded, but live frame scores are streaming in st.history),
        // plot the live focus curve across the recent frames so the chart is responsive during hand focusing.
        if (!st.motorized && pts.length <= 1 && st.history.length >= 1) {
            const histPts = st.history.slice(-60);
            const hVals = histPts.map((p) => p.sm ?? p.score).filter((v) => v != null && isFinite(v));
            if (hVals.length >= 1) {
                const n = hVals.length;
                const xSpan = Math.max(15, n - 1);
                const X = (i) => pad.l + (i / xSpan) * (w - pad.l - pad.r);
                let y0 = Math.min(...hVals), y1 = Math.max(...hVals);
                const mid = (y0 + y1) / 2;
                const ySpan = Math.max(Math.abs(mid) * 0.08, y1 - y0, 0.1);
                y0 = Math.max(0, mid - ySpan * 0.65);
                y1 = mid + ySpan * 0.65;
                const Y = (v) => {
                    const f = (v - y0) / Math.max(1e-9, y1 - y0);
                    return lowerIsBetter
                        ? pad.t + f * (h - pad.t - pad.b)
                        : pad.t + (1 - f) * (h - pad.t - pad.b);
                };

                c.strokeStyle = "#1e293b"; c.fillStyle = "#64748b"; c.lineWidth = 1;
                c.font = "11px ui-monospace, monospace";
                [y0, (y0 + y1) / 2, y1].forEach((v) => {
                    const yy = Y(v);
                    c.beginPath(); c.moveTo(pad.l, yy); c.lineTo(w - pad.r, yy); c.stroke();
                    const label = useHfr ? `${v.toFixed(2)} px` : v.toPrecision(4);
                    c.textAlign = "right";
                    c.fillText(label, pad.l - 6, yy + 4);
                    c.textAlign = "left";
                });

                for (let k = 0; k <= 5; k++) {
                    const idx = Math.round((xSpan * k) / 5);
                    const xx = X(idx);
                    c.beginPath(); c.moveTo(xx, pad.t); c.lineTo(xx, h - pad.b); c.stroke();
                    c.textAlign = "center";
                    c.fillText(`#${idx + 1}`, xx, h - 20);
                    c.textAlign = "left";
                }

                let bestIdx = 0;
                for (let i = 1; i < n; i++) {
                    if (lowerIsBetter ? hVals[i] < hVals[bestIdx] : hVals[i] > hVals[bestIdx]) bestIdx = i;
                }
                const bestVal = hVals[bestIdx];
                st.bestLensPos = curPos;
                st.bestLensVal = bestVal;
                updateCurveSummary();

                if (n >= 2) {
                    c.strokeStyle = "#38bdf8";
                    c.lineWidth = 2.5;
                    c.beginPath();
                    hVals.forEach((v, i) => {
                        const xx = X(i), yy = Y(v);
                        if (i === 0) c.moveTo(xx, yy); else c.lineTo(xx, yy);
                    });
                    c.stroke();
                }

                hVals.forEach((v, i) => {
                    const px = X(i), py = Y(v);
                    const isBest = i === bestIdx;
                    c.fillStyle = isBest ? "#10b981" : "#38bdf8";
                    c.beginPath();
                    c.arc(px, py, isBest ? 5 : 3.5, 0, Math.PI * 2);
                    c.fill();
                    if (isBest) {
                        c.strokeStyle = "rgba(16, 185, 129, 0.5)";
                        c.lineWidth = 2;
                        c.beginPath(); c.arc(px, py, 8, 0, Math.PI * 2); c.stroke();
                    }
                });

                const bx = X(bestIdx), by = Y(bestVal);
                c.strokeStyle = "rgba(16, 185, 129, 0.75)";
                c.lineWidth = 1.5;
                c.setLineDash([4, 3]);
                c.beginPath(); c.moveTo(pad.l, by); c.lineTo(w - pad.r, by); c.stroke();
                c.setLineDash([]);
                c.fillStyle = "#10b981";
                c.font = "bold 11px ui-monospace, monospace";
                const valLabel = useHfr ? `${bestVal.toFixed(2)} px` : bestVal.toFixed(1);
                c.fillText(`★ Sharpest peak: ${valLabel} (current: ${ useHfr ? hVals[n - 1].toFixed(2) + " px" : hVals[n - 1].toFixed(1) })`, pad.l + 6, pad.t + 12);

                c.fillStyle = "#64748b";
                c.font = "10px sans-serif";
                c.fillText(useHfr ? "Star size (HFR px) ↓ sharper" : "Sharpness ↑ sharper", pad.l + 4, pad.t - 8);
                c.textAlign = "center";
                c.fillText("Focus-sweep frames (drag the lens position D slider to plot by Dioptres) →", pad.l + (w - pad.l - pad.r) / 2, h - 5);
                c.textAlign = "left";
                return;
            }
        }

        if (pts.length === 0) {
            const x0 = 0.0, x1 = 1.0;
            const X = (v) => pad.l + ((v - x0) / (x1 - x0)) * (w - pad.l - pad.r);
            c.strokeStyle = "#1e293b"; c.fillStyle = "#64748b"; c.lineWidth = 1;
            c.font = "11px ui-monospace, monospace";
            for (let k = 0; k <= 5; k++) {
                const xv = x0 + ((x1 - x0) * k) / 5;
                const xx = X(xv);
                c.beginPath(); c.moveTo(xx, pad.t); c.lineTo(xx, h - pad.b); c.stroke();
                c.textAlign = "center";
                c.fillText(`${xv.toFixed(2)} D`, xx, h - 20);
                c.textAlign = "left";
            }
            const curX = X(Math.min(x1, Math.max(x0, curPos)));
            c.strokeStyle = "rgba(56, 189, 248, 0.7)";
            c.setLineDash([4, 4]);
            c.beginPath(); c.moveTo(curX, pad.t); c.lineTo(curX, h - pad.b); c.stroke();
            c.setLineDash([]);
            c.fillStyle = "#38bdf8";
            c.fillText(`Current ${curPos.toFixed(3)} D`, Math.min(curX + 6, w - pad.r - 95), pad.t + 16);

            c.fillStyle = "#64748b";
            c.font = "10px sans-serif";
            c.textAlign = "center";
            c.fillText("Lens position (Dioptres) →", pad.l + (w - pad.l - pad.r) / 2, h - 5);

            c.fillStyle = "#94a3b8";
            c.font = "13px sans-serif";
            c.fillText("V-Curve chart: Lens position vs Star size (HFR) / Sharpness", w / 2, h / 2 - 10);
            c.font = "11px sans-serif";
            c.fillStyle = "#64748b";
            c.fillText("Turn the focus ring or drag the lens position slider (MF) to plot the sharpness curve.", w / 2, h / 2 + 12);
            c.textAlign = "left";
            return;
        }

        const xs = pts.map((p) => p.pos);
        const ysRaw = pts.map((p) => p[metricKey] ?? p.score).filter((v) => v != null && isFinite(v));
        if (ysRaw.length === 0) return;

        // Ignore isolated single-point outliers when computing Y-axis scale
        let ys = ysRaw;
        if (ysRaw.length >= 6) {
            const sortedY = ysRaw.slice().sort((a, b) => a - b);
            const q25 = sortedY[Math.floor(sortedY.length * 0.25)];
            const q75 = sortedY[Math.floor(sortedY.length * 0.75)];
            const iqr = Math.max(1e-6, q75 - q25);
            const filtered = ysRaw.filter((v) => v >= q25 - 2.5 * iqr && v <= q75 + 2.5 * iqr);
            if (filtered.length >= 4) ys = filtered;
        }

        let x0 = Math.min(0, ...xs, curPos) - 0.015;
        let x1 = Math.max(0.4, ...xs, curPos) + 0.05;
        if (x1 - x0 < 0.2) x1 = x0 + 0.2;
        st.chartXMin = x0; st.chartXMax = x1;

        let y0 = Math.min(...ys), y1 = Math.max(...ys);
        const ySpan = Math.max(0.1, y1 - y0);
        y0 = Math.max(0, y0 - ySpan * 0.15);
        y1 = y1 + ySpan * 0.15;
        st.chartYMin = y0; st.chartYMax = y1;

        const X = (v) => pad.l + ((v - x0) / (x1 - x0)) * (w - pad.l - pad.r);
        const Y = (v) => {
            const f = (v - y0) / (y1 - y0);
            return lowerIsBetter
                ? pad.t + f * (h - pad.t - pad.b)
                : pad.t + (1 - f) * (h - pad.t - pad.b);
        };
        st.chartYFunc = (p) => Y(p[metricKey] ?? p.score);

        c.strokeStyle = "#1e293b"; c.fillStyle = "#64748b"; c.lineWidth = 1;
        c.font = "11px ui-monospace, monospace";
        [y0, (y0 + y1) / 2, y1].forEach((v) => {
            const yy = Y(v);
            c.beginPath(); c.moveTo(pad.l, yy); c.lineTo(w - pad.r, yy); c.stroke();
            const label = useHfr ? `${v.toFixed(2)} px` : v.toPrecision(4);
            c.textAlign = "right";
            c.fillText(label, pad.l - 6, yy + 4);
            c.textAlign = "left";
        });

        const numTicks = Math.max(3, Math.min(7, Math.floor((w - pad.l - pad.r) / 75)));
        for (let k = 0; k <= numTicks; k++) {
            const xv = Math.max(0, x0) + ((x1 - Math.max(0, x0)) * k) / numTicks;
            const xx = X(xv);
            c.beginPath(); c.moveTo(xx, pad.t); c.lineTo(xx, h - pad.b); c.stroke();
            c.textAlign = "center";
            c.fillText(`${xv.toFixed(2)} D`, xx, h - 20);
            c.textAlign = "left";
        }

        const fit = fitQuadratic(pts, metricKey, lowerIsBetter);
        st.curveFit = fit;
        let bestPos = null, bestVal = null;
        if (st.sweep && st.sweep.best_position != null && st.sweep.fit && Boolean(st.sweep.fit.lower_is_better) === Boolean(lowerIsBetter)) {
            bestPos = st.sweep.best_position;
            bestVal = st.sweep.final_score ?? (fit ? fit.yVertex : null);
        } else if (fit && fit.expectedSign && fit.vertex >= x0 - 0.1 && fit.vertex <= x1 + 0.1) {
            bestPos = fit.vertex;
            bestVal = fit.yVertex;
        } else {
            const bestP = pts.reduce((b, p) => {
                const val = p[metricKey] ?? p.score;
                if (val == null) return b;
                if (!b) return p;
                const bVal = b[metricKey] ?? b.score;
                return lowerIsBetter ? (val < bVal ? p : b) : (val > bVal ? p : b);
            }, null);
            if (bestP) {
                bestPos = bestP.pos;
                bestVal = bestP[metricKey] ?? bestP.score;
            }
        }
        st.bestLensPos = bestPos;
        st.bestLensVal = bestVal;
        updateCurveSummary();

        c.save();
        c.beginPath();
        c.rect(pad.l, pad.t, w - pad.l - pad.r, h - pad.t - pad.b);
        c.clip();

        if (pts.length >= 2) {
            c.strokeStyle = "rgba(56, 189, 248, 0.72)";
            c.lineWidth = 2;
            c.beginPath();
            pts.forEach((p, idx) => {
                const val = p[metricKey] ?? p.score;
                const xx = X(p.pos), yy = Y(val);
                if (idx === 0) c.moveTo(xx, yy); else c.lineTo(xx, yy);
            });
            c.stroke();
        }

        if (fit && fit.expectedSign) {
            c.strokeStyle = "#10b981";
            c.lineWidth = 2.5;
            c.shadowColor = "rgba(16, 185, 129, 0.4)";
            c.shadowBlur = 6;
            c.beginPath();
            const fMin = Math.max(x0, fit.wMin ?? x0);
            const fMax = Math.min(x1, fit.wMax ?? x1);
            const steps = 48;
            for (let k = 0; k <= steps; k++) {
                const xv = fMin + ((fMax - fMin) * k) / steps;
                const dx = xv - (fit.x0 ?? 0);
                const yv = fit.a2 !== undefined
                    ? fit.a2 * dx * dx + fit.a1 * dx + fit.a0
                    : fit.a * xv * xv + fit.b * xv + fit.c;
                const xx = X(xv), yy = Y(yv);
                if (k === 0) c.moveTo(xx, yy); else c.lineTo(xx, yy);
            }
            c.stroke();
            c.shadowBlur = 0;
        }

        pts.forEach((p) => {
            const val = p[metricKey] ?? p.score;
            if (val == null || !isFinite(val)) return;
            const px = X(p.pos), py = Y(val);
            const isBest = bestPos != null && Math.abs(p.pos - bestPos) < 0.015;
            c.fillStyle = isBest ? "#10b981" : (p.phase === "refine" ? "#a7f3d0" : "#38bdf8");
            c.beginPath();
            c.arc(px, py, isBest ? 5 : 3.5, 0, Math.PI * 2);
            c.fill();
            if (isBest) {
                c.strokeStyle = "rgba(16, 185, 129, 0.5)";
                c.lineWidth = 2;
                c.beginPath(); c.arc(px, py, 8, 0, Math.PI * 2); c.stroke();
            }
        });

        c.restore();

        if (bestPos != null && bestPos >= x0 && bestPos <= x1) {
            const bx = X(bestPos);
            c.strokeStyle = "rgba(16, 185, 129, 0.85)";
            c.lineWidth = 1.5;
            c.setLineDash([4, 3]);
            c.beginPath(); c.moveTo(bx, pad.t); c.lineTo(bx, h - pad.b); c.stroke();
            c.setLineDash([]);
            c.fillStyle = "#10b981";
            c.font = "bold 11px ui-monospace, monospace";
            const valLabel = bestVal != null ? (useHfr ? `${bestVal.toFixed(2)} px` : bestVal.toFixed(1)) : "";
            c.fillText(`★ Sharpest ${bestPos.toFixed(3)} D${valLabel ? " (" + valLabel + ")" : ""}`, Math.min(bx + 6, w - pad.r - 140), pad.t + 12);
        }

        const curX = X(Math.min(x1, Math.max(x0, curPos)));
        c.strokeStyle = "rgba(56, 189, 248, 0.8)";
        c.lineWidth = 1.5;
        c.setLineDash([2, 2]);
        c.beginPath(); c.moveTo(curX, pad.t); c.lineTo(curX, h - pad.b); c.stroke();
        c.setLineDash([]);
        c.fillStyle = "#38bdf8";
        c.font = "11px ui-monospace, monospace";
        c.fillText(`Current ${curPos.toFixed(3)} D`, Math.min(curX + 6, w - pad.r - 110), pad.t + 26);

        c.fillStyle = "#64748b";
        c.font = "10px sans-serif";
        c.fillText(useHfr ? "Star size (HFR px) ↓ sharper" : "Sharpness ↑ sharper", pad.l + 4, pad.t - 8);
        c.textAlign = "center";
        c.fillText("Lens position (Dioptres) →", pad.l + (w - pad.l - pad.r) / 2, h - 5);
        c.textAlign = "left";

        if (st.hoverPoint && st.hoverPoint.point) {
            const p = st.hoverPoint.point;
            const hx = st.hoverPoint.x, hy = st.hoverPoint.y;
            c.fillStyle = "#f59e0b";
            c.beginPath(); c.arc(hx, hy, 6, 0, Math.PI * 2); c.fill();

            const tipW = 140, tipH = 46;
            let tx = hx + 10, ty = hy - tipH - 8;
            if (tx + tipW > w - pad.r) tx = hx - tipW - 10;
            if (ty < pad.t) ty = hy + 12;

            c.fillStyle = "rgba(15, 23, 42, 0.92)";
            c.strokeStyle = "#334155";
            c.lineWidth = 1;
            c.fillRect(tx, ty, tipW, tipH);
            c.strokeRect(tx, ty, tipW, tipH);

            c.fillStyle = "#f8fafc";
            c.font = "bold 11px ui-monospace, monospace";
            c.fillText(`Position: ${p.pos.toFixed(3)} D`, tx + 8, ty + 15);
            c.fillStyle = "#94a3b8";
            c.font = "10px ui-monospace, monospace";
            const hfrStr = p.hfr != null ? `HFR: ${p.hfr.toFixed(2)} px` : (p.score ? `Score: ${p.score.toFixed(2)}` : "");
            c.fillText(`${hfrStr} (${p.samples || 1} frame)`, tx + 8, ty + 30);
            if (p.fwhm) c.fillText(`FWHM ≈ ${p.fwhm.toFixed(2)} px`, tx + 8, ty + 42);
        }
    }

    function drawTimeChart(c, w, h) {
        const pts = st.history.slice(-60);
        const res = st.lastResult;
        if (!res || pts.length < 1) {
            c.fillStyle = "#64748b";
            c.font = "12px sans-serif";
            c.fillText("Score history appears here", 12, h / 2);
            return;
        }
        const pad = { l: 56, r: 10, t: 14, b: 16 };
        const sm = pts.map((p) => p.sm ?? p.score);
        const raw = pts.map((p) => p.score);
        const lower = res.method !== "contrast";
        let lo = Math.min(...sm), hi = Math.max(...sm);
        const mid = (lo + hi) / 2;
        const minSpan = Math.max(0.1, Math.abs(mid) * Math.max(0.12, (8 * (res.noise_pct || 0)) / 100));
        if (hi - lo < minSpan) { lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
        const padV = (hi - lo) * 0.08;
        lo -= padV; hi += padV;
        const xSpan = Math.max(15, pts.length - 1);
        const x = (i) => pad.l + (i / xSpan) * (w - pad.l - pad.r);
        const y = (v) => {
            const f = (Math.min(hi, Math.max(lo, v)) - lo) / Math.max(1e-9, hi - lo);
            return pad.t + (lower ? f : 1 - f) * (h - pad.t - pad.b);
        };
        c.strokeStyle = "#1e293b";
        c.fillStyle = "#64748b";
        c.font = "11px ui-monospace, monospace";
        c.lineWidth = 1;
        [lo, mid, hi].forEach((v) => {
            const yy = y(v);
            c.beginPath(); c.moveTo(pad.l, yy); c.lineTo(w - pad.r, yy); c.stroke();
            c.fillText(lower ? v.toFixed(2) : v.toPrecision(4), 4, yy + 4);
        });
        if (res.noise_pct) {
            const cur = sm[sm.length - 1];
            const band = (Math.abs(cur) * res.noise_pct) / 100 / Math.sqrt(Math.max(1, res.smooth || 1));
            const y1 = y(cur - band), y2 = y(cur + band);
            c.fillStyle = "rgba(148,163,184,0.10)";
            c.fillRect(pad.l, Math.min(y1, y2), w - pad.l - pad.r, Math.max(1, Math.abs(y2 - y1)));
        }
        if (res.best !== null && res.best !== undefined) {
            c.strokeStyle = "rgba(16,185,129,0.7)";
            c.setLineDash([4, 4]);
            c.beginPath(); c.moveTo(pad.l, y(res.best)); c.lineTo(w - pad.r, y(res.best)); c.stroke();
            c.setLineDash([]);
        }
        c.fillStyle = "rgba(56,189,248,0.28)";
        raw.forEach((v, i) => { c.beginPath(); c.arc(x(i), y(v), 2.0, 0, Math.PI * 2); c.fill(); });
        if (sm.length >= 2) {
            c.strokeStyle = "#38bdf8";
            c.lineWidth = 2.5;
            c.beginPath();
            sm.forEach((v, i) => { if (i === 0) c.moveTo(x(i), y(v)); else c.lineTo(x(i), y(v)); });
            c.stroke();
        }
        const sMin = Math.min(...sm), sMax = Math.max(...sm);
        const iBest = sm.indexOf(lower ? sMin : sMax), iWorst = sm.indexOf(lower ? sMax : sMin);
        const mark = (i, color, text, above) => {
            const px = x(i), py = y(sm[i]);
            c.strokeStyle = color;
            c.lineWidth = 1;
            c.setLineDash([2, 3]);
            c.beginPath(); c.moveTo(px, pad.t); c.lineTo(px, h - pad.b); c.stroke();
            c.setLineDash([]);
            c.fillStyle = color;
            c.beginPath(); c.arc(px, py, 4, 0, Math.PI * 2); c.fill();
            const tw = c.measureText(text).width;
            const tx = Math.min(Math.max(pad.l, px - tw / 2), w - pad.r - tw);
            c.fillText(text, tx, above ? Math.max(pad.t - 2, py - 8) : Math.min(h - 3, py + 16));
        };
        if (iWorst !== iBest) mark(iWorst, "rgba(239,68,68,0.85)", `softest ${fmtScore(res, sm[iWorst])}`, false);
        mark(iBest, "rgba(16,185,129,0.95)", `sharpest ${fmtScore(res, sm[iBest])}`, true);
        const last = sm.length - 1;
        c.fillStyle = "#f1f5f9";
        c.beginPath(); c.arc(x(last), y(sm[last]), 4.5, 0, Math.PI * 2); c.fill();
        c.fillStyle = "#64748b";
        c.fillText("sharper ↑", w - pad.r - 64, h - 3);
    }

    function placeRoiBox() {
        if (!overviewImg.naturalWidth || !st.frameW) { roiBox.hidden = true; return; }
        roiBox.hidden = false;
        const dispW = overviewImg.clientWidth, dispH = overviewImg.clientHeight;
        const side = Math.min(st.roiSize, st.frameW, st.frameH);
        const bw = (side / st.frameW) * dispW, bh = (side / st.frameH) * dispH;
        let left = st.cx * dispW - bw / 2, top = st.cy * dispH - bh / 2;
        left = Math.min(Math.max(0, left), dispW - bw);
        top = Math.min(Math.max(0, top), dispH - bh);
        Object.assign(roiBox.style, { left: left + "px", top: top + "px", width: bw + "px", height: bh + "px" });
    }

    function refreshOverview() {
        // One request at a time: wait for the previous load/error (or 20 s) before asking again
        if (Date.now() - st.overviewBusyAt < 20000) return;
        st.overviewBusyAt = Date.now();
        overviewImg.src = `/api/focus/overview.jpg?width=800&stretch=${stretchChk.checked ? 1 : 0}&t=${Date.now()}`;
    }
    const overviewDone = () => { st.overviewBusyAt = 0; };
    overviewImg.addEventListener("load", overviewDone);
    overviewImg.addEventListener("error", overviewDone);
    // Back in view: one refresh now (the timer skips while hidden)
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshOverview(); });

    function startOverviewTimer() {
        clearInterval(st.overviewTimer);
        refreshOverview();
        st.overviewTimer = setInterval(() => { if (!document.hidden) refreshOverview(); }, st.active ? 3000 : 15000);
    }

    // ------------------------------------------------------------------
    // Controls
    // ------------------------------------------------------------------
    async function configure(body) {
        st.showSweep = false;
        if (body.method) st.userPickedChartMetric = false;
        st.history = [];
        st.lastResult = null;
        try {
            applyState(await api("/api/focus/config", { method: "POST", body }));
        } catch (e) { console.warn(e); }
        updateStats(null);
        drawChart();
    }

    $("focus-finish").addEventListener("click", async () => {
        const b = $("focus-finish");
        b.disabled = true;
        try {
            const r = await api("/api/focus/finish", { method: "POST" });
            st.active = false;
            applyState(r);
            startOverviewTimer();
        } catch (e) {
            alert(`Finish focus: ${e.message}`);
        } finally {
            b.disabled = false;
        }
    });

    toggleBtn.addEventListener("click", async () => {
        toggleBtn.disabled = true;
        try {
            if (st.active) {
                applyState(await api("/api/focus/stop", { method: "POST" }));
            } else {
                st.history = [];
                st.userPickedChartMetric = false;
                st.showSweep = false;  // time curve, unless AF starts a sweep (then the sweep view returns)
                const startBody = st.roiMode === "manual" ? { roi_mode: "manual" } : {};
                // In AF the server sweeps right away; in MF it only streams for adjusting by hand
                const started = await api("/api/focus/start", { method: "POST", body: startBody });
                applyState(started);
                if (started.message) $("focus-statline").textContent = started.message;
                if (started.lens && started.lens.sweep && started.lens.sweep.state === "running") setLensPoll(700);
                frameLoop();
            }
            startOverviewTimer();
        } catch (e) {
            alert(`Focus mode: ${e.message}`);
        } finally {
            toggleBtn.disabled = false;
        }
    });

    document.querySelectorAll("#focus-method button").forEach((b) =>
        b.addEventListener("click", () => configure({ method: b.dataset.method })));
    document.querySelectorAll("#focus-roi-mode button").forEach((b) =>
        b.addEventListener("click", async () => {
            const mode = b.dataset.roiMode;
            st.roiMode = mode;
            syncRoiModeUi();
            if (mode === "auto") {
                try {
                    st.userPickedChartMetric = false;
                    const r = await api("/api/focus/auto-point", { method: "POST" });
                    st.history = []; st.lastResult = null; updateStats(null); drawChart();
                    applyState(r);
                    $("focus-statline").textContent = r.message || "";
                    if (r.found) await maybeSweepCurrentRoi();
                } catch (e) { console.warn(e); }
            } else {
                await configure({ roi_mode: "manual" });
            }
        }));
    document.querySelectorAll("#focus-smooth button").forEach((b) =>
        b.addEventListener("click", () => configure({ smooth: parseInt(b.dataset.smooth, 10) })));
    roiSizeSel.addEventListener("change", () => configure({ roi_size: parseInt(roiSizeSel.value, 10) }));
    $("focus-reset").addEventListener("click", () => {
        st.lensHistory = [];
        st.bestLensPos = null;
        st.bestLensVal = null;
        st.curveFit = null;
        updateCurveSummary();
        configure({ reset: true });
    });

    async function maybeSweepCurrentRoi() {
        if (!st.active || st.lensMode !== "auto" || !st.motorized) return;
        st.showSweep = true;
        try {
            const s = await api("/api/focus/lens/autofocus", { method: "POST", body: { mode: "sweep" } });
            applyState(s);
            setLensPoll(700);
        } catch (e) { console.warn(e); }
    }

    $("focus-auto-point").addEventListener("click", async () => {
        try {
            st.roiMode = "auto";
            st.userPickedChartMetric = false;
            syncRoiModeUi();
            const r = await api("/api/focus/auto-point", { method: "POST" });
            st.history = []; st.lastResult = null; updateStats(null); drawChart();
            applyState(r);
            $("focus-statline").textContent = r.message || "";
            if (!r.found) alert(r.message);
            else await maybeSweepCurrentRoi();
        } catch (e) { alert(`Auto point: ${e.message}`); }
    });
    [markersChk, crossChk].forEach((c) => c.addEventListener("change", drawZoom));
    stretchChk.addEventListener("change", refreshOverview);

    async function pickSource(lights) {
        try {
            const r = await api(`/api/focus/auto-roi?lights=${lights ? 1 : 0}`, { method: "POST" });
            if (!r.found) alert(lights ? "No bright point light found in the frame." : "No star found in the frame. Try a longer exposure or Find light.");
            st.history = [];
            applyState(r);
            if (r.found) await maybeSweepCurrentRoi();
        } catch (e) { alert(e.message); }
    }
    $("focus-pick-star").addEventListener("click", () => pickSource(false));
    $("focus-pick-light").addEventListener("click", () => pickSource(true));

    overviewWrap.addEventListener("click", async (ev) => {
        const rect = overviewImg.getBoundingClientRect();
        const cx = (ev.clientX - rect.left) / rect.width;
        const cy = (ev.clientY - rect.top) / rect.height;
        if (cx < 0 || cx > 1 || cy < 0 || cy > 1) return;
        st.cx = cx; st.cy = cy; st.roiMode = "manual";
        syncRoiModeUi();
        placeRoiBox();
        await configure({ cx, cy, roi_mode: "manual" });
        await maybeSweepCurrentRoi();
    });
    overviewImg.addEventListener("load", placeRoiBox);
    let resizeRaf = 0;
    window.addEventListener("resize", () => {
        if (resizeRaf) return;
        resizeRaf = requestAnimationFrame(() => { resizeRaf = 0; fitLayout(); placeRoiBox(); drawZoom(); drawChart(); });
    });

    document.addEventListener("keydown", (ev) => {
        if (ev.target.closest("input, select, textarea")) return;
        if (ev.key === "r" || ev.key === "R") {
            st.lensHistory = [];
            st.bestLensPos = null;
            st.bestLensVal = null;
            st.curveFit = null;
            updateCurveSummary();
            configure({ reset: true });
            return;
        }
        if (ev.key === " ") {
            ev.preventDefault();
            recordLensMeasurement(st.lastResult);
            drawChart();
            const recBtn = $("focus-record-point");
            if (recBtn) {
                recBtn.textContent = "Recorded ✓";
                setTimeout(() => { recBtn.textContent = "Record point"; }, 1000);
            }
            return;
        }
        const step = st.frameW ? (st.roiSize / 4) : 0;
        if (!step) return;
        const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
        if (!d) return;
        ev.preventDefault();
        const cx = Math.min(1, Math.max(0, st.cx + (d[0] * step) / st.frameW));
        const cy = Math.min(1, Math.max(0, st.cy + (d[1] * step) / st.frameH));
        st.cx = cx; st.cy = cy; st.roiMode = "manual";
        syncRoiModeUi();
        placeRoiBox();
        configure({ cx, cy, roi_mode: "manual" });
    });

    async function setExposure(exp, gain) {
        try {
            await api("/api/camera/exposure", { method: "POST", body: { exposure_s: exp, gain_db: gain } });
            $("focus-exp").value = exp;
            $("focus-gain").value = gain;
            configure({ reset: true });
        } catch (e) { alert(`Exposure: ${e.message}`); }
    }
    const autoExpBtn = $("focus-auto-exp");
    autoExpBtn.addEventListener("click", async () => {
        if (!st.active) { alert("Start focus mode first."); return; }
        autoExpBtn.disabled = true;
        autoExpBtn.textContent = "Metering…";
        try {
            applyState(await api("/api/focus/auto-exposure", { method: "POST" }));
            // Fresh curve at the new exposure
            st.history = [];
            st.lastResult = null;
            updateStats(null);
            drawChart();
            refreshOverview();
        } catch (e) {
            alert(`Auto exposure: ${e.message}`);
        } finally {
            autoExpBtn.disabled = false;
            autoExpBtn.textContent = "Auto expose";
        }
    });
    document.querySelectorAll(".focus-exp-preset").forEach((b) =>
        b.addEventListener("click", () => setExposure(parseFloat(b.dataset.exp), parseFloat(b.dataset.gain))));
    $("focus-exp-apply").addEventListener("click", () => {
        const exp = parseFloat($("focus-exp").value), gain = parseFloat($("focus-gain").value);
        if (exp > 0 && gain >= 0) setExposure(exp, gain);
    });

    const layout = $("focus-layout");
    function fitLayout() {
        // Measured once the page section is shown (app.js reveals it on DOMContentLoaded, after this runs)
        const top = layout.getBoundingClientRect().top + window.scrollY;
        if (top > 0) layout.style.setProperty("--focus-top", top + "px");
    }
    document.addEventListener("DOMContentLoaded", fitLayout);
    window.addEventListener("load", fitLayout);

    // ------------------------------------------------------------------
    // Motorised lens (Pi Camera Module 3 ...)
    // ------------------------------------------------------------------
    const lensCard = $("focus-lens-card");
    const lensSlider = $("focus-lens-slider");
    let lensPoll = null;
    let lensSweepRunning = false;

    function fmtDist(d) {
        if (d === null || d === undefined) return "";
        if (d < 0.02) return "≈ ∞ (infinity)";
        return d > 1 ? `≈ ${(1 / d).toFixed(2)} m (near)` : `≈ ${(1 / d).toFixed(1)} m`;
    }

    // Integer lens steps: the dioptre range of the lens mapped onto 0..LENS_STEPS (UI-only conversion)
    const LENS_STEPS = 1023;
    function lensStepSize() {
        const span = parseFloat(lensSlider.max) - parseFloat(lensSlider.min);
        return span > 0 ? span / LENS_STEPS : 0;
    }
    function updateLensStep(pos) {
        const el = $("focus-lens-step");
        if (!el) return;
        const size = lensStepSize();
        el.textContent = pos === null || pos === undefined || !size
            ? "—" : String(Math.round((pos - parseFloat(lensSlider.min)) / size));
    }

    function applyLens(L) {
        lensCard.hidden = !L.available;
        if (!L.available) return;
        const motorized = L.motorized !== false;
        st.motorized = motorized;
        st.lensMode = L.mode || "auto";
        const typeBadge = $("focus-lens-type-badge");
        if (typeBadge) {
            typeBadge.textContent = motorized ? "MOTORIZED" : "MANUAL LENS";
            typeBadge.title = motorized ? "Motorized autofocus lens" : "Manual focus CS/C-mount lens (ring marks)";
        }
        const afBtn = $("focus-lens-mode-af");
        if (afBtn) {
            afBtn.classList.toggle("disabled", !motorized);
            afBtn.title = motorized
                ? "Autofocus: the app finds focus by itself (sweep on stars, lights or detail) and keeps it (temperature, periodic checks). Click again to refocus now."
                : "Camera uses a manual-focus lens (no motor). Use Manual mode.";
        }
        // The commanded position updates at once; the position frames report lags a frame or two behind
        const pos = L.held ?? L.position;
        if (pos !== null && pos !== undefined && !sliderDragging) {
            st.lensPos = pos;
            updateCurveSummary();
        }
        // Outside focus mode frames are seconds apart, so the reported position is simply stale
        const lag = st.active && L.held != null && L.position != null && Math.abs(L.held - L.position) > 0.003;
        if (!sliderDragging) {
            $("focus-lens-pos").textContent = pos === null || pos === undefined ? "—" : `${pos.toFixed(3)} D`;
            const distStr = fmtDist(pos);
            const lagStr = lag ? ` · adjusting ${L.position.toFixed(3)} D` : "";
            $("focus-lens-dist").textContent = distStr ? `(${distStr}${lagStr})` : lagStr;
            $("focus-lens-dist").classList.toggle("text-warning", pos > 1);
        }
        lensSlider.max = Math.min(L.max, 3);
        lensSlider.min = L.min;
        if (!sliderDragging && pos !== null && pos !== undefined) lensSlider.value = pos;
        if (!sliderDragging) updateLensStep(pos);
        // MF: slider and nudges set the lens. AF: they only show where the app put it (moving one = MF)
        const mf = L.mode !== "auto" || !motorized;
        lensCard.classList.toggle("lens-af", !mf);
        $("focus-af-row").hidden = mf || !motorized;
        const every = $("focus-refocus-every");
        if (document.activeElement !== every && L.refocus_every_h !== undefined) every.value = String(L.refocus_every_h);
        lensSlider.title = mf ? "Drag to focus" : "AF moves the lens; drag to switch to MF";
        const sv = L.saved;
        $("focus-lens-saved").textContent = sv
            ? `saved ${sv.position.toFixed(3)} D by ${sv.source}${sv.sensor_temp_c != null ? ` at ${sv.sensor_temp_c} °C` : ""} · ${fmtWhen(sv.updated_at)}`
            : `not saved yet — libcamera default ${L.default} D (≈ ${(1 / Math.max(L.default, 0.01)).toFixed(1)} m), not the sky`;
        const tr = L.tracking || {};
        const auto = L.mode === "auto" && motorized;
        document.querySelectorAll("#focus-lens-mode button").forEach((b) => b.classList.toggle("active", b.dataset.lensMode === (auto ? "auto" : "manual")));
        const cp = L.compensation || {};
        $("focus-lens-comp").textContent = !motorized
            ? "MF: Turn the focus ring on the lens and set the position slider to match to plot the V-curve."
            : !auto
            ? "MF: drag the slider or use the nudges; the lens stays exactly there."
            : cp.n_rejected && !cp.learned && cp.rms_d
                ? `Temperature model ignored: checks disagree (±${cp.rms_d} D); ${cp.n_rejected} outlier(s) dropped`
            : cp.learned
                ? `Temperature compensation: ${(cp.slope * 1000).toFixed(1)} mD/°C from ${cp.n} checks over ${cp.span_c} °C` +
                  ` (fit ±${cp.rms_d} D) · now ${cp.temp_c ?? "?"} °C → ${cp.target?.toFixed(3)} D`
                : `Learning temperature drift: ${cp.n || 0} checks over ${cp.span_c || 0} °C (needs 3 over 3 °C)` +
                  (cp.temp_c != null ? ` · sensor ${cp.temp_c} °C` : "");
        $("focus-lens-reset-learning").hidden = !auto || !(cp.n > 0) || !motorized;
        const last = (tr.log || [])[tr.log ? tr.log.length - 1 : 0];
        $("focus-lens-track-msg").textContent = !auto ? "" : tr.base === null || tr.base === undefined
            ? "Run a sweep first: tracking follows the swept position."
            : `around ${tr.base.toFixed(3)} D ±${tr.max_offset} · ` +
              (last ? `last ${fmtWhen(last.t * 1000, true)}: ${last.from} → ${last.to} D${last.temp_c != null ? ` at ${last.temp_c} °C` : ""}` : "no check yet") +
              (tr.next_in_s != null ? ` · next check in ${Math.round(tr.next_in_s / 60)} min` : "") +
              (L.refocus_every_h > 0
                  ? ` · next refocus ${L.next_auto_sweep_in_s > 60 ? `in ${(L.next_auto_sweep_in_s / 3600).toFixed(1)} h` : "as soon as there is something to focus on"}`
                  : " · periodic refocus off");

        const sw = L.sweep;
        const running = !!sw && sw.state === "running";
        $("focus-lens-cancel").hidden = !running;
        $("focus-lens-progress-wrap").hidden = !running;
        if (sw) {
            $("focus-lens-progress").style.width = Math.round((sw.progress || 0) * 100) + "%";
            $("focus-lens-msg").textContent = running
                ? `${sw.mode === "camera" ? "Camera AF" : "Sweep"} · ${sw.phase}${sw.method ? " · " + sw.method : ""} · ${(sw.points || []).length} steps`
                : `${sw.mode === "camera" ? "Camera AF" : "Sweep"} ${sw.state}: ${sw.message || ""}`;
            $("focus-lens-msg").classList.toggle("text-warning", sw.state === "rejected" || sw.state === "failed");
            if (running || (st.showSweep === undefined && !st.active)) st.showSweep = true;
            st.sweep = sw;
            syncSweepPointsToLensHistory(sw);
            drawChart();
        }
        if (running !== lensSweepRunning) {
            lensSweepRunning = running;
            setLensPoll(running ? 700 : 5000);
            if (!running) drawChart();
        }
    }

    // Focus score against lens position for the last sweep, drawn in the main focus chart
    function drawSweepChart(c, w, h, sw) {
        if (sw && sw.points) {
            syncSweepPointsToLensHistory(sw);
        }
        drawLensPositionChart(c, w, h);
    }

    function setLensPoll(ms) {
        clearInterval(lensPoll);
        if (lensCard.hidden && !lensSweepRunning) ms = 15000;
        lensPoll = setInterval(async () => {
            // Full state, not just the lens: the server also starts focus mode by itself (AF sweeps)
            if (document.hidden) return;
            const seq = st.actionSeq;
            try {
                const s = await api("/api/focus");
                // Drop a reply requested before a user action: it would put back the old ROI / method
                if (seq === st.actionSeq) applyState(s);
            } catch (e) { /* retry */ }
        }, ms);
    }

    async function lensCmd(path, body) {
        try {
            const s = await api(path, { method: "POST", body });
            applyState(s);
            drawChart();
        } catch (e) { alert(`Lens: ${e.message}`); }
    }
    document.querySelectorAll("[data-lens-delta]").forEach((b) =>
        b.addEventListener("click", () => lensCmd("/api/focus/lens", { delta: parseFloat(b.dataset.lensDelta) })));
    document.querySelectorAll("[data-lens-step]").forEach((b) =>
        b.addEventListener("click", () => {
            const size = lensStepSize();
            if (size) lensCmd("/api/focus/lens", { delta: parseFloat(b.dataset.lensStep) * size });
        }));
    // Skip server updates only while the slider is being dragged (it keeps focus afterwards, so focus is no test)
    let sliderDragging = false;
    lensSlider.addEventListener("pointerdown", () => { sliderDragging = true; });
    window.addEventListener("pointerup", () => { sliderDragging = false; });
    lensSlider.addEventListener("input", () => {
        const val = parseFloat(lensSlider.value);
        if (isFinite(val)) {
            st.lensPos = val;
            $("focus-lens-pos").textContent = `${val.toFixed(3)} D`;
            const distStr = fmtDist(val);
            $("focus-lens-dist").textContent = distStr ? `(${distStr})` : "";
            $("focus-lens-dist").classList.toggle("text-warning", val > 1);
            updateLensStep(val);
            updateCurveSummary();
            drawLensChart();
        }
    });
    lensSlider.addEventListener("change", () => {
        sliderDragging = false;
        lensCmd("/api/focus/lens", { position: parseFloat(lensSlider.value) });
    });
    $("focus-lens-cancel").addEventListener("click", () => lensCmd("/api/focus/lens/cancel"));

    async function triggerAfSweep() {
        if (st.active) {
            st.showSweep = true;
            await lensCmd("/api/focus/lens", { mode: "auto" });
            setLensPoll(700);
            return;
        }
        // Not focusing yet: start focus mode, which in AF sweeps right away
        st.history = []; st.showSweep = true;
        try {
            const startBody = st.roiMode === "manual" ? { autofocus: true, roi_mode: "manual" } : { autofocus: true };
            const started = await api("/api/focus/start", { method: "POST", body: startBody });
            applyState(started);
            if (started.message) $("focus-statline").textContent = started.message;
            frameLoop(); startOverviewTimer(); setLensPoll(700);
        } catch (e) { alert(`Autofocus: ${e.message}`); }
    }

    document.querySelectorAll("#focus-lens-mode button").forEach((b) =>
        b.addEventListener("click", () => {
            if (b.dataset.lensMode === "auto") {
                triggerAfSweep();
            } else {
                lensCmd("/api/focus/lens", { mode: b.dataset.lensMode });
            }
        }));
    $("focus-refocus-every").addEventListener("change", (e) =>
        lensCmd("/api/focus/lens", { refocus_every_h: parseFloat(e.target.value) }));
    $("focus-refocus-now").addEventListener("click", triggerAfSweep);
    $("focus-run-af").addEventListener("click", triggerAfSweep);
    $("focus-lens-reset-learning").addEventListener("click", () => {
        if (confirm("Forget all focus-vs-temperature samples?")) lensCmd("/api/focus/lens", { reset_learning: true });
    });
    setLensPoll(5000);

    // Any click or key press on this page counts as someone focusing (keeps focus mode from timing out)
    let lastTouch = 0;
    const touch = () => {
        if (!st.active || Date.now() - lastTouch < 30000) return;
        lastTouch = Date.now();
        fetch("/api/focus/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
    };
    document.addEventListener("pointerdown", touch);
    document.addEventListener("keydown", touch);
    keepBtn.addEventListener("click", () => { lastTouch = 0; touch(); keepBtn.hidden = true; });

    // Chart metric toggle (Star Size HFR vs Sharpness)
    document.querySelectorAll("#focus-chart-metric button").forEach((b) => {
        b.addEventListener("click", () => {
            st.userPickedChartMetric = true;
            st.chartMetric = b.dataset.chartMetric;
            document.querySelectorAll("#focus-chart-metric button").forEach((btn) => btn.classList.toggle("active", btn === b));
            drawChart();
        });
    });

    // Record Point button
    const recordPointBtn = $("focus-record-point");
    if (recordPointBtn) {
        recordPointBtn.addEventListener("click", () => {
            if (st.lastResult) {
                recordLensMeasurement(st.lastResult);
                drawChart();
                recordPointBtn.textContent = "Recorded ✓";
                setTimeout(() => { recordPointBtn.textContent = "Record point"; }, 1200);
            } else {
                alert("Enable Focus Mode to receive frames and record measurement points.");
            }
        });
    }

    // Go to Best button
    const goBestBtn = $("focus-go-best");
    if (goBestBtn) {
        goBestBtn.addEventListener("click", () => {
            if (st.bestLensPos !== null && st.bestLensPos !== undefined) {
                lensSlider.value = st.bestLensPos;
                lensCmd("/api/focus/lens", { position: st.bestLensPos });
            }
        });
    }

    // Click on lens chart canvas to command lens position
    if (lensChart) {
        lensChart.addEventListener("click", (ev) => {
            const rect = lensChart.getBoundingClientRect();
            const x = ev.clientX - rect.left;
            const pad = st.chartPad;
            if (x < pad.l || x > rect.width - pad.r) return;
            const f = (x - pad.l) / (rect.width - pad.l - pad.r);
            const targetPos = st.chartXMin + f * (st.chartXMax - st.chartXMin);
            const clampedPos = Math.round(Math.max(0, Math.min(3, targetPos)) * 1000) / 1000;
            lensSlider.value = clampedPos;
            lensCmd("/api/focus/lens", { position: clampedPos });
        });

        // Hover tooltip on lens chart
        lensChart.addEventListener("mousemove", (ev) => {
            if (!st.lensHistory.length) {
                if (st.hoverPoint) { st.hoverPoint = null; drawLensChart(); }
                return;
            }
            const rect = lensChart.getBoundingClientRect();
            const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
            const pad = st.chartPad;
            let closest = null, minD = 24;
            st.lensHistory.forEach((p) => {
                const px = pad.l + ((p.pos - st.chartXMin) / (st.chartXMax - st.chartXMin)) * (rect.width - pad.l - pad.r);
                const py = st.chartYFunc ? st.chartYFunc(p) : 0;
                const dist = Math.hypot(mx - px, my - py);
                if (dist < minD) { minD = dist; closest = { point: p, x: px, y: py }; }
            });
            if (closest || st.hoverPoint) {
                st.hoverPoint = closest;
                drawLensChart();
            }
        });

        lensChart.addEventListener("mouseleave", () => {
            if (st.hoverPoint) { st.hoverPoint = null; drawLensChart(); }
        });
    }

    (async function init() {
        try {
            const s = await api("/api/focus");
            applyState(s);
            if (st.active) frameLoop();
        } catch (e) { console.warn(e); }
        startOverviewTimer();
        drawChart();
    })();
})();
