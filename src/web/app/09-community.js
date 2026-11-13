// Settings → Community: opt-in sharing of this station's card with other Zenivo stations (docs/COMMUNITY.md)
(function () {
    const box = document.getElementById("community-enabled");
    if (!box || currentPage !== "tab-settings") return;
    const nameEl = document.getElementById("community-name");
    const urlEl = document.getElementById("community-url");
    const thumbEl = document.getElementById("community-thumb");
    const seedsEl = document.getElementById("community-seeds");
    const saveBtn = document.getElementById("btn-community-save");
    const statusEl = document.getElementById("community-status");

    function ago(ts) {
        const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
        return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
    }

    function showStatus(st) {
        if (!st.available) {
            statusEl.textContent = `Not available: ${st.reason}`;
        } else if (!st.enabled) {
            statusEl.textContent = "Sharing is off.";
        } else {
            const r = st.last_round || {};
            const round = r.time ? `Last round ${ago(r.time)}: ${r.ok} of ${r.contacted} stations answered.` : "First round runs shortly.";
            statusEl.textContent = `Station id ${String(st.id || "").slice(0, 8)}. ${st.known} other stations known. ${round}`;
        }
        box.disabled = !st.available;
    }

    async function loadStatus() {
        try {
            const res = await fetch("/api/community/status");
            if (res.ok) showStatus(await res.json());
        } catch (err) { /* optional feature */ }
    }

    async function load() {
        try {
            const res = await fetch("/api/community/settings");
            if (!res.ok) return;
            const s = await res.json();
            box.checked = !!s.enabled;
            nameEl.value = s.name || "";
            urlEl.value = s.public_url || "";
            thumbEl.checked = !!s.share_thumbnail;
            seedsEl.value = (s.seeds || []).join("\n");
        } catch (err) { /* optional feature */ }
        loadStatus();
    }

    saveBtn.addEventListener("click", async () => {
        saveBtn.disabled = true;
        try {
            const res = await fetch("/api/community/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    enabled: box.checked,
                    name: nameEl.value.trim(),
                    public_url: urlEl.value.trim(),
                    share_thumbnail: thumbEl.checked,
                    seeds: seedsEl.value.split("\n").map((l) => l.trim()).filter(Boolean),
                }),
            });
            const data = await res.json();
            if (!res.ok) {
                const d = data.detail;
                throw new Error(Array.isArray(d) ? d.map((e) => e.msg).join("; ") : String(d || "Unknown error"));
            }
            showAlert(stationAlert, "Community settings saved", "success");
            setTimeout(loadStatus, 1500);
            loadStatus();
        } catch (err) {
            showAlert(stationAlert, `Failed to save Community settings: ${err.message}`, "error", true);
        } finally {
            saveBtn.disabled = false;
        }
    });

    load();
    setInterval(loadStatus, 30000);
})();
