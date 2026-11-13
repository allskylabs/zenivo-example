/* Zenivo dashboard (part of the former app.js): Gallery, events, timelapse builder and ADS-B settings.
 * Classic script: top-level declarations are shared with the other src/web/app/*.js files; load order is fixed in index.html. */

// Gallery & Events Fetcher + On-Demand Timelapse Builder
// ------------------------------------------------------------------
const galleryDateEl = document.getElementById("gallery-date");
const galleryFrameCountEl = document.getElementById("gallery-frame-count");
const btnBuildTimelapse = document.getElementById("btn-build-timelapse");
const timelapseFpsSelect = document.getElementById("timelapse-fps-select");
const galleryAlert = document.getElementById("gallery-alert");

if (btnBuildTimelapse) {
    btnBuildTimelapse.addEventListener("click", async () => {
        const fps = timelapseFpsSelect ? parseFloat(timelapseFpsSelect.value) || 12 : 12;
        btnBuildTimelapse.disabled = true;
        btnBuildTimelapse.textContent = "⏳ Building MP4 timelapse...";
        showAlert(galleryAlert, `⏳ Compiling today's frames into an H.264 timelapse (${fps} FPS), keogram & startrails...`, "info", true);
        try {
            const res = await fetch(`/api/gallery/generate?fps=${encodeURIComponent(fps)}`, {
                method: "POST",
            });
            const data = await res.json();
            if (res.ok) {
                if (data.gallery) {
                    renderGallery(data.gallery);
                } else {
                    await loadGalleryAndEvents();
                }
                showAlert(
                    galleryAlert,
                    `✅ Timelapse created (${data.archived_frames_count || 0} frames → ${data.duration_s || 0}s @ ${data.fps || fps} FPS, ${data.size_kb || 0} KB)!`,
                    "success",
                    true
                );
            } else {
                showAlert(galleryAlert, `❌ Timelapse build failed: ${data.detail || "Unknown error"}`, "error", true);
            }
        } catch (err) {
            showAlert(galleryAlert, `❌ Connection error while building timelapse: ${err.message}`, "error", true);
        } finally {
            btnBuildTimelapse.disabled = false;
            btnBuildTimelapse.textContent = "🎬 Build / Update Timelapse Now";
        }
    });
}

if (currentPage === "tab-gallery") setTimeout(() => loadGalleryAndEvents(), 0);

// Gallery top tabs: Products | By day | Events (same data, different views)
const galleryContainer = document.getElementById("gallery-container");
const galleryTabs = document.getElementById("gallery-tabs");
const galleryDayTitle = document.getElementById("gallery-day-title");
function setGalleryMode(mode) {
    if (!galleryContainer || !galleryTabs) return;
    galleryContainer.classList.remove("gal-mode-products", "gal-mode-day", "gal-mode-events");
    galleryContainer.classList.add(`gal-mode-${mode}`);
    galleryTabs.querySelectorAll(".gal-tab").forEach((b) => {
        b.classList.toggle("active", b.dataset.gmode === mode);
        b.setAttribute("aria-selected", b.dataset.gmode === mode ? "true" : "false");
    });
    if (galleryDayTitle) galleryDayTitle.textContent = mode === "events" ? "Events" : "By Day";
    try { localStorage.setItem("galleryMode", mode); } catch (e) { /* ignore */ }
}
if (galleryTabs) {
    galleryTabs.addEventListener("click", (e) => {
        const b = e.target.closest(".gal-tab");
        if (b) setGalleryMode(b.dataset.gmode);
    });
    let savedMode = "products";
    try { savedMode = localStorage.getItem("galleryMode") || "products"; } catch (e) { /* ignore */ }
    setGalleryMode(["products", "day", "events"].includes(savedMode) ? savedMode : "products");
}

