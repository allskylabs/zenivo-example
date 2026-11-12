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
    const toastStack = document.getElementById("toast-stack");
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

    function showToast(entry) {
        if (!toastStack) return;
        const t = el("div", `toast lvl-${entry.level}`);
        t.appendChild(el("strong", "", `${entry.level} • ${entry.category}`));
        t.appendChild(el("span", "", entry.message));
        t.addEventListener("click", () => t.remove());
        toastStack.appendChild(t);
        const maxToasts = window.matchMedia("(max-width: 768px)").matches ? 2 : 4;
        while (toastStack.children.length > maxToasts) toastStack.firstChild.remove();
        const ttl = entry.level === "error" ? 20000 : 8000;
        setTimeout(() => t.remove(), ttl);
    }

    function renderList(entries) {
        if (!list) return;
        list.replaceChildren();
        if (!entries.length) {
            list.appendChild(el("li", "log-summary", "No entries match this filter."));
            return;
        }
        for (const e of entries) {
            const li = el("li", `log-item lvl-${e.level}`);
            li.appendChild(el("span", "log-time", fmtTime(e.ts)));
            li.appendChild(el("span", "log-level", e.level));
            li.appendChild(el("span", "log-cat", e.category));
            const msg = el("span", "log-msg", e.message);
            if (e.repeat > 1) {
                msg.appendChild(el("span", "log-repeat", `×${e.repeat} (last ${fmtTime(e.last_ts)})`));
            }
            if (e.details) li.title = JSON.stringify(e.details, null, 1);
            li.appendChild(msg);
            list.appendChild(li);
        }
    }

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
            summaryEl.textContent = `Last 24h: ${c.success || 0} success • ${c.info || 0} info • ${c.warning || 0} warnings • ${c.error || 0} errors`;
        }
        updateBadge(c);
    }

    async function loadStorage() {
        if (!storageEl) return;
        try {
            const res = await fetch("/api/storage");
            if (!res.ok) return;
            const s = await res.json();
            const p = s.policy || {};
            const d = s.disk || {};
            storageEl.textContent =
                `Storage: data ${Number(s.data_gb).toFixed(2)} / ${p.max_data_gb} GB • ${s.frame_files} archived frames over ${(s.frame_days || []).length} day(s) • ` +
                `disk free ${d.free_gb} of ${d.total_gb} GB • keep frames ${p.frames_keep_days} d, videos/keograms ${p.products_keep_days} d`;
        } catch (_) { /* ignore */ }
    }

    async function poll() {
        if (document.hidden) return;
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
        loadPage().catch((err) => { if (summaryEl) summaryEl.textContent = `Could not load log: ${err.message}`; });
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

    poll();
    setInterval(poll, POLL_MS);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
})();
