/* Station chat widget (owner only, docs/COMMUNITY.md "Chat"): floating launcher + panel, loaded by shell.js on every page.
 * One public room plus one private thread per station. All remote text is set with textContent. */
(function () {
    "use strict";
    if (window.__zenivoChatWidget) return;
    window.__zenivoChatWidget = true;

    const API = "/api/community/chat";
    const PUBLIC = "*";
    const MAX = 500;
    const PALETTE = ["--accent", "--success", "--warning", "--violet", "--text-secondary"];
    const SVGNS = "http://www.w3.org/2000/svg";
    const ICONS = {
        chat: "M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 4v-4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
        back: "M15 5l-7 7 7 7",
        close: "M6 6l12 12M18 6L6 18",
        stats: "M5 20V10M12 20V4M19 20v-7",
        more: "M12 5v.01M12 12v.01M12 19v.01",
        send: "M3 11l18-8-8 18-2-8z",
        lock: "M7 11V8a5 5 0 0 1 10 0v3M5 11h14v10H5z",
        globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18",
    };

    let data = null, peer = null, view = "list", open = false, sending = false, stick = true, lastCount = -1;
    let thread = null, pollTimer = null, launch, badge, panel, titleEl, subEl, body, leftEl, rightEl, lastFocus = null;

    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    function icon(name) {
        const s = document.createElementNS(SVGNS, "svg");
        s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
        s.setAttribute("stroke-width", "2"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round");
        s.setAttribute("aria-hidden", "true");
        const p = document.createElementNS(SVGNS, "path");
        p.setAttribute("d", ICONS[name]);
        s.appendChild(p);
        return s;
    }
    function ibtn(name, label, fn) {
        const b = el("button", "zcw-ib");
        b.type = "button"; b.setAttribute("aria-label", label); b.title = label;
        b.appendChild(icon(name));
        b.addEventListener("click", fn);
        return b;
    }
    function hash(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return h; }
    function color(id) { return `var(${PALETTE[hash(id) % PALETTE.length]})`; }
    function nameOf(id) {
        if (id === PUBLIC) return "Public room";
        const st = data && data.stations.find((s) => s.id === id);
        if (st) return st.name;
        const c = data && data.conversations.find((x) => x.peer === id);
        return (c && c.name) || id.slice(0, 8);
    }
    function initials(name) {
        const w = name.trim().split(/[\s_-]+/).filter(Boolean);
        return ((w[0] || "?")[0] + (w.length > 1 ? w[1][0] : "")).toUpperCase();
    }
    function avatar(id) {
        const a = el("span", "zcw-av");
        if (id === PUBLIC) a.appendChild(icon("globe"));
        else { a.textContent = initials(nameOf(id)); a.style.setProperty("--av", color(id)); }
        return a;
    }
    function relTime(t) {
        if (!t) return "";
        const d = new Date(t * 1000), now = new Date(), diff = (now - d) / 1000;
        if (diff < 60) return "now";
        if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        if (diff < 6 * 86400) return d.toLocaleDateString([], { weekday: "short" });
        return d.toLocaleDateString([], { month: "short", day: "numeric" });
    }
    function totalUnread() { return data ? data.conversations.reduce((a, c) => a + (c.unread || 0), 0) : 0; }
    // ---- alerts for new incoming messages: toast, tab title, desktop notification when the tab is hidden ----
    const baseTitle = document.title;
    let toasts = null;
    function openThread(id) {
        if (!panel) return;
        if (!open) setOpen(true);
        go("thread", id);
    }
    function toast(id, text) {
        if (!toasts) { toasts = el("div", "zcw-toasts"); toasts.setAttribute("role", "status"); toasts.setAttribute("aria-live", "polite"); document.body.appendChild(toasts); }
        const t = el("button", "zcw-toast");
        t.type = "button";
        t.append(el("b", null, nameOf(id)), el("span", null, text));
        t.addEventListener("click", () => { t.remove(); openThread(id); });
        toasts.appendChild(t);
        while (toasts.children.length > 3) toasts.firstChild.remove();
        setTimeout(() => t.remove(), 6000);
    }
    function notifyNew(prev) {
        if (!prev || !data) return;
        const old = new Map(prev.conversations.map((c) => [c.peer, c.unread || 0]));
        for (const c of data.conversations) {
            if ((c.unread || 0) <= (old.get(c.peer) || 0)) continue;
            const text = (c.last_text || "").slice(0, 80);
            if (document.hidden) {
                if ("Notification" in window && Notification.permission === "granted") {
                    try {
                        const n = new Notification(nameOf(c.peer), { body: text, tag: "zenivo-chat-" + c.peer });
                        n.onclick = () => { window.focus(); openThread(c.peer); n.close(); };
                    } catch (e) { /* not allowed here */ }
                }
            } else if (!(open && view === "thread" && peer === c.peer)) toast(c.peer, text);
        }
    }
    function updateBadge() {
        const n = totalUnread();
        document.title = n ? `(${n}) ${baseTitle}` : baseTitle;
        badge.textContent = n ? (n > 99 ? "99+" : String(n)) : "";
        launch.setAttribute("aria-label", n ? `Open chat, ${n} unread` : "Open chat");
    }

    // ---- network ----
    let events = null, live = false, kick = null;
    async function post(path, payload) {
        const res = await fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        if (!res.ok) {
            let detail = "";
            try { detail = (await res.json()).detail; } catch (e) { /* no body */ }
            throw new Error(typeof detail === "string" && detail ? detail : `Error ${res.status}`);
        }
    }
    let reqSeq = 0;
    async function refresh(poll) {
        try {
            const q = open && view === "thread" ? peer : "";
            const seq = ++reqSeq;
            const res = await fetch(`${API}/local?peer=${encodeURIComponent(q)}${poll ? "&poll=1" : ""}`);
            if (res.status === 401 || res.status === 403 || res.status === 404) { teardown(); return false; }
            if (!res.ok) return true;
            const next = await res.json();
            // A slower, older request (e.g. the list fetch fired when the panel opened) must not
            // overwrite the thread a newer request already loaded
            if (seq !== reqSeq) return true;
            const prev = data;
            data = next;
            updateBadge();
            notifyNew(prev);
            if (open) render();
            if (open && view === "thread" && data.conversations.some((c) => c.peer === peer && c.unread)) {
                fetch(API + "/read", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ peer }) })
                    .then(() => { const c = data.conversations.find((x) => x.peer === peer); if (c) { c.unread = 0; updateBadge(); } }).catch(() => {});
            }
        } catch (e) { /* offline: next tick */ }
        return true;
    }
    function schedule() {
        clearTimeout(pollTimer);
        pollTimer = setTimeout(async () => {
            if (!document.hidden && !(await refresh(true))) return;
            schedule();
        }, live ? 120000 : open ? 30000 : 60000);
    }
    // Realtime: the station pushes an event when its conversations change; the timer is only the fallback
    function listen() {
        if (events || typeof EventSource === "undefined") return;
        events = new EventSource(API + "/events");
        events.addEventListener("chat", () => {
            clearTimeout(kick);
            kick = setTimeout(() => { if (launch) refresh(false); }, 300);
        });
        events.onopen = () => { live = true; schedule(); };
        events.onerror = () => {
            live = false; schedule();
            if (events && events.readyState === 2) { events = null; setTimeout(() => { if (launch) listen(); }, 5000); }
        };
    }
    function teardown() {
        clearTimeout(pollTimer); pollTimer = null;
        clearTimeout(kick);
        if (events) { events.close(); events = null; }
        live = false;
        if (launch) { launch.remove(); panel.remove(); launch = panel = null; }
        if (toasts) { toasts.remove(); toasts = null; }
        document.title = baseTitle;
        open = false; data = null;
    }

    // ---- chrome ----
    function build() {
        launch = el("button", "zcw-launch");
        launch.type = "button"; launch.setAttribute("aria-haspopup", "dialog"); launch.setAttribute("aria-expanded", "false"); launch.setAttribute("aria-label", "Open chat");
        badge = el("span", "zcw-badge");
        badge.setAttribute("aria-hidden", "true");
        launch.append(icon("chat"), badge);
        launch.addEventListener("click", () => setOpen(!open));
        panel = el("div", "zcw");
        panel.hidden = true; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Station chat");
        const head = el("div", "zcw-head");
        const t = el("div", "zcw-title");
        titleEl = el("b"); subEl = el("small");
        t.append(titleEl, subEl);
        leftEl = el("span"); rightEl = el("span");
        leftEl.style.display = rightEl.style.display = "contents";
        head.append(leftEl, t, rightEl);
        body = el("div", "zcw-body");
        const foot = el("div", "zcw-foot", "Hubs relay messages. Private messages are end-to-end encrypted; hubs see who talks to whom and when.");
        panel.append(head, body, foot);
        panel.addEventListener("keydown", onKey);
        document.body.append(launch, panel);
    }
    function onKey(e) {
        if (e.key === "Escape") { e.stopPropagation(); setOpen(false); return; }
        if (e.key !== "Tab") return;
        const f = [...panel.querySelectorAll("button, textarea, input")].filter((x) => !x.disabled && x.offsetParent !== null);
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    function setOpen(v) {
        if (!panel || v === open) return;
        open = v;
        panel.hidden = !v;
        fitViewport();
        launch.setAttribute("aria-expanded", v ? "true" : "false");
        if (v) {
            lastFocus = document.activeElement;
            view = "list"; peer = null;
            render();
            refresh(true);
            const f = panel.querySelector("button");
            if (f) f.focus();
        } else if (lastFocus && lastFocus.focus) lastFocus.focus();
        schedule();
    }
    function go(v, p) {
        view = v; if (p !== undefined) peer = p;
        stick = true; lastCount = -1;
        render();
        if (v === "thread") refresh(false);
    }

    // ---- views ----
    function render() {
        if (!panel || !data) return;
        const draft = view === "thread" && body.querySelector("textarea") ? body.querySelector("textarea").value : "";
        const hadFocus = view === "thread" && body.querySelector("textarea") === document.activeElement;
        leftEl.textContent = ""; rightEl.textContent = "";
        const close = ibtn("close", "Close chat", () => setOpen(false));
        if (view === "list") {
            titleEl.textContent = "Chat"; subEl.textContent = "";
            rightEl.append(ibtn("stats", "Statistics", () => go("stats")), close);
            renderList();
        } else if (view === "new") {
            titleEl.textContent = "New message"; subEl.textContent = "Private, end-to-end encrypted";
            leftEl.appendChild(ibtn("back", "Back", () => go("list")));
            rightEl.appendChild(close);
            renderNew();
        } else if (view === "stats") {
            titleEl.textContent = "Statistics"; subEl.textContent = "Counters only, no message content";
            leftEl.appendChild(ibtn("back", "Back", () => go("list")));
            rightEl.appendChild(close);
            renderStats();
        } else {
            leftEl.appendChild(ibtn("back", "Back", () => go("list")));
            renderThread(draft, hadFocus, close);
        }
    }
    function mkScroll() { body.textContent = ""; const s = el("div", "zcw-scroll"); body.appendChild(s); return s; }
    function empty(parent, iconName, text) {
        const e = el("div", "zcw-empty");
        e.append(icon(iconName), el("div", null, text));
        parent.appendChild(e);
    }
    function row(id, last, unread, preview) {
        const r = el("button", "zcw-row");
        r.type = "button";
        const mid = el("span", "zcw-mid");
        const l1 = el("span", "zcw-l1");
        l1.append(el("span", "zcw-name", nameOf(id)), el("span", "zcw-time", relTime(last && last.last_t)));
        const l2 = el("span", "zcw-l2");
        l2.appendChild(el("span", "zcw-prev", preview || (last && last.last_text) || "No messages yet"));
        if (unread) l2.appendChild(el("span", "zcw-pill", String(unread)));
        mid.append(l1, l2);
        r.append(avatar(id), mid);
        r.addEventListener("click", () => go("thread", id));
        return r;
    }
    function renderList() {
        const s = mkScroll();
        const nb = el("button", "zcw-new", "New message");
        nb.type = "button";
        nb.addEventListener("click", () => go("new"));
        s.appendChild(nb);
        const pub = data.conversations.find((c) => c.peer === PUBLIC);
        s.appendChild(row(PUBLIC, pub, pub && pub.unread, pub && pub.last_text ? "" : "Say hello to every station"));
        const dms = data.conversations.filter((c) => c.peer !== PUBLIC).sort((a, b) => b.last_t - a.last_t);
        dms.forEach((c) => s.appendChild(row(c.peer, c, c.unread)));
        if (!dms.length) empty(s, "chat", "No private conversations yet. Use New message to write to a station.");
        if ("Notification" in window) {
            const perm = Notification.permission;
            if (perm === "denied") s.appendChild(el("p", "zcw-alerts-note", "Desktop alerts are blocked in the browser."));
            else if (perm === "default") {
                const ab = el("button", "zcw-alerts", "Enable desktop alerts");
                ab.type = "button";
                ab.addEventListener("click", () => { Notification.requestPermission().then(() => { if (view === "list") render(); }).catch(() => {}); });
                s.appendChild(ab);
            }
        }
    }
    function renderNew() {
        body.textContent = "";
        const q = el("input", "zcw-search");
        q.type = "search"; q.placeholder = "Search stations"; q.setAttribute("aria-label", "Search stations");
        const s = el("div", "zcw-scroll");
        body.append(q, s);
        function fill() {
            s.textContent = "";
            const f = q.value.trim().toLowerCase();
            const list = data.stations.filter((st) => st.dm && !st.blocked && (!f || st.name.toLowerCase().includes(f)));
            list.forEach((st) => s.appendChild(row(st.id, null, 0, "Private message")));
            if (!list.length) empty(s, "chat", f ? "No station matches." : "No station can receive private messages yet.");
        }
        q.addEventListener("input", fill);
        fill();
        q.focus();
    }
    const REFUSED = [["Rate limit", "refused_rate"], ["Unknown station", "refused_unknown"], ["Bad signature", "refused_sig"],
        ["Too old", "refused_old"], ["Cap reached", "refused_cap"]];
    const nf = (n) => (n || 0).toLocaleString();
    function ago(ts) {
        if (!ts) return "never";
        const d = Math.max(0, Date.now() / 1000 - ts);
        if (d < 60) return "just now";
        if (d < 3600) return Math.floor(d / 60) + " min ago";
        if (d < 86400) return Math.floor(d / 3600) + " h ago";
        return Math.floor(d / 86400) + " d ago";
    }
    function section(parent, title, hint) {
        const sec = el("section", "zcs-sec");
        const h = el("h4", "zcs-h", title);
        if (hint) h.appendChild(el("small", null, hint));
        sec.appendChild(h);
        parent.appendChild(sec);
        return sec;
    }
    function tiles(sec, items, tot, today) {
        const g = el("div", "zcs-grid");
        items.forEach(([label, key, tone]) => {
            const t = el("div", "zcs-tile" + (tone ? " " + tone : ""));
            t.append(el("div", "zcs-num", nf(tot[key])), el("div", "zcs-lbl", label));
            const td = today[key] || 0;
            t.appendChild(el("div", "zcs-today" + (td ? " up" : ""), td ? "+" + nf(td) + " today" : "none today"));
            g.appendChild(t);
        });
        sec.appendChild(g);
    }
    function bars(sec, days) {
        const keys = ["sent_public", "sent_dm", "recv_public", "recv_dm"];
        const out = [];
        for (let i = 13; i >= 0; i--) {
            const d = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
            const c = days[d] || {};
            out.push([d, keys.reduce((a, k) => a + (c[k] || 0), 0)]);
        }
        const max = Math.max(1, ...out.map((x) => x[1]));
        const w = el("div", "zcs-bars");
        out.forEach(([d, v]) => {
            const b = el("div", "zcs-bar" + (v ? "" : " zero"));
            b.style.height = Math.max(3, Math.round((v / max) * 100)) + "%";
            b.title = d + ": " + v + " message" + (v === 1 ? "" : "s");
            w.appendChild(b);
        });
        sec.appendChild(w);
        const ax = el("div", "zcs-axis");
        ax.append(el("span", null, "14 days ago"), el("span", null, "today"));
        sec.appendChild(ax);
    }
    function renderStats() {
        const s = mkScroll();
        s.appendChild(el("div", "zcw-empty", "Loading..."));
        loadStats(s);
    }
    async function loadStats(s) {
        try {
            const res = await fetch(API + "/stats");
            if (!res.ok) throw new Error();
            const st = await res.json();
            if (view !== "stats") return;
            s.textContent = "";
            const tot = st.totals || {}, days = st.days || {};
            const today = days[new Date().toISOString().slice(0, 10)] || {};
            const wrap = el("div", "zcs");
            s.appendChild(wrap);
            wrap.appendChild(el("div", "zcs-since", "Counting since " + new Date(st.since * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })));

            const mine = section(wrap, "Your messages");
            tiles(mine, [["Sent · public", "sent_public"], ["Sent · private", "sent_dm", "violet"],
                ["Received · public", "recv_public"], ["Received · private", "recv_dm", "violet"]], tot, today);
            bars(mine, days);

            const relay = section(wrap, "Hub relay", "messages this station carried for others");
            tiles(relay, [["From stations", "hub_accepted"], ["Pulled", "hub_pulled"], ["Served out", "hub_served"]], tot, today);
            relay.lastChild.classList.add("three");

            const held = st.held || {};
            const q = section(wrap, "Queue");
            const ql = el("div", "zcs-list");
            const qrow = (label, val, sub) => {
                const r = el("div", "zcs-li");
                const l = el("span", null, label);
                if (sub) l.appendChild(el("small", null, sub));
                r.append(l, el("b", null, val));
                ql.appendChild(r);
            };
            qrow("Held private", nf(held.dm), held.recipients ? "for " + held.recipients + " station" + (held.recipients === 1 ? "" : "s") : "");
            qrow("Held public", nf(held.public));
            qrow("Waiting for a station card", nf(st.pending));
            q.appendChild(ql);

            const prob = section(wrap, "Problems");
            const refused = REFUSED.filter(([, k]) => tot[k]);
            const fails = tot.send_fail || 0;
            if (!refused.length && !fails) {
                prob.appendChild(el("div", "zcs-ok", "No refused messages and no failed sends."));
            } else {
                const pl = el("div", "zcs-chips");
                if (fails) pl.appendChild(el("span", "zcs-chip bad", "Send failures " + nf(fails)));
                refused.forEach(([label, k]) => pl.appendChild(el("span", "zcs-chip warn", label + " " + nf(tot[k]))));
                prob.appendChild(pl);
            }

            const peers = Object.keys(st.peers || {});
            const hs = section(wrap, "Hubs", peers.length ? peers.length + " connected" : "");
            if (!peers.length) hs.appendChild(el("div", "zcs-ok muted", "No hub contacted yet."));
            peers.forEach((o) => {
                const p = st.peers[o];
                const bad = (p.send_fail || p.pull_fail) && (!p.last_ok || Date.now() / 1000 - p.last_ok > 600);
                const card = el("div", "zcs-hub");
                const top = el("div", "zcs-hub-top");
                top.append(el("span", "zcs-dot" + (bad ? " bad" : p.last_ok ? " ok" : "")), el("b", null, o.replace(/^https?:\/\//, "").replace(/\/$/, "")),
                    el("small", null, "ok " + ago(p.last_ok)));
                if (p.last_ok) top.lastChild.title = new Date(p.last_ok * 1000).toLocaleString();
                const ch = el("div", "zcs-chips");
                ch.append(el("span", "zcs-chip", "pulled " + nf(p.pulled)), el("span", "zcs-chip", "served " + nf(p.served)));
                if (p.send_fail) ch.appendChild(el("span", "zcs-chip bad", "send fail " + nf(p.send_fail)));
                if (p.pull_fail) ch.appendChild(el("span", "zcs-chip bad", "pull fail " + nf(p.pull_fail)));
                card.append(top, ch);
                hs.appendChild(card);
            });
        } catch (e) {
            if (view === "stats") { s.textContent = ""; empty(s, "stats", "Statistics are not available."); }
        }
    }

    function showErr(msg) {
        const e = body && body.querySelector("#zcw-err");
        if (!e) return;
        e.textContent = msg || ""; e.hidden = !msg;
    }
    function threadMenu(st) {
        const old = panel.querySelector(".zcw-menu");
        if (old) { old.remove(); return; }
        const m = el("div", "zcw-menu");
        m.setAttribute("role", "menu");
        const b = el("button", null, st && st.blocked ? "Unblock station" : "Block station");
        b.type = "button"; b.setAttribute("role", "menuitem");
        b.title = "Hide this station's messages and stop forwarding them";
        b.addEventListener("click", async () => {
            m.remove();
            try { await post("/block", { id: peer, blocked: !(st && st.blocked) }); showErr(""); refresh(false); } catch (e) { showErr(e.message); }
        });
        m.appendChild(b);
        panel.appendChild(m);
        b.focus();
    }
    function renderThread(draft, hadFocus, closeBtn) {
        const isPub = peer === PUBLIC;
        const st = data.stations.find((x) => x.id === peer);
        titleEl.textContent = nameOf(peer);
        subEl.textContent = "";
        if (isPub) subEl.textContent = "Everyone on the network";
        else {
            const lk = icon("lock");
            lk.style.cssText = "width:11px;height:11px;vertical-align:-1px;margin-right:4px";
            subEl.append(lk, "Private · end-to-end encrypted");
        }
        if (!isPub) {
            const more = ibtn("more", "Conversation options", () => threadMenu(st));
            more.setAttribute("aria-haspopup", "menu");
            rightEl.appendChild(more);
        }
        rightEl.appendChild(closeBtn);

        // Same thread already on screen: refresh only the message list. The textarea, its focus and the on-screen
        // keyboard must survive polls, SSE events and our own send (re-creating it closed the keyboard on every send).
        if (thread && thread.peer === peer && thread.form.isConnected && body.contains(thread.form)) { thread.paint(); return; }
        body.textContent = "";
        const sc = el("div", "zcw-scroll");
        sc.setAttribute("role", "log"); sc.setAttribute("aria-live", "polite"); sc.setAttribute("aria-label", "Messages");
        const list = el("div", "zcw-msgs");
        sc.appendChild(list);
        function fillMsgs(msgs) {
        list.textContent = "";
        if (!msgs.length) empty(list, isPub ? "globe" : "chat", isPub ? "The public room is quiet. Write the first message." : "No messages yet. Say hello.");
        let day = "", prev = null;
        msgs.forEach((m) => {
            const d = new Date(m.t * 1000), dk = d.toDateString();
            if (dk !== day) {
                day = dk; prev = null;
                list.appendChild(el("div", "zcw-day", d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })));
            }
            const who = m.in ? m.from : "me";
            const b = el("div", "zcw-m" + (m.in ? "" : " mine") + (prev !== who ? " first" : ""));
            if (isPub && m.in && prev !== who) {
                const w = el("span", "zcw-who", nameOf(m.from));
                w.style.setProperty("--av", color(m.from));
                b.appendChild(w);
            }
            b.appendChild(document.createTextNode(m.text));
            b.appendChild(el("time", null, d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })));
            list.appendChild(b);
            prev = who;
        });
        }
        const chip = el("button", "zcw-chip", "New messages");
        chip.type = "button"; chip.hidden = true;
        chip.addEventListener("click", () => { stick = true; sc.scrollTop = sc.scrollHeight; chip.hidden = true; });
        sc.addEventListener("scroll", () => {
            stick = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 40;
            if (stick) chip.hidden = true;
        });
        const err = el("p", "zcw-err");
        err.id = "zcw-err"; err.hidden = true; err.setAttribute("role", "alert");
        const form = el("form", "zcw-form");
        form.autocomplete = "off";
        const ta = el("textarea");
        ta.rows = 1; ta.maxLength = MAX; ta.setAttribute("enterkeyhint", "send"); ta.setAttribute("autocapitalize", "sentences"); ta.placeholder = "Message"; ta.setAttribute("aria-label", "Message"); ta.value = draft;
        const count = el("span", "zcw-count");
        count.hidden = true;
        const send = el("button", "zcw-send");
        send.type = "submit"; send.setAttribute("aria-label", "Send message"); send.appendChild(icon("send"));
        form.append(ta, send, count);
        body.append(sc, chip, err, form);

        function fit() {
            ta.style.height = "auto";
            const full = ta.scrollHeight + 2, cap = 4 * 20 + 16;
            ta.style.height = Math.min(full, cap) + "px";
            ta.style.overflowY = full > cap ? "auto" : "hidden";
            const n = ta.value.length;
            count.hidden = n < MAX - 100;
            count.textContent = `${n}/${MAX}`;
            count.classList.toggle("over", n >= MAX);
            send.disabled = sending || !ta.value.trim();
        }
        ta.addEventListener("input", fit);
        ta.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
        });
        // The send button must never take focus (focus loss = keyboard closes). pointerdown/mousedown cover mouse and
        // modern touch; touchstart is non-passive and submits itself because cancelling it also cancels the click.
        const keep = (e) => e.preventDefault();
        send.addEventListener("pointerdown", keep);
        send.addEventListener("mousedown", keep);
        send.addEventListener("touchstart", keep, { passive: false });
        send.addEventListener("touchend", (e) => { e.preventDefault(); if (!send.disabled) form.requestSubmit(); }, { passive: false });
        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            const text = ta.value.trim();
            if (!text || sending) return;
            sending = true; ta.readOnly = true; send.setAttribute("aria-busy", "true"); showErr("");
            try {
                await post("/send", { to: peer, body: text });
                ta.value = ""; stick = true; fit();
                ta.focus({ preventScroll: true });
                await refresh(false);
            } catch (er) { showErr(er.message); }
            sending = false; ta.readOnly = false; fit();
            send.removeAttribute("aria-busy");
            if (document.activeElement !== ta) ta.focus({ preventScroll: true });
        });
        fit();

        function paint() {
            const keepTop = sc.scrollTop;
            const msgs = data.peer === peer ? data.messages : [];
            fillMsgs(msgs);
            const grew = lastCount >= 0 && msgs.length > lastCount;
            lastCount = msgs.length;
            if (stick || !grew && keepTop === 0) sc.scrollTop = sc.scrollHeight;
            else { sc.scrollTop = keepTop; if (grew) chip.hidden = false; }
        }
        thread = { peer, form, paint };
        paint();
        if (hadFocus || draft === "") ta.focus({ preventScroll: true });
    }

    // Phone: the panel is full screen; follow the visual viewport so the input stays above the keyboard
    function fitViewport() {
        if (!panel) return;
        const vv = window.visualViewport;
        if (!vv || !open || !window.matchMedia("(max-width: 640px)").matches) { panel.style.top = panel.style.height = panel.style.bottom = ""; return; }
        panel.style.top = vv.offsetTop + "px"; panel.style.bottom = "auto"; panel.style.height = vv.height + "px";
        const sc = body.querySelector(".zcw-scroll");
        if (sc && stick) sc.scrollTop = sc.scrollHeight;
    }
    if (window.visualViewport) { visualViewport.addEventListener("resize", fitViewport); visualViewport.addEventListener("scroll", fitViewport); }

    // ---- start: only when the owner is signed in and chat is on (200); anything else renders nothing ----
    function start() {
        fetch(`${API}/local?peer=`).then((res) => {
            if (!res.ok) return null;
            return res.json();
        }).then((j) => {
            if (!j) return;
            data = j;
            build();
            updateBadge();
            schedule();
            listen();
            window.dispatchEvent(new Event("zenivo-chat-ready"));
        }).catch(() => {});
    }
    // Used by the community map: a "Message" button in a station popup opens that private thread
    window.ZenivoChat = {
        canDM(id) { return !!(data && data.id !== id && data.stations.some((s) => s.id === id && s.dm)); },
        openDM(id) {
            if (!panel || !this.canDM(id)) return;
            if (!open) setOpen(true);
            go("thread", id);
        },
    };
    document.addEventListener("visibilitychange", () => { if (!document.hidden && launch) refresh(true); });
    start();
})();
