// Settings → Community: opt-in sharing of this station's card with other Zenivo stations (docs/COMMUNITY.md)
(function () {
    const box = document.getElementById("community-enabled");
    if (!box || currentPage !== "tab-settings") return;
    const nameEl = document.getElementById("community-name");
    const urlEl = document.getElementById("community-url");
    const thumbEl = document.getElementById("community-thumb");
    const previewEl = document.getElementById("community-preview");
    const openEl = document.getElementById("community-open");
    const relayEl = document.getElementById("community-relay");
    const chatEl = document.getElementById("community-chat");
    const seedsEl = document.getElementById("community-seeds");
    const statusEl = document.getElementById("community-status");

    function ago(ts) {
        const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
        return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
    }

    let defaultSeeds = [];
    const btnSeeds = document.getElementById("btn-community-seeds-default");
    // Puts the built-in seeds back at the top of the list and saves it
    if (btnSeeds) btnSeeds.addEventListener("click", () => {
        const mine = seedsEl.value.split("\n").map((l) => l.trim()).filter(Boolean);
        seedsEl.value = defaultSeeds.concat(mine.filter((u) => !defaultSeeds.includes(u))).join("\n");
        seedsEl.dispatchEvent(new Event("change"));
    });

    let lastStatus = null;
    function showStatus(st) {
        lastStatus = st;
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
        updateUrlHint();
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
    const netLbl = document.getElementById("community-net-lbl");
    const netIcon = netEl.querySelector("use");
    const NET_ICON = { off: "wifi-off", starting: "loader", connected: "wifi", outbound_only: "arrow-up-right", inbound_only: "inbox", isolated: "wifi-off" };
    const netNote = document.getElementById("community-net-note");
    const targetsEl = document.getElementById("community-targets");
    const checkBtn = document.getElementById("btn-community-check");

    function showHealth(st) {
        if (!healthEl) return;
        healthEl.hidden = !st.available;
        const net = st.network || { state: "off" };
        const [label, note] = NET[net.state] || NET.off;
        netEl.dataset.state = net.state;
        netLbl.textContent = label;
        netIcon.setAttribute("href", `/static/icons.svg#i-${NET_ICON[net.state] || "wifi-off"}`);
        const calls = net.inbound_1h ? ` Other stations called in ${net.inbound_1h} time(s) in the last hour.` : "";
        netNote.textContent = st.enabled ? `${note}${calls} ${st.contacts || 0} address(es) remembered for when the seeds are down.` : note;
        checkBtn.disabled = !st.enabled;
        if (st.enabled) {
            const bits = [];
            if (st.relay) bits.push(`This station relays previews${typeof st.relay_held === "number" ? ` and holds ${st.relay_held}` : ""}.`);
            if ((st.relays || []).length) bits.push(`Your large preview is held by ${st.relays.join(", ")}.`);
            if (bits.length) netNote.textContent += " " + bits.join(" ");
        }
        targetsEl.replaceChildren();
        for (const t of st.targets || []) {
            const ok = t.ok ? (t.self ? "self" : "yes") : "no";
            const total = (t.total_ok || 0) + (t.total_failed || 0);
            const rate = total ? ` Answered ${t.total_ok} of ${total} rounds since start.` : "";
            let info;
            if (t.ok) {
                const sent = t.announced ? "our card was accepted" : `our card was not accepted${t.error ? ` (${t.error})` : ""}`;
                info = t.self
                    ? `Answered in ${t.ms} ms. This is this station's own address, so it does not count as another station.${rate}`
                    : `Answered in ${t.ms} ms, sent ${t.cards} station card(s); ${sent}.${t.relay ? ` Large preview: ${t.relay}.` : ""}${rate}`;
            } else {
                const last = t.last_ok ? ` Last answer ${ago(t.last_ok)}.` : " Never answered since start.";
                info = `No answer: ${t.error || "unknown error"}. ${t.streak} failed round(s) in a row.${last}${rate}`;
            }
            targetsEl.appendChild(targetRow(ok, t.url, t.self ? "this station" : t.role === "seed" ? "seed" : "station", info));
        }
        if (st.enabled && !(st.targets || []).length && st.last_round && st.last_round.time) {
            targetsEl.appendChild(targetRow("no", "No address contacted", "", "The seed list is empty and no station is known yet."));
        }
    }

    // One ds-row per contacted address: status icon, address + role, detail line (textContent only)
    function targetRow(ok, url, role, info) {
        const li = document.createElement("li");
        li.className = "ds-row";
        li.dataset.ok = ok;
        const ic = document.createElement("span");
        ic.className = "ds-row__icon";
        ic.innerHTML = `<svg class="ds-icon" aria-hidden="true"><use href="/static/icons.svg#i-${ok === "no" ? "circle-x" : ok === "self" ? "circle-help" : "circle-check"}"></use></svg>`;
        const body = document.createElement("span");
        body.className = "ds-row__body";
        const ti = document.createElement("span");
        ti.className = "ds-row__title";
        ti.textContent = url;
        const sub = document.createElement("span");
        sub.className = "ds-row__sub sa-wrap";
        sub.textContent = info;
        body.append(ti, sub);
        li.append(ic, body);
        if (role) {
            const chip = document.createElement("span");
            chip.className = "ds-chip";
            chip.textContent = role;
            li.appendChild(chip);
        }
        return li;
    }

    if (checkBtn) checkBtn.addEventListener("click", async () => {
        checkBtn.disabled = true;
        checkBtn.classList.add("is-busy");
        try {
            await fetch("/api/community/check", { method: "POST" });
            // A round takes up to 8 s per address: poll the result a few times
            for (const wait of [2500, 4000, 6000, 10000]) {
                await new Promise((r) => setTimeout(r, wait));
                await loadStatus();
            }
        } finally {
            checkBtn.classList.remove("is-busy");
            checkBtn.disabled = false;
        }
    });

    // Without a public URL nobody can load the large preview or open the station: say so, and offer the address in use
    const urlHint = document.getElementById("community-url-hint");
    function updateUrlHint() {
        if (!urlHint) return;
        urlHint.textContent = "";
        const missing = !urlEl.value.trim() && (previewEl.checked || openEl.checked);
        urlHint.hidden = !missing;
        if (!missing) return;
        const span = document.createElement("span");
        const held = lastStatus && Array.isArray(lastStatus.relays) ? lastStatus.relays : [];
        span.textContent = "No public URL is set, so this station cannot be opened. The large preview is sent to stations that relay previews instead"
            + (held.length ? ` (held now by ${held.join(", ")})` : " (none holds it yet)") + "; the card carries a small 8 KB preview as a fallback.";
        urlHint.appendChild(span);
        const host = location.hostname;
        const isPublicName = location.protocol === "https:" && host.includes(".") && !/^[0-9.]+$/.test(host) && !host.includes(":");
        if (isPublicName) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "ds-btn";
            btn.textContent = "Use " + location.origin;
            btn.addEventListener("click", () => {
                urlEl.value = location.origin;
                urlEl.dispatchEvent(new Event("change", { bubbles: true }));
            });
            urlHint.appendChild(btn);
        }
    }
    [urlEl, previewEl, openEl].forEach((e) => e.addEventListener("change", updateUrlHint));

    // Chat needs sharing on: until then the box is disabled with a hint
    function syncChat() {
        if (!chatEl) return;
        chatEl.disabled = !box.checked;
        const note = document.getElementById("community-chat-note");
        if (note) note.classList.toggle("is-muted", !box.checked);
    }
    box.addEventListener("change", syncChat);

    // One line of chat counters (owner only; counts, never content)
    async function loadChatStats() {
        const el = document.getElementById("community-chat-stats");
        if (!el) return;
        try {
            const res = await fetch("/api/community/chat/stats");
            const s = res.ok ? await res.json() : null;
            if (!s || !s.enabled) { el.hidden = true; return; }
            const t = s.totals || {};
            let line = `Chat: sent ${(t.sent_public || 0) + (t.sent_dm || 0)}, received ${(t.recv_public || 0) + (t.recv_dm || 0)}, failed sends ${t.send_fail || 0}.`;
            if (s.hub) line += ` As a hub: accepted ${t.hub_accepted || 0}, pulled ${t.hub_pulled || 0}, served ${t.hub_served || 0}; holding ${s.held.dm} private message(s) for ${s.held.recipients} station(s); ${s.pending} waiting for a card.`;
            el.textContent = line;
            el.hidden = false;
        } catch (err) { /* optional feature */ }
    }

    async function loadStatus() {
        try {
            const res = await fetch("/api/community/status");
            if (res.ok) showStatus(await res.json());
        } catch (err) { /* optional feature */ }
        loadChatStats();
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
            relayEl.checked = s.relay_previews !== false;
            if (chatEl) chatEl.checked = s.chat_enabled !== false;
            syncChat();
            seedsEl.value = (s.seeds || []).join("\n");
            updateUrlHint();
        } catch (err) { /* optional feature */ }
        loadStatus();
    }

    // Every change saves itself (saving with sharing on publishes the card; the server's validation errors show in the chip)
    autosave(document.getElementById("community-card"), async () => {
        const data = await sendJson("/api/community/settings", {
            enabled: box.checked,
            name: nameEl.value.trim(),
            public_url: urlEl.value.trim(),
            share_thumbnail: thumbEl.checked,
            share_preview: previewEl.checked,
            allow_open: openEl.checked,
            relay_previews: relayEl.checked,
            chat_enabled: chatEl ? chatEl.checked : true,
            seeds: seedsEl.value.split("\n").map((l) => l.trim()).filter(Boolean),
        });
        setTimeout(loadStatus, 1500);
        loadStatus();
        return data;
    });

    load();
    setInterval(loadStatus, 30000);
})();
