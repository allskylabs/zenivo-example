/* Zenivo dashboard (part of the former app.js): Event detail viewer, Archive events list and the Live page feeds (events, flights, radiosondes).
 * Classic script: continues 08-gallery-adsb.js; top-level declarations are shared with the other src/web/app/*.js files; load order is fixed in index.html. */

// ---------- helpers shared by the Archive tiles, the detail viewer and Live (items may be brief records or full records) ----------
const EVT_ICON = { plane: "plane", meteor: "sparkles", satellite: "satellite", clouds: "cloud", flash: "zap" };
const EVT_NAME = { plane: "Plane", meteor: "Meteor", satellite: "Satellite", clouds: "Clouds", flash: "Flash" };
const evCallsign = (ev) => (ev.flight ? (typeof ev.flight === "string" ? ev.flight : ev.flight.callsign || ev.flight.icao || "") : "");
const evSat = (ev) => (ev.satellite ? (typeof ev.satellite === "string" ? ev.satellite : ev.satellite.name || "") : "");
const evIso = (ev) => ev.timestamp || new Date(Number(ev.start_ts) * 1000).toISOString();
const evWhen = (ev) => new Date(evIso(ev));
const evQuality = (ev) => (ev.confidence == null ? "" : ev.confidence >= 0.8 ? "hi" : ev.confidence >= 0.5 ? "mid" : "lo");
const evTypeColor = (t) => `var(--c-ev-${t}, ${EVENT_COLORS[t] || "var(--c-accent)"})`;

// ---------- detail viewer: one element, a modal (phone, Live) or a dock beside the list (Archive, desktop) ----------
const evScrim = document.createElement("div");
evScrim.className = "arc-ev-scrim";
evScrim.hidden = true;
const eventViewer = document.createElement("section");
eventViewer.className = "arc-ev is-modal";
eventViewer.hidden = true;
eventViewer.setAttribute("role", "dialog");
eventViewer.setAttribute("aria-label", "Event detail");
const evBtn = (act, icon, label, extra = "") => `<button type="button" class="ds-btn ds-btn--ghost ds-btn--icon ${extra}" data-ev="${act}" aria-label="${label}" title="${label}">${arcIcon(icon)}</button>`;
eventViewer.innerHTML = `
    <header class="arc-ev__bar">
        ${evBtn("close", "x", "Close (Esc)")}
        <div class="arc-ev__ttl"><span class="ev-ico"></span><span class="ev-ttl"></span></div>
        <span class="arc-ev__cnt"></span>
        ${evBtn("info", "info", "Why it was detected")}
        <button type="button" class="ds-btn ds-btn--ghost ds-btn--icon" data-ev="keys" aria-label="Keyboard: left and right arrows change event, Esc closes" title="← newer · → older · Esc close" tabindex="-1">${arcIcon("keyboard")}</button>
    </header>
    <div class="arc-ev__stage">
        <div class="ev-zoom"><div class="arc-ev__pan"><img class="arc-ev__img" alt="" draggable="false" /><svg class="arc-ev__ovr" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"></svg></div></div>
        ${evBtn("prev", "chevron-left", "Newer event (←)", "arc-ev__nav arc-ev__nav--prev")}
        ${evBtn("next", "chevron-right", "Older event (→)", "arc-ev__nav arc-ev__nav--next")}
    </div>
    <div class="arc-ev__tabs" role="group" aria-label="Picture">
        <button type="button" class="ds-chip" data-ev="crop" aria-pressed="true" aria-label="Close-up" title="Close-up">${arcIcon("crosshair")}</button>
        <button type="button" class="ds-chip" data-ev="sky" aria-pressed="false" aria-label="Whole sky" title="Whole sky">${arcIcon("moon-star")}</button>
    </div>
    <div class="arc-ev__facts"></div>
    <div class="arc-ev__flight" hidden></div>
    <div class="arc-ev__acts">
        <button type="button" class="ds-btn ds-btn--icon" data-admin data-ev="true" data-label="true" aria-pressed="false" aria-label="Real event" title="Real event">${arcIcon("thumbs-up")}</button>
        <button type="button" class="ds-btn ds-btn--icon" data-admin data-ev="false" data-label="false" aria-pressed="false" aria-label="Not real" title="Not real">${arcIcon("thumbs-down")}</button>
        <button type="button" class="ds-btn ds-btn--icon" data-admin data-ev="was" aria-expanded="false" aria-label="It was something else" title="It was…">${arcIcon("pencil")}</button>
        <a class="ds-btn ds-btn--icon" data-ev="dl" download rel="noopener" aria-label="Download picture" title="Download">${arcIcon("download")}</a>
        <button type="button" class="ds-btn ds-btn--icon" data-ev="share" aria-label="Copy link" title="Copy link">${arcIcon("share-2")}</button>
        <span class="arc-ev__sp"></span>
        <button type="button" class="ds-btn ds-btn--icon ds-btn--danger" data-admin data-ev="del" aria-label="Delete event" title="Delete (tap twice)">${arcIcon("trash-2")}</button>
    </div>
    <div class="arc-ev__acts arc-ev__was" hidden></div>
    <div class="arc-ev__card" hidden></div>`;
document.body.append(evScrim, eventViewer);
const evQ = (sel) => eventViewer.querySelector(sel);
const evImg = evQ(".arc-ev__img"), evOvr = evQ(".arc-ev__ovr");
let evList = [], evIndex = 0, evPic = "crop", evFromArchive = false;
const evZoomApi = window.ZenivoZoomPan ? ZenivoZoomPan.attach(evQ(".ev-zoom"), evQ(".arc-ev__pan"), { pill: "hover", active: () => !eventViewer.hidden }) : null;
const evZoomReset = () => { if (evZoomApi) evZoomApi.reset(); };