// Timelapse builder: custom date range -> POST /api/timelapse/build
const tlBuild = document.getElementById("tl-build");
if (tlBuild) {
    const tlFrom = document.getElementById("tl-from");
    const tlTo = document.getElementById("tl-to");
    const tlResult = document.getElementById("tl-result");
    const todayKey = dayKeyOf(new Date());
    tlTo.value = todayKey;
    tlFrom.value = dayKeyOf(new Date(Date.now() - 86400000));
    tlBuild.addEventListener("click", async () => {
        const q = new URLSearchParams({
            date_from: tlFrom.value,
            date_to: tlTo.value,
            fps: document.getElementById("tl-fps").value || "24",
            width: document.getElementById("tl-res").value,
            overlay: document.getElementById("tl-overlay").checked ? "true" : "false",
        });
        tlBuild.disabled = true;
        tlBuild.textContent = "Building...";
        tlResult.classList.remove("hidden");
        tlResult.textContent = "Encoding frames, this can take a minute.";
        try {
            const res = await fetch(`/api/timelapse/build?${q}`, { method: "POST" });
            const d = await res.json();
            if (res.ok) {
                const url = `${d.timelapse_url}?t=${Date.now()}`;
                tlResult.innerHTML = `${d.video_frames_count} frames, ${d.duration_s}s @ ${d.fps} fps, ${formatSize(d.size_kb)} · <a href="${escapeHtml(url)}" download>Download</a><video controls loop muted playsinline src="${escapeHtml(url)}"></video>`;
            } else {
                tlResult.textContent = `Build failed: ${d.detail || "unknown error"}`;
            }
        } catch (err) {
            tlResult.textContent = `Connection error: ${err.message}`;
        } finally {
            tlBuild.disabled = false;
            tlBuild.textContent = "Build timelapse";
        }
    });
}

async function loadGalleryAndEvents() {
    try {
        const resGal = await fetch("/api/gallery");
        if (resGal.ok) {
            const data = await resGal.json();
            renderGallery(data);
        }

        await loadEvents();
    } catch (err) {
        console.error("Failed to load gallery/events:", err);
    }
}

function renderGallery(data) {
    productsGrid.innerHTML = "";
    if (galleryDateEl && data && data.date) {
        galleryDateEl.textContent = `Archive: ${data.date} (${tzLabel})`;
    }
    if (galleryFrameCountEl && data && data.archived_frames_count !== undefined) {
        galleryFrameCountEl.textContent = `${data.archived_frames_count} Frames Archived Today`;
    }

    if (!data || !data.items || data.items.length === 0) {
        productsGrid.innerHTML = "<p class='text-muted'>No products recorded yet.</p>";
        return;
    }

    const cacheBust = Date.now();
    data.items.forEach((item) => {
        const card = document.createElement("div");
        card.className = "product-card";
        const mediaUrl = `${item.url}?t=${cacheBust}`;
        const sizeBadge = item.size_kb ? `${item.size_kb.toFixed(1)} KB` : "Pending";
        const nRejected = item.rejected ? Object.values(item.rejected).reduce((a, b) => a + b, 0) : 0;
        const framesBadge = item.frame_count !== undefined
            ? `${item.frame_count} frames` + (nRejected ? ` (${nRejected} rejected)` : "")
            : "";
        const tsFormatted = item.timestamp ? fmtLocalIso(item.timestamp) : "";

        let mediaHtml = "";
        if (item.type === "timelapse") {
            mediaHtml = `
                <video controls autoplay loop muted playsinline preload="metadata" src="${escapeHtml(mediaUrl)}">
                    Your browser does not support MP4 video.
                </video>
            `;
        } else {
            mediaHtml = `
                <a href="${escapeHtml(mediaUrl)}" data-viewer="${escapeHtml(item.title || item.type)}" title="Click to view full size" style="width:100%;height:100%;display:block;">
                    <img src="${escapeHtml(mediaUrl)}" alt="${escapeHtml(item.title || item.type)}" loading="lazy" />
                </a>
            `;
        }

        card.innerHTML = `
            <div class="product-media">
                ${mediaHtml}
            </div>
            <div class="product-info">
                <div class="product-title">
                    <span>${escapeHtml(item.title || item.type)}</span>
                    <span class="badge badge-accent">${sizeBadge}</span>
                </div>
                <div class="product-meta">
                    <span>${framesBadge}${framesBadge && tsFormatted ? " • " : ""}${tsFormatted}</span>
                    <a href="${escapeHtml(mediaUrl)}" download class="btn btn-sm btn-outline" style="padding: 0.15rem 0.5rem; font-size: 0.75rem; text-decoration: none;">⬇️ Download</a>
                </div>
            </div>
        `;
        productsGrid.appendChild(card);
        if (item.type === "timelapse") attachVideoInfo(card.querySelector("video"), item.info_url);
    });
    galleryHistory = data.history || [];
    renderDays();
}

