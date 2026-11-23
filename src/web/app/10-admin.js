// Settings: shared helpers for the Community/Admin groups: info icons toggle their note, saConfirm() replaces confirm() with a ds-sheet
(function () {
    if (currentPage !== "tab-settings") return;
    document.addEventListener("click", (ev) => {
        const b = ev.target.closest(".sa-info");
        if (!b) return;
        const note = document.getElementById(b.dataset.info);
        if (!note) return;
        const open = note.hidden;
        note.hidden = !open;
        b.setAttribute("aria-expanded", String(open));
    });
    const sheet = document.getElementById("sa-confirm");
    if (!sheet || !window.DS) return;
    const ok = document.getElementById("sa-confirm-ok");
    let resolve = null;
    function settle(v) { if (resolve) { const r = resolve; resolve = null; r(v); } }
    new MutationObserver(() => { if (sheet.dataset.open !== "true") settle(false); }).observe(sheet, { attributes: true, attributeFilter: ["data-open"] });
    ok.addEventListener("click", () => { settle(true); DS.closeSheet(sheet); });
    window.saConfirm = (title, text) => new Promise((res) => {
        settle(false);
        resolve = res;
        document.getElementById("sa-confirm-title").textContent = title;
        document.getElementById("sa-confirm-text").textContent = text;
        DS.openSheet(sheet);
    });
})();

// Settings → Admin: public location precision, change password, sign out everywhere (docs/SECURITY.md)
(function () {
    const sel = document.getElementById("admin-precision");
    if (!sel || currentPage !== "tab-settings") return;
    const form = document.getElementById("admin-password-form");
    const cur = document.getElementById("admin-current");
    const next = document.getElementById("admin-new");
    const allBtn = document.getElementById("btn-admin-logout-all");
    const statusEl = document.getElementById("admin-status");

    function say(msg, type) { showAlert(stationAlert, msg, type, type === "error"); statusEl.textContent = msg; }
    function detail(data) {
        const d = data && data.detail;
        return Array.isArray(d) ? d.map((e) => e.msg).join("; ") : String(d || "Unknown error");
    }

    async function load() {
        try {
            const res = await fetch("/api/community/settings");
            if (!res.ok) return;
            sel.value = String((await res.json()).public_precision_deg);
        } catch (err) { /* admin only: guests never get here */ }
    }

    // The location precision saves itself when changed
    sel.addEventListener("change", () =>
        settingsSave(sel, () => sendJson("/api/community/settings", { public_precision_deg: Number(sel.value) })));

    form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        if (next.value.length < 10) return say("The new password needs at least 10 characters.", "error");
        try {
            const res = await fetch("/api/auth/password", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ current: cur.value, new: next.value }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(detail(data));
            cur.value = next.value = "";
            say("Password changed; other browsers were signed out.", "success");
        } catch (err) {
            say(`Password not changed: ${err.message}`, "error");
        }
    });

    allBtn.addEventListener("click", async () => {
        try {
            const res = await fetch("/api/auth/logout-all", { method: "POST" });
            if (!res.ok) throw new Error(detail(await res.json()));
            window.zenivoAuth.set(false);
            location.href = "/login?next=/settings";
        } catch (err) {
            say(`Failed: ${err.message}`, "error");
        }
    });

    // System tiles: host load, refreshed every 10 s while the Admin section is on screen and the tab is visible
    const card = document.getElementById("admin-card");
    const tiles = {};
    document.querySelectorAll("#admin-system .sa-tile").forEach((t) => { tiles[t.dataset.k] = t; });
    function setTile(k, pct, text, sub, warnAt) {
        const t = tiles[k];
        if (!t) return;
        t.querySelector("b").textContent = text;
        const small = t.querySelector("small");
        if (small) small.textContent = sub || "";
        const v = pct == null ? 0 : Math.max(0, Math.min(100, pct));
        const bar = t.querySelector("u");
        if (bar) bar.style.width = `${v}%`;
        t.classList.toggle("warn", pct != null && pct >= warnAt && pct < 90);
        t.classList.toggle("crit", pct != null && pct >= 90);
    }
    let sysBusy = false;
    async function pollSystem() {
        if (sysBusy || document.hidden || !card || card.offsetParent === null) return;
        sysBusy = true;
        try {
            const res = await fetch("/api/system/resources", { cache: "no-store" });
            if (!res.ok) return;
            const d = await res.json();
            const p = (v) => (v == null ? "--" : `${Math.round(v)}%`);
            setTile("cpu", d.cpu_pct, p(d.cpu_pct), "", 70);
            setTile("ram", d.ram_pct, p(d.ram_pct), d.ram_total_mb ? `${d.ram_used_mb} / ${d.ram_total_mb} MB` : "", 75);
            setTile("disk", d.disk_pct, p(d.disk_pct), d.disk_free_gb != null ? `${d.disk_free_gb} GB free` : "", 75);
            // the bar runs 0-90 C; a Pi throttles from 80 C
            if (tiles.uptime) {
                const u = d.uptime_s;
                const days = Math.floor(u / 86400), hrs = Math.floor((u % 86400) / 3600), mins = Math.floor((u % 3600) / 60);
                tiles.uptime.querySelector("b").textContent = u == null ? "--" : days ? `${days} d` : hrs ? `${hrs} h` : `${mins} m`;
                tiles.uptime.querySelector("small").textContent = u == null ? "" : days ? `${hrs} h ${mins} m` : hrs ? `${mins} m` : "";
            }
            setTile("temp", d.temp_c == null ? null : (d.temp_c / 90) * 100, d.temp_c == null ? "--" : `${Math.round(d.temp_c)} \u00b0C`, "", (75 / 90) * 100);
        } catch (err) { /* offline: keep the last numbers */ } finally { sysBusy = false; }
    }
    pollSystem();
    setInterval(pollSystem, 10000);
    document.addEventListener("visibilitychange", pollSystem);
    window.addEventListener("hashchange", () => setTimeout(pollSystem, 100));

    load();
})();

