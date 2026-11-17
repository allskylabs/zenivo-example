/* Zenivo dashboard (part of the former app.js): Event viewer, events table and the Live page feeds (events, flights, radiosondes).
 * Classic script: continues 08-gallery-adsb.js; top-level declarations are shared with the other src/web/app/*.js files; load order is fixed in index.html. */

const eventViewer = document.createElement("div");
eventViewer.className = "event-viewer hidden";
eventViewer.setAttribute("role", "dialog");
eventViewer.setAttribute("aria-modal", "true");
eventViewer.innerHTML = `
    <div class="ev-panel">
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
            <figure class="ev-fig ev-fig-crop"><img alt="Close-up" /><figcaption>Close-up</figcaption></figure>
            <figure class="ev-fig ev-fig-sky"><img alt="Whole sky" /><figcaption>Whole sky, event boxed</figcaption></figure>
        </div>
        <p class="ev-summary"></p>
        <dl class="ev-facts"></dl>
        <div class="ev-actions">
            <a class="btn btn-sm btn-outline ev-open-crop" target="_blank" rel="noopener">Open close-up ↗</a>
            <a class="btn btn-sm btn-outline ev-open-sky" target="_blank" rel="noopener">Open whole sky ↗</a>
        </div>
    </div>`;
document.body.appendChild(eventViewer);
const evQ = (sel) => eventViewer.querySelector(sel);
let evList = [];
let evIndex = 0;

function showEvent(i) {
    if (!evList.length) return;
    evIndex = Math.max(0, Math.min(evList.length - 1, i));
    const ev = evList[evIndex];
    const when = new Date(ev.timestamp);
    const pill = evQ(".ev-pill");
    pill.textContent = EVENT_LABELS[ev.type] || ev.type;
    pill.style.setProperty("--pill", EVENT_COLORS[ev.type] || "var(--accent)");
    evQ(".ev-when strong").textContent = `${fmtLocalIso(ev.timestamp)} ${tzLabel}`;
    evQ(".ev-when span").textContent = timeAgo(when);
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
    evQ(".ev-facts").innerHTML = eventFacts(ev).map(([k, v, isHtml]) => `<div><dt>${escapeHtml(k)}</dt><dd>${isHtml ? v : escapeHtml(v)}</dd></div>`).join("");
    const oc = evQ(".ev-open-crop"), os = evQ(".ev-open-sky");
    oc.hidden = !crop; os.hidden = !ev.sky_url;
    if (crop) oc.href = crop;
    if (ev.sky_url) os.href = ev.sky_url;
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
    eventViewer.classList.add("hidden");
    document.body.classList.remove("no-scroll");
    eventViewer.querySelectorAll("img").forEach((im) => im.removeAttribute("src"));
}

eventViewer.addEventListener("click", (e) => {
    if (e.target === eventViewer || e.target.closest(".ev-close")) closeEventViewer();
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
eventViewer.addEventListener("touchstart", (e) => { evTouchX = e.touches[0].clientX; }, { passive: true });
eventViewer.addEventListener("touchend", (e) => {
    if (evTouchX === null) return;
    const dx = e.changedTouches[0].clientX - evTouchX;
    evTouchX = null;
    if (Math.abs(dx) > 60) showEvent(evIndex + (dx < 0 ? 1 : -1));
});

let tableEvents = [];
let liveEventsData = [];

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
    const tr = e.target.closest("tr[data-event-idx]");
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

function renderFlights(d) {
    if (!d || !d.enabled) { flightsCard.hidden = true; return; }
    flightsCard.hidden = false;
    const list = d.aircraft || [];
    flightsCount.textContent = `${d.in_view || 0} in view · ${d.total || 0} heard`;
    if (d.source_in_use === "public" && d.public_provider) {
        // adsb.fi / adsb.lol terms: credit the source with a link
        const pp = d.public_provider;
        flightsStatus.innerHTML = `Live from the public network: <a href="${escapeHtml(pp.link)}" target="_blank" rel="noopener">${escapeHtml(pp.name)}</a>`;
    } else {
        flightsStatus.textContent = d.connected ? "Live from the ADS-B receiver" : "Receiver not connected";
    }
    if (!list.length) {
        flightsList.innerHTML = `<li class="live-events-empty">No aircraft heard right now.</li>`;
        return;
    }
    flightsList.innerHTML = list.map((a) => {
        const name = escapeHtml(a.callsign || a.icao);
        const url = `https://globe.adsbexchange.com/?icao=${encodeURIComponent(String(a.icao).toLowerCase())}`;
        const climb = a.climb_m_min == null ? "" : a.climb_m_min > 60 ? " ↗" : a.climb_m_min < -60 ? " ↘" : "";
        const where = a.in_view ? `<span class="fl-inview">● in view</span>`
            : a.above_horizon ? `${compass(a.az_deg)} ${Math.round(a.alt_deg)}° up` : "below horizon";
        const hdg = a.heading_deg == null ? "" : `<span class="fl-hdg" style="transform:rotate(${a.heading_deg}deg)" title="Heading ${a.heading_deg}°">↑</span>`;
        return `<li class="flight${a.in_view ? " in-view" : ""}">
            <div class="fl-top"><a href="${url}" target="_blank" rel="noopener" class="flight-tag">✈ ${name}</a>${hdg}<span class="fl-where">${where}</span></div>
            <div class="fl-meta">
                <span title="Altitude">${escapeHtml(Number(a.altitude_m).toLocaleString())} m${climb}</span>
                <span title="Ground speed">${a.speed_kmh != null ? `${escapeHtml(a.speed_kmh)} km/h` : "-- km/h"}</span>
                <span title="Distance from the camera">${escapeHtml(a.distance_km)} km</span>
            </div></li>`;
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
    ul.innerHTML = list.map((a) => {
        const where = a.in_view ? `<span class="fl-inview">● in view</span>`
            : a.above_horizon ? `${compass(a.az_deg)} ${Math.round(a.alt_deg)}° up` : "below horizon";
        const dir = a.climb_m_s > 1 ? " ↗" : a.climb_m_s < -1 ? " ↘" : "";
        return `<li class="flight${a.in_view ? " in-view" : ""}">
            <div class="fl-top"><a href="https://sondehub.org/${encodeURIComponent(a.serial)}" target="_blank" rel="noopener" class="flight-tag">🎈 ${escapeHtml(a.serial)}</a><span class="fl-where">${where}</span></div>
            <div class="fl-meta">
                <span title="Altitude">${escapeHtml(Number(a.altitude_m).toLocaleString())} m${dir}</span>
                <span title="${escapeHtml(a.type || "")}">${a.freq_mhz ? escapeHtml(a.freq_mhz) + " MHz" : escapeHtml(a.type || "")}</span>
                <span title="Distance from the camera">${escapeHtml(a.distance_km)} km</span>
            </div></li>`;
    }).join("");
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
        if (document.hidden || (sondesOff && Date.now() - sondesLast < 300000)) return;
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
