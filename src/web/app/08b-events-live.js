/* Zenivo dashboard (part of the former app.js): Event viewer, events table and the Live page feeds (events, flights, radiosondes).
 * Classic script: continues 08-gallery-adsb.js; top-level declarations are shared with the other src/web/app/*.js files; load order is fixed in index.html. */

const eventViewer = document.createElement("div");
eventViewer.className = "event-viewer hidden";
eventViewer.setAttribute("role", "dialog");
eventViewer.setAttribute("aria-modal", "true");
eventViewer.innerHTML = `
    <div class="ev-panel">
        <div class="ev-topbar"><button type="button" class="ev-back" aria-label="Back">&larr;</button><b>Event detail</b></div>
        <header class="ev-head">
            <span class="ev-pill"></span>
            <div class="ev-when"><strong></strong><span></span></div>
            <div class="ev-nav">
                <button type="button" class="ev-prev" title="Newer event (←)">‹</button>
                <span class="ev-pos"></span>
                <button type="button" class="ev-next" title="Older event (→)">›</button>
                <button type="button" class="ev-close" title="Close (Esc)">✕</button>
            </div>
        </header>
        <div class="ev-images">
            <figure class="ev-fig ev-fig-crop"><div class="ev-zoom"><img alt="Close-up" draggable="false" /></div><figcaption>Close-up</figcaption></figure>
            <figure class="ev-fig ev-fig-sky"><div class="ev-zoom"><img alt="Whole sky" draggable="false" /></div><figcaption>Whole sky, event boxed</figcaption></figure>
        </div>
        <p class="ev-summary"></p>
        <dl class="ev-facts"></dl>
        <section class="ev-match" hidden><h3>Matched aircraft</h3><div class="ev-match-card"></div></section>
        <div class="ev-actions">
            <span class="ev-label" role="group" aria-label="Owner verdict">
                <button type="button" class="btn btn-sm btn-outline ev-lab-true" data-admin data-label="true" aria-label="Real event" title="Real event" aria-pressed="false"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></button>
                <button type="button" class="btn btn-sm btn-outline ev-lab-false" data-admin data-label="false" aria-label="False alarm" title="False alarm" aria-pressed="false"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
            </span>
            <a class="btn btn-sm btn-outline ev-open-crop" download rel="noopener">Download close-up</a>
            <a class="btn btn-sm btn-outline ev-open-sky" download rel="noopener">Download whole sky</a>
            <button type="button" class="btn btn-sm btn-outline ev-copy">Copy link</button>
            <button type="button" class="btn btn-sm btn-outline ev-del" data-admin>Delete</button>
            <p class="ev-actions-note ev-phone-only">Delete is available to admins only</p>
        </div>
    </div>`;
document.body.appendChild(eventViewer);
const evQ = (sel) => eventViewer.querySelector(sel);
let evList = [];
let evIndex = 0;
// Zoom + pan on each event picture (zoom_pan.js; the pill shows on hover or once zoomed, the tiles are small on phones); keys + - 0 go to the picture under the pointer, else to the whole-sky one
const evZoomOpen = () => !eventViewer.classList.contains("hidden");
const evZoomCrop = window.ZenivoZoomPan ? ZenivoZoomPan.attach(evQ(".ev-fig-crop .ev-zoom"), evQ(".ev-fig-crop img"), { pill: "hover", active: () => evZoomOpen() && evQ(".ev-fig-sky").hidden && !evQ(".ev-fig-sky .ev-zoom").matches(":hover") }) : null;
const evZoomSky = window.ZenivoZoomPan ? ZenivoZoomPan.attach(evQ(".ev-fig-sky .ev-zoom"), evQ(".ev-fig-sky img"), { pill: "hover", active: () => evZoomOpen() && !evQ(".ev-fig-crop .ev-zoom").matches(":hover") }) : null;
const evZoomReset = () => { if (evZoomCrop) evZoomCrop.reset(); if (evZoomSky) evZoomSky.reset(); };