// a brief record has no timestamp / flight object / satellite object: load the full record once
const evFull = new Map();
async function evEnsureFull(ev) {
    if (!ev.event_id || ev.timestamp || evFull.has(ev.event_id)) { if (evFull.has(ev.event_id)) Object.assign(ev, evFull.get(ev.event_id)); return; }
    try {
        const r = await fetch(`/api/events/${encodeURIComponent(ev.event_id)}`);
        if (r.ok) { const full = await r.json(); evFull.set(ev.event_id, full); Object.assign(ev, full); }
    } catch (e) { /* the brief data still renders */ }
}

// line [x1,y1,x2,y2] is normalised on the whole-sky frame: draw it and a padded box only on the sky picture
function evOverlay(ev) {
    evOvr.innerHTML = "";
    const l = ev.line;
    if (evPic !== "sky" || !Array.isArray(l) || l.length < 4) return;
    const [x1, y1, x2, y2] = l.map((v) => v * 100), pad = 3;
    const bx = Math.max(0, Math.min(x1, x2) - pad), by = Math.max(0, Math.min(y1, y2) - pad);
    const bw = Math.min(100, Math.max(x1, x2) + pad) - bx, bh = Math.min(100, Math.max(y1, y2) + pad) - by;
    const col = "var(--c-accent)";
    evOvr.innerHTML = `<rect x="${bx}" y="${by}" width="${bw}" height="${bh}" fill="none" stroke="${col}" stroke-width="1.5"/>`
        + (ev.moving_dot ? `<circle cx="${x1}" cy="${y1}" r="0.8" fill="${col}"/><circle cx="${x2}" cy="${y2}" r="0.8" fill="${col}"/>` : "")
        + `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${col}" stroke-width="1.5" stroke-linecap="round"/>`;
}

function evSetPic(which) {
    const ev = evList[evIndex];
    if (!ev) return;
    const crop = ev.sky_url && ev.thumbnail_url !== ev.sky_url ? ev.thumbnail_url : null;
    if (which === "crop" && !crop) which = "sky";
    if (which === "sky" && !ev.sky_url) which = "crop";
    evPic = which;
    const url = which === "sky" ? ev.sky_url : (crop || ev.thumbnail_url);
    evZoomReset();
    evImg.onload = () => { if (evImg.naturalWidth) eventViewer.style.setProperty("--ev-ar", `${evImg.naturalWidth} / ${evImg.naturalHeight}`); };
    if (url) evImg.src = url; else evImg.removeAttribute("src");
    evImg.alt = which === "sky" ? "Whole sky" : "Close-up";
    eventViewer.querySelectorAll(".arc-ev__tabs .ds-chip").forEach((b) => { b.setAttribute("aria-pressed", String(b.dataset.ev === which)); });
    evQ('[data-ev="crop"]').hidden = !crop;
    evQ('[data-ev="sky"]').hidden = !ev.sky_url;
    evQ(".arc-ev__tabs").hidden = !crop || !ev.sky_url;
    const dl = evQ('[data-ev="dl"]');
    dl.hidden = !url; if (url) dl.href = url;
    evOverlay(ev);
}

function showEvent(i) {
    if (!evList.length) return;
    evIndex = Math.max(0, Math.min(evList.length - 1, i));
    const ev = evList[evIndex];
    evQ(".ev-ico").innerHTML = arcIcon(EVT_ICON[ev.type] || "zap");
    evQ(".ev-ico").style.color = evTypeColor(ev.type);
    const name = evCallsign(ev) || evSat(ev) || EVT_NAME[ev.type] || ev.type;
    const [d = "", t = ""] = fmtLocalIso(evIso(ev)).split(" ");
    evQ(".ev-ttl").textContent = `${name} · ${t}`;
    evQ(".ev-ttl").title = `${d} ${t} ${tzLabel} · ${timeAgo(evWhen(ev))}`;
    evQ(".arc-ev__cnt").textContent = `${evIndex + 1}/${evList.length}`;
    evQ('[data-ev="prev"]').disabled = evIndex === 0;
    evQ('[data-ev="next"]').disabled = evIndex === evList.length - 1;
    // facts as icon chips (value only; the label is the tooltip)
    const chips = [`<span class="ds-chip" title="${escapeHtml(`${d} ${tzLabel}`)}">${arcIcon("clock")}${escapeHtml(t)}</span>`];
    const q = evQuality(ev);
    if (q) chips.push(`<span class="ds-chip arc-q--${q}" title="Confidence">${arcIcon("shield")}${Math.round(ev.confidence * 100)}%</span>`);
    const dur = eventDuration(ev);
    if (dur) chips.push(`<span class="ds-chip" title="Duration">${arcIcon("activity")}${escapeHtml(fmtSeconds(dur))}</span>`);
    if (ev.frames > 1) chips.push(`<span class="ds-chip" title="Frames">${arcIcon("layers")}${ev.frames}</span>`);
    if (ev.length_px) chips.push(`<span class="ds-chip" title="${ev.moving_dot ? "Path length" : "Streak length"}">${arcIcon("maximize")}${Math.round(ev.length_px)} px</span>`);
    evQ(".arc-ev__facts").innerHTML = chips.join("");
    const fl = evQ(".arc-ev__flight"), fo = typeof ev.flight === "object" && ev.flight ? ev : null;
    const sat = evSat(ev);
    fl.hidden = !(fo || sat || evCallsign(ev));
    fl.innerHTML = fo ? flightHtml(ev) : evCallsign(ev) ? `<span class="flight-tag">${arcIcon("plane")} ${escapeHtml(evCallsign(ev))}</span>` : sat ? `<span class="flight-tag">${arcIcon("satellite")} ${escapeHtml(sat)}</span>` : "";
    if (fo && sat) fl.innerHTML += ` <span class="flight-tag">${arcIcon("satellite")} ${escapeHtml(sat)}</span>`;
    // detection reasons behind the info icon
    const card = evQ(".arc-ev__card");
    const facts = eventFacts(ev).map(([k, v, isHtml]) => `<dt>${escapeHtml(k)}</dt><dd>${isHtml ? v : escapeHtml(v)}</dd>`).join("");
    const fx = fo ? flightFacts(ev).map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("") : "";
    card.innerHTML = `<ul><li>${escapeHtml(eventSummary(ev))}</li></ul><dl>${facts}${fx}</dl>`;
    card.hidden = true;
    evQ('[data-ev="info"]').setAttribute("aria-expanded", "false");
    evQ(".arc-ev__was").hidden = true;
    evQ('[data-ev="was"]').setAttribute("aria-expanded", "false");
    evDelDisarm();
    evLabelShow(ev.owner_label);
    evSetPic(evPic);
    if (evFromArchive && typeof arcMarkSeen === "function") arcMarkSeen(ev.event_id);
    // fetch the full record in the background when only the brief one is here
    if (!ev.timestamp && ev.event_id) evEnsureFull(ev).then(() => { if (evList[evIndex] === ev && ev.timestamp) showEvent(evIndex); });
}