// Settings → Admin: software update (check automatically, update by hand; /api/update/*)
(function () {
    const line = document.getElementById("upd-line");
    if (!line) return;
    const $ = (id) => document.getElementById(id);
    const auto = $("upd-auto"), btnCheck = $("upd-check"), btnApply = $("upd-apply"), btnBack = $("upd-rollback");
    const notes = $("upd-notes"), statusEl = $("upd-status");
    let polling = 0;
    const when = (t) => (t ? new Date(t * 1000).toLocaleString() : "never");

    function render(st) {
        const l = st.latest;
        line.textContent = `Installed ${st.current}. ` + (st.available
            ? `Version ${l.version} is available${l.prerelease ? " (pre-release)" : ""}.`
            : (l ? `You are up to date (latest ${l.version}).` : "No release information yet.")) + ` Last checked: ${when(st.checked_at)}.`
            + (st.error ? ` Last check failed: ${st.error}` : "");
        auto.checked = !!st.auto_check;
        btnApply.hidden = !st.available;
        btnBack.hidden = !(st.backups && st.backups.length);
        notes.hidden = !(st.available && l && l.notes);
        if (!notes.hidden) notes.innerHTML = `<a href="${escapeHtml(l.url || "#")}" target="_blank" rel="noopener">Release notes</a>`;
        const a = st.apply || {};
        if (a.state === "running") statusEl.textContent = `Updating: ${a.message || a.step}`;
        else if (a.state === "error") statusEl.textContent = `Update failed: ${a.message}`;
        else if (a.state === "done") statusEl.textContent = a.message;
        else if (!st.systemd) statusEl.textContent = "Not running under systemd: after an update, restart Zenivo yourself.";
        else statusEl.textContent = "";
        document.querySelectorAll('.sh-row[data-section="admin"]').forEach((el) => el.classList.toggle("has-update", !!st.available));
        return a.state;
    }
    async function call(path, body) {
        const res = await fetch(path, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body || {}) });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.status);
        return data;
    }
    async function refresh() {
        try {
            const state = render(await call("/api/update/status"));
            if (state === "running" && !polling) polling = setInterval(refresh, 2500);
            if (state !== "running" && polling) { clearInterval(polling); polling = 0; }
            // the service restarts after an update: reload once it answers again with the new version
        } catch (e) {
            if (polling) statusEl.textContent = "Restarting the service...";
        }
    }
    btnCheck.addEventListener("click", async () => {
        btnCheck.disabled = true;
        try { render(await call("/api/update/check", {})); } catch (e) { statusEl.textContent = `Check failed: ${e.message}`; }
        btnCheck.disabled = false;
    });
    auto.addEventListener("change", () => call("/api/update/settings", { auto_check: auto.checked }).then(render).catch((e) => { statusEl.textContent = e.message; }));
    btnApply.addEventListener("click", async () => {
        if (!(await saConfirm("Update now?", "Downloads and installs the new version. The station restarts and is offline for about a minute."))) return;
        try { render(await call("/api/update/apply", {})); if (!polling) polling = setInterval(refresh, 2500); } catch (e) { statusEl.textContent = `Could not start: ${e.message}`; }
    });
    btnBack.addEventListener("click", async () => {
        if (!(await saConfirm("Go back?", "Restores the program files saved before the last update and restarts."))) return;
        try { render(await call("/api/update/rollback", {})); if (!polling) polling = setInterval(refresh, 2500); } catch (e) { statusEl.textContent = `Could not go back: ${e.message}`; }
    });
    refresh();
})();

// Settings → Admin: restart the service on demand (POST /api/system/restart)
(function () {
    const btn = document.getElementById("btn-restart");
    if (!btn) return;
    const out = document.getElementById("restart-status");
    btn.addEventListener("click", async () => {
        if (!(await saConfirm("Restart Zenivo?", "The station is offline for about 30 seconds."))) return;
        btn.disabled = true;
        out.textContent = "Restarting...";
        try {
            const res = await fetch("/api/system/restart", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
            if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.status);
        } catch (e) {
            out.textContent = `Could not restart: ${e.message}`;
            btn.disabled = false;
            return;
        }
        // wait for the service to go away and come back, then reload
        let down = false;
        const t0 = Date.now();
        const timer = setInterval(async () => {
            let up = false;
            try { up = (await fetch("/api/status", { cache: "no-store" })).ok; } catch (e) { up = false; }
            if (!up) down = true;
            if ((up && down) || Date.now() - t0 > 180000) {
                clearInterval(timer);
                if (up) location.reload(); else { out.textContent = "Still offline after 3 minutes: check the station."; btn.disabled = false; }
            }
        }, 3000);
    });
})();
