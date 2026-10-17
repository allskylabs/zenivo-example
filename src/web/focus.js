/**
 * Focus assistant page (/focus).
 * Streams a native-resolution crop from /api/focus/frame as fast as the camera delivers frames
 * (long-poll: each request waits for the next capture), draws it zoomed without smoothing,
 * and plots the focus score (HFR for stars/lights, edge contrast for daytime) against its best value.
 */
(function () {
    "use strict";

    const onFocusPage = (window.location.pathname.replace(/\/+$/, "") || "/") === "/focus";
    if (!onFocusPage) return;

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
        history: [],      // {t, score}
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
        if (s.exposure_s !== undefined && document.activeElement !== $("focus-exp")) $("focus-exp").value = +s.exposure_s.toPrecision(4);
        if (s.gain_db !== undefined && document.activeElement !== $("focus-gain")) $("focus-gain").value = +s.gain_db.toFixed(1);
        toggleBtn.textContent = st.active ? "Stop focus mode" : "Start focus mode";
        toggleBtn.classList.toggle("btn-danger", st.active);
        badge.textContent = st.active ? "LIVE" : "OFF";
        badge.classList.toggle("on", st.active);
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

    function onResult(res) {
        st.lastResult = res;
        st.frameW = res.frame_width;
        st.frameH = res.frame_height;
        const fam = familyOf(res.method);
        if (fam !== st.family) { st.history = []; st.family = fam; }
        if (res.score !== null && res.score !== undefined) {
            st.history.push({ t: res.frame_ts, score: res.score });
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
        $("focus-score-label").textContent = METHOD_LABEL[res.method] || res.method;
        $("focus-score-value").textContent = fmtScore(res, res.score);
        let sub = "";
        if (res.method === "contrast") sub = "higher is sharper";
        else if (res.score !== null && res.score !== undefined) sub = `FWHM ≈ ${res.fwhm} px · ${res.n_sources} source${res.n_sources === 1 ? "" : "s"} · lower is sharper`;
        else sub = "no point sources in the zoom region — move it or use Find star";
        $("focus-score-sub").textContent = sub;
        const q = res.quality_pct;
        $("focus-quality").textContent = q === null || q === undefined ? "—" : q.toFixed(1) + " %";
        $("focus-best").textContent = fmtScore(res, res.best);
        const fill = $("focus-meter-fill");
        // The bar spans 80-100 % of best: the last few percent are what fine focusing is about
        fill.style.width = Math.max(0, Math.min(100, ((q || 0) - 80) * 5)) + "%";
        fill.className = "focus-meter-fill " + (q >= 97 ? "good" : q >= 85 ? "near" : "far");

        // Trend from a 3-frame average against the 3 frames before it
        const h = st.history;
        let trend = "";
        if (h.length >= 6) {
            const avg = (a) => a.reduce((x, p) => x + p.score, 0) / a.length;
            const now = avg(h.slice(-3)), before = avg(h.slice(-6, -3));
            const lower = res.method !== "contrast";
            const rel = (now - before) / Math.max(1e-9, Math.abs(before));
            if (Math.abs(rel) > 0.02) trend = (lower ? rel < 0 : rel > 0) ? "▲ sharper" : "▼ softer";
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
        const vals = st.history.map((p) => p.score);
        const f = (v) => (res ? fmtScore(res, v) : "—");
        $("focus-stat-now").textContent = res ? f(res.smoothed ?? res.score) : "—";
        $("focus-stat-best").textContent = res ? f(res.best) : "—";
        $("focus-stat-min").textContent = vals.length ? f(Math.min(...vals)) : "—";
        $("focus-stat-max").textContent = vals.length ? f(Math.max(...vals)) : "—";
        $("focus-stat-n").textContent = String(vals.length);
    }

    function drawChart() {
        const dpr = window.devicePixelRatio || 1;
        const w = chart.clientWidth, h = chart.clientHeight;
        if (chart.width !== Math.round(w * dpr)) { chart.width = Math.round(w * dpr); chart.height = Math.round(h * dpr); }
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
        const pad = { l: 52, r: 10, t: 10, b: 18 };
        const vals = pts.map((p) => p.score);
        let lo = Math.min(...vals), hi = Math.max(...vals);
        if (hi - lo < 1e-9) { hi += Math.abs(hi) * 0.05 + 1e-3; lo -= Math.abs(lo) * 0.05 + 1e-3; }
        const lower = res.method !== "contrast";
        const x = (i) => pad.l + (i / (HISTORY_MAX - 1)) * (w - pad.l - pad.r);
        // Sharper is always up: HFR axis is inverted
        const y = (v) => {
            const f = (v - lo) / (hi - lo);
            return pad.t + (lower ? f : 1 - f) * (h - pad.t - pad.b);
        };
        c.strokeStyle = "#1e293b";
        c.fillStyle = "#64748b";
        c.font = "11px ui-monospace, monospace";
        c.lineWidth = 1;
        [lo, (lo + hi) / 2, hi].forEach((v) => {
            const yy = y(v);
            c.beginPath(); c.moveTo(pad.l, yy); c.lineTo(w - pad.r, yy); c.stroke();
            c.fillText(lower ? v.toFixed(2) : v.toPrecision(3), 4, yy + 4);
        });
        if (res.best !== null && res.best !== undefined && res.best >= lo && res.best <= hi) {
            c.strokeStyle = "rgba(16,185,129,0.7)";
            c.setLineDash([4, 4]);
            c.beginPath(); c.moveTo(pad.l, y(res.best)); c.lineTo(w - pad.r, y(res.best)); c.stroke();
            c.setLineDash([]);
        }
        // Sharpest and softest frames, so the lens can be turned back to where the peak was
        const iBest = vals.indexOf(lower ? lo : hi), iWorst = vals.indexOf(lower ? hi : lo);
        const mark = (i, color, text, above) => {
            const px = x(i), py = y(vals[i]);
            c.strokeStyle = color;
            c.setLineDash([2, 3]);
            c.beginPath(); c.moveTo(px, pad.t); c.lineTo(px, h - pad.b); c.stroke();
            c.setLineDash([]);
            c.fillStyle = color;
            c.beginPath(); c.arc(px, py, 4, 0, Math.PI * 2); c.fill();
            const tw = c.measureText(text).width;
            const tx = Math.min(Math.max(pad.l, px - tw / 2), w - pad.r - tw);
            c.fillText(text, tx, above ? Math.max(pad.t + 9, py - 8) : Math.min(h - 4, py + 16));
        };
        mark(iWorst, "rgba(239,68,68,0.85)", `softest ${fmtScore(res, vals[iWorst])}`, false);
        mark(iBest, "rgba(16,185,129,0.95)", `sharpest ${fmtScore(res, vals[iBest])}`, true);
        // Raw points and 3-frame moving average
        c.fillStyle = "rgba(56,189,248,0.55)";
        pts.forEach((p, i) => { c.beginPath(); c.arc(x(i), y(p.score), 2, 0, Math.PI * 2); c.fill(); });
        c.strokeStyle = "#38bdf8";
        c.lineWidth = 2;
        c.beginPath();
        pts.forEach((p, i) => {
            const win = pts.slice(Math.max(0, i - 2), i + 1);
            const v = win.reduce((a, q) => a + q.score, 0) / win.length;
            if (i === 0) c.moveTo(x(i), y(v)); else c.lineTo(x(i), y(v));
        });
        c.stroke();
        const last = pts.length - 1;
        c.fillStyle = "#f1f5f9";
        c.beginPath(); c.arc(x(last), y(vals[last]), 4, 0, Math.PI * 2); c.fill();
        c.fillStyle = "#64748b";
        c.fillText("sharper ↑", w - pad.r - 64, h - 4);
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
        overviewImg.src = `/api/focus/overview.jpg?width=800&t=${Date.now()}`;
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
    roiSizeSel.addEventListener("change", () => configure({ roi_size: parseInt(roiSizeSel.value, 10) }));
    $("focus-reset").addEventListener("click", () => configure({ reset: true }));
    $("focus-center").addEventListener("click", () => configure({ cx: 0.5, cy: 0.5 }));
    [markersChk, crossChk].forEach((c) => c.addEventListener("change", drawZoom));

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
