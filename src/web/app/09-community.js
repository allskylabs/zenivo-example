// Settings → Community: opt-in sharing of this station's card with other Zenivo stations (docs/COMMUNITY.md)
(function () {
    const box = document.getElementById("community-enabled");
    if (!box || currentPage !== "tab-settings") return;
    const nameEl = document.getElementById("community-name");
    const urlEl = document.getElementById("community-url");
    const thumbEl = document.getElementById("community-thumb");
    const previewEl = document.getElementById("community-preview");
    const openEl = document.getElementById("community-open");
    const seedsEl = document.getElementById("community-seeds");
    const saveBtn = document.getElementById("btn-community-save");
    const statusEl = document.getElementById("community-status");

    function ago(ts) {
        const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
        return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
    }

    let defaultSeeds = [];
    const btnSeeds = document.getElementById("btn-community-seeds-default");
    // Puts the built-in seeds back at the top of the list (nothing is saved until Save is pressed)
    if (btnSeeds) btnSeeds.addEventListener("click", () => {
        const mine = seedsEl.value.split("\n").map((l) => l.trim()).filter(Boolean);
        seedsEl.value = defaultSeeds.concat(mine.filter((u) => !defaultSeeds.includes(u))).join("\n");
    });

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
        defaultSeeds = Array.isArray(st.default_seeds) ? st.default_seeds : defaultSeeds;
        showHealth(st);
        box.disabled = !st.available;
    }

    // Network health: is this station part of the network, and how did each seed/station answer in the last round
    const NET = {
        off: ["Off", "Sharing is off: this station is not part of the network."],
        starting: ["Starting", "The first round runs about 20 seconds after sharing is turned on."],
        connected: ["Connected", "This station reaches other stations and other stations reach it."],
        outbound_only: ["Connected (outgoing only)", "This station reaches others, but nobody has called in during the last 2 hours. That is normal without a public URL, or right after joining."],
        inbound_only: ["Reachable only", "Other stations call in, but none of the addresses below answered in the last round."],
        isolated: ["Isolated", "No station answered in the last round and nobody called in. Check the seed list and the internet connection."],
    };
    const healthEl = document.getElementById("community-health");
    const netEl = document.getElementById("community-net");
    const netNote = document.getElementById("community-net-note");
    const targetsEl = document.getElementById("community-targets");
    const checkBtn = document.getElementById("btn-community-check");

    function showHealth(st) {
        if (!healthEl) return;
        healthEl.hidden = !st.available;
        const net = st.network || { state: "off" };
        const [label, note] = NET[net.state] || NET.off;
        netEl.dataset.state = net.state;
        netEl.textContent = `Network: ${label}`;
        const calls = net.inbound_1h ? ` Other stations called in ${net.inbound_1h} time(s) in the last hour.` : "";
        netNote.textContent = st.enabled ? `${note}${calls} ${st.contacts || 0} address(es) remembered for when the seeds are down.` : note;
        checkBtn.disabled = !st.enabled;
        targetsEl.replaceChildren();
        for (const t of st.targets || []) {
            const li = document.createElement("li");
            li.className = "cm-target";
            li.dataset.ok = t.ok ? (t.self ? "self" : "yes") : "no";
            const head = document.createElement("div");
            head.className = "cm-target-url";
            head.textContent = t.url;
            const tag = document.createElement("span");
            tag.className = "cm-target-role";
            tag.textContent = t.self ? "this station" : t.role === "seed" ? "seed" : "station";
            head.appendChild(tag);
            const info = document.createElement("div");
            info.className = "cm-target-info";
            const total = (t.total_ok || 0) + (t.total_failed || 0);
            const rate = total ? ` Answered ${t.total_ok} of ${total} rounds since start.` : "";
            if (t.ok) {
                const sent = t.announced ? "our card was accepted" : `our card was not accepted${t.error ? ` (${t.error})` : ""}`;
                info.textContent = t.self
                    ? `Answered in ${t.ms} ms. This is this station's own address, so it does not count as another station.${rate}`
                    : `Answered in ${t.ms} ms, sent ${t.cards} station card(s); ${sent}.${rate}`;
            } else {
                const last = t.last_ok ? ` Last answer ${ago(t.last_ok)}.` : " Never answered since start.";
                info.textContent = `No answer: ${t.error || "unknown error"}. ${t.streak} failed round(s) in a row.${last}${rate}`;
            }
            li.append(head, info);
            targetsEl.appendChild(li);
        }
        if (st.enabled && !(st.targets || []).length && st.last_round && st.last_round.time) {
            const li = document.createElement("li");
            li.className = "cm-target";
            li.textContent = "No address was contacted: the seed list is empty and no station is known yet.";
            targetsEl.appendChild(li);
        }
    }

    if (checkBtn) checkBtn.addEventListener("click", async () => {
        checkBtn.disabled = true;
        checkBtn.textContent = "Checking\u2026";
        try {
            await fetch("/api/community/check", { method: "POST" });
            // A round takes up to 8 s per address: poll the result a few times
            for (const wait of [2500, 4000, 6000, 10000]) {
                await new Promise((r) => setTimeout(r, wait));
                await loadStatus();
            }
        } finally {
            checkBtn.textContent = "Check now";
            checkBtn.disabled = false;
        }
    });

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
            previewEl.checked = s.share_preview !== false;
            openEl.checked = s.allow_open !== false;
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
                    share_preview: previewEl.checked,
                    allow_open: openEl.checked,
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