function formatSize(kb) {
    return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb.toFixed(0)} KB`;
}

// Gallery "By Day": one collapsible block per local day (newest first) holding that day's
// timelapse / keogram / startrails and its sky events. Today's live products stay in the grid above.
let galleryHistory = [];
let eventsMeta = { total_count: 0, enabled: {} };

function dayKeyOf(date) {
    const p = (n) => String(n).padStart(2, "0");
    const sd = stationDate(date);
    return `${sd.getUTCFullYear()}-${p(sd.getUTCMonth() + 1)}-${p(sd.getUTCDate())}`;
}

function renderDays() {
    if (!dayList) return;
    const todayKey = dayKeyOf(new Date());
    const byDay = new Map();
    const ensure = (key) => {
        if (!byDay.has(key)) byDay.set(key, { date: key, product: null, events: [] });
        return byDay.get(key);
    };
    ensure(todayKey);
    galleryHistory.forEach((d) => { ensure(d.date).product = d; });
    tableEvents.forEach((ev, i) => ensure(dayKeyOf(new Date(ev.timestamp))).events.push(i));

    const days = [...byDay.values()].sort((a, b) => (a.date < b.date ? 1 : -1));
    if (dayCountBadge) dayCountBadge.textContent = `${days.length} ${days.length === 1 ? "Day" : "Days"}`;

    let notice = "";
    if (tableEvents.length === 0) {
        const on = Object.entries(eventsMeta.enabled || {}).filter(([, v]) => v).map(([k]) => EVENT_LABELS[k] || k);
        notice = `No events yet. Detecting: ${on.join(", ") || "nothing (turn types on in Settings)"}.`;
    } else if (eventsMeta.total_count > tableEvents.length) {
        notice = `Showing the newest ${tableEvents.length} of ${eventsMeta.total_count} events.`;
    }
    dayList.innerHTML = (notice ? `<p class="text-muted day-notice">${notice}</p>` : "")
        + days.map((day, n) => dayBlockHtml(day, todayKey, n === 0)).join("");
    dayList.querySelectorAll("video[data-info]").forEach((v) => attachVideoInfo(v, v.dataset.info));
}

function dayBlockHtml(day, todayKey, first) {
    const isToday = day.date === todayKey;
    const p = day.product || {};
    const evs = day.events;
    const tl = p.timelapse;

    let products = "";
    if (!isToday) {
        const tiles = [];
        if (tl) tiles.push(`<video controls loop muted playsinline preload="metadata" src="${escapeHtml(tl.url)}#t=0.5" data-info="${escapeHtml(tl.url.replace(/\.mp4$/, ".json"))}"></video>`);
        [["keogram", "Keogram"], ["startrails", "Startrails"]].forEach(([k, label]) => {
            if (p[k]) tiles.push(`<a class="day-tile" href="${escapeHtml(p[k].url)}" data-viewer="${label} — ${escapeHtml(day.date)}" title="View ${label}"><img src="${escapeHtml(p[k].url)}" alt="${label} ${escapeHtml(day.date)}" loading="lazy" /><span>${label}</span></a>`);
        });
        if (tiles.length) {
            products = `<div class="day-products">${tiles.join("")}</div>`;
            if (tl) products = `<div class="day-actions"><a href="${escapeHtml(tl.url)}" download class="btn btn-sm btn-outline">⬇️ Video · ${formatSize(tl.size_kb)}</a></div>` + products;
        }
    }

    const eventsHtml = evs.length
        ? `<div class="events-table-wrapper"><table class="events-table">
                <thead><tr><th>Image</th><th>Type</th><th>Time</th><th>Details</th><th>Whole sky</th></tr></thead>
                <tbody>${evs.map((i) => eventRowHtml(tableEvents[i], i)).join("")}</tbody>
            </table></div>`
        : `<p class="text-muted day-empty">No sky events.</p>`;

    return `
        <details class="day-block" ${first ? "open" : ""}>
            <summary class="day-summary">
                <span class="day-date">${escapeHtml(day.date)}</span>
                ${isToday ? `<span class="badge badge-accent">Today</span>` : ""}
                <span class="badge">${evs.length} ${evs.length === 1 ? "event" : "events"}</span>
                ${tl ? `<span class="text-muted day-meta">${tl.size_kb ? formatSize(tl.size_kb) + " video" : ""}</span>` : ""}
            </summary>
            <div class="day-body">${products}${eventsHtml}</div>
        </details>`;
}

// In-page image viewer for keograms / startrails (Esc, click or ✕ to close)
const viewer = document.createElement("div");
viewer.className = "image-viewer hidden";
viewer.innerHTML = `
    <button class="image-viewer-close" title="Close (Esc)">✕</button>
    <img alt="" />
    <div class="image-viewer-caption"></div>
