/**
 * Focus assistant: ROI overview, configuration, source picking, exposure and layout controls.
 * Part of the Focus page script split (focus/*.js, loaded in order by index.html); shares helpers through window.ZenivoFocus.
 */
(function () {
    "use strict";
    const F = window.ZenivoFocus;
    if (!F || !F.onFocusPage) return;
    const { toggleBtn, overviewImg, overviewWrap, roiBox, roiSizeSel, stretchChk, markersChk, crossChk, peakChk, st, api, syncRoiModeUi, applyState, frameLoop, drawZoom, updateStats, $, recordLensMeasurement, updateCurveSummary, drawChart } = F;
    // Defined in files loaded later: call through F at run time
    const setLensPoll = (...a) => F.setLensPoll(...a);

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

    // "Finish & save" is the primary button (#focus-toggle while focus mode is on); #focus-finish is the secondary "Stop without saving"
    async function finishAndSave() {
        const b = toggleBtn;
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
    }

    $("focus-finish").addEventListener("click", async () => {
        const b = $("focus-finish");
        b.disabled = true;
        try {
            applyState(await api("/api/focus/stop", { method: "POST" }));
            startOverviewTimer();
        } catch (e) {
            alert(`Focus mode: ${e.message}`);
        } finally {
            b.disabled = false;
        }
    });

    toggleBtn.addEventListener("click", async () => {
        if (st.active) { finishAndSave(); return; }
        toggleBtn.disabled = true;
        try {
            {
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
    [markersChk, crossChk, peakChk].forEach((c) => c.addEventListener("change", drawZoom));
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

    Object.assign(F, { placeRoiBox, startOverviewTimer });
})();