// Owner verdict -> POST /api/events/<id>/label; tapping the active one again withdraws it
function evLabelShow(label) {
    eventViewer.querySelectorAll("[data-label]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.label === label)));
}
async function evLabelPost(body, btn) {
    const ev = evList[evIndex];
    if (!ev || !ev.event_id) return false;
    try {
        const res = await fetch(`/api/events/${encodeURIComponent(ev.event_id)}/label`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        if (!res.ok) throw new Error(res.status === 401 || res.status === 403 ? "Sign in as admin" : `HTTP ${res.status}`);
        ev.owner_label = body.label === "clear" ? undefined : body.label;
        evLabelShow(ev.owner_label);
        return true;
    } catch (err) {
        if (window.DS && DS.toast) DS.toast(`Not saved: ${err.message}`, "error"); else if (btn) btn.title = `Not saved: ${err.message}`;
        return false;
    }
}
function evLabel(btn) { return evLabelPost({ label: btn.getAttribute("aria-pressed") === "true" ? "clear" : btn.dataset.label }, btn); }
// "It was X": a not-real verdict that also records what it actually was
function evToggleWas() {
    const box = evQ(".arc-ev__was"), b = evQ('[data-ev="was"]');
    box.hidden = !box.hidden;
    b.setAttribute("aria-expanded", String(!box.hidden));
    if (!box.hidden) box.innerHTML = Object.keys(EVT_ICON).map((t) => `<button type="button" class="ds-chip" data-was="${t}" aria-label="It was ${EVT_NAME[t]}" title="It was ${EVT_NAME[t]}">${arcIcon(EVT_ICON[t])}</button>`).join("")
        + `<button type="button" class="ds-chip" data-was="other" aria-label="It was something else" title="Something else">${arcIcon("circle-help")}</button>`;
}
async function evWas(kind) {
    if (await evLabelPost({ label: "false", actual_type: kind }, null)) { evQ(".arc-ev__was").hidden = true; evQ('[data-ev="was"]').setAttribute("aria-expanded", "false"); if (window.DS && DS.toast) DS.toast("Saved", "ok"); }
}

// Deep link to one event: /gallery?event=<id> (the page opens it in this viewer)
const evLink = (ev) => `${location.origin}/gallery?event=${encodeURIComponent(ev.event_id)}`;
let evDelTimer = null;
function evDelDisarm() {
    clearTimeout(evDelTimer);
    const b = evQ('[data-ev="del"]');
    b.dataset.armed = "";
    b.title = "Delete (tap twice)";
}
async function evCopyLink() {
    const ev = evList[evIndex];
    if (!ev || !ev.event_id) return;
    const url = evLink(ev);
    try {
        await navigator.clipboard.writeText(url);
        if (window.DS && DS.toast) DS.toast("Link copied", "ok");
    } catch (e) {
        window.prompt("Copy this link", url);   // clipboard blocked (plain http): let the person copy it by hand
    }
}
async function evDelete() {
    const ev = evList[evIndex], b = evQ('[data-ev="del"]');
    if (!ev || !ev.event_id) return;
    if (!b.dataset.armed) {   // first tap asks, second (within 4 s) deletes
        b.dataset.armed = "true";
        b.title = "Tap again to delete";
        if (window.DS && DS.toast) DS.toast("Tap delete again to confirm", "warn");
        evDelTimer = setTimeout(evDelDisarm, 4000);
        return;
    }
    evDelDisarm();
    b.disabled = true;
    try {
        const res = await fetch(`/api/events/${encodeURIComponent(ev.event_id)}`, { method: "DELETE" });
        if (!res.ok) throw new Error(res.status === 401 || res.status === 403 ? "Sign in as admin" : `HTTP ${res.status}`);
        [evList, tableEvents, liveEventsData, arcEvents].forEach((arr) => {
            const k = arr.findIndex((x) => x.event_id === ev.event_id);
            if (k >= 0) arr.splice(k, 1);
        });
        if (evList.length) showEvent(Math.min(evIndex, evList.length - 1)); else closeEventViewer();
        if (document.getElementById("arc-list")) arcRender();
        if (typeof loadLiveEvents === "function" && document.getElementById("live-events-list")) loadLiveEvents();
    } catch (err) {
        if (window.DS && DS.toast) DS.toast(`Not deleted: ${err.message}`, "error");
    } finally { b.disabled = false; }
}

function openEventViewer(list, i, fromArchive) {
    evList = list || [];
    if (!evList.length) return;
    evFromArchive = !!fromArchive;
    const dock = document.getElementById("arc-dock");
    const asDock = evFromArchive && dock && window.matchMedia("(min-width: 1024px)").matches;
    eventViewer.classList.toggle("is-dock", !!asDock);
    eventViewer.classList.toggle("is-modal", !asDock);
    if (asDock) { dock.appendChild(eventViewer); dock.parentElement.classList.add("has-dock"); }
    else { document.body.appendChild(eventViewer); }
    evScrim.hidden = !!asDock;
    eventViewer.hidden = false;
    if (!asDock) document.body.classList.add("no-scroll");
    evPic = "crop";
    showEvent(i);
    evQ('[data-ev="close"]').focus({ preventScroll: true });
    arcMarkCurrent();
}

function closeEventViewer() {
    if (eventViewer.hidden) return;
    const u = new URL(location.href);
    if (u.searchParams.has("event")) { u.searchParams.delete("event"); history.replaceState(null, "", u.pathname + u.search + u.hash); }
    evZoomReset();
    eventViewer.hidden = true;
    evScrim.hidden = true;
    const split = document.querySelector(".arc-split");
    if (split) split.classList.remove("has-dock");
    document.body.classList.remove("no-scroll");
    evImg.removeAttribute("src");
    evOvr.innerHTML = "";
    arcMarkCurrent();
}

eventViewer.addEventListener("click", (e) => {
    const b = e.target.closest("[data-ev],[data-was]");
    if (!b) return;
    if (b.dataset.was) { evWas(b.dataset.was); return; }
    switch (b.dataset.ev) {
        case "close": closeEventViewer(); break;
        case "prev": showEvent(evIndex - 1); arcMarkCurrent(true); break;
        case "next": showEvent(evIndex + 1); arcMarkCurrent(true); break;
        case "crop": case "sky": evSetPic(b.dataset.ev); break;
        case "info": { const c = evQ(".arc-ev__card"); c.hidden = !c.hidden; b.setAttribute("aria-expanded", String(!c.hidden)); break; }
        case "true": case "false": evLabel(b); break;
        case "was": evToggleWas(); break;
        case "share": evCopyLink(); break;
        case "del": evDelete(); break;
        default: break;
    }
});
evScrim.addEventListener("click", closeEventViewer);
document.addEventListener("keydown", (e) => {
    if (eventViewer.hidden || /^(INPUT|SELECT|TEXTAREA)$/.test((e.target || {}).tagName || "")) return;
    if (e.key === "Escape") closeEventViewer();
    else if (e.key === "ArrowLeft") { showEvent(evIndex - 1); arcMarkCurrent(true); }
    else if (e.key === "ArrowRight") { showEvent(evIndex + 1); arcMarkCurrent(true); }
});
// Swipe left/right on phones (not while zoomed)
let evTouchX = null;
eventViewer.addEventListener("touchstart", (e) => { evTouchX = e.touches.length === 1 ? e.touches[0].clientX : null; }, { passive: true });
eventViewer.addEventListener("touchend", (e) => {
    if (evTouchX === null) return;
    if (evZoomApi && evZoomApi.zoomed()) { evTouchX = null; return; }
    const dx = e.changedTouches[0].clientX - evTouchX;
    evTouchX = null;
    if (Math.abs(dx) > 60) { showEvent(evIndex + (dx < 0 ? 1 : -1)); arcMarkCurrent(true); }
});

let tableEvents = [];
let liveEventsData = [];

// ?event=<id> in the address opens that event (shared with the share icon)
(function openLinkedEvent() {
    const id = new URLSearchParams(location.search).get("event");
    if (!id || !/^[a-z]+_\d{8}_\d{6}_\d{3}$/.test(id)) return;
    fetch(`/api/events/${id}`).then((r) => (r.ok ? r.json() : null)).then((ev) => { if (ev) openEventViewer([ev], 0); }).catch(() => {});
})();

// ---------- Archive: Events list (grouped tiles, type chips, date range, infinite scroll) ----------
let arcEvents = [];          // brief records in the chosen range, newest first
let arcTiles = [];           // arcEvents with fragments of one flight/group merged
let arcShown = 0;
const ARC_PAGE = 36;
let arcType = "";
let arcObserver = null;
const arcList = document.getElementById("arc-list");
const arcMore = document.getElementById("arc-more");
const arcSeenKey = "zenivoSeenEvents";
const arcSeen = new Set((() => { try { return JSON.parse(localStorage.getItem(arcSeenKey) || "[]"); } catch (e) { return []; } })());
const arcSaveSeen = () => { try { localStorage.setItem(arcSeenKey, JSON.stringify([...arcSeen].slice(-1500))); } catch (e) { /* ignore */ } };
function arcMarkSeen(id) { if (id && !arcSeen.has(id)) { arcSeen.add(id); arcSaveSeen(); const d = arcList && arcList.querySelector(`[data-event-id="${id}"] .arc-unseen`); if (d) d.remove(); } }

// start of a station-local day as epoch seconds
function arcDayStart(key, endOfDay) {
    const off = stationDate(new Date()).getTime() - Date.now();
    return (Date.parse(`${key}T00:00:00Z`) - Math.round(off / 60000) * 60000) / 1000 + (endOfDay ? 86400 : 0);
}

// fragments of one passage share a group_id; the same callsign within 10 min is one flight too
function arcGroup(evs) {
    const tiles = [], byKey = new Map();
    evs.forEach((ev) => {
        const cs = evCallsign(ev);
        const key = ev.group_id ? `g:${ev.group_id}` : cs && ev.type === "plane" ? `c:${cs}` : "";
        const head = key ? byKey.get(key) : null;
        if (head && Math.abs(head.start_ts - ev.start_ts) < 600) { head.members.push(ev); return; }
        const t = { head: ev, members: [ev], get start_ts() { return this.head.start_ts; } };
        tiles.push(t);
        if (key) byKey.set(key, t.head && t);
    });
    return tiles;
}

function arcTrack(ev) {
    const l = ev.line;
    if (!Array.isArray(l) || l.length < 4) return "";
    const [x1, y1, x2, y2] = l;
    // the thumbnail is a crop around the event: fit the line into its own box, so the shape (not the sky position) is shown
    const minx = Math.min(x1, x2), miny = Math.min(y1, y2), span = Math.max(Math.abs(x2 - x1), Math.abs(y2 - y1), 1e-6);
    const f = (v, m) => 15 + ((v - m) / span) * 70;
    const col = evTypeColor(ev.type);
    return `<svg class="arc-tile__trk" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><line x1="${f(x1, minx)}" y1="${f(y1, miny)}" x2="${f(x2, minx)}" y2="${f(y2, miny)}" stroke="${col}" stroke-width="2" stroke-linecap="round"/>`
        + `<circle cx="${f(x2, minx)}" cy="${f(y2, miny)}" r="3" fill="${col}"/></svg>`;
}

function arcPreview(ev) {
    if (!ev.sky_url) return "";
    const l = Array.isArray(ev.line) && ev.line.length >= 4 ? ev.line.map((v) => v * 100) : null;
    const ovr = l ? `<svg class="arc-tile__trk" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><line x1="${l[0]}" y1="${l[1]}" x2="${l[2]}" y2="${l[3]}" stroke="var(--c-accent)" stroke-width="2" stroke-linecap="round"/></svg>` : "";
    return `<div class="arc-tile__pv" aria-hidden="true"><div style="position:relative"><img src="${escapeHtml(ev.sky_url)}" alt="" loading="lazy" />${ovr}</div></div>`;
}

function arcTileHtml(t, idx) {
    const ev = t.head, n = t.members.length;
    const name = evCallsign(ev) || evSat(ev) || EVT_NAME[ev.type] || ev.type;
    const time = fmtLocalIso(evIso({ start_ts: ev.start_ts }), true).slice(0, 5);
    const q = evQuality(ev);
    const unseen = t.members.some((m) => !arcSeen.has(m.event_id));
    const img = ev.thumbnail_url || ev.sky_url;
    const lab = `${EVT_NAME[ev.type] || ev.type} ${name} ${time}${n > 1 ? `, ${n} detections` : ""}`;
    return `<button type="button" class="arc-tile arc-t-${escapeHtml(ev.type)}" data-tile="${idx}" data-event-id="${escapeHtml(ev.event_id)}" aria-label="${escapeHtml(lab)}" title="${escapeHtml(lab)}">
        <span class="arc-tile__img">${img ? `<img src="${escapeHtml(img)}" alt="" loading="lazy" decoding="async" />` : ""}${ev.type === "plane" || ev.type === "meteor" || ev.type === "satellite" ? arcTrack(ev) : ""}
            <span class="arc-tile__tl"><span class="arc-badge" style="color:${evTypeColor(ev.type)}">${arcIcon(EVT_ICON[ev.type] || "zap")}</span>${n > 1 ? `<span class="arc-badge">${arcIcon("layers")}${n}</span>` : ""}</span>
            <span class="arc-tile__tr">${q ? `<span class="arc-badge arc-q arc-q--${q}" title="Confidence ${Math.round(ev.confidence * 100)}%">${arcIcon("shield")}</span>` : ""}</span>
            ${unseen ? `<span class="arc-unseen" title="New: not opened yet"></span>` : ""}
        </span>
        <span class="arc-tile__foot"><b>${escapeHtml(name)}</b><span>${escapeHtml(time)}</span></span>${arcPreview(ev)}</button>`;
}

function arcCountsUpdate(counts) {
    const c = counts || {};
    const all = Object.values(c).reduce((a, b) => a + (Number(b) || 0), 0);
    document.querySelectorAll("#events-filter [data-count]").forEach((b) => { const k = b.dataset.count; b.textContent = String(k === "all" ? all : c[k] || 0); });
}

function arcRender() {
    if (!arcList) return;
    arcTiles = arcGroup(arcEvents);
    arcShown = 0;
    arcList.innerHTML = "";
    const badge = document.getElementById("day-count-badge");
    if (!arcTiles.length) {
        arcList.innerHTML = arcState("inbox", arcType ? "No events of this type" : "No events yet");
        if (badge) badge.textContent = "0";
        if (arcMore) arcMore.hidden = true;
        return;
    }
    arcAppend();
}

function arcAppend() {
    if (!arcList || arcShown >= arcTiles.length) { if (arcMore) arcMore.hidden = true; return; }
    const next = arcTiles.slice(arcShown, arcShown + ARC_PAGE);
    let html = "", curDay = "";
    const lastHead = arcList.querySelector(".arc-dayhead:last-of-type");
    if (lastHead) curDay = lastHead.dataset.day;
    let open = false;
    const frag = document.createElement("div");
    next.forEach((t, k) => {
        const day = dayKeyOf(new Date(t.head.start_ts * 1000));
        if (day !== curDay) {
            if (open) html += `</div>`;
            curDay = day;
            const label = new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
            const cnt = arcTiles.filter((x) => dayKeyOf(new Date(x.head.start_ts * 1000)) === day).length;
            html += `<h3 class="arc-dayhead" data-day="${day}">${arcIcon("calendar-days")}<b>${escapeHtml(label)}</b><span class="arc-badge">${cnt}</span></h3><div class="arc-tiles" data-day="${day}">`;
            open = true;
        } else if (!open) {
            html += `<div class="arc-tiles" data-day="${day}">`; open = true;
        }
        html += arcTileHtml(t, arcShown + k);
    });
    if (open) html += `</div>`;
    frag.innerHTML = html;
    // continue the previous day's grid when the page boundary falls inside one day
    const firstGrid = frag.firstElementChild;
    const prevGrid = arcList.lastElementChild;
    if (firstGrid && firstGrid.classList.contains("arc-tiles") && prevGrid && prevGrid.classList.contains("arc-tiles")) { while (firstGrid.firstChild) prevGrid.appendChild(firstGrid.firstChild); firstGrid.remove(); }
    while (frag.firstChild) arcList.appendChild(frag.firstChild);
    arcShown += next.length;
    const badge = document.getElementById("day-count-badge");
    if (badge) badge.textContent = `${arcShown}/${arcTiles.length}`;
    if (arcMore) arcMore.hidden = arcShown >= arcTiles.length;
    arcMarkCurrent();
}

function arcSentinelInit() {
    if (!arcMore || arcObserver || !("IntersectionObserver" in window)) return;
    arcObserver = new IntersectionObserver((es) => { if (es.some((x) => x.isIntersecting)) arcAppend(); }, { rootMargin: "400px" });
    arcObserver.observe(arcMore);
}

// highlight the open tile (aria-current); optionally scroll it into view while paging with the arrows
function arcMarkCurrent(scroll) {
    if (!arcList) return;
    const cur = !eventViewer.hidden && evFromArchive && evList[evIndex] ? evList[evIndex].event_id : "";
    arcList.querySelectorAll('.arc-tile[aria-current="true"]').forEach((b) => b.removeAttribute("aria-current"));
    if (!cur) return;
    const tile = arcList.querySelector(`[data-event-id="${cur}"]`);
    if (tile) { tile.setAttribute("aria-current", "true"); if (scroll) tile.scrollIntoView({ block: "nearest" }); }
}

if (arcList) arcList.addEventListener("click", (e) => {
    const b = e.target.closest("[data-tile]");
    if (!b) return;
    const idx = Number(b.dataset.tile);
    openEventViewer(arcTiles.map((t) => t.head), idx, true);
});

// ---- type chips, date range, mark all seen ----
const eventsFilter = document.getElementById("events-filter");
if (eventsFilter) eventsFilter.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-type]");
    if (!btn) return;
    arcType = btn.dataset.type;
    eventsFilter.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    loadEvents();
});
const arcRangeBtn = document.getElementById("btn-arc-range"), arcRangeBox = document.getElementById("arc-range");
if (arcRangeBtn && arcRangeBox) {
    arcRangeBtn.addEventListener("click", () => { arcRangeBox.hidden = !arcRangeBox.hidden; arcRangeBtn.setAttribute("aria-expanded", String(!arcRangeBox.hidden)); });
    ["arc-from", "arc-to"].forEach((id) => document.getElementById(id).addEventListener("change", () => loadEvents()));
    document.getElementById("arc-range-clear").addEventListener("click", () => { document.getElementById("arc-from").value = ""; document.getElementById("arc-to").value = ""; loadEvents(); });
}
const arcSeenBtn = document.getElementById("btn-arc-seen");
if (arcSeenBtn) arcSeenBtn.addEventListener("click", () => {
    arcEvents.forEach((ev) => arcSeen.add(ev.event_id));
    arcSaveSeen();
    document.querySelectorAll(".arc-unseen").forEach((d) => d.remove());
    if (window.DS && DS.toast) DS.toast("All marked seen", "ok");
});
// By day card -> Events for that one day
function arcShowDay(day) {
    document.getElementById("arc-from").value = day;
    document.getElementById("arc-to").value = day;
    if (arcRangeBox) { arcRangeBox.hidden = false; arcRangeBtn.setAttribute("aria-expanded", "true"); }
    setGalleryMode("events");
    loadEvents();
}