`;
document.body.appendChild(viewer);
const viewerImg = viewer.querySelector("img");
const viewerCaption = viewer.querySelector(".image-viewer-caption");

function closeViewer() {
    viewer.classList.add("hidden");
    viewerImg.removeAttribute("src");
}

document.addEventListener("click", (e) => {
    const link = e.target.closest("a[data-viewer]");
    if (link) {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return; // keep open-in-new-tab
        e.preventDefault();
        viewerImg.src = link.getAttribute("href");
        viewerCaption.textContent = link.dataset.viewer;
        viewer.classList.remove("hidden");
    } else if (!viewer.classList.contains("hidden") && viewer.contains(e.target)) {
        closeViewer();
    }
});
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !viewer.classList.contains("hidden")) closeViewer();
});

const EVENT_LABELS = {
    meteor: "☄️ Meteor", plane: "✈️ Aircraft", satellite: "🛰️ Satellite", clouds: "☁️ Clouds", flash: "⚡ Flash",
};

function eventDetails(ev) {
    if (ev.type === "clouds") return `Sky turned ${escapeHtml(ev.state)} (${Number(ev.cloud_cover_pct).toFixed(0)}% cloud)`;
    if (ev.type === "flash") return `Sky brightness ${escapeHtml(ev.sky_mean_before)} → ${escapeHtml(ev.sky_mean)}`;
    const parts = [`${Number(ev.length_px || 0).toFixed(0)} px`];
    if (ev.frames > 1) parts.push(`${ev.frames} frames`);
    if (ev.dashes > 1) parts.push(`${ev.dashes} dashes`);
    if (ev.peak_dn) parts.push(`peak +${ev.peak_dn} DN`);
    return parts.join(" · ");
}

function escapeHtml(v) {
    return String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Flight named by ADS-B (callsign, altitude, distance), linked to a public flight tracker
function flightHtml(ev) {
    const fl = ev.flight;
    if (!fl) return "";
    const name = escapeHtml(fl.callsign || fl.icao);
    const url = `https://globe.adsbexchange.com/?icao=${encodeURIComponent(String(fl.icao).toLowerCase())}`;
    return `<a class="flight-tag" href="${url}" target="_blank" rel="noopener" title="ICAO ${escapeHtml(fl.icao)} · az ${escapeHtml(fl.az_deg)}° alt ${escapeHtml(fl.alt_deg)}° · match ${escapeHtml(fl.match_px)} px">✈ ${name}</a>`
        + ` <span class="flight-meta">${escapeHtml(Number(fl.altitude_m).toLocaleString())} m · ${escapeHtml(fl.distance_km)} km</span>`;
}