function showEvent(i) {
    if (!evList.length) return;
    evIndex = Math.max(0, Math.min(evList.length - 1, i));
    const ev = evList[evIndex];
    evZoomReset();
    const when = new Date(ev.timestamp);
    const pill = evQ(".ev-pill");
    const lab = EVENT_LABELS[ev.type] || ev.type, lm = /^(\S+)\s+(.+)$/.exec(lab);
    // the leading emoji is wrapped so the phone layout (mockup: plain chip) can hide it
    pill.innerHTML = lm && !/^[A-Za-z]/.test(lm[1]) ? `<span class="ev-emo">${escapeHtml(lm[1])}</span> ${escapeHtml(lm[2])}` : escapeHtml(lab);
    pill.style.setProperty("--pill", EVENT_COLORS[ev.type] || "var(--accent)");
    const [evDate = "", evTime = ""] = fmtLocalIso(ev.timestamp).split(" ");
    evQ(".ev-when strong").innerHTML = `<span class="evw-d">${escapeHtml(evDate)} </span>${escapeHtml(evTime)}<span class="evw-z"> ${escapeHtml(tzLabel)}</span>`;
    evQ(".ev-when > span").textContent = timeAgo(when);
    evQ(".ev-pos").textContent = `${evIndex + 1} / ${evList.length}`;
    evQ(".ev-prev").disabled = evIndex === 0;
    evQ(".ev-next").disabled = evIndex === evList.length - 1;
    const crop = ev.sky_url && ev.thumbnail_url !== ev.sky_url ? ev.thumbnail_url : null;
    const figCrop = evQ(".ev-fig-crop"), figSky = evQ(".ev-fig-sky");
    figCrop.hidden = !crop;
    figSky.hidden = !ev.sky_url;
    if (crop) figCrop.querySelector("img").src = crop;
    if (ev.sky_url) figSky.querySelector("img").src = ev.sky_url;
    figSky.querySelector("figcaption").textContent = ev.moving_dot ? "Whole sky, path marked" : (crop ? "Whole sky, event boxed" : "Whole sky");
    evQ(".ev-images").classList.toggle("single", !crop || !ev.sky_url);
    evQ(".ev-summary").textContent = eventSummary(ev);
    // phone card shows the date under TIME (the head carries only the clock time)
    const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], dp = evDate.split("-");
    const dateTxt = dp.length === 3 ? `${Number(dp[2])} ${MON[Number(dp[1]) - 1] || ""} ${dp[0]}` : evDate;
    evQ(".ev-facts").innerHTML = `<div class="ev-phone-only"><dt>Time</dt><dd>${escapeHtml(`${dateTxt} · ${tzLabel}`)}</dd></div>` + eventFacts(ev).map(([k, v, isHtml]) => `<div><dt>${escapeHtml(k)}</dt><dd>${isHtml ? v : escapeHtml(v)}</dd></div>`).join("");
    const matchEl = evQ(".ev-match");
    matchEl.hidden = !ev.flight;
    if (ev.flight) {
        const fl = ev.flight;
        evQ(".ev-match-card").innerHTML = `<div class="evm-head">${flightHtml(ev)}</div>`
            + `<dl class="evm-rows">${flightFacts(ev).map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join("")}</dl>`;
    }
    const oc = evQ(".ev-open-crop"), os = evQ(".ev-open-sky");
    oc.hidden = !crop; os.hidden = !ev.sky_url;
    if (crop) oc.href = crop;
    if (ev.sky_url) os.href = ev.sky_url;
    evDelDisarm();
    evLabelShow(ev.owner_label);
    evQ(".ev-copy").textContent = "Copy link";
}

