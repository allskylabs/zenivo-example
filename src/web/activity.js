/**
 * Activity log page (/logs) and toast notifications on every page.
 * Polls /api/logs for new entries; warnings, errors and notable successes pop up as toasts,
 * and the nav badge shows how many errors happened in the last 24 hours.
 */
(function () {
    "use strict";

    const POLL_MS = 10000;
    const TOAST_SUCCESS_CATEGORIES = new Set(["calibration", "station", "storage", "camera"]);
    const list = document.getElementById("log-list");
    const summaryEl = document.getElementById("log-summary");
    const storageEl = document.getElementById("log-storage");
    const levelSel = document.getElementById("log-filter-level");
    const catSel = document.getElementById("log-filter-category");
    const badge = document.getElementById("log-error-badge");
    const cleanupBtn = document.getElementById("btn-storage-cleanup");
    const onLogsPage = (window.location.pathname.replace(/\/+$/, "") || "/") === "/logs";

    let lastSeenId = null;

    function fmtTime(iso) {
        const d = new Date(iso);
        if (isNaN(d)) return iso || "";
        const pad = (n) => String(n).padStart(2, "0");
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    }

    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    const KIND = { success: "ok", warning: "warn", error: "danger", info: "info" };
    const LV_ICON = { success: "circle-check", warning: "triangle-alert", error: "circle-x", info: "info" };
    const CAT_ICON = { system: "cpu", camera: "camera", capture: "image", archive: "archive", timelapse: "film", products: "layers", storage: "hard-drive", sync: "refresh-cw", calibration: "sliders-horizontal", station: "map-pin" };

    function icon(name) {
        return window.DS ? window.DS.icon(name) : document.createElement("span");
    }

    function showToast(entry) {
        if (!window.DS) return;
        window.DS.toast(`${entry.category}: ${entry.message}`, KIND[entry.level] || "info", entry.level === "error" ? 12000 : 6000);
    }

    function clock(iso) {
        const d = new Date(iso);
        if (isNaN(d)) return iso || "";
        const pad = (n) => String(n).padStart(2, "0");
        return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    }

    function renderList(entries) {
        if (!list) return;
        list.replaceChildren();
        if (!entries.length) {
            const li = el("li", "ds-empty");
            li.appendChild(icon("inbox"));
            li.appendChild(el("p", "ds-empty__title", "No entries"));
            list.appendChild(li);
            return;
        }
        const today = new Date().toDateString();
        const yest = new Date(Date.now() - 86400000).toDateString();
        let lastDay = "";
        for (const e of entries) {
            const dt = new Date(e.ts);
            const dayKey = isNaN(dt) ? "" : dt.toDateString();
            if (dayKey && dayKey !== lastDay) {
                lastDay = dayKey;
                const label = dayKey === today ? "Today" : dayKey === yest ? "Yesterday"
                    : dt.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
                list.appendChild(el("li", "lg-day", label));
            }
            const li = el("li", `ds-row lg-item lg-${e.level}`);
            const ic = el("span", "ds-row__icon");
            ic.appendChild(icon(LV_ICON[e.level] || "info"));
            const body = el("span", "ds-row__body");
            const title = el("span", "ds-row__title lg-msg", e.message);
            const sub = el("span", "ds-row__sub");
            sub.appendChild(icon(CAT_ICON[e.category] || "activity"));
            sub.appendChild(el("span", "", e.category));
            if (e.repeat > 1) sub.appendChild(el("span", "ds-chip lg-rep", `×${e.repeat}`));
            body.append(title, sub);
            const tm = el("span", "ds-row__value lg-time", clock(e.ts));
            tm.title = e.repeat > 1 ? `${fmtTime(e.ts)} → ${fmtTime(e.last_ts)}` : fmtTime(e.ts);
            if (e.details) li.title = JSON.stringify(e.details, null, 1);
            li.append(ic, body, tm);
            list.appendChild(li);
        }
    }

    // Level and category chips drive the hidden selects (which stay the source of truth for loadPage)
    function wireChips(boxId, sel, attr) {
        const box = document.getElementById(boxId);
        if (!box || !sel) return;
        box.addEventListener("click", (ev) => {
            const b = ev.target.closest("button.ds-chip");
            if (!b) return;
            sel.value = b.dataset[attr];
            sel.dispatchEvent(new Event("change", { bubbles: true }));
            box.querySelectorAll("button.ds-chip").forEach((c) => {
                const on = c === b;
                c.classList.toggle("ds-chip--on", on);
                c.setAttribute("aria-pressed", String(on));
            });
        });
    }
    wireChips("log-level-chips", levelSel, "level");
    wireChips("log-cat-chips", catSel, "cat");

    function updateBadge(counts) {
        if (!badge || !counts) return;
        const n = counts.error || 0;
        badge.textContent = String(n);
        badge.hidden = n === 0;
        badge.title = `${n} error(s) in the last 24 hours`;
    }

    async function loadPage() {
        const params = new URLSearchParams({ limit: "300" });
        if (levelSel && levelSel.value) params.set("level", levelSel.value);
        if (catSel && catSel.value) params.set("category", catSel.value);
        const res = await fetch(`/api/logs?${params}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderList(data.entries || []);
        const c = data.counts_24h || {};
        if (summaryEl) {
            summaryEl.replaceChildren();
            summaryEl.dataset.worst = c.error ? "error" : c.warning ? "warning" : "info";
            [["success", "ok"], ["warning", "warn"], ["error", "danger"]].forEach(([k, cls]) => {
                const n = c[k] || 0;
                const chip = el("span", `ds-chip ds-chip--${cls}${n ? "" : " lg-zero"}`);
                chip.appendChild(icon(LV_ICON[k]));
                chip.appendChild(el("b", "", String(n)));
                chip.title = `${n} ${k} in the last 24 h`;
                summaryEl.appendChild(chip);
            });
            const h = icon("clock");
            h.classList.add("lg-when");
            h.setAttribute("title", "last 24 h");
            summaryEl.appendChild(h);
        }
        updateBadge(c);
    }

    // The sign-in state arrives a moment after load: fetch the admin-only storage line once it says "admin"
    document.addEventListener("zenivo-auth", (ev) => { if (ev.detail && ev.detail.admin) loadStorage(); });

    async function loadStorage() {
        if (!storageEl) return;
        if (!(window.zenivoAuth && window.zenivoAuth.admin)) return;  // storage details are admin-only
        try {
            const res = await fetch("/api/storage");
            if (!res.ok) return;
            const s = await res.json();
            const p = s.policy || {};
            const d = s.disk || {};
            const items = [
                ["hard-drive", `${Number(s.data_gb).toFixed(1)}/${p.max_data_gb} GB`, "Data used / limit"],
                ["image", String(s.frame_files), `Archived frames over ${(s.frame_days || []).length} day(s)`],
                ["memory-stick", `${d.free_gb} GB`, `Disk free of ${d.total_gb} GB`],
                ["calendar-days", `${p.frames_keep_days} / ${p.products_keep_days} d`, "Keep frames / videos+keograms"],
            ];
            storageEl.replaceChildren();
            for (const [ic, v, tip] of items) {
                const t = el("span", "ds-chip");
                t.appendChild(icon(ic));
                t.appendChild(el("b", "", v));
                t.title = tip;
                storageEl.appendChild(t);
            }
            storageEl.hidden = false;
        } catch (_) { /* ignore */ }
    }

    async function poll() {
        if (document.hidden) return;
        // The activity log is admin-only: guests do not poll it (the server would answer 401)
        if (!(window.zenivoAuth && window.zenivoAuth.admin)) return;
        try {
            const since = lastSeenId === null ? 0 : lastSeenId;
            const res = await fetch(`/api/logs?since_id=${since}&limit=50`);
            if (!res.ok) return;
            const data = await res.json();
            updateBadge(data.counts_24h);
            if (lastSeenId !== null) {
                const fresh = (data.entries || []).slice().reverse();
                for (const e of fresh) {
                    if (e.level === "error" || e.level === "warning" || (e.level === "success" && TOAST_SUCCESS_CATEGORIES.has(e.category))) {
                        showToast(e);
                    }
                }
                if (onLogsPage && fresh.length) loadPage().catch(() => {});
            }
            lastSeenId = data.last_id || 0;
        } catch (_) { /* offline: try again next tick */ }
    }

    if (onLogsPage) {
        loadPage().catch((err) => { if (list) { list.replaceChildren(); const li = el("li", "ds-empty"); li.appendChild(icon("wifi-off")); li.appendChild(el("p", "ds-empty__title", err.message)); list.appendChild(li); } });
        loadStorage();
        setInterval(loadStorage, 60000);
        if (levelSel) levelSel.addEventListener("change", () => loadPage().catch(() => {}));
        if (catSel) catSel.addEventListener("change", () => loadPage().catch(() => {}));
    }
    if (cleanupBtn) {
        cleanupBtn.addEventListener("click", async () => {
            cleanupBtn.disabled = true;
            try {
                const res = await fetch("/api/storage/cleanup", { method: "POST" });
                const r = await res.json();
                showToast({
                    level: r.success === false ? "error" : "success",
                    category: "storage",
                    message: r.success === false ? `Cleanup failed: ${r.error}` : `Cleanup done: removed ${r.deleted_count} item(s), freed ${r.freed_mb} MB`,
                });
                loadStorage();
                loadPage().catch(() => {});
            } catch (err) {
                showToast({ level: "error", category: "storage", message: `Cleanup request failed: ${err.message}` });
            } finally {
                cleanupBtn.disabled = false;
            }
        });
    }

    window.zenivoPollLogs = poll;
    poll();
    setInterval(() => { if (!(window.overlayPushOn && window.overlayPushOn("logs"))) poll(); }, POLL_MS);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
})();