const EVENT_COLORS = { meteor: "#f59e0b", plane: "#38bdf8", satellite: "#a78bfa", clouds: "#94a3b8", flash: "#facc15" };

function eventDuration(ev) {
    const s = Number(ev.end_ts) - Number(ev.start_ts);
    return Number.isFinite(s) && s > 0.5 ? s : 0;
}

function fmtSeconds(s) {
    return s >= 90 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${s.toFixed(s < 10 ? 1 : 0)} s`;
}

// One sentence a non-astronomer understands
function eventSummary(ev) {
    const frames = Number(ev.frames || 1);
    const dur = eventDuration(ev);
    const fl = ev.flight;
    switch (ev.type) {
        case "meteor":
            return "A bright streak appeared in a single frame and was gone in the next: the signature of a meteor (shooting star). "
                + "Car lights, lit windows and glints on the lens can look similar, so check the close-up.";
        case "plane": {
            let t = ev.moving_dot
                ? `A light moved steadily across the sky for ${frames} frames${dur ? ` (${fmtSeconds(dur)})` : ""}: an aircraft.`
                : frames > 1 ? `A streak continued along the same line over ${frames} frames: an aircraft.`
                    : "A dashed streak (blinking navigation lights): an aircraft.";
            if (ev.dashes > 1 && ev.moving_dot) t += " Its lights blinked on the way.";
            if (fl) t += ` Identified from ADS-B as flight ${fl.callsign || fl.icao}, ${Number(fl.altitude_m).toLocaleString()} m up and ${fl.distance_km} km away.`;
            if (ev.reclassified_from) t += ` (First detected as a ${ev.reclassified_from}.)`;
            return t;
        }
        case "satellite":
            return `A faint, steady light crossed the sky over ${frames} frames${dur ? ` (${fmtSeconds(dur)})` : ""} without blinking: most likely a satellite catching sunlight.`;
        case "flash":
            return `The whole sky brightened at once (${ev.sky_mean_before} → ${ev.sky_mean}): lightning, or a light shining on the camera.`;
        case "clouds":
            return ev.state === "cloudy"
                ? `Clouds moved in: ${Number(ev.cloud_cover_pct).toFixed(0)}% of the sky is now covered.`
                : `The sky cleared: cloud cover dropped to ${Number(ev.cloud_cover_pct).toFixed(0)}%.`;
        default:
            return "";
    }
}

function eventFacts(ev) {
    const facts = [];
    const dur = eventDuration(ev);
    if (ev.type === "clouds") {
        facts.push(["Cloud cover", `${Number(ev.cloud_cover_pct).toFixed(0)}%`]);
    } else if (ev.type === "flash") {
        facts.push(["Sky brightness", `${ev.sky_mean_before} → ${ev.sky_mean}`]);
    } else {
        facts.push(["Seen in", `${ev.frames || 1} frame${(ev.frames || 1) > 1 ? "s" : ""}`]);
        if (dur) facts.push(["Duration", fmtSeconds(dur)]);
        if (ev.length_px) facts.push([ev.moving_dot ? "Path length" : "Streak length", `${Number(ev.length_px).toFixed(0)} px`]);
        if (ev.speed_px_s) facts.push(["Speed", `${Number(ev.speed_px_s).toFixed(0)} px/s`]);
        if (ev.peak_dn) facts.push(["Brightness", `+${ev.peak_dn} above the sky`]);
        if (ev.dashes > 1) facts.push([ev.moving_dot ? "Blinks" : "Dashes", String(ev.dashes)]);
    }
    if (ev.sky_from && ev.sky_to) {
        const d = (p) => `${COMPASS[Math.round(((p.az_deg % 360) + 360) % 360 / 45) % 8]} ${Math.round(p.alt_deg)}° up`;
        facts.push(["Where in the sky", ev.frames > 1 ? `${d(ev.sky_from)} → ${d(ev.sky_to)}` : d(ev.sky_from)]);
    }
    const fl = ev.flight;
    if (fl) {
        facts.push(["Flight", flightHtml(ev), true]); // true = already-escaped HTML
        facts.push(["Altitude", `${Number(fl.altitude_m).toLocaleString()} m`]);
        if (fl.speed_kmh != null) facts.push(["Ground speed", `${fl.speed_kmh} km/h`]);
        if (fl.heading_deg != null) facts.push(["Heading", `${fl.heading_deg}° (${COMPASS[Math.round(fl.heading_deg / 45) % 8]})`]);
        if (fl.climb_m_min != null && Math.abs(fl.climb_m_min) > 60) facts.push([fl.climb_m_min > 0 ? "Climbing" : "Descending", `${Math.abs(fl.climb_m_min)} m/min`]);
        facts.push(["Distance", `${fl.distance_km} km`]);
        facts.push(["Direction", `az ${fl.az_deg}°, ${fl.alt_deg}° above horizon`]);
    }
    return facts;
}

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
    flightsStatus.textContent = d.connected ? "Live from the ADS-B receiver" : "Receiver not connected";
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
    setInterval(() => { if (!document.hidden) loadFlights(); }, 10000);
}

if (currentPage === "tab-live" && liveEventsList) {
    loadLiveEvents();
    setInterval(() => { if (!document.hidden) loadLiveEvents(); }, 20000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) loadLiveEvents(); });
}

// Settings → Event detection
const eventToggles = [...document.querySelectorAll("input[data-event]")];

function syncEventConfigUI(data) {
    if (!data || !data.enabled) return;
    eventToggles.forEach((el) => { el.checked = !!data.enabled[el.dataset.event]; });
    const st = document.getElementById("evt-status");
    if (st) {
        st.textContent = data.night
            ? `Watching now: ${data.frames_compared} of ${data.frames_seen} frames compared tonight.`
            : "Daytime: only aircraft and flashes are watched (meteors and satellites need the dark).";
    }
}

async function loadEventConfig() {
    if (!eventToggles.length) return;
    try {
        const res = await fetch("/api/events/config");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        syncEventConfigUI(await res.json());
    } catch (err) {
        showAlert(stationAlert, `❌ Could not load event detection settings: ${err.message}`, "error", true);
    }
}

eventToggles.forEach((el) => {
    el.addEventListener("change", async () => {
        const ok = await settingsSave(el, async () => {
            syncEventConfigUI(await sendJson("/api/events/config", { [el.dataset.event]: el.checked }));
        });
        if (!ok) loadEventConfig();
    });
});
if (currentPage === "tab-settings") loadEventConfig();

// Settings → ADS-B flight matching
const adsbHost = document.getElementById("adsb-host");
const adsbPort = document.getElementById("adsb-port");
const adsbEnabled = document.getElementById("adsb-enabled");
const adsbStatus = document.getElementById("adsb-status");

const sondesEnabled = document.getElementById("sondes-enabled");

function syncAdsbUI(d) {
    if (!d || !adsbHost) return;
    if (document.activeElement !== adsbHost) adsbHost.value = d.host || "";
    if (document.activeElement !== adsbPort) adsbPort.value = d.port || 30003;
    adsbEnabled.checked = !!d.enabled;
    const st = d.status;
    let txt = !d.enabled ? "Off." : !st ? "Starting…"
        : st.connected ? `Connected: ${st.with_position} aircraft with position.`
        : st.last_error ? `Not connected: ${st.last_error}` : "Idle (reads the feed only at night with aircraft detection on).";
    if (d.enabled && !d.pointing_solved) txt += " Needs a star-solved camera pointing (plate solve) to match flights.";
    adsbStatus.textContent = txt;
    syncFr24UI(d);
    if (sondesEnabled) sondesEnabled.checked = d.radiosondes_enabled !== 0 && d.radiosondes_enabled !== false;
}

async function loadAdsb() {
    try {
        const res = await fetch("/api/adsb");
        if (res.ok) syncAdsbUI(await res.json());
    } catch (err) { /* optional feature */ }
}

// Local receiver: host, port and the switch save themselves (host may be empty = receiver off)
if (adsbHost) {
    autosave(adsbHost.closest("fieldset"), async () => {
        syncAdsbUI(await sendJson("/api/adsb", { host: adsbHost.value.trim(), port: Number(adsbPort.value) || 30003, enabled: adsbEnabled.checked }));
        setTimeout(loadAdsb, 3000);
    });
}
if (currentPage === "tab-settings") loadAdsb();

// Settings → Flightradar24: switch, optional account (e-mail + password, or a subscription token). The token and the
// password are write-only: the server only says whether a token is set. They are sent only when typed here.
const fr24Enabled = document.getElementById("fr24-enabled");
const fr24Email = document.getElementById("fr24-email");
const fr24Password = document.getElementById("fr24-password");
const fr24Token = document.getElementById("fr24-token");
const fr24TokenState = document.getElementById("fr24-token-state");
const fr24Clear = document.getElementById("btn-fr24-clear-token");
const fr24Status = document.getElementById("fr24-status");

let fr24Loaded = false;
function syncFr24UI(d) {
    if (!fr24Enabled || !d) return;
    fr24Loaded = true;
    fr24Enabled.checked = !!d.fr24_enabled;
    if (document.activeElement !== fr24Email) fr24Email.value = d.fr24_email || "";
    const has = !!d.fr24_has_token;
    fr24TokenState.textContent = has ? "Token saved" : "";
    fr24Clear.hidden = !has;
    fr24Token.placeholder = has ? "Paste a new token to replace it" : "";
    const f = d.fr24 || {};
    let txt;
    if (!d.fr24_enabled) txt = "Off.";
    else {
        txt = has || f.authenticated ? "Using your Flightradar24 account (subscription feed)." : "Using the public feed (no account).";
        if (f.last_error) txt += ` Last error: ${f.last_error}`;
        else if (f.last_fetch_age_s !== null && f.last_fetch_age_s !== undefined) txt += ` Last update ${Math.round(f.last_fetch_age_s)} s ago, ${f.global_total || 0} aircraft worldwide.`;
    }
    fr24Status.textContent = txt;
}

async function saveFr24(withSecrets) {
    const body = { fr24_enabled: fr24Enabled.checked };
    if (withSecrets) {
        const em = fr24Email.value.trim();
        if (em && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)) throw new Error("that is not an e-mail address");
        body.fr24_email = em;
    }
    // The password and token leave this page only on the explicit button (never on autosave: a browser's
    // password manager can fill these fields by itself, and a filled field must not be sent anywhere)
    const pw = withSecrets ? fr24Password.value : "";
    const tk = withSecrets ? fr24Token.value.trim() : "";
    if (!fr24Loaded) throw new Error("still loading, try again");
    if (pw && !body.fr24_email) throw new Error("enter the account e-mail with the password");
    if (pw) body.fr24_password = pw;
    if (tk) body.fr24_token = tk;
    const data = await sendJson("/api/adsb", body);
    if (withSecrets) {
        fr24Password.value = "";
        fr24Token.value = "";
    }
    syncAdsbUI(data);
    if (data.fr24_login && !data.fr24_login.ok) throw new Error(`Flightradar24 sign-in failed: ${data.fr24_login.message}`);
}

if (fr24Enabled) {
    autosave(fr24Enabled.closest("fieldset"), () => saveFr24(false));
    const fr24Signin = document.getElementById("btn-fr24-signin");
    if (fr24Signin) fr24Signin.addEventListener("click", async () => {
        await settingsSave(fr24Signin, () => saveFr24(true));
    });
    fr24Clear.addEventListener("click", async () => {
        await settingsSave(fr24Clear, async () => {
            syncAdsbUI(await sendJson("/api/adsb", { fr24_token: "" }));
        });
    });
}

// Settings → Weather balloons (SondeHub): one switch, saved on change
if (sondesEnabled) {
    autosave(sondesEnabled.closest("fieldset"), async () => {
        syncAdsbUI(await sendJson("/api/adsb", { radiosondes_enabled: sondesEnabled.checked }));
    });
}

// Settings → Detection → daytime objects (birds, insects, aircraft)
const dayObj = {
    group: document.getElementById("dayobj-group"),
    enabled: document.getElementById("dayobj-enabled"),
    classes: document.querySelectorAll("[data-dayobj-class]"),
    sens: document.getElementById("dayobj-sensitivity"),
    days: document.getElementById("dayobj-days"),
    mb: document.getElementById("dayobj-mb"),
    minA: document.getElementById("dayobj-min-area"),
    maxA: document.getElementById("dayobj-max-area"),
    contrast: document.getElementById("dayobj-contrast"),
    status: document.getElementById("dayobj-status"),
};
const DAYOBJ_NAMES = { plane: "aircraft", bird: "birds", insect: "insects", object: "other" };

function syncDayObjUI(d) {
    const c = d && d.config;
    if (!c || !dayObj.group) return;
    const set = (el, v) => { if (el && document.activeElement !== el) el.value = v; };
    dayObj.enabled.checked = !!c.enabled;
    dayObj.classes.forEach((el) => { el.checked = c.classes.includes(el.dataset.dayobjClass); });
    dayObj.sens.value = c.sensitivity;
    set(dayObj.days, c.retention_days);
    set(dayObj.mb, c.max_mb);
    set(dayObj.minA, c.min_blob_area);
    set(dayObj.maxA, c.max_blob_area);
    set(dayObj.contrast, c.min_contrast_dn === null ? "" : c.min_contrast_dn);
    if (d.presets && d.presets[c.sensitivity]) dayObj.contrast.placeholder = `${d.presets[c.sensitivity].min_contrast_dn} (from sensitivity)`;
}

async function loadDayObjStatus() {
    if (!dayObj.group) return;
    try {
        const res = await fetch("/api/day-objects/status");
        if (!res.ok) return;
        const st = await res.json();
        syncDayObjUI(st);
        const parts = [];
        if (!st.config.enabled) parts.push("Off.");
        else if (st.tracker && st.tracker.raining) parts.push("Rain on the dome: recording paused.");
        const midnight = new Date();
        midnight.setHours(0, 0, 0, 0);
        const r = await fetch(`/api/day-objects?t0=${midnight.getTime() / 1000}&t1=${Date.now() / 1000}&limit=20000`);
        if (r.ok) {
            const counts = {};
            ((await r.json()).tracks || []).forEach((t) => { counts[t.cls] = (counts[t.cls] || 0) + 1; });
            const txt = Object.keys(DAYOBJ_NAMES).filter((k) => counts[k]).map((k) => `${counts[k]} ${DAYOBJ_NAMES[k]}`).join(", ");
            parts.push(`Today: ${txt || "nothing recorded yet"}.`);
        }
        dayObj.status.textContent = ` ${parts.join(" ")}`;
    } catch (err) { /* optional feature */ }
}

if (dayObj.group) {
    autosave(dayObj.group, async () => {
        const body = {
            enabled: dayObj.enabled.checked,
            classes: [...dayObj.classes].filter((el) => el.checked).map((el) => el.dataset.dayobjClass),
            sensitivity: dayObj.sens.value,
            retention_days: Number(dayObj.days.value),
            max_mb: Number(dayObj.mb.value),
            min_blob_area: Number(dayObj.minA.value),
            max_blob_area: Number(dayObj.maxA.value),
            min_contrast_dn: dayObj.contrast.value.trim() === "" ? null : Number(dayObj.contrast.value),
        };
        for (const k of ["retention_days", "max_mb", "min_blob_area", "max_blob_area"]) {
            if (!Number.isFinite(body[k]) || body[k] <= 0) throw new Error("enter a number");
        }
        syncDayObjUI(await sendJson("/api/day-objects/config", body));
        loadDayObjStatus();
    });
    if (currentPage === "tab-settings") {
        loadDayObjStatus();
        setInterval(loadDayObjStatus, 60000);
    }
}