async function loadEvents() {
    if (!arcList) return;
    const from = document.getElementById("arc-from").value, to = document.getElementById("arc-to").value;
    const p = new URLSearchParams({ brief: "true", limit: "3000" });
    if (arcType) p.set("type", arcType);
    if (from) p.set("since", String(arcDayStart(from, false)));
    if (to) p.set("until", String(arcDayStart(to, true)));
    arcList.innerHTML = `<div class="ds-skeleton arc-sk"></div><div class="ds-skeleton arc-sk"></div><div class="ds-skeleton arc-sk"></div>`;
    if (arcMore) arcMore.hidden = true;
    try {
        const res = await fetch(`/api/events?${p}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const d = await res.json();
        arcEvents = (d.events || []).slice().sort((a, b) => b.start_ts - a.start_ts);
        tableEvents = arcEvents;
        eventsMeta = { total_count: d.total_count || 0, enabled: d.enabled || {}, counts: d.counts || {} };
        arcCountsUpdate(d.counts);
        arcRender();
        arcSentinelInit();
        if (typeof renderDays === "function" && !arcType && !from && !to) renderDays();
    } catch (err) {
        console.error("Failed to load events:", err);
        arcList.innerHTML = arcState("triangle-alert", "Could not load events", "btn-arc-retry");
        const r = document.getElementById("btn-arc-retry");
        if (r) r.addEventListener("click", () => loadEvents());
    }
}
const arcMoreBtn = document.getElementById("btn-arc-more");
if (arcMoreBtn) arcMoreBtn.addEventListener("click", () => arcAppend());

// Live page: feed of recent sky events on the left, refreshed every 20 s
const liveEventsList = document.getElementById("live-events-list");
const liveEventsStatus = document.getElementById("live-events-status");
const liveEventsCount = document.getElementById("live-events-count");
const liveEventsSeen = new Set();
let liveEventsFirst = true;

function timeAgo(date) {
    const s = Math.max(0, (Date.now() - date.getTime()) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return date.toLocaleDateString();
}

function renderLiveEvents(evData) {
    const events = evData.events || [];
    // "Tonight": since noon today, or since noon yesterday before noon
    const since = new Date();
    if (since.getHours() < 12) since.setDate(since.getDate() - 1);
    since.setHours(12, 0, 0, 0);
    const tonight = events.filter((ev) => new Date(ev.timestamp) >= since).length;
    liveEventsCount.textContent = `${tonight} tonight`;

    const on = Object.entries(evData.enabled || {}).filter(([, v]) => v).map(([k]) => EVENT_LABELS[k] || k);
    const night = evData.night;
    liveEventsStatus.innerHTML = on.length
        ? `<span class="dot ${night ? "on" : ""}"></span>${night ? "Watching" : "Paused until dark"} · ${on.map((l) => l.split(" ")[0]).join(" ")}`
        : `<span class="dot"></span>Detection off (Settings → Event detection)`;
    liveEventsStatus.title = liveEventsStatus.textContent;   // the card shows only the status dot; the words are the tooltip
    liveEventsStatus.setAttribute("aria-label", liveEventsStatus.textContent);

    if (!events.length) {
        liveEventsList.innerHTML = `<li class="live-events-empty">No events yet.<br>Meteors and aircraft show up here as soon as they are detected.</li>`;
        return;
    }
    liveEventsList.innerHTML = "";
    liveEventsData = events;
    events.forEach((ev, i) => {
        const when = new Date(ev.timestamp);
        const li = document.createElement("li");
        li.className = "live-event" + (!liveEventsFirst && !liveEventsSeen.has(ev.event_id) ? " fresh" : "");
        li.dataset.eventIdx = i;
        li.tabIndex = 0;
        li.setAttribute("role", "button");
        li.title = "Click for details";
        li.style.setProperty("--pill", EVENT_COLORS[ev.type] || "var(--accent)");
        liveEventsSeen.add(ev.event_id);
        const label = EVENT_LABELS[ev.type] || ev.type;
        const thumb = ev.thumbnail_url
            ? `<img src="${escapeHtml(ev.thumbnail_url)}" alt="${escapeHtml(ev.type)}" loading="lazy" />`
            : `<span class="no-thumb"></span>`;
        li.innerHTML = `${thumb}<div>
            <div class="ev-type">${escapeHtml(label)}</div>
            <div class="ev-time" title="${fmtLocalIso(ev.timestamp)} ${tzLabel}">${fmtLocalIso(ev.timestamp, true)} · ${timeAgo(when)}</div>
            <div class="ev-detail">${ev.flight ? flightHtml(ev) : eventDetails(ev)}</div></div>`;
        liveEventsList.appendChild(li);
    });
    liveEventsFirst = false;
}

if (liveEventsList) {
    const openLive = (e) => {
        if (e.target.closest("a")) return;
        const li = e.target.closest("li[data-event-idx]");
        if (!li) return;
        const ev = liveEventsData[Number(li.dataset.eventIdx)];
        if (window.openFrameViewerAt && ev && ev.start_ts) window.openFrameViewerAt(ev.start_ts, ev.event_id);
        else openEventViewer(liveEventsData, Number(li.dataset.eventIdx));
    };
    liveEventsList.addEventListener("click", openLive);
    liveEventsList.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openLive(e); } });
}

async function loadLiveEvents() {
    try {
        const [evRes, cfgRes] = await Promise.all([fetch("/api/events?limit=40"), fetch("/api/events/config")]);
        if (!evRes.ok) return;
        const data = await evRes.json();
        if (cfgRes.ok) {
            const cfg = await cfgRes.json();
            data.enabled = cfg.enabled;
            data.night = cfg.night;
        }
        renderLiveEvents(data);
    } catch (err) {
        liveEventsStatus.textContent = "Events unavailable";
    }
}

// Live page: aircraft heard by the ADS-B receiver, nearest in-view first, refreshed every 10 s
const flightsCard = document.getElementById("flights-card");
const flightsList = document.getElementById("flights-list");
const flightsStatus = document.getElementById("flights-status");
const flightsCount = document.getElementById("flights-count");
const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
const compass = (az) => COMPASS[Math.round(((az % 360) + 360) % 360 / 45) % 8];

let flightsLast = null;
// Live "Overhead now" rows: title (+ IN VIEW badge), sub line, and a right column with the distance and "NE · 42° up"
const OV_ROWS = 4;
const ovWhere = (up, az, alt) => (up !== false && alt != null && alt >= 0 ? `${compass(az)} · ${Math.round(alt)}° up` : "below horizon");
const ovRow = (inView, tag, sub, dist, where) => `<li class="flight${inView ? " in-view" : ""}"><div class="fl-main">${tag}${inView ? `<span class="fl-inview">IN VIEW</span>` : ""}</div>` +
    `<div class="fl-sub">${sub}</div><div class="fl-dist" title="Distance from the camera">${dist}</div><div class="fl-where">${where}</div></li>`;

let flightsFilter = "all";
const FL_FILTERS = {
    all: () => true,
    view: (a) => !!a.in_view,
    up: (a) => a.climb_m_min != null && a.climb_m_min > 60,
    down: (a) => a.climb_m_min != null && a.climb_m_min < -60,
};
document.getElementById("flights-chips")?.addEventListener("click", (e) => {
    const b = e.target.closest(".zchip");
    if (!b) return;
    flightsFilter = b.dataset.fl;
    document.querySelectorAll("#flights-chips .zchip").forEach((c) => {
        const on = c === b;
        c.classList.toggle("active", on);
        c.setAttribute("aria-pressed", String(on));
    });
    if (flightsLast) renderFlights(flightsLast);
});

function renderFlights(d) {
    if (!d || !d.enabled) { flightsCard.hidden = true; return; }
    flightsLast = d;
    flightsCard.hidden = false;
    const all = d.aircraft || [];
    const list = all.filter(FL_FILTERS[flightsFilter] || FL_FILTERS.all);
    flightsCount.textContent = `${d.in_view || 0} in view · ${d.total || 0} heard`;
    if (d.source_in_use === "public" && d.public_provider) {
        // adsb.fi / adsb.lol terms: credit the source with a link
        const pp = d.public_provider;
        flightsStatus.innerHTML = `Live from the public network: <a href="${escapeHtml(pp.link)}" target="_blank" rel="noopener">${escapeHtml(pp.name)}</a>`;
    } else {
        flightsStatus.textContent = d.connected ? "Live from the ADS-B receiver" : "Receiver not connected";
    }
    if (!list.length) {
        flightsList.innerHTML = `<li class="live-events-empty">${all.length ? "No aircraft match this filter." : "No aircraft heard right now."}</li>`;
        return;
    }
    flightsList.innerHTML = list.slice(0, OV_ROWS).map((a) => {
        const name = escapeHtml(a.callsign || a.icao);
        const url = `https://globe.adsbexchange.com/?icao=${encodeURIComponent(String(a.icao).toLowerCase())}`;
        const climb = a.climb_m_min == null ? "" : a.climb_m_min > 60 ? " ↗" : a.climb_m_min < -60 ? " ↘" : "";
        const speed = a.speed_kmh != null ? `${escapeHtml(a.speed_kmh)} km/h` : "-- km/h";
        return ovRow(a.in_view, `<a href="${url}" target="_blank" rel="noopener" class="flight-tag">${name}</a>`,
            `${escapeHtml(Number(a.altitude_m).toLocaleString())} m${climb} · ${speed}`, `${escapeHtml(a.distance_km)} km`, ovWhere(a.above_horizon, a.az_deg, a.alt_deg));
    }).join("");
}

