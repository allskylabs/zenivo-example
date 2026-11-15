/**
 * Focus assistant page (/focus): shared helpers, saved-focus history, and the banner shown on the other pages.
 * First of the focus/*.js files; the rest (state, curve, charts, controls, lens) stream and draw the page itself.
 * Streams a native-resolution crop from /api/focus/frame as fast as the camera delivers frames
 * (long-poll: each request waits for the next capture), draws it zoomed without smoothing,
 * and plots the focus score (HFR for stars/lights, edge contrast for daytime) against its best value.
 */
(function () {
    "use strict";
    const F = (window.ZenivoFocus = {});

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

    // Shared with the other focus/*.js files (loaded after this one; they do nothing off the /focus page)
    Object.assign(F, { onFocusPage, esc, fmtWhen, fmtRecScore, fmtRecLens, fmtExp, renderHistory, useRecord });

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
})();
