/* Zenivo dashboard (part of the former app.js): Archive (Products / By day / Events), timelapse builder and shared event helpers.
 * Classic script: top-level declarations are shared with the other src/web/app/*.js files; load order is fixed in index.html. */

// Archive page: Products | By day | Events (rebuilt, icons first). The Events list and the detail viewer live in 08b-events-live.js.
// ------------------------------------------------------------------
const arcIcon = (n) => `<svg class="ds-icon" aria-hidden="true"><use href="/static/icons.svg#i-${n}"></use></svg>`;
const galleryDateEl = document.getElementById("gallery-date-t");
const galleryFrameCountEl = document.getElementById("gallery-frame-count");
const galleryAlert = document.getElementById("gallery-alert");
const galleryContainer = document.getElementById("gallery-container");
const galleryTabs = document.getElementById("gallery-tabs");
let galleryHistory = [];
let eventsMeta = { total_count: 0, enabled: {}, counts: {} };

// Zoom + pan for timelapse videos (zoom_pan.js); the wheel needs Ctrl/Cmd here so the page still scrolls over the tiles
const cardZooms = [], tlZooms = [];
function destroyZooms(list) { list.splice(0).forEach((z) => z.destroy()); }
function zoomVideo(video, list) {
    if (!video || !video.parentElement || !window.ZenivoZoomPan) return;
    const zp = ZenivoZoomPan.attach(video.parentElement, video, { pill: "hover", wheel: "ctrl" });
    if (zp) list.push(zp);
}

