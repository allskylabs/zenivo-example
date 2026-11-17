// Overhead page (/overhead): lists of aircraft, satellites and radiosondes above the station plus a small 2D sky radar.
// Data comes from the live socket (/ws/live WITHOUT binary=1, so no camera pictures are sent: only a small JSON telemetry message,
// which this page ignores, and the subscribed packed overlays, decoded by /static/overlay_decode.js). REST is used once for the first
// paint and while the socket is down. Object names come from the receivers: they only reach the page through escapeHtml or textContent.
(function () {
    "use strict";

    // ---- shared shell behaviour (same as the other standalone pages): Red Vision, UTC clock, phone Menu sheet ----
    const PREFS = "allsky.ui.prefs.v1";
    const $ = (id) => document.getElementById(id);
    const nv = $("btn-night-vision");
    const readPrefs = () => { try { return JSON.parse(localStorage.getItem(PREFS)) || {}; } catch (e) { return {}; } };
    function applyNight(on) {
        document.body.classList.toggle("night-mode", on);
        if (on) document.documentElement.setAttribute("data-theme", "red"); else document.documentElement.removeAttribute("data-theme");
        if (nv) nv.classList.toggle("btn-primary", on);
    }
    applyNight(!!readPrefs().nightVision);
    if (nv) nv.addEventListener("click", () => {
        const on = !document.body.classList.contains("night-mode");
        applyNight(on);
        try { localStorage.setItem(PREFS, JSON.stringify(Object.assign(readPrefs(), { nightVision: on }))); } catch (e) { /* not persisted */ }
        drawRadar();
    });
    const clock = $("nav-utc-clock");
    if (clock) { const tick = () => { clock.textContent = new Date().toISOString().slice(11, 19); }; tick(); setInterval(tick, 1000); }
    const moreBtn = $("m-more-btn"), sheet = $("m-more-sheet"), backdrop = $("m-sheet-backdrop"), rv = $("m-rv-toggle");
    const path = location.pathname.replace(/\/+$/, "") || "/";
    document.querySelectorAll("[data-mpath]").forEach((el) => el.classList.toggle("active", el.getAttribute("data-mpath").split(",").includes(path)));
    if (moreBtn && sheet && backdrop) {
        const setOpen = (o) => { sheet.hidden = !o; backdrop.hidden = !o; moreBtn.setAttribute("aria-expanded", o ? "true" : "false"); };
        moreBtn.addEventListener("click", () => setOpen(sheet.hidden));
        backdrop.addEventListener("click", () => setOpen(false));
        document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
    }
    if (rv && nv) rv.addEventListener("click", () => {
        nv.click();
        const on = nv.classList.contains("btn-primary");
        rv.classList.toggle("btn-primary", on); rv.setAttribute("aria-checked", on ? "true" : "false");
    });

    // ---- helpers ----
    const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    const compass = (az) => COMPASS[Math.round(((az % 360) + 360) % 360 / 45) % 8];
    const LAYERS = ["aircraft", "satellites", "sondes"];
    const TITLES = { aircraft: "Aircraft now", satellites: "Satellites now", sondes: "Sondes now" };
    const idOf = { aircraft: (a) => a.icao, satellites: (s) => s.norad, sondes: (s) => s.serial };
    const labelOf = { aircraft: (a) => a.callsign || a.icao, satellites: (s) => s.name, sondes: (s) => s.serial };

    // Filter chips per segment, only from fields that exist (the aircraft ones are the Live card's)
    const FILTERS = {
        aircraft: [["all", "All", () => true], ["view", "In view", (a) => !!a.in_view],
            ["up", "Climbing", (a) => a.climb_m_min != null && a.climb_m_min > 60], ["down", "Descending", (a) => a.climb_m_min != null && a.climb_m_min < -60]],
        satellites: [["all", "All", () => true], ["view", "In view", (s) => !!s.in_view], ["vis", "Visible", (s) => !!s.visible]],
        sondes: [["all", "All", () => true], ["view", "In view", (s) => !!s.in_view]],
    };

    const state = { seg: "aircraft", filter: { aircraft: "all", satellites: "all", sondes: "all" }, data: {}, sel: null, wsLive: false, restAt: 0 };

    // ---- rows (same markup and classes as the Live "Overhead now" card, see app/08b-events-live.js) ----
    const whereOf = (a) => a.in_view ? `<span class="fl-inview">● in view</span>`
        : a.above_horizon !== false && a.alt_deg >= 0 ? `${compass(a.az_deg)} ${Math.round(a.alt_deg)}° up` : "below horizon";
    function rowHtml(layer, a) {
        const id = esc(idOf[layer](a));
        const cls = `flight${a.in_view ? " in-view" : ""}${state.sel && state.sel.layer === layer && String(state.sel.id) === String(idOf[layer](a)) ? " sel" : ""}`;
        const open = `<li class="${cls}" data-id="${id}" tabindex="0" role="button" aria-label="${esc(labelOf[layer](a))}">`;
        if (layer === "aircraft") {
            const climb = a.climb_m_min == null ? "" : a.climb_m_min > 60 ? " ↗" : a.climb_m_min < -60 ? " ↘" : "";
            const url = `https://globe.adsbexchange.com/?icao=${encodeURIComponent(String(a.icao).toLowerCase())}`;
            return `${open}<div class="fl-top"><a href="${url}" target="_blank" rel="noopener" class="flight-tag">✈ ${esc(labelOf[layer](a))}</a><span class="fl-where">${whereOf(a)}</span></div>
                <div class="fl-meta"><span title="Altitude">${esc(Number(a.altitude_m).toLocaleString())} m${climb}</span>
                <span title="Ground speed">${a.speed_kmh != null ? `${esc(a.speed_kmh)} km/h` : "-- km/h"}</span>
                <span title="Distance from the camera">${esc(a.distance_km)} km</span></div></li>`;
        }
        if (layer === "sondes") {
            const dir = a.climb_m_s > 1 ? " ↗" : a.climb_m_s < -1 ? " ↘" : "";
            return `${open}<div class="fl-top"><a href="https://sondehub.org/${encodeURIComponent(a.serial)}" target="_blank" rel="noopener" class="flight-tag">🎈 ${esc(a.serial)}</a><span class="fl-where">${whereOf(a)}</span></div>
                <div class="fl-meta"><span title="Altitude">${esc(Number(a.altitude_m).toLocaleString())} m${dir}</span>
                <span title="${esc(a.type || "")}">${a.freq_mhz ? esc(a.freq_mhz) + " MHz" : esc(a.type || "")}</span>
                <span title="Distance from the camera">${esc(a.distance_km)} km</span></div></li>`;
        }
        const where = a.in_view ? `<span class="fl-inview">● in view</span>` : `${compass(a.az_deg)} ${Math.round(a.alt_deg)}° up`;
        return `${open}<div class="fl-top"><a href="https://www.n2yo.com/satellite/?s=${encodeURIComponent(a.norad)}" target="_blank" rel="noopener" class="flight-tag">🛰 ${esc(a.name)}</a><span class="fl-where">${where}</span></div>
            <div class="fl-meta"><span title="Height above the ground">${esc(Number(a.height_km).toLocaleString())} km</span>
            <span>${compass(a.az_deg)} ${Math.round(a.alt_deg)}°</span><span>${a.visible ? "visible" : "not visible"}</span></div></li>`;
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
    function draw() {
        const layer = state.seg, d = state.data[layer];
        LAYERS.forEach((l) => {
            const t = $("oh-tab-" + l), on = l === layer;
            t.classList.toggle("active", on); t.setAttribute("aria-selected", on ? "true" : "false");
            const dd = state.data[l];
            $("oh-n-" + l).textContent = !dd ? "–" : (dd.enabled === false || dd.disabled) ? "off" : String(objectsOf(l).length);
        });
        $("oh-radar-title").textContent = TITLES[layer];
        const [count, status] = statusOf(layer);
        $("oh-count").textContent = count;
        $("oh-status").textContent = status;
        const live = $("oh-live");
        live.textContent = state.wsLive ? "live" : "updating"; live.classList.toggle("off", !state.wsLive);
        // chips
        const chips = $("oh-chips");
        if (chips.dataset.layer !== layer) {
            chips.dataset.layer = layer;
            chips.innerHTML = FILTERS[layer].map(([k, t]) => `<button type="button" class="zchip" data-f="${k}" aria-pressed="false">${esc(t)}</button>`).join("");
        }
        chips.querySelectorAll(".zchip").forEach((c) => { const on = c.dataset.f === state.filter[layer]; c.classList.toggle("active", on); c.setAttribute("aria-pressed", String(on)); });
        // rows
        const ul = $("oh-rows");
        const all = objectsOf(layer);
        const fn = (FILTERS[layer].find((f) => f[0] === state.filter[layer]) || FILTERS[layer][0])[2];
        const list = all.filter(fn);
        if (!d) ul.innerHTML = "";
        else if (d.enabled === false || d.disabled) ul.innerHTML = `<li class="live-events-empty">${esc(status)}.</li>`;
        else if (!list.length) {
            const none = layer === "aircraft" ? "aircraft heard" : layer === "satellites" ? "large satellite above the horizon" : "radiosonde flying nearby";
            ul.innerHTML = `<li class="live-events-empty">${all.length ? "Nothing matches this filter." : `No ${none} right now.`}</li>`;
        } else ul.innerHTML = list.map((a) => rowHtml(layer, a)).join("");
        drawRadar();
    }

    // ---- sky radar: N up, zenith in the centre, horizon at the edge, rings at 30 and 60 degrees of elevation ----
    const cv = $("oh-canvas");
    let dots = [];   // hit targets of the last draw: { id, x, y }
    function drawRadar() {
        if (!cv) return;
        const box = cv.parentElement, size = Math.max(160, Math.min(box.clientWidth, window.innerWidth <= 520 ? 240 : 340)), dpr = window.devicePixelRatio || 1;
        if (cv.width !== Math.round(size * dpr)) { cv.width = Math.round(size * dpr); cv.height = Math.round(size * dpr); }
        cv.style.width = size + "px"; cv.style.height = size + "px";
        const g = cv.getContext("2d");
        g.setTransform(dpr, 0, 0, dpr, 0, 0);
        g.clearRect(0, 0, size, size);
        const css = getComputedStyle(document.documentElement);
        const col = (n, f) => (css.getPropertyValue(n) || "").trim() || f;
        const ink = col("--text-secondary", "#94a3b8"), line = col("--border-color", "#1e293b"), accent = col("--accent", "#38bdf8"),
            warn = col("--warning", "#fbbf24"), main = col("--text-primary", "#f1f5f9"), sub = col("--bg-card-sub", "#0b1220");
        const R = size / 2 - 22, cx = size / 2, cy = size / 2;
        g.fillStyle = sub; g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.fill();
        g.strokeStyle = line; g.lineWidth = 1.5; g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.stroke();
        g.lineWidth = 1;
        [1 / 3, 2 / 3].forEach((f) => { g.beginPath(); g.arc(cx, cy, R * f, 0, Math.PI * 2); g.stroke(); });
        g.beginPath(); g.moveTo(cx - R, cy); g.lineTo(cx + R, cy); g.moveTo(cx, cy - R); g.lineTo(cx, cy + R); g.stroke();
        g.fillStyle = ink; g.font = "600 12px " + col("--font-sans", "sans-serif"); g.textAlign = "center"; g.textBaseline = "middle";
        g.fillStyle = accent; g.fillText("N", cx, cy - R - 11); g.fillStyle = ink;
        g.fillText("S", cx, cy + R + 11); g.fillText("E", cx + R + 11, cy); g.fillText("W", cx - R - 11, cy);
        g.font = "10px " + col("--font-sans", "sans-serif"); g.fillStyle = ink;
        g.fillText("60°", cx + 14, cy - R / 3 + 8); g.fillText("30°", cx + 14, cy - 2 * R / 3 + 8);
        g.fillStyle = main; g.beginPath(); g.arc(cx, cy, 2.5, 0, Math.PI * 2); g.fill();
        dots = [];
        const layer = state.seg, objs = objectsOf(layer).filter((o) => typeof o.az_deg === "number" && typeof o.alt_deg === "number" && o.alt_deg >= 0);
        const label = objs.length <= 12;
        g.textBaseline = "middle";
        objs.forEach((o) => {
            const r = R * (90 - Math.min(90, o.alt_deg)) / 90, th = o.az_deg * Math.PI / 180;
            const x = cx + r * Math.sin(th), y = cy - r * Math.cos(th), id = idOf[layer](o);
            const sel = state.sel && state.sel.layer === layer && String(state.sel.id) === String(id);
            dots.push({ id, x, y });
            if (sel) { g.strokeStyle = warn; g.lineWidth = 2; g.beginPath(); g.arc(x, y, 9, 0, Math.PI * 2); g.stroke(); }
            g.fillStyle = sel ? warn : o.in_view ? main : accent;
            g.beginPath(); g.arc(x, y, sel ? 5.5 : 4.5, 0, Math.PI * 2); g.fill();
            if (sel || label) {
                const t = String(labelOf[layer](o)); g.font = (sel ? "700 " : "") + "11px " + col("--font-sans", "sans-serif");
                g.fillStyle = sel ? warn : main; const right = x < cx + R * 0.45;
                g.textAlign = right ? "left" : "right"; g.fillText(t, x + (right ? 9 : -9), y - (sel ? 10 : 0));
            }
        });
        cv.setAttribute("aria-label", `Sky radar: ${objs.length} ${layer} above the horizon. N up, zenith in the centre, horizon at the edge`);
    }
    function select(layer, id, scroll) {
        const same = state.sel && state.sel.layer === layer && String(state.sel.id) === String(id);
        state.sel = same ? null : { layer, id };
        render();
        if (scroll && state.sel) requestAnimationFrame(() => { const li = $("oh-rows").querySelector(".flight.sel"); if (li) li.scrollIntoView({ block: "nearest", behavior: "smooth" }); });
    }
    cv.addEventListener("click", (e) => {
        const b = cv.getBoundingClientRect(), x = e.clientX - b.left, y = e.clientY - b.top;
        let best = null, bd = 22 * 22;
        dots.forEach((d) => { const q = (d.x - x) ** 2 + (d.y - y) ** 2; if (q < bd) { bd = q; best = d; } });
        if (best) select(state.seg, best.id, true);
    });
    if (window.ResizeObserver) new ResizeObserver(drawRadar).observe(cv.parentElement);

    // ---- interaction ----
    document.querySelectorAll(".oh-seg .ov-tab").forEach((t) => t.addEventListener("click", () => {
        if (state.seg === t.dataset.layer) return;
        state.seg = t.dataset.layer; state.sel = null;
        subscribe(); render();
    }));
    $("oh-chips").addEventListener("click", (e) => {
        const b = e.target.closest(".zchip");
        if (!b) return;
        state.filter[state.seg] = b.dataset.f; render();
    });
    const rows = $("oh-rows");
    rows.addEventListener("click", (e) => { if (e.target.closest("a")) return; const li = e.target.closest("li.flight"); if (li) select(state.seg, li.dataset.id, false); });
    rows.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && !e.target.closest("a")) { const li = e.target.closest("li.flight"); if (li) { e.preventDefault(); select(state.seg, li.dataset.id, false); } } });

    // ---- data: REST for the first paint and while the socket is down ----
    const REST = { aircraft: "/api/adsb/aircraft?limit=60", satellites: "/api/satellites", sondes: "/api/radiosondes" };
    async function loadRest() {
        state.restAt = Date.now();
        await Promise.all(LAYERS.map(async (l) => {
            try {
                const r = await fetch(REST[l]);
                if (!r.ok) return;
                const d = await r.json();
                if (state.wsLive && state.data[l]) return;   // the socket got there first
                state.data[l] = d;
            } catch (e) { /* optional feature */ }
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
