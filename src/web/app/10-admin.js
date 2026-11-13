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
