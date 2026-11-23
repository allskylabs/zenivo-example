// Community page: map and list of the Zenivo stations that share themselves (docs/COMMUNITY.md).
// Card fields come from other people's stations: they are only ever put into the page with textContent / DOM APIs.
(function () {
    "use strict";

    // Red Vision, clock and Menu come from shell.js; redraw the markers when the theme flips
    window.addEventListener("zenivo:rv", () => drawMarkers());

    // ---- map ----
    const THUMB_DATA = /^data:image\/(?:jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/;
    const listEl = document.getElementById("cstations");
    const emptyEl = document.getElementById("cempty");
    const countEl = document.getElementById("ccount");
    
    // Neutral basemap drawn by the page itself: coastlines only (Natural Earth 1:50m land, public domain),
    // no borders, no place names and no third-party tile server
    const css = getComputedStyle(document.documentElement);
    const tone = (name, fallback) => (css.getPropertyValue(name) || "").trim() || fallback;
    let popupOpen = false;   // the station sheet is open: do not rebuild the list under it
    const world = L.latLngBounds([-85, -180], [85, 180]);
    const map = L.map("cmap", {
        minZoom: 0.5, zoomSnap: 0.25, maxZoom: 9, preferCanvas: true, maxBounds: world.pad(0.6), maxBoundsViscosity: 0.6,
        attributionControl: false,
    }).setView([20, 10], 2);
    // The whole world must fit the box, also on a phone: the lowest zoom is the one that shows it all (not a fixed 2)
    function fitMinZoom() {
        const z = Math.max(0.5, map.getBoundsZoom(L.latLngBounds([-58, -180], [83, 180]), false));
        map.setMinZoom(z);
        if (map.getZoom() < z) map.setZoom(z);
    }
    L.control.attribution({ prefix: false }).addAttribution("Land, lakes, rivers: Natural Earth (public domain)").addTo(map);
    // The map box gets its final size after the shell lays out (and on rotation): tell Leaflet, or the map lands off-centre
    // Until the visitor moves the map themselves, every size change re-fits it to the stations: on phones the box gets
    // its size late, and a fit done before that left the map zoomed on Africa with no station in view
    let userMoved = false, lastPts = [];
    const cmapEl = document.getElementById("cmap");
    for (const t of ["pointerdown", "wheel", "keydown"]) cmapEl.addEventListener(t, () => { userMoved = true; }, { passive: true, capture: true });
    function autoFit() {
        if (userMoved) return;
        if (lastPts.length === 1) map.setView(lastPts[0], 5, { animate: false });
        else if (lastPts.length) map.fitBounds(lastPts, { padding: [40, 40], maxZoom: 6, animate: false });
        else map.setView([20, 10], Math.max(map.getMinZoom(), Math.min(2, map.getZoom())), { animate: false });
    }
    if (window.ResizeObserver) new ResizeObserver(() => { map.invalidateSize(); fitMinZoom(); autoFit(); }).observe(cmapEl);
    fitMinZoom();
    if (map.getMinZoom() > 2) map.setView([20, 10], map.getMinZoom());
    const ink = tone("--c-text-3", "#64748b"), landFill = tone("--c-surface-2", "#1e293b");
    const sea = tone("--c-bg", "#0a0f1c");
    const gridLines = (step, lim) => {
        const g = [];
        for (let lon = -180; lon <= 180; lon += step) g.push([[-85, lon], [85, lon]]);
        for (let lat = -lim; lat <= lim; lat += step) g.push([[lat, -180], [lat, 180]]);
        return L.polyline(g, { color: ink, weight: 1, opacity: 0.12, interactive: false });
    };
    const gridCoarse = gridLines(30, 60).addTo(map), gridFine = gridLines(10, 80);
    // Neutral detail layers: land, lakes and rivers only (Natural Earth, public domain). The 1:10m set loads when zoomed in.
    const mk = (kind) => ({
        land: () => L.geoJSON(null, { interactive: false, style: { color: ink, weight: 0.8, opacity: 0.7, fillColor: landFill, fillOpacity: 1 } }),
        lakes: () => L.geoJSON(null, { interactive: false, style: { color: ink, weight: 0.6, opacity: 0.55, fillColor: sea, fillOpacity: 1 } }),
        rivers: () => L.geoJSON(null, { interactive: false, style: { color: ink, weight: 0.7, opacity: 0.45, fill: false } }),
    })[kind]();
    const sets = {
        low: { land: mk("land"), lakes: mk("lakes"), rivers: mk("rivers") },
        high: { land: mk("land"), lakes: mk("lakes"), rivers: mk("rivers") },
    };
    const files = {
        low: { land: "land", lakes: "lakes", rivers: "rivers" },
        high: { land: "land10", lakes: "lakes10", rivers: "rivers10" },
    };
    const loaded = {};
    const load = (lvl) => {
        if (loaded[lvl]) return;
        loaded[lvl] = true;
        for (const k of ["land", "lakes", "rivers"]) {
            fetch(`/static/geo/${files[lvl][k]}.json`).then((r) => r.json()).then((g) => {
                sets[lvl][k].addData(g);
                applyLevel();
            }).catch(() => { loaded[lvl] = false; /* the stations still show on the plain grid */ });
        }
    };
    const order = ["land", "lakes", "rivers"];
    function applyLevel() {
        const hi = map.getZoom() >= 5 && sets.high.land.getLayers().length > 0;
        for (const lvl of ["low", "high"]) {
            const on = (lvl === "high") === hi;
            for (const k of order) {
                const lyr = sets[lvl][k];
                if (on && !map.hasLayer(lyr)) lyr.addTo(map);
                if (!on && map.hasLayer(lyr)) map.removeLayer(lyr);
            }
        }
        for (const k of ["rivers", "lakes", "land"]) for (const lvl of ["low", "high"]) if (map.hasLayer(sets[lvl][k])) sets[lvl][k].bringToBack();
        const fine = map.getZoom() >= 5;
        if (fine && !map.hasLayer(gridFine)) { gridFine.addTo(map); map.removeLayer(gridCoarse); }
        if (!fine && !map.hasLayer(gridCoarse)) { gridCoarse.addTo(map); map.removeLayer(gridFine); }
    }
    map.on("zoomend", () => { if (map.getZoom() >= 4) load("high"); applyLevel(); });
    load("low");
    // Nearby stations merge into a cluster (Leaflet.markercluster); without that file the page falls back to plain markers
    const clustered = typeof L.markerClusterGroup === "function";
    let spiderfied = false;   // a spider is open: do not rebuild the markers under it
    const stateTone = { online: "var(--c-ok)", idle: "var(--c-warn)", offline: "var(--c-text-3)" };
    function clusterIcon(c) {
        const kids = c.getAllChildMarkers(), n = { online: 0, idle: 0, offline: 0 };
        let own = false;
        for (const m of kids) { n[m.options.cmState] = (n[m.options.cmState] || 0) + 1; if (m.options.cmOwn) own = true; }
        const total = kids.length;
        let acc = 0;
        const stops = [];
        for (const k of ["online", "idle", "offline"]) {
            if (!n[k]) continue;
            stops.push(`${stateTone[k]} ${(acc / total * 100).toFixed(2)}% ${((acc + n[k]) / total * 100).toFixed(2)}%`);
            acc += n[k];
        }
        const label = `${total} stations: ` + ["online", "idle", "offline"].filter((k) => n[k]).map((k) => `${n[k]} ${k}`).join(", ");
        return L.divIcon({ className: "cm-mk cm-cluster", iconSize: [40, 40], iconAnchor: [20, 20],
            html: `<span class="cm-cl${own ? " own" : ""}" role="img" title="${label}" aria-label="${label}" style="background:conic-gradient(${stops.join(",")})"><span class="cm-cl-n">${total}</span></span>` });
    }
    const layer = clustered ? L.markerClusterGroup({
        maxClusterRadius: 40, showCoverageOnHover: false,
        // keep markers that pan out of view: otherwise a popup taller than a short (phone) map pans its own marker
        // away, markercluster drops the marker and the popup closes with it
        removeOutsideVisibleBounds: false, zoomToBoundsOnClick: true, spiderfyOnMaxZoom: true,
        spiderfyDistanceMultiplier: 1.3, animate: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
        spiderLegPolylineOptions: { weight: 1.5, color: tone("--c-text-2", "gray"), opacity: 0.6 },
        iconCreateFunction: clusterIcon,
    }) : L.layerGroup();
    layer.addTo(map);
    if (clustered) {
        layer.on("spiderfied", () => { spiderfied = true; });
        layer.on("unspiderfied", () => { spiderfied = false; });
    }
    let stations = [];   // [{card, marker}]
    let ownId = null;
    let cards = [];
    let fitted = false;

    const finite = (v) => typeof v === "number" && Number.isFinite(v);

    function httpsUrl(u) {
        if (typeof u !== "string") return null;
        try { const p = new URL(u); return p.protocol === "https:" && !p.username && !p.password ? p.href : null; } catch (e) { return null; }
    }

    function agoText(ts) {
        const s = Math.max(0, Math.round(Date.now() / 1000 - Number(ts)));
        if (!Number.isFinite(s)) return "unknown";
        if (s < 90) return "just now";
        if (s < 5400) return `${Math.round(s / 60)} min ago`;
        if (s < 172800) return `${Math.round(s / 3600)} h ago`;
        return `${Math.round(s / 86400)} days ago`;
    }

    const ICON = { online: "wifi", idle: "clock", offline: "wifi-off" };
    const STATE_LABEL = { online: "Online", idle: "Idle", offline: "Offline" };
    function icon(name) { return window.DS ? window.DS.icon(name) : document.createElementNS("http://www.w3.org/2000/svg", "svg"); }
    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    // Full-screen preview (Esc, the close button or a click outside the image closes it)
    // `sources`: image URLs tried in order when one fails to load
    function openLightbox(sources, title, note) {
        const back = el("div", "cm-light");
        back.setAttribute("role", "dialog");
        back.setAttribute("aria-modal", "true");
        back.setAttribute("aria-label", `Preview of ${title}`);
        const fig = el("figure");
        const img = document.createElement("img");
        img.alt = `Latest frame of ${title}`;
        img.referrerPolicy = "no-referrer";
        let next = 1;
        img.addEventListener("error", () => { if (next < sources.length) img.src = sources[next++]; });
        img.src = sources[0];
        const cap = el("figcaption", "", note ? `${title} \u2014 ${note}` : title);
        const close = el("button", "ds-btn ds-btn--icon cm-light-close");
        close.type = "button";
        close.setAttribute("aria-label", "Close");
        close.appendChild(icon("x"));
        const shut = () => { back.remove(); document.removeEventListener("keydown", onKey, true); };
        const onKey = (ev) => { if (ev.key === "Escape") { ev.stopPropagation(); shut(); } };
        back.addEventListener("click", (ev) => { if (ev.target === back || ev.target.closest(".cm-light-close")) shut(); });
        document.addEventListener("keydown", onKey, true);
        fig.append(img, cap);
        back.append(close, fig);
        document.body.appendChild(back);
        close.focus();
    }

    // Large-preview candidates for a station without a public address: the copy this station holds, then its relays
    let relayed = {};
    let seenMap = {};
    const STATION_ID = /^[0-9a-f]{32}$/;
    function relaySources(card) {
        const out = [];
        if (!STATION_ID.test(String(card.id))) return out;
        if (relayed && finite(relayed[card.id])) out.push({ url: `/api/community/relay/${card.id}.jpg`, host: location.host });
        for (const r of Array.isArray(card.relays) ? card.relays.slice(0, 3) : []) {
            const o = httpsUrl(r);
            if (!o) continue;
            const origin = new URL(o).origin;
            out.push({ url: `${origin}/api/community/relay/${card.id}.jpg`, host: new URL(o).host });
        }
        return out;
    }

    // "Updated" is when the card was last signed (up to 6 h ago on a healthy station); "last seen" is when the station
    // itself last reached this one, so it is the better sign of life. Falls back to the card time when we never heard it.
    function agoOnly(card) {
        const t = Number(seenMap[card.id]);
        return Number.isFinite(t) && t > 0 ? agoText(t) : agoText(card.updated);
    }

    function seenText(card, isOwn) {
        if (isOwn) return "online now";
        const t = Number(seenMap[card.id]);
        return Number.isFinite(t) && t > 0 ? `last seen ${agoText(t)}` : `updated ${agoText(card.updated)}`;
    }

    // online: heard from within 15 min; idle: within 6 h (a card is re-signed that often); offline: older or never
    function stationState(card, isOwn) {
        if (isOwn) return "online";
        const t = Number(seenMap[card.id]) || Number(card.updated) || 0;
        const age = Date.now() / 1000 - (t > 1e12 ? t / 1000 : t);
        return !t ? "offline" : age < 900 ? "online" : age < 21600 ? "idle" : "offline";
    }
    // Station sheet: icon-led facts, the latest frame, then Open / Message
    const sheet = document.getElementById("cm-sheet");
    const fact = (ic, title, value) => {
        const r = el("div", "ds-row");
        const i = el("span", "ds-row__icon"); i.appendChild(icon(ic));
        const b = el("span", "ds-row__body"); b.appendChild(el("span", "ds-row__title", title));
        r.append(i, b);
        if (value) r.appendChild(el("span", "ds-row__value", value));
        return r;
    };
    function openSheet(card, isOwn) {
        const stt = stationState(card, isOwn), name = String(card.name || "Unnamed station");
        document.getElementById("cm-sheet-name").textContent = name;
        document.getElementById("cm-sheet-sub").textContent = (isOwn ? "You \u00b7 " : "") + STATE_LABEL[stt] + " \u00b7 " + (isOwn ? "now" : agoOnly(card));
        const stEl = document.getElementById("cm-sheet-st");
        stEl.className = `cm-st ${stt}`;
        stEl.replaceChildren(icon(ICON[stt]));
        const body = document.getElementById("cm-sheet-body");
        body.textContent = "";
        const base = httpsUrl(card.url);
        let src = null;
        if (base) src = base.replace(/\/+$/, "") + "/api/community/thumb.jpg";
        else if (typeof card.thumb === "string" && THUMB_DATA.test(card.thumb)) src = card.thumb;
        if (src) {
            const img = document.createElement("img");
            img.alt = `Latest frame of ${name}`;
            img.referrerPolicy = "no-referrer";
            const zoom = el("button", "cm-frame");
            zoom.type = "button";
            zoom.title = "Enlarge";
            zoom.setAttribute("aria-label", `Enlarge the preview of ${name}`);
            img.addEventListener("error", () => zoom.remove());
            img.src = src;
            zoom.appendChild(img);
            const relays = base ? [] : relaySources(card);
            const larges = base && card.preview === true ? [base.replace(/\/+$/, "") + "/api/community/preview.jpg"] : relays.map((r) => r.url);
            const note = relays.length ? `Relayed by ${relays[0].host}` : (card.preview === true ? "" : "Small preview only");
            zoom.addEventListener("click", () => openLightbox(larges.concat([src]), name, note));
            body.appendChild(zoom);
        }
        const facts = el("div", "ds-list cm-facts");
        if (card.camera) facts.appendChild(fact("camera", String(card.camera)));
        if (card.software) facts.appendChild(fact("cpu", String(card.software)));
        facts.appendChild(fact("clock", seenText(card, isOwn).replace(/^./, (c) => c.toUpperCase())));
        if (!base) facts.appendChild(fact("lock", "No public address"));
        else if (card.open === false) facts.appendChild(fact("lock", "Page not linked by the owner"));
        body.appendChild(facts);
        const acts = el("div", "cm-acts");
        if (base && card.open !== false) {
            const a = el("a", "ds-btn ds-btn--primary");
            a.href = base; a.target = "_blank"; a.rel = "noopener noreferrer";
            a.setAttribute("aria-label", "Open station");
            a.append(icon("arrow-up-right"), el("span", "", "Open"));
            acts.appendChild(a);
        }
        if (!isOwn && window.ZenivoChat && window.ZenivoChat.canDM(card.id)) {
            const m = el("button", "ds-btn");
            m.type = "button";
            m.setAttribute("aria-label", `Message ${name}`);
            m.append(icon("message-circle"), el("span", "", "Message"));
            m.addEventListener("click", () => { window.DS.closeSheet(sheet); window.ZenivoChat.openDM(card.id); });
            acts.appendChild(m);
        }
        if (acts.childNodes.length) body.appendChild(acts);
        popupOpen = true;
        window.DS.openSheet(sheet);
    }
    new MutationObserver(() => { if (!sheet.hasAttribute("data-open")) popupOpen = false; }).observe(sheet, { attributes: true, attributeFilter: ["data-open"] });

    function drawMarkers() {
        layer.clearLayers();
        stations = [];
        listEl.textContent = "";
        const valid = cards.filter((c) => c && finite(c.lat) && finite(c.lon) && Math.abs(c.lat) <= 90 && Math.abs(c.lon) <= 180);
        const rank = { online: 0, idle: 1, offline: 2 }, seenOf = (c) => { const t = Number(seenMap[c.id]) || Number(c.updated) || 0; return t > 1e12 ? t / 1000 : t; };
        valid.sort((a, b) => (a.id === ownId ? -1 : b.id === ownId ? 1 : 0) || rank[stationState(a, false)] - rank[stationState(b, false)] || seenOf(b) - seenOf(a) || String(a.name).localeCompare(String(b.name)));
        const pts = [];
        const counts = { online: 0, idle: 0, offline: 0 };
        for (const card of valid) {
            const isOwn = card.id === ownId;
            const st = stationState(card, isOwn);
            const name = String(card.name || "Unnamed station");
            const marker = L.marker([card.lat, card.lon], {
                icon: L.divIcon({ className: "cm-mk", iconSize: [26, 26], iconAnchor: [13, 13],
                    html: `<span class="cm-dot ${st}${isOwn ? " own" : ""}"></span>` }),
                title: `${name} (${st})`, keyboard: true, cmState: st, cmOwn: isOwn,
            }).addTo(layer);
            marker.on("click", () => openSheet(card, isOwn));
            pts.push([card.lat, card.lon]);
            const item = el("li", "cm-row-wrap");
            const row = el("button", "ds-row");
            row.type = "button";
            row.setAttribute("aria-label", `${name}, ${STATE_LABEL[st]}`);
            const ic = el("span", `cm-st ${st}`); ic.appendChild(icon(ICON[st]));
            const bd = el("span", "ds-row__body");
            bd.appendChild(el("span", isOwn ? "ds-row__title cm-you" : "ds-row__title", isOwn ? `${name} \u00b7 You` : name));
            bd.appendChild(el("span", "ds-row__sub", card.camera || seenText(card, isOwn)));
            row.append(ic, bd, el("span", "ds-row__value", isOwn ? "now" : agoOnly(card)));
            counts[st] += 1;
            row.addEventListener("click", () => {
                userMoved = true;
                map.setView([card.lat, card.lon], Math.max(map.getZoom(), 6), { animate: false });
                openSheet(card, isOwn);
                if (window.matchMedia("(max-width: 1023px)").matches) document.querySelector(".cm-mapbox").scrollIntoView({ block: "start", behavior: "smooth" });
            });
            item.appendChild(row);
            if (!isOwn && window.ZenivoChat && window.ZenivoChat.canDM(card.id)) {
                // admin only: the chat widget exists only for the signed-in owner
                const dm = el("button", "cm-dm");
                dm.type = "button";
                dm.title = "Message";
                dm.setAttribute("aria-label", `Message ${name}`);
                dm.appendChild(icon("message-circle"));
                dm.addEventListener("click", () => window.ZenivoChat.openDM(card.id));
                item.appendChild(dm);
            }
            listEl.appendChild(item);
            stations.push({ card, marker });
        }
        countEl.textContent = valid.length ? `${counts.online}/${valid.length}` : "";
        const lg = document.getElementById("clegend");
        if (lg) {
            lg.hidden = !valid.length;
            lg.replaceChildren(...["online", "idle", "offline"].map((k) => {
                const sp = el("span", `ds-chip ds-chip--${k === "online" ? "ok" : k === "idle" ? "warn" : "info"}`);
                sp.title = STATE_LABEL[k];
                sp.append(icon(ICON[k]), document.createTextNode(String(counts[k])));
                sp.setAttribute("aria-label", `${STATE_LABEL[k]} ${counts[k]}`);
                return sp;
            }));
        }
        lastPts = pts;
        if (!fitted && pts.length) {
            fitted = true;
            autoFit();
        }
    }

    function showEmpty(kind, reason) {
        listEl.textContent = "";
        emptyEl.hidden = false;
        emptyEl.className = "ds-empty cm-empty";
        emptyEl.textContent = "";
        const off = kind === "off";
        emptyEl.appendChild(icon(off ? "share-2" : "wifi-off"));
        emptyEl.appendChild(el("div", "ds-empty__title", off ? "Sharing is off" : "Not available"));
        if (!off && reason) emptyEl.appendChild(el("div", "ds-empty__hint", String(reason)));
        if (off) {
            const a = el("a", "ds-btn ds-btn--primary");
            a.href = "/settings#community";
            a.setAttribute("aria-label", "Open sharing settings");
            a.append(icon("settings"), el("span", "", "Sharing"));
            emptyEl.appendChild(a);
        }
    }

    async function refresh() {
        try {
            const st = await (await fetch("/api/community/status")).json();
            ownId = st.id || null;
            if (!st.available || !st.enabled) {
                cards = [];
                drawMarkers();
                showEmpty(st.available ? "off" : "unavailable", st.reason);
                return;
            }
            emptyEl.hidden = true;
            const res = await fetch("/api/community/peers");
            const data = res.ok ? await res.json() : { cards: [] };
            cards = Array.isArray(data.cards) ? data.cards : [];
            relayed = data.relayed && typeof data.relayed === "object" ? data.relayed : {};
            seenMap = data.seen && typeof data.seen === "object" ? data.seen : {};
            if (!popupOpen && !spiderfied) drawMarkers();
            if (!cards.length) showEmpty("unavailable", "No stations yet");
        } catch (err) {
            showEmpty("unavailable", "Could not load the stations. Retrying.");
        }
    }

    // the chat widget loads after this page's list: redraw once it knows which stations take messages
    window.addEventListener("zenivo-chat-ready", () => { if (!popupOpen && !spiderfied) drawMarkers(); });
    refresh();
    setInterval(refresh, 60000);
})();