// Owner verdict (true / false alarm) -> POST /api/events/<id>/label; tapping the active one again withdraws it
function evLabelShow(label) {
    eventViewer.querySelectorAll(".ev-label button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.label === label)));
}
async function evLabel(btn) {
    const ev = evList[evIndex];
    if (!ev || !ev.event_id) return;
    const want = btn.getAttribute("aria-pressed") === "true" ? "clear" : btn.dataset.label;
    try {
        const res = await fetch(`/api/events/${encodeURIComponent(ev.event_id)}/label`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: want }),
        });
        if (!res.ok) throw new Error(res.status === 401 || res.status === 403 ? "Sign in as admin" : `HTTP ${res.status}`);
        ev.owner_label = want === "clear" ? undefined : want;
        evLabelShow(ev.owner_label);
    } catch (err) {
        btn.title = `Not saved: ${err.message}`;
    }
}

// Deep link to one event: /gallery?event=<id> (the page opens it in this viewer)
const evLink = (ev) => `${location.origin}/gallery?event=${encodeURIComponent(ev.event_id)}`;
let evDelTimer = null;
function evDelDisarm() {
    clearTimeout(evDelTimer);
    const b = evQ(".ev-del");
    b.dataset.armed = "";
    b.textContent = "Delete";
}
async function evCopyLink() {
    const ev = evList[evIndex], b = evQ(".ev-copy");
    if (!ev || !ev.event_id) return;
    const url = evLink(ev);
    try {
        await navigator.clipboard.writeText(url);
        b.textContent = "Link copied";
    } catch (e) {
        window.prompt("Copy this link", url);   // clipboard blocked (plain http): let the person copy it by hand
    }
    setTimeout(() => { b.textContent = "Copy link"; }, 2000);
}
async function evDelete() {
    const ev = evList[evIndex], b = evQ(".ev-del");
    if (!ev || !ev.event_id) return;
    if (!b.dataset.armed) {   // first tap asks, second (within 4 s) deletes
        b.dataset.armed = "1";
        b.textContent = "Tap again to delete";
        evDelTimer = setTimeout(evDelDisarm, 4000);
        return;
    }
    evDelDisarm();
    b.disabled = true;
    try {
        const res = await fetch(`/api/events/${encodeURIComponent(ev.event_id)}`, { method: "DELETE" });
        if (!res.ok) throw new Error(res.status === 401 || res.status === 403 ? "Sign in as admin" : `HTTP ${res.status}`);
        [evList, tableEvents, liveEventsData].forEach((arr) => {
            const k = arr.findIndex((x) => x.event_id === ev.event_id);
            if (k >= 0) arr.splice(k, 1);
        });
        if (evList.length) showEvent(Math.min(evIndex, evList.length - 1)); else closeEventViewer();
        if (typeof loadEvents === "function" && document.getElementById("day-list")) loadEvents();
        if (typeof loadLiveEvents === "function" && document.getElementById("live-events-list")) loadLiveEvents();
    } catch (err) {
        b.textContent = `Not deleted: ${err.message}`;
        setTimeout(() => { b.textContent = "Delete"; }, 3000);
    } finally { b.disabled = false; }
}

function openEventViewer(list, i) {
    evList = list || [];
    if (!evList.length) return;
    showEvent(i);
    eventViewer.classList.remove("hidden");
    document.body.classList.add("no-scroll");
    evQ(".ev-close").focus({ preventScroll: true });
}

function closeEventViewer() {
    const u = new URL(location.href);
    if (u.searchParams.has("event")) { u.searchParams.delete("event"); history.replaceState(null, "", u.pathname + u.search + u.hash); }
    evZoomReset();
    eventViewer.classList.add("hidden");
    document.body.classList.remove("no-scroll");
    eventViewer.querySelectorAll("img").forEach((im) => im.removeAttribute("src"));
}

