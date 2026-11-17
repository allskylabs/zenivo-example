/* Zenivo dashboard, Live page UI (mobile-first redesign): Layers sheet, "Overhead now" segments, Now strip, 48 h weather,
 * accordions, compact cloud range, and tap-for-info on the live picture.
 * Classic script: reads the shared globals of the other src/web/app/*.js files (prefs, view, zoomAt, applyView, aircraftList,
 * satelliteList, sondeList, liveFrameT ...); load order is fixed in index.html (after 08c). */
(function () {
    "use strict";
    if (typeof currentPage === "undefined" || currentPage !== "tab-live") return;
    const $ = (id) => document.getElementById(id);
    const box = $("viewport-container");
    const esc = (v) => (typeof escapeHtml === "function" ? escapeHtml(v) : String(v));
    const num = (v, nd = 0) => Number(v).toLocaleString("en-US", { maximumFractionDigits: nd, minimumFractionDigits: nd }).replace(/,/g, " ");
    const dir = (az) => (typeof compass === "function" ? compass(az) : "");
    const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // ------------------------------------------------------------------
    // Layers sheet: switches drive the same hidden checkboxes the old chips used (their change handlers persist and subscribe)
    // ------------------------------------------------------------------
    const CHK_IDS = ["chk-sky-overlay", "chk-sky-labels", "chk-grid-overlay", "chk-eq-grid", "chk-aircraft-overlay", "chk-satellite-overlay", "chk-sonde-overlay"];
    const lay = $("layers-sheet"), layBd = $("layers-backdrop"), layBtn = $("btn-layers");
    function overheadCounts() {
        const ac = typeof aircraftList !== "undefined" ? aircraftList.filter((a) => a.above_horizon !== false && (a.distance_km == null || a.distance_km <= AIRCRAFT_MAX_KM)).length : 0;
        const sat = typeof satelliteList !== "undefined" ? satelliteList.filter((s) => s.above_horizon !== false).length : 0;
        const sd = typeof sondeList !== "undefined" ? sondeList.length : 0;
        return { ac, sat, sd };
    }
    function syncLayers() {
        const on = CHK_IDS.filter((id) => $(id) && $(id).checked).length;
        const cnt = $("layers-count");
        if (cnt) cnt.textContent = `${on} on`;
        if (layBtn) layBtn.classList.toggle("is-on", on > 0);
        if (!lay || lay.hidden) return;
        lay.querySelectorAll(".sw[data-chk]").forEach((b) => {
            const c = $(b.dataset.chk);
            b.setAttribute("aria-checked", c && c.checked ? "true" : "false");
        });
        const n = overheadCounts();
        const set = (id, t) => { const el = $(id); if (el) el.textContent = t; };
        set("lay-sub-aircraft", n.ac ? `${n.ac} overhead` : "none overhead right now");
        set("lay-sub-satellites", n.sat ? `${n.sat} above the horizon` : "none above the horizon");
        set("lay-sub-sondes", n.sd ? `${n.sd} nearby` : "none nearby");
    }
    function openLayers() {
        if (!lay) return;
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});   // the sheet lives outside the fullscreen picture
        lay.hidden = false; layBd.hidden = false;
        layBtn.setAttribute("aria-expanded", "true");
        syncLayers();
        const done = $("layers-done");
        if (done) done.focus({ preventScroll: true });
    }
    function closeLayers() {
        if (!lay || lay.hidden) return;
        lay.hidden = true; layBd.hidden = true;
        layBtn.setAttribute("aria-expanded", "false");
        layBtn.focus({ preventScroll: true });
    }
    if (lay && layBtn) {
        layBtn.addEventListener("click", openLayers);
        layBd.addEventListener("click", closeLayers);
        $("layers-done").addEventListener("click", closeLayers);
        document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeLayers(); });
        lay.addEventListener("click", (e) => {
            const sw = e.target.closest(".sw[data-chk]");
            if (!sw) return;
            const c = $(sw.dataset.chk);
            if (!c) return;
            c.checked = !c.checked;
            c.dispatchEvent(new Event("change", { bubbles: true }));
            syncLayers();
        });
        CHK_IDS.forEach((id) => { const c = $(id); if (c) c.addEventListener("change", syncLayers); });
        setInterval(syncLayers, 1500);   // live counts under the switches, and layers changed elsewhere (OBS sync)
        setTimeout(syncLayers, 400);
    }

    // ------------------------------------------------------------------
    // Overhead now: the three existing lists in one card, one shown at a time
    // ------------------------------------------------------------------
    const ovCard = $("overhead-card");
    const PANELS = [
        ["flights-card", "ov-tab-flights", "ov-n-flights", "flights-count", /(\d+)\s+heard/],
        ["sats-card", "ov-tab-sats", "ov-n-sats", "sats-count", /(\d+)\s+up/],
        ["sondes-card", "ov-tab-sondes", "ov-n-sondes", "sondes-count", /(\d+)\s+heard/],
    ];
    let ovSel = prefs && prefs.ovTab;
    let ovQueued = false;
    function ovSync() {
        ovQueued = false;
        if (!ovCard) return;
        const avail = [];
        PANELS.forEach(([pid, tid, nid, cid, re]) => {
            const p = $(pid), t = $(tid);
            const ok = !!p && !p.hidden;
            t.hidden = !ok;
            if (ok) avail.push(pid);
            const txt = ($(cid) && $(cid).textContent) || "";
            const m = re.exec(txt);
            const rows = p ? p.querySelectorAll(".flights-list > li.flight").length : 0;
            $(nid).textContent = m ? m[1] : /off/.test(txt) ? "off" : String(rows);
        });
        ovCard.hidden = avail.length === 0;
        if (!avail.includes(ovSel)) ovSel = avail[0];
        PANELS.forEach(([pid, tid]) => {
            const sel = pid === ovSel;
            $(pid).classList.toggle("ov-off", !sel);
            $(tid).classList.toggle("active", sel);
            $(tid).setAttribute("aria-selected", sel ? "true" : "false");
        });
        const live = $("ov-live");
        if (live) {
            const on = typeof overlayPushOn === "function" && overlayPushOn();
            live.textContent = on ? "live" : "updating";
            live.classList.toggle("off", !on);
        }
    }
    function ovQueue() { if (!ovQueued) { ovQueued = true; requestAnimationFrame(ovSync); } }
    if (ovCard) {
        ovCard.querySelectorAll(".ov-tab").forEach((t) => t.addEventListener("click", () => {
            ovSel = t.dataset.ov;
            if (typeof savePrefs === "function") savePrefs({ ovTab: ovSel });
            ovSync();
        }));
        if (window.MutationObserver) {
            const mo = new MutationObserver(ovQueue);
            PANELS.forEach(([pid, , , cid]) => {
                if ($(pid)) mo.observe($(pid), { attributes: true, attributeFilter: ["hidden"] });
                if ($(cid)) mo.observe($(cid), { childList: true, characterData: true, subtree: true });
                const ul = $(pid) && $(pid).querySelector(".flights-list");
                if (ul) mo.observe(ul, { childList: true });
            });
        }
        setInterval(ovSync, 3000);
        ovSync();
    }

    // ------------------------------------------------------------------
    // Now strip: cloud, sky (from the Sun's altitude tile), temperature (weather when on, else the exposure), events tonight
    // ------------------------------------------------------------------
    let wxTemp = null;
    function tickNow() {
        const set = (id, t) => { const el = $(id); if (el && el.textContent !== t) el.textContent = t; };
        const cm = /(-?[\d.]+)/.exec(($("telem-clouds") || {}).textContent || "");
        set("now-cloud", cm ? `${Math.round(parseFloat(cm[1]))}%` : "--");
        // Sun altitude: the server's value (satellite push, < 5 min old); else the local tile once the station position is known; else "--"
        const srv = window.ALLSKY_SUN_ALT;
        let sa = srv && Date.now() - srv.t < 300000 ? srv.v : NaN;
        if (!Number.isFinite(sa)) {
            const la = $("station-lat"), lo = $("station-lon");
            const known = la && lo && la.value !== "" && lo.value !== "" && (parseFloat(la.value) !== 0 || parseFloat(lo.value) !== 0);
            if (known) sa = parseFloat((($("telem-sun-alt") || {}).textContent || "").replace("\u2212", "-").replace("+", ""));
        }
        set("now-sky", Number.isFinite(sa) ? (sa > -0.83 ? "Day" : sa > -18 ? "Twilight" : "Night") : "--");
        if (wxTemp != null) { set("now-temp-l", "Temp"); set("now-temp", `${Math.round(wxTemp)}°C`); }
        else { set("now-temp-l", "Exposure"); set("now-temp", (($("telem-exp") || {}).textContent || "--").replace(/\s+/g, "") || "--"); }
        const em = /^\s*(\d+)/.exec(($("live-events-count") || {}).textContent || "");
        set("now-events", em ? em[1] : "--");
    }
    setInterval(tickNow, 1000);
    tickNow();

    // ------------------------------------------------------------------
    // Weather, next 48 h (accordion shown only when the weather source is on and answered)
    // ------------------------------------------------------------------
    const wxAcc = $("weather-acc");
    async function loadLiveWeather() {
        if (!wxAcc) return;
        try {
            const r = await fetch("/api/weather");
            if (!r.ok) return;
            const d = await r.json();
            if (!d || !d.enabled || !d.available) { wxAcc.hidden = true; wxTemp = null; return; }
            const c = d.current || {};
            wxTemp = Number.isFinite(c.temp_c) ? c.temp_c : null;
            wxAcc.hidden = false;
            const f = (v, u, nd = 0) => (v == null ? "–" : Number(v).toFixed(nd) + u);
            $("lwx-sum").textContent = `${c.summary || ""} ${f(c.temp_c, "°C")}`.trim();
            const o = d.outlook || {};
            $("lwx-now").textContent = `${c.summary || "Now"} · cloud ${f(c.cloud_pct, "%")} · humidity ${f(c.humidity_pct, "%")} · wind ${f(c.wind_ms, " m/s", 1)} · `
                + `next 12 h: ${o.clear_hours_next_12 != null ? o.clear_hours_next_12 : "–"} clear hours${o.rain_expected_next_12 ? ", rain likely" : ""}`;
            const hrs = $("lwx-hours");
            hrs.textContent = "";
            (d.hourly || []).slice(0, 48).forEach((h) => {
                const cell = document.createElement("div");
                cell.className = "lwx-h";
                cell.title = `${h.summary || ""} · rain ${f(h.precip_prob_pct, "%")}`;
                const hh = typeof fmtLocalIso === "function" ? fmtLocalIso(new Date(h.t * 1000).toISOString(), true).slice(0, 2) : String(new Date(h.t * 1000).getHours()).padStart(2, "0");
                cell.innerHTML = `<span>${esc(hh)}h</span><b>${f(h.temp_c, "°")}</b><i style="--c:${Math.max(0, Math.min(100, h.cloud_pct || 0))}%"></i><span>${f(h.cloud_pct, "%")}</span>`;
                hrs.appendChild(cell);
            });
        } catch (e) { /* optional feature */ }
    }
    loadLiveWeather();
    setInterval(loadLiveWeather, 600000);

    // ------------------------------------------------------------------
    // Accordions: closed on a phone (the histogram is not drawn while closed), open on a wide screen
    // ------------------------------------------------------------------
    const accs = [...document.querySelectorAll("details.acc")];
    const mq = window.matchMedia ? window.matchMedia("(max-width: 768px)") : { matches: false, addEventListener() {} };
    function accDefaults(initial) {
        accs.forEach((a) => {
            if (!mq.matches) a.open = true;
            else if (initial) a.open = false;
        });
    }
    accDefaults(true);
    if (mq.addEventListener) mq.addEventListener("change", () => accDefaults(false));
    const histAcc = $("histogram-card");
    if (histAcc) histAcc.addEventListener("toggle", () => {
        if (histAcc.open && window.zenHistPending && typeof drawHistogram === "function") drawHistogram(window.zenHistPending);
    });

    // ------------------------------------------------------------------
    // Cloud cover range: a compact "24h v" control on a phone (the buttons keep their ids/handlers in 07-cloud.js)
    // ------------------------------------------------------------------
    const ranges = document.querySelector(".cloud-ranges");
    if (ranges) {
        const tgl = document.createElement("button");
        tgl.type = "button"; tgl.className = "cr-toggle"; tgl.id = "cloud-range-toggle";
        tgl.setAttribute("aria-haspopup", "true"); tgl.setAttribute("aria-expanded", "false");
        ranges.insertBefore(tgl, ranges.firstChild);
        const label = () => {
            const act = ranges.querySelector(".btn-range.active");
            tgl.textContent = `${act ? act.textContent.trim() : "24h"} ▾`;
        };
        const setOpen = (o) => { ranges.classList.toggle("open", o); tgl.setAttribute("aria-expanded", o ? "true" : "false"); };
        tgl.addEventListener("click", (e) => { e.stopPropagation(); setOpen(!ranges.classList.contains("open")); });
        ranges.querySelectorAll(".btn-range").forEach((b) => b.addEventListener("click", () => { setOpen(false); setTimeout(label, 0); }));
        document.addEventListener("click", (e) => { if (!ranges.contains(e.target)) setOpen(false); });
        document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
        if (window.MutationObserver) new MutationObserver(label).observe(ranges, { attributes: true, attributeFilter: ["class"], subtree: true });
        label();
    }

    // ------------------------------------------------------------------
    // Tap for info: pick the nearest drawn/known object under the finger and show it in the card under the picture
    // ------------------------------------------------------------------
    const ring = $("sel-ring"), card = $("obj-card");
    const PICK_PX = 24;
    const KIND = {   // chip text, tone (a CSS variable name), priority (lower wins), where "more" goes
        aircraft: ["Aircraft", "accent", 0], satellite: ["Satellite", "violet", 1], sonde: ["Radiosonde", "success", 2],
        planet: ["Planet", "warning", 3], star: ["Star", "accent", 3], dso: ["Deep sky", "warning", 3],
        sun: ["Sun", "warning", 3], moon: ["Moon", "text-secondary", 3], con: ["Constellation", "accent", 4],
    };
    let sel = null;   // { kind, key, last }

    function geom() {
        if (!box) return null;
        const w = box.clientWidth, h = box.clientHeight;
        if (w <= 10 || h <= 10) return null;
        const st = state.station || {};
        const sensor = st.camera_sensor || {};
        const sW = Number(sensor.width_px || 2592), sH = Number(sensor.height_px || 1944);
        let rW = w, rH = h, oX = 0, oY = 0;
        if (w / h > sW / sH) { rW = h * (sW / sH); oX = (w - rW) / 2; } else { rH = w / (sW / sH); oY = (h - rH) / 2; }
        const tf = st.image_transform || {};
        const rot180 = Number(tf.rotate_deg || 0) % 360 === 180;
        return { w, h, oX, oY, rW, rH, mX: !!tf.flip_horizontal !== rot180, mY: !!tf.flip_vertical !== rot180 };
    }
    const clampLag = (v) => Math.max(-5, Math.min(20, v));
    // Where an overlay row sits in stage px, the same way drawAircraft() places its mark (null when outside the picture)
    function rowPos(g, row, lag) {
        if (!row || !row.frame_xy) return null;
        let fx = row.frame_xy[0], fy = row.frame_xy[1];
        if (lag && row.frame_v) { fx += row.frame_v[0] * lag; fy += row.frame_v[1] * lag; }
        if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return null;
        if (g.mX) fx = 1 - fx;
        if (g.mY) fy = 1 - fy;
        return { x: g.oX + fx * g.rW, y: g.oY + fy * g.rH };
    }
    // Everything that can be picked right now. Overlay lists are fed whether or not their layer is drawn; sky objects are the ones drawReticle() drew.
    function candidates() {
        const g = geom();
        if (!g) return [];
        const out = [];
        if (typeof aircraftList !== "undefined") {
            const shift = aircraftT && liveFrameT != null ? liveFrameT - aircraftT : (Date.now() - aircraftFetchedAt) / 1000;
            aircraftList.forEach((a) => {
                if (a.distance_km != null && a.distance_km > AIRCRAFT_MAX_KM) return;
                const p = rowPos(g, a, clampLag(shift));
                if (p) out.push({ kind: "aircraft", key: String(a.icao), d: a, ...p });
            });
        }
        if (typeof satelliteList !== "undefined") {
            satelliteList.forEach((s) => {
                if (!s.in_view) return;
                const p = rowPos(g, s, 0);
                if (p) out.push({ kind: "satellite", key: String(s.norad), d: s, ...p });
            });
        }
        if (typeof sondeList !== "undefined") {
            const shift = sondeT && liveFrameT != null ? liveFrameT - sondeT : (Date.now() - sondeFetchedAt) / 1000;
            sondeList.forEach((s) => {
                if (!s.in_view) return;
                const p = rowPos(g, s, clampLag(shift));
                if (p) out.push({ kind: "sonde", key: String(s.serial), d: s, ...p });
            });
        }
        (window.zenSkyHits || []).forEach((h) => out.push({ kind: h.kind, key: h.kind + ":" + (h.id || h.name), d: h, x: h.x, y: h.y, hw: h.hw || 0, hh: h.hh || 0 }));
        return out;
    }
    // Stage px -> container px (the picture is the stage scaled by view.zoom around the view centre)
    function toScreen(x, y) {
        const w = box.clientWidth, h = box.clientHeight;
        return { x: w * (0.5 - view.cx * view.zoom) + x * view.zoom, y: h * (0.5 - view.cy * view.zoom) + y * view.zoom };
    }
    function pick(px, py) {
        let best = null;
        candidates().forEach((c) => {
            const s = toScreen(c.x, c.y);
            const dx = Math.max(Math.abs(s.x - px) - (c.hw || 0) * view.zoom, 0), dy = Math.max(Math.abs(s.y - py) - (c.hh || 0) * view.zoom, 0);
            const dist = Math.hypot(dx, dy);
            if (dist > PICK_PX) return;
            const pr = KIND[c.kind] ? KIND[c.kind][2] : 9;
            if (!best || pr < best.pr || (pr === best.pr && dist < best.dist)) best = { c, pr, dist };
        });
        return best && best.c;
    }

    const fmtAz = (az) => `${Math.round(az)}° ${dir(az)}`;
    const fmtRa = (raDeg) => { const h = ((raDeg % 360) + 360) % 360 / 15; const hh = Math.floor(h); return `${String(hh).padStart(2, "0")}h${String(Math.floor((h - hh) * 60)).padStart(2, "0")}m`; };
    const fmtDec = (d) => `${d < 0 ? "−" : "+"}${Math.abs(d).toFixed(1)}°`;
    const conName = (id) => { const k = ((window.ALLSKY_CATALOG || {}).constellations || []).find((x) => x.id === id); return k ? k.name : ""; };
    const magTxt = (m) => (m == null ? "" : `mag ${m < 0 ? "−" : ""}${Math.abs(m).toFixed(1)}`);

    // -> { title, sub, stats: [[label, value]], link: [text, href, newTab] }
    function describe(c) {
        const d = c.d;
        const aa = (alt, az) => [["Altitude", `${Math.round(alt)}°`], ["Azimuth", fmtAz(az)]];
        switch (c.kind) {
            case "aircraft": {
                const sub = [d.callsign ? `ICAO ${String(d.icao).toUpperCase()}` : "", d.heading_deg != null ? `heading ${Math.round(d.heading_deg)}°` : "",
                    d.climb_m_min != null && Math.abs(d.climb_m_min) > 60 ? `${d.climb_m_min > 0 ? "climbing" : "descending"} ${num(Math.abs(d.climb_m_min))} m/min` : ""].filter(Boolean).join(" · ");
                return { title: d.callsign || String(d.icao).toUpperCase(), sub: sub || "ADS-B", link: ["3D map", "/flights3d"],
                    stats: [["Altitude", d.altitude_m != null ? `${num(d.altitude_m)} m` : "--"], ["Speed", d.speed_kmh != null ? `${num(d.speed_kmh)} km/h` : "--"],
                        ["Distance", d.distance_km != null ? `${num(d.distance_km, 1)} km` : "--"], ["Alt / Az", `${Math.round(d.alt_deg)}° / ${Math.round(d.az_deg)}°`]] };
            }
            case "satellite":
                return { title: d.name, sub: `NORAD ${d.norad} · ${d.visible ? "sunlit, visible" : "above the camera, not visible now"}`, link: ["3D map", "/satellites3d"],
                    stats: [["Height", `${num(d.height_km)} km`], ...aa(d.alt_deg, d.az_deg), ["Visible", d.visible ? "yes" : "no"]] };
            case "sonde":
                return { title: d.serial, sub: [d.type || "Radiosonde", d.climb_m_s != null ? `${d.climb_m_s >= 0 ? "rising" : "falling"} ${Math.abs(d.climb_m_s).toFixed(1)} m/s` : ""].filter(Boolean).join(" · "),
                    link: ["SondeHub", `https://sondehub.org/${encodeURIComponent(d.serial)}`, true],
                    stats: [["Altitude", d.altitude_m != null ? `${num(d.altitude_m)} m` : "--"], ["Distance", d.distance_km != null ? `${num(d.distance_km, 1)} km` : "--"], ...aa(d.alt_deg, d.az_deg)] };
            case "planet":
                return { title: d.name, sub: magTxt(d.mag), link: ["Open in Sky map", "/sky"], stats: [...aa(d.alt, d.az), ["RA", fmtRa(d.ra)], ["Dec", fmtDec(d.dec)]] };
            case "star":
                return { title: d.name, sub: [d.bayer, d.con ? `in ${conName(d.con) || d.con}` : "", magTxt(d.mag)].filter(Boolean).join(" · "), link: ["Open in Sky map", "/sky"],
                    stats: [...aa(d.alt, d.az), ["RA", fmtRa(d.ra)], ["Dec", fmtDec(d.dec)]] };
            case "dso":
                return { title: `${d.id} ${d.name}`, sub: ["Messier object", magTxt(d.mag)].filter(Boolean).join(" · "), link: ["Open in Sky map", "/sky"],
                    stats: [...aa(d.alt, d.az), ["RA", fmtRa(d.ra)], ["Dec", fmtDec(d.dec)]] };
            case "sun":
            case "moon":
                return { title: d.name, sub: d.illum != null ? `${Math.round(d.illum * 100)}% illuminated` : "", link: ["Open in Sky map", "/sky"], stats: aa(d.alt, d.az) };
            default: {
                const k = ((window.ALLSKY_CATALOG || {}).constellations || []).find((x) => x.id === d.id);
                return { title: d.name, sub: d.common || "Constellation", link: ["Open in Sky map", "/sky"],
                    stats: [["Altitude", `${Math.round(d.alt)}°`], ...(k ? [["RA", fmtRa(k.ra)], ["Dec", fmtDec(k.dec)]] : [])] };
            }
        }
    }

    function renderCard(c) {
        const k = KIND[c.kind] || [c.kind, "accent"];
        const info = describe(c);
        const stats = info.stats.map(([l, v]) => `<div><span>${esc(l)}</span><b>${esc(v)}</b></div>`).join("");
        const link = info.link ? `<a class="oc-btn oc-primary" href="${esc(info.link[1])}"${info.link[2] ? ' target="_blank" rel="noopener"' : ""}>${esc(info.link[0])}</a>` : "";
        const html = `<div class="oc-head"><span class="oc-chip" style="--tone:var(--${k[1]})">${esc(k[0])}</span>
            <button type="button" class="oc-close" id="obj-close" aria-label="Close">&times;</button></div>
            <h3 class="oc-title">${esc(info.title)}</h3><p class="oc-sub">${esc(info.sub)}</p>
            <div class="oc-stats">${stats}</div>
            <div class="oc-actions">${link}<button type="button" class="oc-btn" id="obj-zoom">Zoom here</button></div>`;
        if (card.dataset.html !== html) { card.innerHTML = html; card.dataset.html = html; }
        card.style.setProperty("--tone", `var(--${k[1]})`);
        card.hidden = false;
    }
    function placeRing(c) {
        if (!c) { ring.hidden = true; return; }
        const s = toScreen(c.x, c.y);
        const inside = s.x >= 0 && s.y >= 0 && s.x <= box.clientWidth && s.y <= box.clientHeight;
        ring.hidden = !inside;
        if (inside) { ring.style.left = `${s.x}px`; ring.style.top = `${s.y}px`; }
    }
    function select(c) {
        sel = { kind: c.kind, key: c.key, last: c };
        renderCard(c);
        placeRing(c);
    }
    function closeInfo() {
        sel = null;
        if (card) { card.hidden = true; card.innerHTML = ""; card.dataset.html = ""; }
        if (ring) ring.hidden = true;
    }
    // Follow the selected object: a plane moves, the view pans and zooms
    function refreshSel() {
        if (!sel) return;
        const c = candidates().find((x) => x.kind === sel.kind && x.key === sel.key);
        if (c) { sel.last = c; renderCard(c); placeRing(c); }
        else { placeRing(null); }   // left the picture: the card keeps its last values until closed
    }
    window.zenTapSelect = (px, py) => {
        const c = pick(px, py);
        if (c) select(c); else closeInfo();
    };
    window.zenCloseInfo = closeInfo;
    window.zenAfterView = () => { if (sel) { placeRing(sel.last); } };
    if (card) {
        card.addEventListener("click", (e) => {
            if (e.target.closest("#obj-close")) { closeInfo(); return; }
            if (e.target.closest("#obj-zoom") && sel && sel.last) {
                const c = sel.last;
                const w = box.clientWidth, h = box.clientHeight;
                view.zoom = Math.min(ZOOM_MAX, Math.max(3, view.zoom * 2));
                view.cx = c.x / w; view.cy = c.y / h;
                applyView();
                if (!reduceMotion) box.scrollIntoView({ block: "nearest", behavior: "smooth" });
            }
        });
        setInterval(refreshSel, 500);
        document.addEventListener("keydown", (e) => { if (e.key === "Escape" && sel && (!lay || lay.hidden)) closeInfo(); });
    }
})();
