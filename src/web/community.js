// Community page: map and list of the Zenivo stations that share themselves (docs/COMMUNITY.md).
// Card fields come from other people's stations: they are only ever put into the page with textContent / DOM APIs.
(function () {
    "use strict";

    // ---- shared shell behaviour (same as the other standalone pages): Red Vision, UTC clock, phone More sheet ----
    const PREFS = "allsky.ui.prefs.v1";
    const nv = document.getElementById("btn-night-vision");
    const readPrefs = () => { try { return JSON.parse(localStorage.getItem(PREFS)) || {}; } catch (e) { return {}; } };
    function applyNight(on) {
        document.body.classList.toggle("night-mode", on);
        if (on) document.documentElement.setAttribute("data-theme", "red"); else document.documentElement.removeAttribute("data-theme");
        nv.classList.toggle("btn-primary", on);
    }
    applyNight(!!readPrefs().nightVision);
    nv.addEventListener("click", () => {
        const on = !document.body.classList.contains("night-mode");
        applyNight(on);
        try { localStorage.setItem(PREFS, JSON.stringify(Object.assign(readPrefs(), { nightVision: on }))); } catch (e) { /* not persisted */ }
        drawMarkers();
    });
    const clock = document.getElementById("nav-utc-clock");
    const tick = () => { clock.textContent = new Date().toISOString().slice(11, 19); };
    tick(); setInterval(tick, 1000);

    const moreBtn = document.getElementById("m-more-btn"), sheet = document.getElementById("m-more-sheet");
    const backdrop = document.getElementById("m-sheet-backdrop"), rv = document.getElementById("m-rv-toggle");
    const path = location.pathname.replace(/\/+$/, "") || "/";
    document.querySelectorAll("[data-mpath]").forEach((el) => el.classList.toggle("active", el.getAttribute("data-mpath").split(",").includes(path)));
    if (moreBtn && sheet && backdrop) {
        const setOpen = (o) => { sheet.hidden = !o; backdrop.hidden = !o; moreBtn.setAttribute("aria-expanded", o ? "true" : "false"); };
        moreBtn.addEventListener("click", () => setOpen(sheet.hidden));
        backdrop.addEventListener("click", () => setOpen(false));
        document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
    }
    if (rv) rv.addEventListener("click", () => {
        nv.click();
        const on = nv.classList.contains("btn-primary");
        rv.classList.toggle("btn-primary", on); rv.setAttribute("aria-checked", on ? "true" : "false");
    });

    // ---- map ----
    const THUMB_DATA = /^data:image\/(?:jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/;
    const listEl = document.getElementById("cstations");
    const emptyEl = document.getElementById("cempty");
    const countEl = document.getElementById("ccount");
    const noteEl = document.getElementById("cnote");

    // Neutral basemap drawn by the page itself: coastlines only (Natural Earth 1:50m land, public domain),
    // no borders, no place names and no third-party tile server
    const css = getComputedStyle(document.documentElement);
    const tone = (name, fallback) => (css.getPropertyValue(name) || "").trim() || fallback;
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
    if (window.ResizeObserver) new ResizeObserver(() => { map.invalidateSize(); fitMinZoom(); }).observe(document.getElementById("cmap"));
    fitMinZoom();
    if (map.getMinZoom() > 2) map.setView([20, 10], map.getMinZoom());
    const ink = tone("--text-muted", "#64748b"), landFill = tone("--border-color", "#1e293b");
    const sea = tone("--bg-primary", "#0a0f1c");
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
    const layer = L.layerGroup().addTo(map);
    let stations = [];   // [{card, marker}]
    let ownId = null;
    let cards = [];
    let fitted = false;

    const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
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
        const fig = el("figure", "cm-light-fig");
        const img = document.createElement("img");
        img.alt = `Latest frame of ${title}`;
        img.referrerPolicy = "no-referrer";
        let next = 1;
        img.addEventListener("error", () => { if (next < sources.length) img.src = sources[next++]; });
        img.src = sources[0];
        const cap = el("figcaption", "", note ? `${title} \u2014 ${note}` : title);
        const close = el("button", "cm-light-close", "Close");
        close.type = "button";
        const shut = () => { back.remove(); document.removeEventListener("keydown", onKey); };
        const onKey = (ev) => { if (ev.key === "Escape") shut(); };
        back.addEventListener("click", (ev) => { if (ev.target === back || ev.target === close) shut(); });
        document.addEventListener("keydown", onKey);
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
    function popupFor(card, isOwn) {
        const box = el("div", "cm-pop");
        if (isOwn) box.appendChild(el("div", "own", "This station"));
        box.appendChild(el("h3", "", String(card.name || "Unnamed station")));
        if (card.camera) box.appendChild(el("div", "meta", String(card.camera)));
        const stt = stationState(card, isOwn);
        box.appendChild(el("div", `cm-state ${stt}`, stt === "online" ? "Online" : stt === "idle" ? "Idle" : "Offline"));
        if (card.software) box.appendChild(el("div", "meta", String(card.software)));
        box.appendChild(el("div", "meta", seenText(card, isOwn).replace(/^./, (c) => c.toUpperCase())));
        const base = httpsUrl(card.url);
        let src = null;
        if (base) src = base.replace(/\/+$/, "") + "/api/community/thumb.jpg";
        else if (typeof card.thumb === "string" && THUMB_DATA.test(card.thumb)) src = card.thumb;
        if (src) {
            const img = document.createElement("img");
            img.alt = `Latest frame of ${String(card.name || "the station")}`;
            img.loading = "lazy";
            img.referrerPolicy = "no-referrer";
            img.addEventListener("error", () => img.remove());
            img.src = src;
            // Click to enlarge: the large preview when the station offers one, else the same small image scaled up
            const relays = base ? [] : relaySources(card);
            const larges = base && card.preview === true ? [base.replace(/\/+$/, "") + "/api/community/preview.jpg"]
                : relays.map((r) => r.url);
            const relayNote = relays.length ? `Preview relayed by ${relays[0].host}: this station has no public address.` : "";
            const zoom = el("button", "cm-zoom");
            zoom.type = "button";
            zoom.title = "Enlarge";
            zoom.setAttribute("aria-label", `Enlarge the preview of ${String(card.name || "the station")}`);
            zoom.appendChild(img);
            zoom.addEventListener("click", () => openLightbox(larges.concat([src]), String(card.name || "Unnamed station"),
                relayNote || (card.preview === true ? "" : "This station shares only a small preview.")));
            box.appendChild(zoom);
            if (relayNote) box.appendChild(el("div", "meta", relayNote));
        }
        if (base && card.open !== false) {
            const a = el("a", "open", "Open station");
            a.href = base;
            a.target = "_blank";
            a.rel = "noopener noreferrer";
            box.appendChild(a);
        } else if (base) {
            box.appendChild(el("div", "meta", "The owner does not link to this station's page."));
        } else if (!(card.relays || relayed[card.id])) {
            box.appendChild(el("div", "meta", "This station has no public address, so it cannot be opened and shares only a small preview."));
        }
        return box;
    }

    let popupOpen = false;   // do not rebuild the markers under an open popup
    map.on("popupopen", () => { popupOpen = true; });
    map.on("popupclose", () => { popupOpen = false; });

    function drawMarkers() {
        layer.clearLayers();
        stations = [];
        listEl.textContent = "";
        const accent = cssVar("--accent"), other = cssVar("--success");
        const valid = cards.filter((c) => c && finite(c.lat) && finite(c.lon) && Math.abs(c.lat) <= 90 && Math.abs(c.lon) <= 180);
        const rank = { online: 0, idle: 1, offline: 2 }, seenOf = (c) => { const t = Number(seenMap[c.id]) || Number(c.updated) || 0; return t > 1e12 ? t / 1000 : t; };
        valid.sort((a, b) => (a.id === ownId ? -1 : b.id === ownId ? 1 : 0) || rank[stationState(a, false)] - rank[stationState(b, false)] || seenOf(b) - seenOf(a) || String(a.name).localeCompare(String(b.name)));
        const pts = [];
        const counts = { online: 0, idle: 0, offline: 0 };
        for (const card of valid) {
            const isOwn = card.id === ownId;
            const st = stationState(card, isOwn);
            const marker = L.marker([card.lat, card.lon], {
                icon: L.divIcon({ className: "cm-mk", iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -12],
                    html: `<span class="cm-dot ${st}${isOwn ? " own" : ""}"></span>` }),
                title: `${String(card.name || "Station")} (${st})`, keyboard: true,
            }).addTo(layer);
            marker.bindPopup(() => popupFor(card, isOwn), { maxWidth: 340, keepInView: true, autoPanPaddingTopLeft: [16, 76], maxHeight: Math.max(220, Math.min(460, window.innerHeight - 200)), autoPanPaddingBottomRight: [16, 24] });
            pts.push([card.lat, card.lon]);
            const item = el("li");
            const btn = el("button");
            btn.type = "button";
            const nm = el("span", isOwn ? "nm you" : "nm");
            nm.appendChild(el("i", `cm-led ${st}`));
            nm.appendChild(document.createTextNode(String(card.name || "Unnamed station")));
            if (isOwn) nm.appendChild(el("em", "you-pill", "You"));
            btn.appendChild(nm);
            btn.appendChild(el("span", "sub", card.camera || seenText(card, isOwn)));
            const rt = el("span", "st-r");
            rt.appendChild(el("b", `cm-state-lbl ${st}`, st === "online" ? "Online" : st === "idle" ? "Idle" : "Offline"));
            rt.appendChild(el("small", "", isOwn ? "now" : agoOnly(card)));
            btn.appendChild(rt);
            counts[st] += 1;
            btn.addEventListener("click", () => {
                map.flyTo([card.lat, card.lon], Math.max(map.getZoom(), 6));
                marker.openPopup();
                if (window.matchMedia("(max-width: 768px)").matches) document.getElementById("cmap-wrap").scrollIntoView({ block: "start", behavior: "smooth" });
            });
            item.appendChild(btn);
            listEl.appendChild(item);
            stations.push({ card, marker });
        }
        countEl.textContent = valid.length ? `${valid.length} · ${counts.online} online` : "";
        const lg = document.getElementById("clegend");
        if (lg) {
            lg.hidden = !valid.length;
            lg.replaceChildren(...["online", "idle", "offline"].map((k) => {
                const sp = el("span", "lg-item");
                sp.appendChild(el("i", `cm-led ${k}`));
                sp.appendChild(document.createTextNode(`${k[0].toUpperCase()}${k.slice(1)} ${counts[k]}`));
                return sp;
            }));
        }
        noteEl.hidden = !valid.length;
        if (!fitted && pts.length) {
            fitted = true;
            if (pts.length === 1) map.setView(pts[0], 5); else map.fitBounds(pts, { padding: [40, 40], maxZoom: 6 });
        }
    }

    function showEmpty(kind, reason) {
        emptyEl.hidden = false;
        emptyEl.textContent = "";
        if (kind === "off") {
            emptyEl.appendChild(document.createTextNode("Sharing is off. Turn it on in "));
            const a = el("a", "", "Settings → Community");
            a.href = "/settings#community";
            emptyEl.appendChild(a);
            emptyEl.appendChild(document.createTextNode("."));
        } else {
            emptyEl.textContent = reason || "Community is not available.";
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
            if (!popupOpen) drawMarkers();
        } catch (err) {
            showEmpty("unavailable", "Could not load the stations. Retrying.");
        }
    }

    refresh();
    setInterval(refresh, 60000);
})();