eventViewer.addEventListener("click", (e) => {
    if (e.target === eventViewer || e.target.closest(".ev-close") || e.target.closest(".ev-back")) closeEventViewer();
    else if (e.target.closest(".ev-copy")) evCopyLink();
    else if (e.target.closest(".ev-del")) evDelete();
    else if (e.target.closest(".ev-label button")) evLabel(e.target.closest(".ev-label button"));
    else if (e.target.closest(".ev-prev")) showEvent(evIndex - 1);
    else if (e.target.closest(".ev-next")) showEvent(evIndex + 1);
});
document.addEventListener("keydown", (e) => {
    if (eventViewer.classList.contains("hidden")) return;
    if (e.key === "Escape") closeEventViewer();
    else if (e.key === "ArrowLeft") showEvent(evIndex - 1);
    else if (e.key === "ArrowRight") showEvent(evIndex + 1);
});
// Swipe left/right on phones
let evTouchX = null;
eventViewer.addEventListener("touchstart", (e) => { evTouchX = e.touches.length === 1 ? e.touches[0].clientX : null; }, { passive: true });   // a pinch is not a swipe
eventViewer.addEventListener("touchend", (e) => {
    if (evTouchX === null) return;
    if ((evZoomCrop && evZoomCrop.zoomed()) || (evZoomSky && evZoomSky.zoomed())) { evTouchX = null; return; }   // zoomed: a drag pans, it does not change event
    const dx = e.changedTouches[0].clientX - evTouchX;
    evTouchX = null;
    if (Math.abs(dx) > 60) showEvent(evIndex + (dx < 0 ? 1 : -1));
});

let tableEvents = [];
let liveEventsData = [];

// ?event=<id> in the address opens that event (shared with "Copy link")
(function openLinkedEvent() {
    const id = new URLSearchParams(location.search).get("event");
    if (!id || !/^[a-z]+_\d{8}_\d{6}_\d{3}$/.test(id)) return;
    fetch(`/api/events/${id}`).then((r) => (r.ok ? r.json() : null)).then((ev) => { if (ev) openEventViewer([ev], 0); }).catch(() => {});
})();

function eventRowHtml(ev, i) {
    const when = new Date(ev.timestamp);
    const thumb = ev.thumbnail_url
        ? `<img class="event-thumb" src="${escapeHtml(ev.thumbnail_url)}" alt="${escapeHtml(ev.type)}" loading="lazy" />`
        : "--";
    return `
        <tr class="event-row" data-event-idx="${i}" title="Click for details">
            <td>${thumb}</td>
            <td><span class="ev-pill" style="--pill:${EVENT_COLORS[ev.type] || "var(--accent)"}">${escapeHtml(EVENT_LABELS[ev.type] || ev.type)}</span></td>
            <td title="${escapeHtml(ev.timestamp)}">${fmtLocalIso(ev.timestamp, true)} ${tzLabel}<br><span class="text-muted">${timeAgo(when)}</span></td>
            <td>${eventDetails(ev)}${ev.flight ? `<br>${flightHtml(ev)}` : ""}</td>
            <td><button type="button" class="btn btn-sm btn-outline">Details</button></td>
        </tr>`;
}

function renderEvents(evData) {
    tableEvents = evData.events || [];
    eventsMeta = { total_count: evData.total_count || 0, enabled: evData.enabled || {} };
    renderDays();
}

dayList.addEventListener("click", (e) => {
    if (e.target.closest("a")) return; // flight link
    const tr = e.target.closest("[data-event-idx]");
    if (tr) openEventViewer(tableEvents, Number(tr.dataset.eventIdx));
});

let eventsFilterType = "";
const eventsFilter = document.getElementById("events-filter");
if (eventsFilter) {
    eventsFilter.addEventListener("click", async (e) => {
        const btn = e.target.closest("button[data-type]");
        if (!btn) return;
        eventsFilterType = btn.dataset.type;
        eventsFilter.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
        await loadEvents();
    });
}

async function loadEvents() {
    const q = eventsFilterType ? `&type=${eventsFilterType}` : "";
    const res = await fetch(`/api/events?limit=100${q}`); // API max; By Day groups these by local date
    if (res.ok) renderEvents(await res.json());
}

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
