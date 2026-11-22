// Community page: station chat panel (owner only; docs/COMMUNITY.md "Chat"). All remote text is set with textContent.
(function () {
    const panel = document.getElementById("cchat");
    if (!panel) return;
    const peerEl = document.getElementById("cchat-peer");
    const logEl = document.getElementById("cchat-log");
    const formEl = document.getElementById("cchat-form");
    const textEl = document.getElementById("cchat-text");
    const errEl = document.getElementById("cchat-err");
    const blockEl = document.getElementById("cchat-block");
    const unreadEl = document.getElementById("cchat-unread");
    const PUBLIC = "*";
    let peer = PUBLIC;
    let data = null;
    let timer = null;

    function err(msg) {
        errEl.textContent = msg || "";
        errEl.hidden = !msg;
    }

    function nameOf(id) {
        if (id === PUBLIC) return "Public room";
        const st = data && data.stations.find((s) => s.id === id);
        return st ? st.name : id.slice(0, 8);
    }

    function render() {
        if (!data) return;
        const opts = [{ peer: PUBLIC, unread: 0 }];
        const seen = new Set([PUBLIC]);
        data.conversations.forEach((c) => { if (!seen.has(c.peer)) { seen.add(c.peer); opts.push(c); } });
        data.stations.forEach((s) => { if (!seen.has(s.id) && s.dm) { seen.add(s.id); opts.push({ peer: s.id, unread: 0 }); } });
        if (!seen.has(peer)) peer = PUBLIC;
        const unread = {};
        data.conversations.forEach((c) => { unread[c.peer] = c.unread; });
        peerEl.textContent = "";
        opts.forEach((o) => {
            const op = document.createElement("option");
            op.value = o.peer;
            const n = unread[o.peer] || 0;
            op.textContent = nameOf(o.peer) + (n ? ` (${n})` : "");
            peerEl.appendChild(op);
        });
        peerEl.value = peer;
        const total = Object.values(unread).reduce((a, b) => a + b, 0);
        unreadEl.textContent = total ? `${total} new` : "";
        blockEl.hidden = peer === PUBLIC;
        const st = data.stations.find((s) => s.id === peer);
        blockEl.textContent = st && st.blocked ? "Unblock" : "Block";
        logEl.textContent = "";
        data.messages.forEach((m) => {
            const row = document.createElement("div");
            row.className = "cm" + (m.in ? "" : " mine");
            const who = document.createElement("b");
            who.textContent = m.in ? nameOf(m.from) : "You";
            const when = document.createElement("time");
            when.textContent = new Date(m.t * 1000).toLocaleString([], { hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" });
            const body = document.createElement("div");
            body.textContent = m.text;
            row.append(who, when, body);
            logEl.appendChild(row);
        });
        logEl.scrollTop = logEl.scrollHeight;
    }

    async function refresh(poll) {
        try {
            const res = await fetch(`/api/community/chat/local?peer=${encodeURIComponent(peer)}${poll ? "&poll=1" : ""}`);
            if (res.status === 404 || res.status === 401 || res.status === 403) {   // chat off, or not the owner
                panel.hidden = true;
                if (timer) { clearInterval(timer); timer = null; }
                return;
            }
            if (!res.ok) return;
            data = await res.json();
            panel.hidden = false;
            if (!timer) timer = setInterval(() => { if (!document.hidden) refresh(true); }, 30000);
            render();
            if (data.conversations.some((c) => c.peer === peer && c.unread)) {
                fetch("/api/community/chat/read", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ peer }) });
            }
        } catch (e) { /* offline: try again on the next tick */ }
    }

    async function post(path, body) {
        const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        if (!res.ok) {
            let detail = "";
            try { detail = (await res.json()).detail; } catch (e) { /* no body */ }
            throw new Error(typeof detail === "string" && detail ? detail : `Error ${res.status}`);
        }
    }

    peerEl.addEventListener("change", () => { peer = peerEl.value; err(""); refresh(false); });
    blockEl.addEventListener("click", async () => {
        const st = data && data.stations.find((s) => s.id === peer);
        try {
            await post("/api/community/chat/block", { id: peer, blocked: !(st && st.blocked) });
            refresh(false);
        } catch (e) { err(e.message); }
    });
    formEl.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const body = textEl.value.trim();
        if (!body) return;
        err("");
        try {
            await post("/api/community/chat/send", { to: peer, body });
            textEl.value = "";
            refresh(false);
        } catch (e) { err(e.message); }
    });

    refresh(true);
})();
