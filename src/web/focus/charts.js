/**
 * Focus assistant: lens-position and time-series chart drawing.
 * Part of the Focus page script split (focus/*.js, loaded in order by index.html); shares helpers through window.ZenivoFocus.
 */
(function () {
    "use strict";
    const F = window.ZenivoFocus;
    if (!F || !F.onFocusPage) return;
    const { st, fmtScore, $, fitQuadratic, updateCurveSummary, syncSweepPointsToLensHistory } = F;

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

    Object.assign(F, { drawLensPositionChart, drawTimeChart });
})();
