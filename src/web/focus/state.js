/**
 * Focus assistant: DOM references, state, state application and the frame stream.
 * Part of the Focus page script split (focus/*.js, loaded in order by index.html); shares helpers through window.ZenivoFocus.
 */
(function () {
    "use strict";
    const F = window.ZenivoFocus;
    if (!F || !F.onFocusPage) return;
    const { esc, fmtWhen, fmtRecScore, fmtRecLens, fmtExp, renderHistory, useRecord } = F;
    // Defined in files loaded later: call through F at run time
    const recordLensMeasurement = (...a) => F.recordLensMeasurement(...a);
    const drawChart = (...a) => F.drawChart(...a);
    const placeRoiBox = (...a) => F.placeRoiBox(...a);
    const startOverviewTimer = (...a) => F.startOverviewTimer(...a);
    const fmtDist = (...a) => F.fmtDist(...a);
    const applyLens = (...a) => F.applyLens(...a);
    const setLensPoll = (...a) => F.setLensPoll(...a);

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
    const lensCard = $("focus-lens-card");
    const lensSlider = $("focus-lens-slider");
    // Flags shared by several focus files (a plain `let` cannot cross file boundaries)
    // Skip server updates only while the slider is being dragged (it keeps focus afterwards, so focus is no test)
    const flags = { lensSweepRunning: false, sliderDragging: false };

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
        // One filled button: Start focus mode while off, Finish & save while on (Stop without saving is a plain pill)
        toggleBtn.textContent = st.active ? "Finish & save" : "Start focus mode";
        toggleBtn.title = st.active ? "Save the lens position and this result to Settings, then leave focus mode" : "Start focus mode";
        $("focus-finish").hidden = !st.active;
        $("focus-toggle-hint").hidden = st.active;
        $("focus-howto").classList.toggle("is-on", st.active);
        renderSaved(s);
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
        if (livePos !== undefined && livePos !== null && !flags.sliderDragging) {
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
            if (running && !flags.lensSweepRunning) {
                flags.lensSweepRunning = true;
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

    Object.assign(F, { $, api, applyState, badge, canvas, crossChk, drawZoom, flags, fmtScore, frameLoop, keepBtn, lensCard, lensChart, lensChartCtx, lensSlider, markersChk, overviewImg, overviewWrap, roiBox, roiSizeSel, setTxt, st, stretchChk, syncRoiModeUi, timeChart, timeChartCtx, toggleBtn, updateStats, wrap });
})();
