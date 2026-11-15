/**
 * Focus assistant: curve fit, lens-position measurements and chart dispatch.
 * Part of the Focus page script split (focus/*.js, loaded in order by index.html); shares helpers through window.ZenivoFocus.
 */
(function () {
    "use strict";
    const F = window.ZenivoFocus;
    if (!F || !F.onFocusPage) return;
    const { setTxt, wrap, lensChart, lensChartCtx, timeChart, timeChartCtx, flags, st, $ } = F;
    // Defined in files loaded later: call through F at run time
    const drawLensPositionChart = (...a) => F.drawLensPositionChart(...a);
    const drawTimeChart = (...a) => F.drawTimeChart(...a);

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
        if (flags.lensSweepRunning || (st.showSweep && st.sweep && (st.sweep.state === "done" || st.sweep.state === "rejected"))) {
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

    Object.assign(F, { fitQuadratic, recordLensMeasurement, updateCurveSummary, drawChart, drawLensChart, syncSweepPointsToLensHistory });
})();
