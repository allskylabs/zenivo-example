/**
 * Focus assistant page (/focus).
 * Streams a native-resolution crop from /api/focus/frame as fast as the camera delivers frames
 * (long-poll: each request waits for the next capture), draws it zoomed without smoothing,
 * and plots the focus score (HFR for stars/lights, edge contrast for daytime) against its best value.
 */
(function () {
    "use strict";

    const onFocusPage = (window.location.pathname.replace(/\/+$/, "") || "/") === "/focus";
    if (!onFocusPage) {
        // Other pages: warn while focus mode holds the exposure (auto-exposure and archiving are paused)
        const bar = document.createElement("div");
        bar.className = "focus-banner";
        bar.hidden = true;
        bar.innerHTML = 'Focus mode is on: auto-exposure and archiving are paused. <a href="/focus">Open focus</a> <button class="btn btn-sm btn-outline">Stop focus mode</button>';
        const nav = document.querySelector(".tab-bar");
        if (nav) nav.after(bar);
        bar.querySelector("button").addEventListener("click", async () => {
            await fetch("/api/focus/stop", { method: "POST" });
            bar.hidden = true;
        });
        const check = async () => {
            try {
                const s = await (await fetch("/api/focus", { cache: "no-store" })).json();
                bar.hidden = !s.active;
            } catch (e) { /* offline: keep the last state */ }
        };
        check();
        setInterval(check, 20000);
        return;
    }

    const $ = (id) => document.getElementById(id);
    const canvas = $("focus-zoom-canvas");
    const ctx = canvas.getContext("2d");
    const wrap = $("focus-zoom-wrap");
    const emptyMsg = $("focus-zoom-empty");
    const chart = $("focus-chart");
    const chartCtx = chart.getContext("2d");
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

    const METHOD_LABEL = { stars: "Stars · HFR", lights: "Lights · HFR", contrast: "Day · edge contrast" };
    const HISTORY_MAX = 240;

    const st = {
        active: false,
        running: false,
        lastTs: 0,
        cx: 0.5,
        cy: 0.5,
        roiSize: 256,
        method: "auto",
        frameW: 0,
        frameH: 0,
        history: [],      // {t, score (raw), sm (running average from the server)}
        family: null,
        lastResult: null,
        bitmap: null,
        overviewTimer: null,
        fpsTimes: [],
    };

    function familyOf(method) {
        return method === "contrast" ? "contrast" : "hfr";
    }

    async function api(path, opts = {}) {
        const init = { method: opts.method || "GET", headers: {} };
        if (opts.body !== undefined) {
            init.headers["Content-Type"] = "application/json";
            init.body = JSON.stringify(opts.body);
        }
        const r = await fetch(path, init);
        if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
        return r.json();
    }

    function applyState(s) {
        st.active = !!s.active;
        st.cx = s.cx;
        st.cy = s.cy;
        st.roiSize = s.roi_size;
        st.method = s.method;
        if (s.frame_width) { st.frameW = s.frame_width; st.frameH = s.frame_height; }
        roiSizeSel.value = String(s.roi_size);
        document.querySelectorAll("#focus-method button").forEach((b) => b.classList.toggle("active", b.dataset.method === s.method));
        if (s.smooth) document.querySelectorAll("#focus-smooth button").forEach((b) => b.classList.toggle("active", +b.dataset.smooth === s.smooth));
        if (s.exposure_s !== undefined && document.activeElement !== $("focus-exp")) $("focus-exp").value = +s.exposure_s.toPrecision(4);
        if (s.gain_db !== undefined && document.activeElement !== $("focus-gain")) $("focus-gain").value = +s.gain_db.toFixed(1);
        toggleBtn.textContent = st.active ? "Stop focus mode" : "Start focus mode";
        toggleBtn.classList.toggle("btn-danger", st.active);
        badge.textContent = st.active ? "LIVE" : "OFF";
        badge.classList.toggle("on", st.active);
        showExpiry(s.expires_in_s);
        if (s.lens) applyLens(s.lens);
        emptyMsg.hidden = st.active && !!st.bitmap;
        placeRoiBox();
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
        const fam = familyOf(res.method);
        if (fam !== st.family) { st.history = []; st.family = fam; }
        if (res.score !== null && res.score !== undefined) {
            st.history.push({ t: res.frame_ts, score: res.score, sm: res.smoothed ?? res.score });
            if (st.history.length > HISTORY_MAX) st.history.shift();
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
            `${fps ? fps.toFixed(1) + " fps" : "…"} · exposure ${exp} · gain ${res.gain_db.toFixed(1)} dB · ` +
            `${res.capture_path || ""} · ROI ${res.roi[2]} px at (${res.roi[0]}, ${res.roi[1]}) · ` +
            `bg ${res.background} · noise ${res.noise} · ${res.calc_ms} ms`;
        updateStats(res);
    }

    function updateStats(res) {
        const vals = st.history.map((p) => p.sm);
        const f = (v) => (res ? fmtScore(res, v) : "—");
        $("focus-stat-noise").textContent = res && res.noise_pct != null ? `±${res.noise_pct.toFixed(1)} %` : "—";
        $("focus-stat-now").textContent = res ? f(res.smoothed ?? res.score) : "—";
        $("focus-stat-best").textContent = res ? f(res.best) : "—";
        $("focus-stat-min").textContent = vals.length ? f(Math.min(...vals)) : "—";
        $("focus-stat-max").textContent = vals.length ? f(Math.max(...vals)) : "—";
        $("focus-stat-n").textContent = String(vals.length);
    }

    function drawChart() {
        const dpr = window.devicePixelRatio || 1;
        const w = chart.clientWidth, h = chart.clientHeight;
        if (chart.width !== Math.round(w * dpr) || chart.height !== Math.round(h * dpr)) {
            chart.width = Math.round(w * dpr);
            chart.height = Math.round(h * dpr);
        }
        const c = chartCtx;
        c.setTransform(dpr, 0, 0, dpr, 0, 0);
        c.clearRect(0, 0, w, h);
        const pts = st.history;
        const res = st.lastResult;
        if (!res || pts.length < 2) {
            c.fillStyle = "#64748b";
            c.font = "12px sans-serif";
            c.fillText("Score history appears here", 12, h / 2);
            return;
        }
        const pad = { l: 56, r: 10, t: 14, b: 16 };
        const sm = pts.map((p) => p.sm);
        const raw = pts.map((p) => p.score);
        const lower = res.method !== "contrast";
        // Scale on the averaged curve, never tighter than ±8 % of its level (or 4x the frame noise):
        // otherwise a steady lens fills the chart with noise and every frame looks like a big jump
        let lo = Math.min(...sm), hi = Math.max(...sm);
        const mid = (lo + hi) / 2;
        const minSpan = Math.abs(mid) * Math.max(0.16, (8 * (res.noise_pct || 0)) / 100);
        if (hi - lo < minSpan) { lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
        const padV = (hi - lo) * 0.08;
        lo -= padV; hi += padV;
        const x = (i) => pad.l + (i / (HISTORY_MAX - 1)) * (w - pad.l - pad.r);
        // Sharper is always up: the HFR axis is inverted
        const y = (v) => {
            const f = (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo);
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
        // Noise band around the current average: changes inside it mean nothing
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
        // Raw frames as faint dots, the averaged score as the line
        c.fillStyle = "rgba(56,189,248,0.28)";
        raw.forEach((v, i) => { c.beginPath(); c.arc(x(i), y(v), 1.6, 0, Math.PI * 2); c.fill(); });
        c.strokeStyle = "#38bdf8";
        c.lineWidth = 2.5;
        c.beginPath();
        sm.forEach((v, i) => { if (i === 0) c.moveTo(x(i), y(v)); else c.lineTo(x(i), y(v)); });
        c.stroke();
        // Sharpest and softest (averaged) points, so the lens can be turned back to where the peak was
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
        overviewImg.src = `/api/focus/overview.jpg?width=800&stretch=${stretchChk.checked ? 1 : 0}&t=${Date.now()}`;
    }

    function startOverviewTimer() {
        clearInterval(st.overviewTimer);
        refreshOverview();
        st.overviewTimer = setInterval(refreshOverview, st.active ? 3000 : 15000);
    }

    // ------------------------------------------------------------------
    // Controls
    // ------------------------------------------------------------------
    async function configure(body) {
        st.history = [];
        st.lastResult = null;
        try {
            applyState(await api("/api/focus/config", { method: "POST", body }));
        } catch (e) { console.warn(e); }
        updateStats(null);
        drawChart();
    }

    toggleBtn.addEventListener("click", async () => {
        toggleBtn.disabled = true;
        try {
            if (st.active) {
                applyState(await api("/api/focus/stop", { method: "POST" }));
            } else {
                st.history = [];
                applyState(await api("/api/focus/start", { method: "POST", body: {} }));
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
    document.querySelectorAll("#focus-smooth button").forEach((b) =>
        b.addEventListener("click", () => configure({ smooth: parseInt(b.dataset.smooth, 10) })));
    roiSizeSel.addEventListener("change", () => configure({ roi_size: parseInt(roiSizeSel.value, 10) }));
    $("focus-reset").addEventListener("click", () => configure({ reset: true }));
    $("focus-center").addEventListener("click", () => configure({ cx: 0.5, cy: 0.5 }));
    [markersChk, crossChk].forEach((c) => c.addEventListener("change", drawZoom));
    stretchChk.addEventListener("change", refreshOverview);

    async function pickSource(lights) {
        try {
            const r = await api(`/api/focus/auto-roi?lights=${lights ? 1 : 0}`, { method: "POST" });
            if (!r.found) alert(lights ? "No bright point light found in the frame." : "No star found in the frame. Try a longer exposure or Find light.");
            st.history = [];
            applyState(r);
        } catch (e) { alert(e.message); }
    }
    $("focus-pick-star").addEventListener("click", () => pickSource(false));
    $("focus-pick-light").addEventListener("click", () => pickSource(true));

    overviewWrap.addEventListener("click", (ev) => {
        const rect = overviewImg.getBoundingClientRect();
        const cx = (ev.clientX - rect.left) / rect.width;
        const cy = (ev.clientY - rect.top) / rect.height;
        if (cx < 0 || cx > 1 || cy < 0 || cy > 1) return;
        st.cx = cx; st.cy = cy;
        placeRoiBox();
        configure({ cx, cy });
    });
    overviewImg.addEventListener("load", placeRoiBox);
    window.addEventListener("resize", () => { fitLayout(); placeRoiBox(); drawZoom(); drawChart(); });

    document.addEventListener("keydown", (ev) => {
        if (ev.target.closest("input, select, textarea")) return;
        if (ev.key === "r" || ev.key === "R") { configure({ reset: true }); return; }
        const step = st.frameW ? (st.roiSize / 4) : 0;
        if (!step) return;
        const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
        if (!d) return;
        ev.preventDefault();
        const cx = Math.min(1, Math.max(0, st.cx + (d[0] * step) / st.frameW));
        const cy = Math.min(1, Math.max(0, st.cy + (d[1] * step) / st.frameH));
        st.cx = cx; st.cy = cy;
        placeRoiBox();
        configure({ cx, cy });
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
        return d < 0.02 ? "(nominal ∞)" : `(≈ ${(1 / d).toFixed(d > 1 ? 2 : 1)} m)`;
    }

    function applyLens(L) {
        lensCard.hidden = !L.available;
        if (!L.available) return;
        const pos = L.position ?? L.held;
        $("focus-lens-pos").textContent = pos === null || pos === undefined ? "—" : `${pos.toFixed(3)} D`;
        $("focus-lens-dist").textContent = fmtDist(pos);
        lensSlider.max = Math.min(L.max, 3);
        lensSlider.min = L.min;
        if (document.activeElement !== lensSlider && pos !== null && pos !== undefined) lensSlider.value = pos;
        const AF = { 0: "AF idle", 1: "AF scanning", 2: "AF focused", 3: "AF failed" };
        $("focus-lens-af").textContent = L.af_state !== null && L.af_state !== undefined ? AF[L.af_state] || "" : "";
        const sv = L.saved;
        $("focus-lens-saved").textContent = sv
            ? `saved ${sv.position.toFixed(3)} D by ${sv.source}${sv.sensor_temp_c != null ? ` at ${sv.sensor_temp_c} °C` : ""} · ${new Date(sv.updated_at).toLocaleString()}`
            : `not saved yet — libcamera default ${L.default} D (≈ ${(1 / Math.max(L.default, 0.01)).toFixed(1)} m), not the sky`;
        const tr = L.tracking || {};
        const auto = L.mode === "auto";
        document.querySelectorAll("#focus-lens-mode button").forEach((b) => b.classList.toggle("active", b.dataset.lensMode === L.mode));
        const cp = L.compensation || {};
        $("focus-lens-comp").textContent = !auto
            ? "Manual: the lens stays exactly where you put it."
            : cp.learned
                ? `Temperature compensation: ${(cp.slope * 1000).toFixed(1)} mD/°C from ${cp.n} checks over ${cp.span_c} °C` +
                  ` (fit ±${cp.rms_d} D) · now ${cp.temp_c ?? "?"} °C → ${cp.target?.toFixed(3)} D`
                : `Learning temperature drift: ${cp.n || 0} checks over ${cp.span_c || 0} °C (needs 3 over 3 °C)` +
                  (cp.temp_c != null ? ` · sensor ${cp.temp_c} °C` : "");
        $("focus-lens-reset-learning").hidden = !auto || !(cp.n > 0);
        const last = (tr.log || [])[tr.log ? tr.log.length - 1 : 0];
        $("focus-lens-track-msg").textContent = !auto ? "" : tr.base === null || tr.base === undefined
            ? "Run a sweep first: tracking follows the swept position."
            : `around ${tr.base.toFixed(3)} D ±${tr.max_offset} · ` +
              (last ? `last ${new Date(last.t * 1000).toLocaleTimeString()}: ${last.from} → ${last.to} D${last.temp_c != null ? ` at ${last.temp_c} °C` : ""}` : "no check yet") +
              (tr.next_in_s != null ? ` · next star check in ${Math.round(tr.next_in_s / 60)} min (night only)` : "");

        const sw = L.sweep;
        const running = !!sw && sw.state === "running";
        $("focus-lens-cancel").hidden = !running;
        $("focus-lens-sweep").disabled = running;
        $("focus-lens-camaf").disabled = running;
        $("focus-lens-progress-wrap").hidden = !running;
        if (sw) {
            $("focus-lens-progress").style.width = Math.round((sw.progress || 0) * 100) + "%";
            $("focus-lens-msg").textContent = running
                ? `${sw.mode === "camera" ? "Camera AF" : "Sweep"} · ${sw.phase}${sw.method ? " · " + sw.method : ""} · ${(sw.points || []).length} steps`
                : `${sw.mode === "camera" ? "Camera AF" : "Sweep"} ${sw.state}: ${sw.message || ""}`;
            drawLensChart(sw);
        }
        if (running !== lensSweepRunning) {
            lensSweepRunning = running;
            setLensPoll(running ? 700 : 5000);
            if (!running) { st.history = []; st.lastResult = null; updateStats(null); drawChart(); }
        }
    }

    function drawLensChart(sw) {
        const cv = $("focus-lens-chart");
        const pts = (sw.points || []).filter((p) => p.score !== null && p.score !== undefined);
        cv.hidden = sw.mode === "camera" || pts.length < 2;
        if (cv.hidden) return;
        const dpr = window.devicePixelRatio || 1;
        const w = cv.clientWidth, h = cv.clientHeight;
        if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
        const c = cv.getContext("2d");
        c.setTransform(dpr, 0, 0, dpr, 0, 0);
        c.clearRect(0, 0, w, h);
        const lower = sw.method !== "contrast";
        const xs = pts.map((p) => p.pos), ys = pts.map((p) => p.score);
        const x0 = Math.min(...xs), x1 = Math.max(...xs, x0 + 0.05);
        const y0 = Math.min(...ys), y1 = Math.max(...ys, y0 * 1.01 + 1e-6);
        const X = (v) => 30 + ((v - x0) / (x1 - x0)) * (w - 38);
        const Y = (v) => { const f = (v - y0) / (y1 - y0); return 6 + (lower ? f : 1 - f) * (h - 22); };
        c.fillStyle = "#64748b";
        c.font = "10px ui-monospace, monospace";
        c.fillText(`${x0.toFixed(2)} D`, 30, h - 3);
        c.fillText(`${x1.toFixed(2)} D`, w - 46, h - 3);
        c.fillText("sharp", 0, 12);
        pts.forEach((p) => {
            c.fillStyle = p.phase === "fine" ? "#10b981" : "#38bdf8";
            c.beginPath(); c.arc(X(p.pos), Y(p.score), 2.5, 0, Math.PI * 2); c.fill();
        });
        if (sw.best_position !== undefined && sw.best_position !== null) {
            c.strokeStyle = "#f1f5f9";
            c.setLineDash([3, 3]);
            c.beginPath(); c.moveTo(X(sw.best_position), 4); c.lineTo(X(sw.best_position), h - 14); c.stroke();
            c.setLineDash([]);
        }
    }

    function setLensPoll(ms) {
        clearInterval(lensPoll);
        if (lensCard.hidden && !lensSweepRunning) ms = 15000;
        lensPoll = setInterval(async () => {
            try { const s = await api("/api/focus"); if (s.lens) applyLens(s.lens); } catch (e) { /* retry */ }
        }, ms);
    }

    async function lensCmd(path, body) {
        try {
            const s = await api(path, { method: "POST", body });
            applyState(s);
            st.history = []; st.lastResult = null; updateStats(null); drawChart();
        } catch (e) { alert(`Lens: ${e.message}`); }
    }
    document.querySelectorAll("[data-lens-delta]").forEach((b) =>
        b.addEventListener("click", () => lensCmd("/api/focus/lens", { delta: parseFloat(b.dataset.lensDelta) })));
    lensSlider.addEventListener("change", () => lensCmd("/api/focus/lens", { position: parseFloat(lensSlider.value) }));
    $("focus-lens-sweep").addEventListener("click", () => {
        if (!st.active) { alert("Start focus mode first, and put the zoom on stars, a light or a detailed distant object."); return; }
        lensCmd("/api/focus/lens/autofocus", { mode: "sweep" });
        setLensPoll(700);
    });
    $("focus-lens-camaf").addEventListener("click", () => {
        if (!st.active) { alert("Start focus mode first."); return; }
        lensCmd("/api/focus/lens/autofocus", { mode: "camera" });
        setLensPoll(700);
    });
    $("focus-lens-cancel").addEventListener("click", () => lensCmd("/api/focus/lens/cancel"));
    document.querySelectorAll("#focus-lens-mode button").forEach((b) =>
        b.addEventListener("click", () => lensCmd("/api/focus/lens", { mode: b.dataset.lensMode })));
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
