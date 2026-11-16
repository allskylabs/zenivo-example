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
        document.querySelectorAll('a[data-sub="admin"]').forEach((el) => el.classList.toggle("has-update", !!st.available));
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
        if (!confirm("Download and install the new version now? The station restarts and is offline for about a minute.")) return;
        try { render(await call("/api/update/apply", {})); if (!polling) polling = setInterval(refresh, 2500); } catch (e) { statusEl.textContent = `Could not start: ${e.message}`; }
    });
    btnBack.addEventListener("click", async () => {
        if (!confirm("Restore the program files saved before the last update and restart?")) return;
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
        if (!confirm("Restart Zenivo now? The station is offline for about 30 seconds.")) return;
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
