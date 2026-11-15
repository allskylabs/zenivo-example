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
