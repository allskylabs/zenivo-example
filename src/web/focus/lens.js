/**
 * Focus assistant: motorised lens control, chart interaction and page start-up.
 * Part of the Focus page script split (focus/*.js, loaded in order by index.html); shares helpers through window.ZenivoFocus.
 */
(function () {
    "use strict";
    const F = window.ZenivoFocus;
    if (!F || !F.onFocusPage) return;
    const { fmtWhen, canvas, wrap, lensChart, badge, keepBtn, lensCard, lensSlider, flags, st, api, applyState, frameLoop, $, recordLensMeasurement, updateCurveSummary, drawChart, drawLensChart, syncSweepPointsToLensHistory, drawLensPositionChart, startOverviewTimer } = F;

    // ------------------------------------------------------------------
    // Motorised lens (Pi Camera Module 3 ...)
    // ------------------------------------------------------------------
    let lensPoll = null;

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
        if (pos !== null && pos !== undefined && !flags.sliderDragging) {
            st.lensPos = pos;
            updateCurveSummary();
        }
        // Outside focus mode frames are seconds apart, so the reported position is simply stale
        const lag = st.active && L.held != null && L.position != null && Math.abs(L.held - L.position) > 0.003;
        if (!flags.sliderDragging) {
            $("focus-lens-pos").textContent = pos === null || pos === undefined ? "—" : `${pos.toFixed(3)} D`;
            const distStr = fmtDist(pos);
            const lagStr = lag ? ` · adjusting ${L.position.toFixed(3)} D` : "";
            $("focus-lens-dist").textContent = distStr ? `(${distStr}${lagStr})` : lagStr;
            $("focus-lens-dist").classList.toggle("text-warning", pos > 1);
        }
        lensSlider.max = Math.min(L.max, 3);
        lensSlider.min = L.min;
        if (!flags.sliderDragging && pos !== null && pos !== undefined) lensSlider.value = pos;
        if (!flags.sliderDragging) updateLensStep(pos);
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
        if (running !== flags.lensSweepRunning) {
            flags.lensSweepRunning = running;
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
        if (lensCard.hidden && !flags.lensSweepRunning) ms = 15000;
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
    lensSlider.addEventListener("pointerdown", () => { flags.sliderDragging = true; });
    window.addEventListener("pointerup", () => { flags.sliderDragging = false; });
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
        flags.sliderDragging = false;
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

    Object.assign(F, { fmtDist, applyLens, setLensPoll });
})();
