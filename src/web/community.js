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
        minZoom: 2, maxZoom: 9, preferCanvas: true, maxBounds: world.pad(0.1), maxBoundsViscosity: 0.8,
        attributionControl: false,
    }).setView([20, 10], 2);
    L.control.attribution({ prefix: false }).addAttribution("Coastlines: Natural Earth (public domain)").addTo(map);
    // The map box gets its final size after the shell lays out (and on rotation): tell Leaflet, or the map lands off-centre
    if (window.ResizeObserver) new ResizeObserver(() => map.invalidateSize()).observe(document.getElementById("cmap"));
    const grid = [];
    for (let lon = -180; lon <= 180; lon += 30) grid.push([[-85, lon], [85, lon]]);
    for (let lat = -60; lat <= 60; lat += 30) grid.push([[lat, -180], [lat, 180]]);
    L.polyline(grid, { color: tone("--text-muted", "#64748b"), weight: 1, opacity: 0.12, interactive: false }).addTo(map);
    fetch("/static/geo/land.json").then((r) => r.json()).then((land) => {
        L.geoJSON(land, {
            interactive: false,
            style: { color: tone("--text-muted", "#64748b"), weight: 0.8, opacity: 0.7, fillColor: tone("--border-color", "#1e293b"), fillOpacity: 1 },
        }).addTo(map).bringToBack();
    }).catch(() => { /* the stations still show on the plain grid */ });
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

    function popupFor(card, isOwn) {
        const box = el("div", "cm-pop");
        if (isOwn) box.appendChild(el("div", "own", "This station"));
        box.appendChild(el("h3", "", String(card.name || "Unnamed station")));
        if (card.camera) box.appendChild(el("div", "meta", String(card.camera)));
        if (card.software) box.appendChild(el("div", "meta", String(card.software)));
        box.appendChild(el("div", "meta", `Updated ${agoText(card.updated)}`));
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
            box.appendChild(img);
        }
        if (base) {
            const a = el("a", "open", "Open station");
            a.href = base;
            a.target = "_blank";
            a.rel = "noopener noreferrer";
            box.appendChild(a);
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
        valid.sort((a, b) => String(a.name).localeCompare(String(b.name)));
        const pts = [];
        for (const card of valid) {
            const isOwn = card.id === ownId;
            const marker = L.circleMarker([card.lat, card.lon], {
                radius: isOwn ? 10 : 7, weight: isOwn ? 3 : 2, color: isOwn ? cssVar("--text-primary") : accent,
                fillColor: isOwn ? accent : other, fillOpacity: 0.9,
            }).addTo(layer);
            marker.bindPopup(() => popupFor(card, isOwn));
            pts.push([card.lat, card.lon]);
            const item = el("li");
            const btn = el("button");
            btn.type = "button";
            btn.appendChild(el("span", isOwn ? "nm you" : "nm", `${String(card.name || "Unnamed station")}${isOwn ? " (this station)" : ""}`));
            btn.appendChild(el("span", "sub", [card.camera, `updated ${agoText(card.updated)}`].filter(Boolean).join(" · ")));
            btn.addEventListener("click", () => {
                map.flyTo([card.lat, card.lon], Math.max(map.getZoom(), 6));
                marker.openPopup();
                if (window.matchMedia("(max-width: 768px)").matches) document.getElementById("cmap-wrap").scrollIntoView({ block: "start", behavior: "smooth" });
            });
            item.appendChild(btn);
            listEl.appendChild(item);
            stations.push({ card, marker });
        }
        countEl.textContent = valid.length ? String(valid.length) : "";
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
            if (!popupOpen) drawMarkers();
        } catch (err) {
            showEmpty("unavailable", "Could not load the stations. Retrying.");
        }
    }

    refresh();
    setInterval(refresh, 60000);
})();
