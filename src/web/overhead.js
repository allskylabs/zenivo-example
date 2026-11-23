// Overhead page (/overhead): lists of aircraft, satellites and radiosondes above the station plus a small 2D sky radar.
// Data comes from the live socket (/ws/live WITHOUT binary=1, so no camera pictures are sent: only a small JSON telemetry message,
// which this page ignores, and the subscribed packed overlays, decoded by /static/overlay_decode.js). REST is used once for the first
// paint and while the socket is down. Object names come from the receivers: they only reach the page through escapeHtml or textContent.
(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);
    // Red Vision, clock and Menu come from shell.js; redraw the radar when the theme flips
    window.addEventListener("zenivo:rv", () => drawRadar());

    // ---- helpers ----
    const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    const compass = (az) => COMPASS[Math.round(((az % 360) + 360) % 360 / 45) % 8];
    const LAYERS = ["aircraft", "satellites", "sondes"];
    const idOf = { aircraft: (a) => a.icao, satellites: (s) => s.norad, sondes: (s) => s.serial };
    const labelOf = { aircraft: (a) => a.callsign || a.icao, satellites: (s) => s.name, sondes: (s) => s.serial };

    const ic = (n) => `<svg class="ds-icon" aria-hidden="true"><use href="/static/icons.svg#i-${n}"></use></svg>`;
    const LAYER_ICON = { aircraft: "plane", satellites: "satellite", sondes: "balloon" };
    // Filter chips per segment: [key, short label, test, icon], only from fields that exist
    const FILTERS = {
        aircraft: [["all", "All", () => true, "layers"], ["view", "In view", (a) => !!a.in_view, "eye"],
            ["up", "Climbing", (a) => a.climb_m_min != null && a.climb_m_min > 60, "arrow-up-right"], ["down", "Descending", (a) => a.climb_m_min != null && a.climb_m_min < -60, "chevron-down"]],
        satellites: [["all", "All", () => true, "layers"], ["view", "In view", (s) => !!s.in_view, "eye"], ["vis", "Visible", (s) => !!s.visible, "sparkles"]],
        sondes: [["all", "All", () => true, "layers"], ["view", "In view", (s) => !!s.in_view, "eye"]],
    };

    const state = { seg: "aircraft", filter: { aircraft: "all", satellites: "all", sondes: "all" }, data: {}, failed: {}, sel: null, wsLive: false, restAt: 0 };

    const elOf = (a) => a.in_view || (a.above_horizon !== false && a.alt_deg >= 0) ? `El ${Math.round(a.alt_deg)}° · ${compass(a.az_deg)}` : "Below horizon";
    function rowHtml(layer, a) {
        const rid = idOf[layer](a), id = esc(rid), name = esc(labelOf[layer](a));
        const sel = state.sel && state.sel.layer === layer && String(state.sel.id) === String(rid);
        const num = (v) => esc(Number(v).toLocaleString());
        let sub, val, url, ext;
        if (layer === "aircraft") {
            const climb = a.climb_m_min == null ? "" : a.climb_m_min > 60 ? " ↗" : a.climb_m_min < -60 ? " ↘" : "";
            sub = `${num(a.altitude_m)} m${climb}${a.speed_kmh != null ? ` · ${esc(a.speed_kmh)} km/h` : ""}`;
            val = `${esc(a.distance_km)} km`;
            url = `https://globe.adsbexchange.com/?icao=${encodeURIComponent(String(a.icao).toLowerCase())}`; ext = "ADS-B Exchange";
        } else if (layer === "sondes") {
            const dir = a.climb_m_s > 1 ? " ↗" : a.climb_m_s < -1 ? " ↘" : "";
            sub = `${num(a.altitude_m)} m${dir}${a.freq_mhz ? " · " + esc(a.freq_mhz) + " MHz" : ""}`;
            val = `${esc(a.distance_km)} km`;
            url = `https://sondehub.org/${encodeURIComponent(a.serial)}`; ext = "SondeHub";
        } else {
            sub = a.visible ? "Visible" : "Not visible";
            val = `${num(a.height_km)} km`;
            url = `https://www.n2yo.com/satellite/?s=${encodeURIComponent(a.norad)}`; ext = "N2YO";
        }
        const el = `${a.in_view ? ic("eye") + " " : ""}${esc(elOf(a))} · ${sub}`;
        return `<li class="ds-row oh-row${a.in_view ? " in-view" : ""}${sel ? " sel" : ""}" data-id="${id}" tabindex="0" role="button" aria-pressed="${sel ? "true" : "false"}" aria-label="${name}">
            <span class="ds-row__icon">${ic(LAYER_ICON[layer])}</span>
            <span class="ds-row__body"><span class="ds-row__title">${name}</span><span class="ds-row__sub">${el}</span></span>
            <span class="ds-row__value">${val}</span>
            <a class="ds-btn ds-btn--ghost ds-btn--icon oh-ext" href="${url}" target="_blank" rel="noopener" aria-label="${esc(ext)}" title="${esc(ext)}">${ic("arrow-up-right")}</a></li>`;
    }

    // Objects of a segment: satellites only while above the horizon, sorted like the Live card
    function objectsOf(layer) {
        const d = state.data[layer];
        let list = (d && d[layer]) || [];
        if (layer === "satellites") {
            list = list.filter((s) => s.above_horizon !== false);
            list = list.slice().sort((a, b) => (b.in_view - a.in_view) || (b.visible - a.visible) || (b.alt_deg - a.alt_deg));
        } else if (layer === "sondes") {
            list = list.slice().sort((a, b) => (a.distance_km ?? 1e9) - (b.distance_km ?? 1e9));
        }
        return list;
    }

    function statusOf(layer) {
        const d = state.data[layer];
        if (!d) return ["--", "Loading…"];
        const all = objectsOf(layer), inView = all.filter((x) => x.in_view).length;
        if (layer === "aircraft") {
            if (d.enabled === false) return ["off", "ADS-B is turned off"];
            let st = d.connected ? "Live from the ADS-B receiver" : "Receiver not connected";
            if (d.source_in_use === "public" && d.public_provider) st = "Live from the public network: " + d.public_provider.name;
            return [`${inView} in view · ${d.total != null ? d.total : all.length} heard`, st];
        }
        if (layer === "sondes") {
            if (d.enabled === false) return ["off", "Weather balloons are turned off"];
            return [`${inView} in view · ${d.total != null ? d.total : all.length} heard`, "Live from SondeHub (within 350 km)"];
        }
        if (d.disabled) return ["off", "Satellites are turned off"];
        const sun = d.sun_alt_deg;
        return [`${inView} in view · ${all.length} up`, typeof sun !== "number" ? "Large satellites above the horizon"
            : sun > -6 ? "Daylight: positions only, not visible to the eye" : "Bright = sunlit under a dark sky"];
    }

    let queued = false;
    function render() { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; draw(); }); } }
    const empty = (icon, title, extra) => `<li class="ds-empty oh-empty">${ic(icon)}<span class="ds-empty__title">${esc(title)}</span>${extra || ""}</li>`;
    const SKEL = '<li class="oh-skel" aria-hidden="true"><span class="ds-skeleton"></span><span class="ds-skeleton"></span><span class="ds-skeleton"></span></li>'.repeat(4);
    function draw() {
        const layer = state.seg, d = state.data[layer];
        LAYERS.forEach((l) => {
            const t = $("oh-tab-" + l);
            t.setAttribute("aria-selected", l === layer ? "true" : "false");
            const dd = state.data[l];
            $("oh-n-" + l).textContent = !dd ? "–" : (dd.enabled === false || dd.disabled) ? "off" : String(objectsOf(l).length);
        });
        const [count, status] = statusOf(layer);
        $("oh-count").textContent = count;
        $("oh-status").textContent = status;
        $("oh-info").title = "Info";
        const live = $("oh-live"); live.classList.toggle("is-on", state.wsLive);
        live.setAttribute("aria-label", state.wsLive ? "Live" : "Updating"); live.title = state.wsLive ? "Live" : "Updating";
        const chips = $("oh-chips"), all = objectsOf(layer);
        if (chips.dataset.layer !== layer) {
            chips.dataset.layer = layer;
            chips.innerHTML = FILTERS[layer].map(([k, t, , i]) => `<button type="button" class="ds-chip" data-f="${k}" aria-pressed="false">${ic(i)}<span>${esc(t)}</span></button>`).join("");
            chips.dataset.n = "";
        }
        const allBtn = chips.querySelector('[data-f="all"] span'), nAll = String(all.length);
        if (allBtn && chips.dataset.n !== nAll) { allBtn.textContent = `All ${nAll}`; chips.dataset.n = nAll; }
        chips.querySelectorAll(".ds-chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.f === state.filter[layer])));
        const ul = $("oh-rows");
        const fn = (FILTERS[layer].find((f) => f[0] === state.filter[layer]) || FILTERS[layer][0])[2];
        const list = all.filter(fn);
        if (!d) ul.innerHTML = state.failed[layer] ? empty("wifi-off", "Can't load", `<button type="button" class="ds-btn" data-retry>${ic("refresh-cw")}<span>Retry</span></button>`) : SKEL;
        else if (d.enabled === false || d.disabled) ul.innerHTML = empty("power", "Off");
        else if (!list.length) ul.innerHTML = empty(all.length ? "funnel" : LAYER_ICON[layer], all.length ? "No match" : "Nothing above");
        else ul.innerHTML = list.map((a) => rowHtml(layer, a)).join("");
        drawRadar();
    }

    // ---- sky radar: N up, zenith in the centre, horizon at the edge, rings at 30 and 60 degrees of elevation ----
    const cv = $("oh-canvas");
    let dots = [];   // hit targets of the last draw: { id, x, y }
    function drawRadar() {
        if (!cv) return;
        const box = cv.parentElement, size = Math.max(160, Math.min(box.clientWidth, window.innerWidth <= 640 ? 320 : 520)), dpr = window.devicePixelRatio || 1;
        if (cv.width !== Math.round(size * dpr)) { cv.width = Math.round(size * dpr); cv.height = Math.round(size * dpr); }
        cv.style.width = size + "px"; cv.style.height = size + "px";
        const g = cv.getContext("2d");
        g.setTransform(dpr, 0, 0, dpr, 0, 0);
        g.clearRect(0, 0, size, size);
        const css = getComputedStyle(document.documentElement);
        const col = (n, f) => (css.getPropertyValue(n) || "").trim() || f;
        const ink = col("--c-text-2", "gray"), line = col("--c-border", "gray"), accent = col("--c-accent", "gray"),
            warn = col("--c-warn", "gray"), main = col("--c-text", "gray"), sub = col("--c-surface-2", "black");
        const R = size / 2 - 22, cx = size / 2, cy = size / 2;
        g.fillStyle = sub; g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.fill();
        g.strokeStyle = line; g.lineWidth = 1.5; g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.stroke();
        g.lineWidth = 1;
        [1 / 3, 2 / 3].forEach((f) => { g.beginPath(); g.arc(cx, cy, R * f, 0, Math.PI * 2); g.stroke(); });
        g.beginPath(); g.moveTo(cx - R, cy); g.lineTo(cx + R, cy); g.moveTo(cx, cy - R); g.lineTo(cx, cy + R); g.stroke();
        g.fillStyle = ink; g.font = "600 12px " + col("--font-ui", "sans-serif"); g.textAlign = "center"; g.textBaseline = "middle";
        g.fillStyle = accent; g.fillText("N", cx, cy - R - 11); g.fillStyle = ink;
        g.fillText("S", cx, cy + R + 11); g.fillText("E", cx + R + 11, cy); g.fillText("W", cx - R - 11, cy);
        g.font = "12px " + col("--font-ui", "sans-serif"); g.fillStyle = ink;
        g.fillText("60°", cx + 14, cy - R / 3 + 8); g.fillText("30°", cx + 14, cy - 2 * R / 3 + 8);
        g.fillStyle = main; g.beginPath(); g.arc(cx, cy, 2.5, 0, Math.PI * 2); g.fill();
        dots = [];
        const layer = state.seg, objs = objectsOf(layer).filter((o) => typeof o.az_deg === "number" && typeof o.alt_deg === "number" && o.alt_deg >= 0);
        g.textBaseline = "middle";
        const placed = [];   // [{o, x, y, sel}] in draw order; every object gets a dot, only some get a label
        objs.forEach((o) => {
            const r = R * (90 - Math.min(90, o.alt_deg)) / 90, th = o.az_deg * Math.PI / 180;
            const x = cx + r * Math.sin(th), y = cy - r * Math.cos(th), id = idOf[layer](o);
            const sel = !!state.sel && state.sel.layer === layer && String(state.sel.id) === String(id);
            dots.push({ id, x, y });
            placed.push({ o, x, y, sel });
            if (sel) { g.strokeStyle = warn; g.lineWidth = 2; g.beginPath(); g.arc(x, y, 9, 0, Math.PI * 2); g.stroke(); }
            g.fillStyle = sel ? warn : o.in_view ? main : accent;
            g.beginPath(); g.arc(x, y, sel ? 5.5 : 4.5, 0, Math.PI * 2); g.fill();
        });
        // Labels: the selected object always, then at most 5 others that find free space (greedy, boxes must not touch a dot or another label)
        const MAX_LABELS = 6, taken = [];
        const hit = (a, b) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;
        const fam = col("--font-ui", "sans-serif");
        const order = placed.filter((p) => p.sel).concat(placed.filter((p) => !p.sel));
        let drawn = 0;
        order.forEach((p) => {
            if (drawn >= MAX_LABELS) return;
            const t = String(labelOf[layer](p.o));
            g.font = (p.sel ? "700 " : "") + "12px " + fam;
            const w = g.measureText(t).width, h = 13, dy = p.sel ? -10 : 0, preferRight = p.x < cx + R * 0.45;
            const boxFor = (right) => { const x0 = right ? p.x + 9 : p.x - 9 - w, y0 = p.y + dy - h / 2; return { x0, y0, x1: x0 + w, y1: y0 + h, right }; };
            const free = (bx) => bx.x0 >= 0 && bx.x1 <= size && bx.y0 >= 0 && bx.y1 <= size && !taken.some((t2) => hit(bx, t2)) &&
                !placed.some((q) => hit(bx, { x0: q.x - 6, y0: q.y - 6, x1: q.x + 6, y1: q.y + 6 }) && q !== p);
            let box = boxFor(preferRight);
            if (!free(box)) { const alt = boxFor(!preferRight); box = free(alt) ? alt : (p.sel ? box : null); }
            if (!box) return;
            taken.push(box); drawn += 1;
            g.fillStyle = p.sel ? warn : main; g.textAlign = box.right ? "left" : "right";
            g.fillText(t, box.right ? box.x0 : box.x1, p.y + dy);
        });
        cv.setAttribute("aria-label", `Sky radar: ${objs.length} ${layer} above the horizon. N up, zenith in the centre, horizon at the edge`);
    }
    function select(layer, id, scroll) {
        const same = state.sel && state.sel.layer === layer && String(state.sel.id) === String(id);
        state.sel = same ? null : { layer, id };
        render();
        if (scroll && state.sel) requestAnimationFrame(() => { const li = $("oh-rows").querySelector(".oh-row.sel"); if (li) li.scrollIntoView({ block: "nearest", behavior: "smooth" }); });
    }
    cv.addEventListener("click", (e) => {
        const b = cv.getBoundingClientRect(), x = e.clientX - b.left, y = e.clientY - b.top;
        let best = null, bd = 22 * 22;
        dots.forEach((d) => { const q = (d.x - x) ** 2 + (d.y - y) ** 2; if (q < bd) { bd = q; best = d; } });
        if (best) select(state.seg, best.id, true);
    });
    if (window.ResizeObserver) new ResizeObserver(drawRadar).observe(cv.parentElement);

    // ---- interaction ----
    document.querySelectorAll(".oh-seg .oh-tab").forEach((t) => t.addEventListener("click", () => {
        if (state.seg === t.dataset.layer) return;
        state.seg = t.dataset.layer; state.sel = null;
        subscribe(); render();
    }));
    $("oh-chips").addEventListener("click", (e) => {
        const b = e.target.closest(".ds-chip");
        if (!b) return;
        state.filter[state.seg] = b.dataset.f; render();
    });
    const rows = $("oh-rows");
    rows.addEventListener("click", (e) => {
        if (e.target.closest("a")) return;
        if (e.target.closest("[data-retry]")) { loadRest(); return; }
        const li = e.target.closest("li.oh-row"); if (li) select(state.seg, li.dataset.id, false);
    });
    $("oh-info").addEventListener("click", () => { const n = $("oh-status"), open = n.hidden; n.hidden = !open; $("oh-info").setAttribute("aria-expanded", String(open)); });
    rows.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && !e.target.closest("a")) { const li = e.target.closest("li.oh-row"); if (li) { e.preventDefault(); select(state.seg, li.dataset.id, false); } } });

    // ---- data: REST for the first paint and while the socket is down ----
    const REST = { aircraft: "/api/adsb/aircraft?limit=60", satellites: "/api/satellites", sondes: "/api/radiosondes" };
    async function loadRest() {
        state.restAt = Date.now();
        await Promise.all(LAYERS.map(async (l) => {
            try {
                const r = await fetch(REST[l]);
                if (!r.ok) { state.failed[l] = true; return; }
                const d = await r.json();
                state.failed[l] = false;
                if (state.wsLive && state.data[l]) return;   // the socket got there first
                state.data[l] = d;
            } catch (e) { state.failed[l] = true; }
        }));
        render();
    }
    loadRest();
    setInterval(() => { if (!state.wsLive && !document.hidden && Date.now() - state.restAt > 14000) loadRest(); }, 5000);

    // ---- data: live socket (packed overlays only; no camera pictures) ----
    let ws = null, retryMs = 2000, retryTimer = 0;
    // every layer is subscribed; the two the page is not showing come at the slow pace (every 10 s), so the segment counts stay fresh
    function subscribe() {
        if (ws && ws.readyState === 1) ws.send(JSON.stringify({ action: "subscribe", layers: LAYERS, slow: LAYERS.filter((l) => l !== state.seg) }));
    }
    function connect() {
        clearTimeout(retryTimer);
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        try { ws = new WebSocket(`${proto}//${location.host}/ws/live`); } catch (e) { retryTimer = setTimeout(connect, retryMs); return; }
        const mine = ws;
        mine.onopen = () => { retryMs = 2000; subscribe(); if (document.hidden) mine.send(JSON.stringify({ action: "idle", on: true })); };
        mine.onmessage = (ev) => {
            if (typeof ev.data !== "string") return;
            let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
            if (msg.type === "ov") {
                const d = window.ZenivoOverlay.apply(msg);
                if (d) { state.data[msg.l] = d; state.wsLive = true; render(); }
            } else if (msg.type === "subscribed") { state.wsLive = true; render(); }
        };
        mine.onclose = () => {
            if (ws === mine) ws = null;
            state.wsLive = false; render();
            retryTimer = setTimeout(connect, retryMs); retryMs = Math.min(retryMs * 2, 30000);
        };
        mine.onerror = () => { try { mine.close(); } catch (e) { /* already closed */ } };
    }
    connect();
    document.addEventListener("visibilitychange", () => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ action: "idle", on: document.hidden })); });
})();