async function loadFlights() {
    try {
        const res = await fetch("/api/adsb/aircraft?limit=12");
        if (res.ok) renderFlights(await res.json());
    } catch (err) { /* optional feature */ }
}

// Live page: radiosondes (weather balloons) near the station, refreshed every 20 s
const sondesCard = document.getElementById("sondes-card");
let sondesOff = false;
let sondesLast = 0;
function renderSondes(d) {
    const list = (d && d.sondes) || [];
    if (d && d.enabled === false) {
        sondesOff = true;
        sondesCard.hidden = false;
        document.getElementById("sondes-count").textContent = "off";
        document.getElementById("sondes-status").textContent = "Weather balloons are turned off";
        document.getElementById("sondes-list").innerHTML = "";
        return;
    }
    sondesOff = false;
    if (!d || (!list.length && d.error)) { sondesCard.hidden = true; return; }
    sondesCard.hidden = false;
    document.getElementById("sondes-count").textContent = `${d.in_view || 0} in view · ${d.total || 0} heard`;
    document.getElementById("sondes-status").textContent = "Live from SondeHub (within 350 km)";
    const ul = document.getElementById("sondes-list");
    if (!list.length) { ul.innerHTML = `<li class="live-events-empty">No radiosonde flying nearby right now.</li>`; return; }
    ul.innerHTML = list.slice(0, OV_ROWS).map((a) => {
        const dir = a.climb_m_s > 1 ? " ↗" : a.climb_m_s < -1 ? " ↘" : "";
        const climb = a.climb_m_s == null ? "" : ` · ${a.climb_m_s > 0 ? "+" : ""}${Number(a.climb_m_s).toFixed(1)} m/s`;
        return ovRow(a.in_view, `<a href="https://sondehub.org/${encodeURIComponent(a.serial)}" target="_blank" rel="noopener" class="flight-tag">${escapeHtml(a.serial)}</a>`,
            `${escapeHtml(Number(a.altitude_m).toLocaleString())} m${dir}${climb}`, `${escapeHtml(a.distance_km)} km`, ovWhere(a.above_horizon, a.az_deg, a.alt_deg));
    }).join("");
}
// Live page: satellites above the horizon, those inside the camera frame first (fed by the same push as the overlay)
const satsCard = document.getElementById("sats-card");
function renderSats(d) {
    if (!satsCard) return;
    if (d && d.disabled) { satsCard.hidden = true; return; }
    const all = ((d && d.satellites) || []).filter((s) => s.above_horizon !== false);
    satsCard.hidden = false;
    const inView = all.filter((s) => s.in_view).length;
    document.getElementById("sats-count").textContent = `${inView} in view · ${all.length} up`;
    const sun = d && d.sun_alt_deg;
    if (typeof sun === "number") window.ALLSKY_SUN_ALT = { v: sun, t: Date.now() };
    document.getElementById("sats-status").textContent = sun == null ? "Large satellites above the horizon"
        : sun > -6 ? "Daylight: positions only, not visible to the eye" : "Bright = sunlit under a dark sky";
    const ul = document.getElementById("sats-list");
    if (!all.length) { ul.innerHTML = `<li class="live-events-empty">No large satellite above the horizon right now.</li>`; return; }
    const list = all.slice().sort((a, b) => (b.in_view - a.in_view) || (b.visible - a.visible) || (b.alt_deg - a.alt_deg)).slice(0, OV_ROWS);
    ul.innerHTML = list.map((s) => ovRow(s.in_view, `<a href="https://www.n2yo.com/satellite/?s=${encodeURIComponent(s.norad)}" target="_blank" rel="noopener" class="flight-tag">${escapeHtml(s.name)}</a>`,
        `${escapeHtml(Number(s.height_km).toLocaleString())} km high`, s.range_km != null ? `${escapeHtml(Number(s.range_km).toLocaleString())} km` : `${Math.round(s.alt_deg)}°`, ovWhere(true, s.az_deg, s.alt_deg))).join("");
}
async function loadSatsCard() {
    try {
        const res = await fetch("/api/satellites");
        if (res.ok) renderSats(await res.json());
    } catch (err) { /* optional feature */ }
}
if (currentPage === "tab-live" && satsCard) {
    setTimeout(() => { if (!overlayPushOn("satellites")) loadSatsCard(); }, 3000);
    setInterval(() => { if (!document.hidden && !overlayPushOn("satellites")) loadSatsCard(); }, 20000);
}

async function loadSondes() {
    try {
        const res = await fetch("/api/radiosondes?limit=8");
        if (res.ok) renderSondes(await res.json());
    } catch (err) { /* optional feature */ }
}
if (currentPage === "tab-live" && sondesCard) {
    loadSondes();
    setInterval(() => {
        // when switched off in Settings, look again only every 5 minutes
        if (document.hidden || overlayPushOn("sondes") || (sondesOff && Date.now() - sondesLast < 300000)) return;
        sondesLast = Date.now();
        loadSondes();
    }, 20000);
}

if (currentPage === "tab-live" && flightsCard) {
    loadFlights();
    setInterval(() => { if (!document.hidden && !overlayPushOn("aircraft")) loadFlights(); }, 10000);
}

if (currentPage === "tab-live" && liveEventsList) {
    loadLiveEvents();
    setInterval(() => { if (!document.hidden && !overlayPushOn("events")) loadLiveEvents(); }, 20000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) loadLiveEvents(); });
}