function formatSize(kb) {
    return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb.toFixed(0)} KB`;
}

function dayKeyOf(date) {
    const p = (n) => String(n).padStart(2, "0");
    const sd = stationDate(date);
    return `${sd.getUTCFullYear()}-${p(sd.getUTCMonth() + 1)}-${p(sd.getUTCDate())}`;
}

// A state block with an icon, a short title and an optional retry (ds-empty)
function arcState(icon, title, retryId) {
    return `<div class="ds-empty">${arcIcon(icon)}<p class="ds-empty__title">${escapeHtml(title)}</p>`
        + (retryId ? `<button type="button" class="ds-btn" id="${retryId}" aria-label="Retry" title="Retry">${arcIcon("rotate-ccw")}</button>` : "") + `</div>`;
}

// ---------- view switch (segmented control) ----------
function setGalleryMode(mode) {
    if (!galleryContainer || !galleryTabs) return;
    galleryContainer.dataset.gmode = mode;
    galleryTabs.querySelectorAll(".arc-seg").forEach((b) => b.setAttribute("aria-selected", b.dataset.gmode === mode ? "true" : "false"));
    ["products", "day", "events"].forEach((m) => { const p = document.getElementById(`pane-${m}`); if (p) p.hidden = m !== mode; });
    const rb = document.getElementById("btn-arc-range"), sb = document.getElementById("btn-arc-seen");
    if (rb) rb.hidden = mode !== "events";
    if (sb) sb.hidden = mode !== "events";
    if (mode !== "events") { const r = document.getElementById("arc-range"); if (r) r.hidden = true; if (rb) rb.setAttribute("aria-expanded", "false"); }
    if (mode !== "events" && typeof closeEventViewer === "function") closeEventViewer();
    try { localStorage.setItem("galleryMode", mode); } catch (e) { /* ignore */ }
}
if (galleryTabs) {
    galleryTabs.addEventListener("click", (e) => {
        const b = e.target.closest(".arc-seg");
        if (b) setGalleryMode(b.dataset.gmode);
    });
    let savedMode = "products";
    try { savedMode = localStorage.getItem("galleryMode") || "products"; } catch (e) { /* ignore */ }
    setGalleryMode(["products", "day", "events"].includes(savedMode) ? savedMode : "products");
}

// ---------- Products ----------
function renderGallery(data) {
    productsGrid.innerHTML = "";
    destroyZooms(cardZooms);
    if (galleryDateEl && data && data.date) galleryDateEl.textContent = `${data.date} ${tzLabel}`;
    if (galleryFrameCountEl && data && data.archived_frames_count !== undefined) galleryFrameCountEl.textContent = String(data.archived_frames_count);
    galleryHistory = (data && data.history) || [];
    if (!data || !data.items || data.items.length === 0) {
        productsGrid.innerHTML = arcState("film", "No products yet");
        return;
    }
    const cacheBust = Date.now();
    data.items.forEach((item) => {
        const card = document.createElement("figure");
        card.className = "arc-card";
        const mediaUrl = `${item.url}?t=${cacheBust}`;
        const title = item.title || item.type;
        const nRejected = item.rejected ? Object.values(item.rejected).reduce((a, b) => a + b, 0) : 0;
        const media = item.type === "timelapse"
            ? `<video controls autoplay loop muted playsinline preload="metadata" src="${escapeHtml(mediaUrl)}" aria-label="${escapeHtml(title)}"></video>`
            : `<a href="${escapeHtml(mediaUrl)}" data-viewer="${escapeHtml(title)}" aria-label="${escapeHtml(title)}"><img src="${escapeHtml(mediaUrl)}" alt="${escapeHtml(title)}" loading="lazy" /></a>`;
        const badges = [`<span class="arc-badge">${arcIcon(item.type === "timelapse" ? "film" : "image")}${escapeHtml(title)}</span>`];
        if (item.frame_count !== undefined) badges.push(`<span class="arc-badge" title="Frames${nRejected ? `, ${nRejected} rejected` : ""}">${arcIcon("layers")}${item.frame_count}</span>`);
        if (item.size_kb) badges.push(`<span class="arc-badge">${formatSize(item.size_kb)}</span>`);
        card.innerHTML = `<div class="arc-card__media">${media}<div class="arc-card__bar">${badges.join("")}</div></div>
            <figcaption class="arc-card__foot"><span>${item.timestamp ? escapeHtml(fmtLocalIso(item.timestamp)) : ""}</span>
            <a class="ds-btn ds-btn--ghost ds-btn--icon" href="${escapeHtml(mediaUrl)}" download aria-label="Download ${escapeHtml(title)}" title="Download">${arcIcon("download")}</a></figcaption>`;
        productsGrid.appendChild(card);
        if (item.type === "timelapse") { attachVideoInfo(card.querySelector("video"), item.info_url); zoomVideo(card.querySelector("video"), cardZooms); }
    });
}

// ---------- By day: one card per night, newest first, empty days collapsed ----------
function renderDays() {
    if (!dayList) return;
    const todayKey = dayKeyOf(new Date());
    const byDay = new Map();
    const ensure = (k) => { if (!byDay.has(k)) byDay.set(k, { date: k, product: null, events: 0 }); return byDay.get(k); };
    galleryHistory.forEach((d) => { ensure(d.date).product = d; });
    (typeof arcEvents !== "undefined" ? arcEvents : []).forEach((ev) => { ensure(dayKeyOf(new Date(ev.start_ts * 1000))).events++; });
    const days = [...byDay.values()].filter((d) => d.product || d.events).sort((a, b) => (a.date < b.date ? 1 : -1));
    if (!days.length) { dayList.innerHTML = arcState("calendar-days", "No days archived yet"); return; }
    dayList.innerHTML = days.map((day) => {
        const p = day.product || {};
        const utc = new Date(`${day.date}T00:00:00Z`);
        const label = utc.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
        const thumbs = [];
        if (p.timelapse) thumbs.push(`<div class="arc-thumb"><video controls loop muted playsinline preload="metadata" src="${escapeHtml(p.timelapse.url)}#t=0.5" aria-label="Timelapse ${escapeHtml(day.date)}"></video><span class="arc-badge">${arcIcon("film")}</span></div>`);
        [["keogram", "Keogram", "activity"], ["startrails", "Startrails", "sparkles"]].forEach(([k, name, ic]) => {
            if (p[k]) thumbs.push(`<a class="arc-thumb" href="${escapeHtml(p[k].url)}" data-viewer="${name} — ${escapeHtml(day.date)}" aria-label="${name} ${escapeHtml(day.date)}"><img src="${escapeHtml(p[k].url)}" alt="${name} ${escapeHtml(day.date)}" loading="lazy" /><span class="arc-badge">${arcIcon(ic)}</span></a>`);
        });
        // missing products: one labelled row (the day's products are built after the night ends), never blank tiles
        const missing = [["timelapse", "Timelapse", "film"], ["keogram", "Keogram", "activity"], ["startrails", "Startrails", "sparkles"]].filter(([k]) => !p[k]);
        const pending = day.date >= todayKey || (day.date === dayKeyOf(new Date(Date.now() - 86400000)) && new Date().getHours() < 7);
        if (missing.length === 3) thumbs.length = 0, thumbs.push(`<div class="arc-thumb arc-thumb--none">${arcIcon(pending ? "clock" : "image-off")}<span>${pending ? "Products build after the night ends" : "No products for this night"}</span></div>`);
        else missing.forEach(([, name, ic]) => thumbs.push(`<div class="arc-thumb arc-thumb--empty">${arcIcon(ic)}<span>${name}</span></div>`));
        const evChip = day.events
            ? `<button type="button" class="ds-chip" data-arc-day="${escapeHtml(day.date)}" aria-label="${day.events} events on ${escapeHtml(day.date)}" title="Show events">${arcIcon("zap")}<b>${day.events}</b></button>` : "";
        return `<section class="arc-day"><header class="arc-day__head">${arcIcon("moon-star")}<b>${escapeHtml(label)}</b>${day.date === todayKey ? `<span class="arc-badge">${arcIcon("clock")}</span>` : ""}${evChip}</header><div class="arc-day__thumbs">${thumbs.join("")}</div></section>`;
    }).join("");
}
// thumbnails: skeleton until loaded, error icon on failure (capture phase: load/error do not bubble)
if (dayList) {
    dayList.addEventListener("loadeddata", (e) => { const t = e.target.closest && e.target.closest(".arc-thumb"); if (t) t.classList.add("is-loaded"); }, true);
    dayList.addEventListener("load", (e) => { const t = e.target.closest && e.target.closest(".arc-thumb"); if (t) t.classList.add("is-loaded"); }, true);
    dayList.addEventListener("error", (e) => { const t = e.target.closest && e.target.closest(".arc-thumb"); if (!t) return; t.classList.add("is-error"); e.target.remove(); t.insertAdjacentHTML("afterbegin", `${arcIcon("image-off")}<span>Could not load</span>`); }, true);
}
if (dayList) dayList.addEventListener("click", (e) => {
    const b = e.target.closest("[data-arc-day]");
    if (b && typeof arcShowDay === "function") arcShowDay(b.dataset.arcDay);
});

async function loadGalleryAndEvents() {
    productsGrid.innerHTML = `<div class="ds-skeleton arc-sk"></div><div class="ds-skeleton arc-sk"></div>`;
    try {
        const resGal = await fetch("/api/gallery");
        if (!resGal.ok) throw new Error(`HTTP ${resGal.status}`);
        renderGallery(await resGal.json());
    } catch (err) {
        console.error("Failed to load gallery:", err);
        productsGrid.innerHTML = arcState("triangle-alert", "Could not load products", "btn-gal-retry");
        const r = document.getElementById("btn-gal-retry");
        if (r) r.addEventListener("click", loadGalleryAndEvents);
    }
    if (typeof loadEvents === "function") await loadEvents();
    renderDays();
}
if (currentPage === "tab-gallery") setTimeout(() => loadGalleryAndEvents(), 0);
const arcRefresh = document.getElementById("btn-arc-refresh");
if (arcRefresh) arcRefresh.addEventListener("click", () => loadGalleryAndEvents());

// ---------- Timelapse builder (sheet): date range -> POST /api/timelapse/build; today only -> /api/gallery/generate ----------
const tlBuild = document.getElementById("tl-build");
if (tlBuild) {
    const tlFrom = document.getElementById("tl-from"), tlTo = document.getElementById("tl-to"), tlResult = document.getElementById("tl-result");
    tlTo.value = dayKeyOf(new Date());
    tlFrom.value = dayKeyOf(new Date(Date.now() - 86400000));
    tlBuild.addEventListener("click", async () => {
        const fps = document.getElementById("tl-fps").value || "24";
        const todayOnly = tlFrom.value === tlTo.value && tlTo.value === dayKeyOf(new Date());
        tlBuild.disabled = true;
        tlResult.hidden = false;
        destroyZooms(tlZooms);
        tlResult.innerHTML = `<div class="ds-skeleton arc-sk"></div>`;
        try {
            let res;
            if (todayOnly) {
                res = await fetch(`/api/gallery/generate?fps=${encodeURIComponent(fps)}`, { method: "POST" });
            } else {
                const q = new URLSearchParams({ date_from: tlFrom.value, date_to: tlTo.value, fps, width: document.getElementById("tl-res").value,
                    overlay: document.getElementById("tl-overlay").checked ? "true" : "false" });
                res = await fetch(`/api/timelapse/build?${q}`, { method: "POST" });
            }
            const d = await res.json();
            if (!res.ok) throw new Error(d.detail || "unknown error");
            if (todayOnly) {
                tlResult.innerHTML = arcState("circle-check", "Done");
                if (d.gallery) renderGallery(d.gallery); else loadGalleryAndEvents();
            } else {
                const url = `${d.timelapse_url}?t=${Date.now()}`;
                tlResult.innerHTML = `<div class="arc-tl__meta"><span class="arc-badge">${arcIcon("layers")}${d.video_frames_count}</span><span class="arc-badge">${arcIcon("clock")}${d.duration_s}s</span><span class="arc-badge">${formatSize(d.size_kb)}</span>`
                    + `<a class="ds-btn ds-btn--ghost ds-btn--icon" href="${escapeHtml(url)}" download aria-label="Download timelapse" title="Download">${arcIcon("download")}</a></div>`
                    + `<div class="tl-zoom"><video controls loop muted playsinline src="${escapeHtml(url)}"></video></div>`;
                zoomVideo(tlResult.querySelector("video"), tlZooms);
            }
        } catch (err) {
            tlResult.innerHTML = arcState("triangle-alert", `Build failed: ${err.message}`);
        } finally {
            tlBuild.disabled = false;
        }
    });
}

// ---------- Keogram / startrails viewer (Esc or the close icon) ----------
const viewer = document.createElement("div");
viewer.className = "arc-iv";
viewer.setAttribute("role", "dialog");
viewer.setAttribute("aria-modal", "true");
viewer.setAttribute("aria-label", "Image");
viewer.innerHTML = `<button type="button" class="ds-btn ds-btn--icon arc-iv__close" aria-label="Close" title="Close (Esc)">${arcIcon("x")}</button><div class="iv-zoom"><img alt="" draggable="false" /></div>`;
document.body.appendChild(viewer);
const viewerImg = viewer.querySelector("img");
const viewerZoom = window.ZenivoZoomPan ? ZenivoZoomPan.attach(viewer.querySelector(".iv-zoom"), viewerImg, { active: () => viewer.dataset.open === "true" }) : null;
function closeViewer() {
    if (viewerZoom) viewerZoom.reset();
    viewer.dataset.open = "false";
    viewerImg.removeAttribute("src");
}
document.addEventListener("click", (e) => {
    const link = e.target.closest("a[data-viewer]");
    if (link) {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;   // keep open-in-new-tab
        e.preventDefault();
        if (viewerZoom) viewerZoom.reset();
        viewerImg.src = link.getAttribute("href");
        viewerImg.alt = link.dataset.viewer || "";
        viewer.dataset.open = "true";
    } else if (viewer.dataset.open === "true" && (e.target.closest(".arc-iv__close") || e.target === viewer)) {
        closeViewer();
    }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && viewer.dataset.open === "true") closeViewer(); });

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
    if (ev.satellite && ev.satellite.name) facts.push(["Satellite", ev.satellite.name]);
    if (ev.confidence != null) facts.push(["Confidence", `${Math.round(ev.confidence * 100)}%`]);
    if (ev.sky_from && ev.sky_to) {
        const d = (p) => `${COMPASS[Math.round(((p.az_deg % 360) + 360) % 360 / 45) % 8]} ${Math.round(p.alt_deg)}° up`;
        facts.push(["Where in the sky", ev.frames > 1 ? `${d(ev.sky_from)} → ${d(ev.sky_to)}` : d(ev.sky_from)]);
    }
    return facts;
}

// Matched aircraft (ADS-B) rows for the event detail card
function flightFacts(ev) {
    const fl = ev.flight;
    const rows = [];
    if (!fl) return rows;
    rows.push(["Altitude", `${Number(fl.altitude_m).toLocaleString()} m`]);
    if (fl.speed_kmh != null) rows.push(["Ground speed", `${fl.speed_kmh} km/h`]);
    if (fl.heading_deg != null) rows.push(["Heading", `${fl.heading_deg}° (${COMPASS[Math.round(fl.heading_deg / 45) % 8]})`]);
    if (fl.climb_m_min != null && Math.abs(fl.climb_m_min) > 60) rows.push([fl.climb_m_min > 0 ? "Climbing" : "Descending", `${Math.abs(fl.climb_m_min)} m/min`]);
    rows.push(["Distance", `${fl.distance_km} km`]);
    rows.push(["Direction", `az ${fl.az_deg}°, ${fl.alt_deg}° above horizon`]);
    return rows;
}
