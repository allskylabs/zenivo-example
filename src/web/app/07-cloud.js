/* Zenivo dashboard (part of the former app.js): Cloud cover history chart and cloud settings.
 * Classic script: top-level declarations are shared with the other src/web/app/*.js files; load order is fixed in index.html. */

// ------------------------------------------------------------------
// Cloud Cover History chart (inline SVG, refreshed every 5 min)
// ------------------------------------------------------------------
const cloudChart = document.getElementById("cloud-chart");
if (cloudChart && currentPage === "tab-live") {
    const SVG_NS = "http://www.w3.org/2000/svg";
    const cloudTip = document.getElementById("cloud-tooltip");
    const cloudEmpty = document.getElementById("cloud-empty");
    const cloudWrap = document.getElementById("cloud-chart-wrap");
    const rangeBtns = document.querySelectorAll(".cloud-ranges .btn-range");
    let cloudHours = 24;
    try {
        cloudHours = Number(localStorage.getItem("cloudHours")) || 24;
    } catch (e) {
        /* storage unavailable */
    }
    let cloudData = null;
    const mainStore = { geom: null };
    const fvStore = { geom: null };
    let cloudEvents = [];   // brief sky events inside the charted range
    let viewTs = null;      // moment shown in the frame viewer (marked on the chart)

    const svgEl = (tag, attrs, parent) => {
        const el = document.createElementNS(SVG_NS, tag);
        Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
        if (parent) parent.appendChild(el);
        return el;
    };
    const fmtTime = (t, withDate) => {
        const d = stationDate(new Date(t * 1000));
        const p = (n) => String(n).padStart(2, "0");
        const hm = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
        const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getUTCMonth()];
        return withDate ? `${p(d.getUTCDate())} ${mon} ${hm}` : hm;
    };

    function renderChart(svg, store) {
        if (!cloudData) return;
        const box = svg.getBoundingClientRect();
        const W = Math.max(280, Math.round(box.width));
        const H = Math.round(box.height) || 260;
        const LANE = 16;   // event ticks sit in a strip under the plot, above the time labels
        const m = { l: 40, r: 12, t: 10, b: 24 + LANE };
        const pw = W - m.l - m.r;
        const ph = H - m.t - m.b;
        const { start, end } = cloudData;
        const pts = cloudData.points.filter((p) => p.cc !== null && p.cc !== undefined);
        const x = (t) => m.l + ((t - start) / (end - start)) * pw;
        const y = (v) => m.t + (1 - v / 100) * ph;
        svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
        svg.replaceChildren();
        if (svg === cloudChart) cloudEmpty.classList.toggle("hidden", pts.length > 0);

        // Night bands from consecutive night samples
        const all = cloudData.points;
        const step = Math.max(cloudData.interval_s, (end - start) / 600);
        let runStart = null;
        all.forEach((p, i) => {
            const night = p.m === "night";
            if (night && runStart === null) runStart = p.t - step / 2;
            const next = all[i + 1];
            const endsHere = night && (!next || next.m !== "night" || next.t - p.t > step * 4);
            if (endsHere) {
                const x0 = Math.max(m.l, x(runStart));
                const x1 = Math.min(m.l + pw, x(p.t + step / 2));
                if (x1 > x0) svgEl("rect", { class: "night-band", x: x0, y: m.t, width: x1 - x0, height: ph }, svg);
                runStart = null;
            }
        });

        // Grid + y labels
        [0, 25, 50, 75, 100].forEach((v) => {
            svgEl("line", { class: "grid", x1: m.l, x2: m.l + pw, y1: y(v), y2: y(v) }, svg);
            const tx = svgEl("text", { class: "axis-label", x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, svg);
            tx.textContent = `${v}%`;
        });
        // X ticks at round local hours / days
        const span = end - start;
        const tickH = span <= 6 * 3600 ? 1 : span <= 26 * 3600 ? 3 : span <= 80 * 3600 ? 12 : span <= 170 * 3600 ? 24 : 120;
        const tickS = tickH * 3600;
        const tzOff = -STATION_UTC_OFFSET_MIN * 60;
        let t0 = Math.ceil((start - tzOff) / tickS) * tickS + tzOff;
        const maxTicks = Math.max(2, Math.floor(pw / 70));
        const stride = Math.max(1, Math.ceil(span / tickS / maxTicks));
        for (let t = t0, k = 0; t <= end; t += tickS, k++) {
            if (k % stride) continue;
            const tx = svgEl("text", { class: "axis-label", x: x(t), y: H - 6, "text-anchor": "middle" }, svg);
            tx.textContent = tickH >= 24 ? fmtTime(t, true).split(" ").slice(0, 2).join(" ") : fmtTime(t, false);
        }

        // Line segments, broken over gaps (camera off, overexposed frames)
        const gap = step * 3.5;
        const segs = [];
        pts.forEach((p, i) => {
            if (i === 0 || p.t - pts[i - 1].t > gap) segs.push([]);
            segs[segs.length - 1].push(p);
        });
        segs.forEach((seg) => {
            const line = seg.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.cc).toFixed(1)}`).join("");
            if (seg.length > 1) {
                svgEl("path", { class: "cc-area", d: `${line}L${x(seg[seg.length - 1].t).toFixed(1)},${y(0)}L${x(seg[0].t).toFixed(1)},${y(0)}Z` }, svg);
                svgEl("path", { class: "cc-line", d: line }, svg);
            } else {
                svgEl("circle", { class: "hover-dot", cx: x(seg[0].t), cy: y(seg[0].cc), r: 3 }, svg);
            }
        });
        // Low-confidence samples as hollow markers (only when sparse enough to read)
        if (pts.length <= 300) {
            pts.filter((p) => (p.conf ?? 1) < 0.5).forEach((p) => svgEl("circle", { class: "cc-low", cx: x(p.t), cy: y(p.cc), r: 3.5 }, svg));
        }
        // Event lane: one tick per meteor / aircraft / satellite / flash, drawn on top so it stays clickable
        const laneY = m.t + ph + 3;
        svgEl("rect", { class: "ev-lane", x: m.l, y: laneY, width: pw, height: LANE - 3 }, svg);
        const evs = cloudEvents.filter((e) => e.start_ts >= start && e.start_ts <= end);
        evs.forEach((e) => svgEl("rect", { class: `ev-tick ev-${e.type}`, x: (x(e.start_ts) - 1).toFixed(1), y: laneY + 1, width: 2, height: LANE - 5, rx: 0.5 }, svg));
        store.geom = { pts, x, y, m, pw, ph, W, H, laneY, LANE, evs, start, end };
        setMark(svg, store);
    }

    // The viewed moment as a dashed line on a chart (moved without redrawing the whole chart)
    function setMark(svg, store) {
        svg.querySelectorAll(".cc-mark").forEach((el) => el.remove());
        const g = store.geom;
        if (!g || viewTs === null || viewTs < g.start || viewTs > g.end) return;
        svgEl("line", { class: "cc-mark", x1: g.x(viewTs), x2: g.x(viewTs), y1: g.m.t, y2: g.laneY + g.LANE - 3 }, svg);
    }
    function renderCloudChart() {
        renderChart(cloudChart, mainStore);
        if (fv && !fv.classList.contains("hidden")) renderChart(fvChart, fvStore);
    }

    const SNAP_PX = 8;   // an event this close to the pointer (horizontally) is snapped to
    // Chart x position of a pointer, in the svg's own units, and the event (if any) close enough to snap to
    function pointerAt(svg, store, e) {
        const g = store.geom;
        if (!g) return null;
        const rect = svg.getBoundingClientRect();
        const px = ((e.clientX - rect.left) / rect.width) * g.W;
        const py = ((e.clientY - rect.top) / rect.height) * g.H;
        if (px < g.m.l - 4 || px > g.m.l + g.pw + 4) return null;
        let snap = null;
        g.evs.forEach((ev) => {
            const d = Math.abs(g.x(ev.start_ts) - px);
            if (d <= SNAP_PX && (!snap || d < snap[0])) snap = [d, ev];
        });
        return { g, rect, px, py, ev: snap && snap[1], t: cloudData.start + ((px - g.m.l) / g.pw) * (cloudData.end - cloudData.start) };
    }

    function showCloudTip(svg, store, tip, e) {
        const at = pointerAt(svg, store, e);
        svg.querySelectorAll(".crosshair, .hover-sel").forEach((el) => el.remove());
        if (!at || !at.g.pts.length) { tip.classList.add("hidden"); return; }
        const { g, rect, px } = at;
        svg.style.cursor = "pointer";
        if (at.ev) {   // snapped onto an event
            const cx = g.x(at.ev.start_ts);
            svgEl("line", { class: "crosshair", x1: cx, x2: cx, y1: g.m.t, y2: g.laneY + g.LANE - 3 }, svg);
            tip.innerHTML = `<strong>${escapeHtml(EV_NAMES[at.ev.type] || at.ev.type)}${at.ev.label ? " " + escapeHtml(at.ev.label) : ""}</strong><br>${fmtTime(at.ev.start_ts, true)} ${tzLabel}<br>click to view`;
            placeTip(tip, rect, cx, g.W);
            return;
        }
        let best = g.pts[0];
        g.pts.forEach((p) => {
            if (Math.abs(g.x(p.t) - px) < Math.abs(g.x(best.t) - px)) best = p;
        });
        const cx = g.x(best.t);
        svgEl("line", { class: "crosshair", x1: cx, x2: cx, y1: g.m.t, y2: g.m.t + g.ph }, svg);
        svgEl("circle", { class: "hover-dot hover-sel", cx, cy: g.y(best.cc), r: 5 }, svg);
        const mode = best.m === "night" ? "Night" : best.m === "day" ? "Day" : "";
        const extra = best.m === "night" ? ` · ${best.stars} stars` : "";
        const conf = (best.conf ?? 1) < 0.5 ? " · low confidence" : "";
        const avg = best.n > 1 ? ` · avg of ${best.n}` : "";
        tip.innerHTML = `<strong>${best.cc.toFixed(0)}%</strong> cloud<br>${fmtTime(best.t, true)} ${tzLabel}<br>${mode}${extra}${conf}${avg}`;
        placeTip(tip, rect, cx, g.W);
    }
    function placeTip(tip, rect, cx, W) {
        tip.classList.remove("hidden");
        const left = (cx / W) * rect.width;
        const tipW = tip.offsetWidth;
        tip.style.left = `${Math.min(rect.width - tipW - 4, Math.max(4, left + (left > rect.width / 2 ? -tipW - 12 : 12)))}px`;
    }

    // Hover shows the cloud value (or the event it snaps to); a click opens the frame at that moment / event
    function bindChart(svg, store, tip) {
        let downAt = null;
        svg.addEventListener("pointermove", (e) => showCloudTip(svg, store, tip, e));
        svg.addEventListener("pointerleave", () => {
            tip.classList.add("hidden");
            svg.querySelectorAll(".crosshair, .hover-sel").forEach((el) => el.remove());
        });
        svg.addEventListener("pointerdown", (e) => { downAt = [e.clientX, e.clientY]; });
        svg.addEventListener("pointerup", (e) => {
            if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 6) return;
            const at = pointerAt(svg, store, e);
            if (!at || at.py > at.g.laneY + at.g.LANE) return;
            if (at.ev) openFrameAt(at.ev.start_ts, at.ev);
            else openFrameAt(at.t, null);
        });
    }

    // ---- Frame viewer: the same chart with its events, plus play / pause / prev / next through the frames ----
    const fv = document.getElementById("frame-viewer");
    const fvImg = document.getElementById("fv-img");
    const fvMsg = document.getElementById("fv-msg");
    const fvChart = document.getElementById("fv-chart");
    const fvPlay = document.getElementById("fv-play");
    const fvSpeed = document.getElementById("fv-speed");
    let fvEvent = null;     // event the viewer was opened on (or is passing through while playing)
    let fvFrames = [];      // archived frames around the viewed moment, oldest first
    let fvIdx = 0;
    let fvTimer = null;
    let fvReq = 0;
    const EV_NAMES = { meteor: "Meteor", plane: "Aircraft", satellite: "Satellite", flash: "Flash" };
    const SPAN_BEFORE = 20 * 60, SPAN_AFTER = 60 * 60;

    const eventAt = (ts) => cloudEvents.find((e) => ts >= e.start_ts - 4 && ts <= Math.max(e.end_ts || 0, e.start_ts) + 4) || null;

    async function loadSpan(ts) {
        // A service restart or a flaky phone link fails the first call: retry before giving up
        let lastErr;
        for (let attempt = 0; attempt < 4; attempt++) {
            try {
                const r = await fetch(`/api/frames/span?start=${Math.floor(ts - SPAN_BEFORE)}&end=${Math.ceil(ts + SPAN_AFTER)}&max=900`);
                if (!r.ok) throw new Error("span");
                return (await r.json()).frames || [];
            } catch (e) {
                lastErr = e;
                await new Promise((res) => setTimeout(res, 1200 * (attempt + 1)));
            }
        }
        throw lastErr;
    }
    const nearestIdx = (frames, ts) => frames.reduce((bi, f, i) => (Math.abs(f.ts - ts) < Math.abs(frames[bi].ts - ts) ? i : bi), 0);

    function showFrame(i) {
        fvIdx = Math.max(0, Math.min(fvFrames.length - 1, i));
        const f = fvFrames[fvIdx];
        if (!f) return;
        viewTs = f.ts;
        fvImg.src = f.url;
        fvImg.classList.remove("hidden");
        fvMsg.classList.add("hidden");
        for (let k = 1; k <= 3 && fvIdx + k < fvFrames.length; k++) new Image().src = fvFrames[fvIdx + k].url;   // preload what plays next
        const ev = eventAt(f.ts);
        if (ev) fvEvent = ev; else if (fvEvent && (f.ts < fvEvent.start_ts - 4 || f.ts > Math.max(fvEvent.end_ts || 0, fvEvent.start_ts) + 60)) fvEvent = null;
        const near = cloudData && cloudData.points.length
            ? cloudData.points.reduce((a, b) => (Math.abs(b.t - f.ts) < Math.abs(a.t - f.ts) ? b : a)) : null;
        const cc = near && near.cc !== null && near.cc !== undefined && Math.abs(near.t - f.ts) < 1800 ? `${near.cc.toFixed(0)}% cloud` : "";
        document.getElementById("fv-title").textContent = ev
            ? `${EV_NAMES[ev.type] || ev.type}${ev.label ? " " + ev.label : ""} · ${fmtTime(ev.start_ts, true)} ${tzLabel}`
            : `Frame · ${fmtTime(f.ts, true)} ${tzLabel}`;
        const sec = (n) => String(n).padStart(2, "0");
        const d = stationDate(new Date(f.ts * 1000));
        document.getElementById("fv-meta").innerHTML =
            `<span><strong>${fmtTime(f.ts, true)}:${sec(d.getUTCSeconds())}</strong> ${tzLabel}</span>` +
            `<span>frame ${fvIdx + 1} / ${fvFrames.length}</span>${cc ? `<span>${escapeHtml(cc)}</span>` : ""}` +
            (ev && ev.end_ts ? `<span>event lasts ${Math.max(1, Math.round(ev.end_ts - ev.start_ts))} s</span>` : "");
        document.getElementById("fv-open").href = f.url;
        const crop = document.getElementById("fv-crop");
        const cropUrl = ev && (ev.sky_url || ev.thumbnail_url);
        crop.classList.toggle("hidden", !cropUrl);
        if (cropUrl) crop.href = cropUrl;
        setMark(cloudChart, mainStore);
        setMark(fvChart, fvStore);
        drawBoxes();
        markEventList(true);
    }

    const EV_COLORS = { meteor: "#f472b6", plane: "#fbbf24", satellite: "#34d399", flash: "#f87171" };
    const fvBoxes = document.getElementById("fv-boxes");
    // Where an event is at a moment: moving dots are interpolated along their path (line is the first -> last point,
    // normalised to the frame); a streak is drawn as the whole line
    // Archive frames are 30 s apart and their file times do not line up exactly with the detector's clock, so a
    // dot's place is found in the pixels: brighter than in both neighbouring frames, close to the event's path.
    const GW = 648, GH = 365;
    const grayCache = new Map();
    function grayOf(url) {
        if (!grayCache.has(url)) {
            if (grayCache.size > 12) grayCache.delete(grayCache.keys().next().value);
            grayCache.set(url, new Promise((resolve) => {
                const im = new Image();
                im.onload = () => {
                    const c = document.createElement("canvas");
                    c.width = GW; c.height = GH;
                    const g = c.getContext("2d", { willReadFrequently: true });
                    g.drawImage(im, 0, 0, GW, GH);
                    const d = g.getImageData(0, 0, GW, GH).data, out = new Float32Array(GW * GH);
                    for (let i = 0; i < out.length; i++) out[i] = d[i * 4] * 0.3 + d[i * 4 + 1] * 0.59 + d[i * 4 + 2] * 0.11;
                    resolve(out);
                };
                im.onerror = () => resolve(null);
                im.src = url;
            }));
        }
        return grayCache.get(url);
    }
    async function locateDot(e, i) {
        const cur = await grayOf(fvFrames[i].url);
        if (!cur) return null;
        const nb = (await Promise.all([i - 1, i + 1].filter((j) => j >= 0 && j < fvFrames.length).map((j) => grayOf(fvFrames[j].url)))).filter(Boolean);
        if (!nb.length) return null;
        const [x0, y0, x1, y1] = [e.line[0] * GW, e.line[1] * GH, e.line[2] * GW, e.line[3] * GH];
        const L2 = (x1 - x0) ** 2 + (y1 - y0) ** 2 || 1, R = 14;
        const dur = Math.max(1, (e.end_ts || e.start_ts) - e.start_ts);
        const u0 = (fvFrames[i].ts - e.start_ts) / dur, uw = 35 / dur;   // the clocks differ by seconds, not minutes
        let best = 0, bx = -1, by = -1;
        for (let y = 1; y < GH - 1; y++) {
            for (let x = 1; x < GW - 1; x++) {
                const t = ((x - x0) * (x1 - x0) + (y - y0) * (y1 - y0)) / L2;
                if (t < -0.06 || t > 1.06 || t < u0 - uw || t > u0 + uw) continue;
                const px = x0 + t * (x1 - x0) - x, py = y0 + t * (y1 - y0) - y;
                if (px * px + py * py > R * R) continue;
                const k = y * GW + x;
                let m = 0;
                for (const n of nb) m = Math.max(m, n[k], n[k - 1], n[k + 1], n[k - GW], n[k + GW]);
                const v = cur[k] - m;
                if (v > best) { best = v; bx = x; by = y; }
            }
        }
        return best >= 10 ? [bx / GW, by / GH] : null;
    }

    let boxReq = 0;
    async function drawBoxes() {
        const req = ++boxReq;
        const f = fvFrames[fvIdx], i = fvIdx;
        fvBoxes.innerHTML = "";
        if (!f || f.baked || !fvImg.naturalWidth) return;   // baked: the event's own image already has the box drawn in
        const wrap = fvImg.parentElement.getBoundingClientRect(), im = fvImg.getBoundingClientRect();
        const W = fvImg.naturalWidth, H = fvImg.naturalHeight;
        const k = Math.min(im.width / W, im.height / H);
        const w = W * k, h = H * k;   // object-fit: contain
        Object.assign(fvBoxes.style, { left: `${im.left - wrap.left + (im.width - w) / 2}px`, top: `${im.top - wrap.top + (im.height - h) / 2}px`, width: `${w}px`, height: `${h}px` });
        fvBoxes.setAttribute("viewBox", `0 0 ${W} ${H}`);
        const active = cloudEvents.filter((e) => e.line && f.ts >= e.start_ts - 30 && f.ts <= Math.max(e.end_ts || 0, e.start_ts) + 30);
        const found = await Promise.all(active.map((e) => (e.moving_dot ? locateDot(e, i) : null)));
        if (req !== boxReq) return;
        active.forEach((e, n) => {
            const [x0, y0, x1, y1] = e.line, c = EV_COLORS[e.type] || "#fff";
            const dur = (e.end_ts || e.start_ts) - e.start_ts;
            const name = `${EV_NAMES[e.type] || e.type}${e.label ? " " + e.label : ""}`;
            const inside = f.ts >= e.start_ts - 4 && f.ts <= (e.end_ts || e.start_ts) + 4;
            let bx, by, bw, bh, dim = false;
            if (e.moving_dot && dur > 0) {
                svgEl("line", { x1: x0 * W, y1: y0 * H, x2: x1 * W, y2: y1 * H, stroke: c, "stroke-width": 1, "stroke-dasharray": "5 4", opacity: 0.45, "vector-effect": "non-scaling-stroke" }, fvBoxes);
                let pos = found[n];
                if (!pos) {   // dot not visible in this frame (strobe off / too faint): fall back to the timed position
                    if (!inside) return;
                    const u = Math.max(0, Math.min(1, (f.ts - e.start_ts) / dur));
                    pos = [x0 + (x1 - x0) * u, y0 + (y1 - y0) * u];
                    dim = true;
                }
                const r = 0.02 * W;
                bx = pos[0] * W - r; by = pos[1] * H - r; bw = bh = 2 * r;
            } else {
                if (!inside) return;
                const pad = 0.015 * W;
                bx = Math.min(x0, x1) * W - pad; by = Math.min(y0, y1) * H - pad;
                bw = Math.abs(x1 - x0) * W + 2 * pad; bh = Math.abs(y1 - y0) * H + 2 * pad;
            }
            svgEl("rect", { x: bx, y: by, width: bw, height: bh, stroke: c, rx: 3, "stroke-dasharray": dim ? "4 3" : "none", opacity: dim ? 0.6 : 1 }, fvBoxes);
            const t = svgEl("text", { x: bx, y: Math.max(14, by - 6), fill: c, "font-size": 0.015 * W, opacity: dim ? 0.6 : 1 }, fvBoxes);
            t.textContent = name;
        });
    }

    // The night's events beside the viewer (same list as "Recent events"); the one in view is highlighted
    const fvList = document.getElementById("fv-events");
    function renderEventList() {
        const evs = cloudEvents.slice().sort((a, b) => b.start_ts - a.start_ts);
        fvList.innerHTML = evs.map((e) => {
            const bits = [e.flight ? `✈ ${escapeHtml(e.flight)}` : "", e.length_px ? `${Math.round(e.length_px)} px` : "",
                e.frames > 1 ? `${e.frames} frames` : "", e.peak_dn ? `peak +${e.peak_dn} DN` : ""].filter(Boolean).join(" · ");
            return `<li data-ts="${e.start_ts}" data-id="${escapeHtml(e.event_id || "")}" style="--pill:${EV_COLORS[e.type] || "#fff"}"><div class="ev-type">${EV_NAMES[e.type] || e.type}</div>` +
                `<div class="ev-time">${fmtTime(e.start_ts, true)}</div><div class="ev-detail">${bits}</div></li>`;
        }).join("");
        markEventList(false);
    }
    function markEventList(scroll) {
        let on = null;
        fvList.querySelectorAll("li").forEach((li) => {
            const hit = fvEvent && li.dataset.id === (fvEvent.event_id || "");
            li.classList.toggle("active", !!hit);
            if (hit) on = li;
        });
        if (on && scroll) on.scrollIntoView({ block: "nearest" });
    }
    fvList.addEventListener("click", (e) => {
        const li = e.target.closest("li[data-ts]");
        if (!li) return;
        const ev = cloudEvents.find((x) => (x.event_id || "") === li.dataset.id);
        openFrameAt(Number(li.dataset.ts), ev || null);
    });
    window.openFrameViewerAt = (ts, evId) => openFrameAt(ts, cloudEvents.find((x) => x.event_id === evId) || null);
    fvImg.addEventListener("load", drawBoxes);
    window.addEventListener("resize", drawBoxes);

    // Jump to a moment: (re)load the frames around it unless it is already inside the loaded window
    async function openFrameAt(ts, ev) {
        let fvLoadFailed = false;
        stopPlay();
        if (ev !== undefined) fvEvent = ev;
        fv.classList.remove("hidden");
        renderChart(fvChart, fvStore);
        renderEventList();
        const req = ++fvReq;
        const inside = fvFrames.length && ts >= fvFrames[0].ts - 30 && ts <= fvFrames[fvFrames.length - 1].ts + 30;
        if (!inside) {
            fvMsg.textContent = "Loading…";
            fvMsg.classList.remove("hidden");
            try {
                const frames = await loadSpan(ts);
                if (req !== fvReq) return;
                fvFrames = frames;
            } catch (e) {
                fvFrames = [];
                fvLoadFailed = true;
            }
            if (req !== fvReq) return;
        }
        if (!fvFrames.length) {
            fvImg.classList.add("hidden");
            fvMsg.textContent = fvLoadFailed ? "Could not load the archived frames (the server did not answer). Tap the event again."
                : "No archived frame near that time (frames are kept for a limited number of days).";
            fvMsg.classList.remove("hidden");
            return;
        }
        // The archive keeps one frame per 30 s, so a short event (a meteor) usually falls between two of them. The
        // event's own whole-sky image (taken from the live stream at that moment) is slotted in as a frame so the viewer shows it.
        if (ev && ev.sky_url && !fvFrames.some((f) => f.ev_id === ev.event_id)) {
            const near = fvFrames[nearestIdx(fvFrames, ev.start_ts)];
            if (!near || Math.abs(near.ts - ev.start_ts) > 4) {
                const at = fvFrames.findIndex((f) => f.ts > ev.start_ts);
                fvFrames.splice(at < 0 ? fvFrames.length : at, 0, { ts: ev.start_ts, url: ev.sky_url, ev_id: ev.event_id, baked: true });
            }
        }
        showFrame(nearestIdx(fvFrames, ts));
    }

    // Step one frame; at the edge of the loaded window load the next stretch and carry on
    async function stepFrame(dir) {
        const j = fvIdx + dir;
        if (j >= 0 && j < fvFrames.length) { showFrame(j); return true; }
        if (!fvFrames.length) return false;
        const edge = fvFrames[fvIdx].ts, req = ++fvReq;
        try {
            const frames = await loadSpan(dir > 0 ? edge + SPAN_AFTER / 2 : edge - SPAN_AFTER / 2);
            if (req !== fvReq || !frames.length) return false;
            const k = nearestIdx(frames, edge);
            const next = k + dir;
            if (next < 0 || next >= frames.length) return false;
            fvFrames = frames;
            showFrame(next);
            return true;
        } catch (e) {
            return false;
        }
    }

    function stopPlay() {
        if (fvTimer) clearInterval(fvTimer);
        fvTimer = null;
        fvPlay.textContent = "▶ Play";
        fvPlay.classList.remove("active");
    }
    function startPlay() {
        if (!fvFrames.length) return;
        stopPlay();
        fvPlay.textContent = "⏸ Pause";
        fvPlay.classList.add("active");
        let busy = false;
        fvTimer = setInterval(async () => {
            if (busy) return;
            busy = true;
            if (!(await stepFrame(1))) stopPlay();   // reached the newest frame
            busy = false;
        }, 1000 / Number(fvSpeed.value));
    }
    function jumpEvent(dir) {
        if (viewTs === null) return;
        const evs = cloudEvents.slice().sort((a, b) => a.start_ts - b.start_ts);
        const ev = dir > 0 ? evs.find((e) => e.start_ts > viewTs + 2) : evs.slice().reverse().find((e) => e.start_ts < viewTs - 2);
        if (ev) openFrameAt(ev.start_ts, ev);
    }
    function closeFrameViewer() {
        fvReq++;
        stopPlay();
        fv.classList.add("hidden");
        viewTs = null;
        fvEvent = null;
        fvFrames = [];
        renderCloudChart();
    }
    document.getElementById("fv-close").addEventListener("click", closeFrameViewer);
    fv.addEventListener("click", (e) => { if (e.target === fv) closeFrameViewer(); });
    fvPlay.addEventListener("click", () => (fvTimer ? stopPlay() : startPlay()));
    fvSpeed.addEventListener("change", () => { if (fvTimer) startPlay(); });
    document.getElementById("fv-prev").addEventListener("click", () => { stopPlay(); stepFrame(-1); });
    document.getElementById("fv-next").addEventListener("click", () => { stopPlay(); stepFrame(1); });
    document.getElementById("fv-prev-ev").addEventListener("click", () => jumpEvent(-1));
    document.getElementById("fv-next-ev").addEventListener("click", () => jumpEvent(1));
    document.addEventListener("keydown", (e) => {
        if (fv.classList.contains("hidden")) return;
        if (e.key === "Escape") closeFrameViewer();
        else if (e.key === " ") { e.preventDefault(); fvTimer ? stopPlay() : startPlay(); }
        else if (e.key === "ArrowLeft") { stopPlay(); stepFrame(-1); }
        else if (e.key === "ArrowRight") { stopPlay(); stepFrame(1); }
    });
    bindChart(cloudChart, mainStore, cloudTip);
    bindChart(fvChart, fvStore, document.getElementById("fv-tooltip"));

    async function loadCloudHistory() {
        try {
            const maxPoints = Math.min(1200, Math.max(100, Math.round(cloudWrap.clientWidth / 2)));
            const res = await fetch(`/api/sky/clouds/history?hours=${cloudHours}&max_points=${maxPoints}`);
            if (!res.ok) return;
            cloudData = await res.json();
            const sm = cloudData.summary || {};
            const pct = (v) => (v === null || v === undefined ? "--" : `${Number(v).toFixed(0)}%`);
            document.getElementById("cloud-mean").textContent = pct(sm.mean);
            document.getElementById("cloud-clear").textContent = pct(sm.clear_pct);
            document.getElementById("cloud-range").textContent = sm.min === null || sm.min === undefined ? "--" : `${pct(sm.min)} – ${pct(sm.max)}`;
            document.getElementById("cloud-samples").textContent = sm.samples ?? "--";
            const avgEl = document.getElementById("cloud-avg");
            if (avgEl) avgEl.textContent = pct(sm.mean);
            const avgLbl = document.getElementById("cloud-avg-label");
            if (avgLbl) { const act = document.querySelector(".cloud-ranges .btn-range.active"); avgLbl.textContent = act ? act.textContent.trim() : "24h"; }
            try {
                const er = await fetch(`/api/events?since=${cloudData.start}&until=${cloudData.end}&limit=3000&brief=true`);
                cloudEvents = er.ok ? ((await er.json()).events || []).filter((e) => e.type !== "clouds") : [];
            } catch (e) {
                cloudEvents = [];
            }
            renderCloudChart();
        } catch (e) {
            /* keep the previous chart */
        }
    }
    rangeBtns.forEach((btn) => {
        btn.classList.toggle("active", Number(btn.dataset.hours) === cloudHours);
        btn.addEventListener("click", () => {
            cloudHours = Number(btn.dataset.hours);
            rangeBtns.forEach((b) => b.classList.toggle("active", b === btn));
            try {
                localStorage.setItem("cloudHours", String(cloudHours));
            } catch (e) {
                /* storage unavailable */
            }
            loadCloudHistory();
        });
    });
    let resizeTimer = null;
    window.addEventListener("resize", () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(renderCloudChart, 150);
    });
    loadCloudHistory();
    setInterval(() => {
        if (document.hidden) return;
        loadCloudHistory();
    }, 300000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) loadCloudHistory(); });
}

// ------------------------------------------------------------------
// Cloud cover settings (sky region + learned reference)
// ------------------------------------------------------------------
const roiInputs = document.querySelectorAll("input[data-roi]");
const cloudRefNote = document.getElementById("cloud-ref-note");
const btnCloudRefReset = document.getElementById("btn-cloud-ref-reset");

function syncCloudConfigUI(cfg) {
    if (!cfg || !cfg.sky_roi) return;
    roiInputs.forEach((inp) => {
        if (document.activeElement !== inp) inp.value = Math.round(cfg.sky_roi[Number(inp.dataset.roi)] * 100);
    });
    if (cloudRefNote && cfg.reference) {
        const r = cfg.reference;
        cloudRefNote.textContent = `Clear-sky template learned from ${r.night_samples} dark-night samples${r.learned ? "" : " (still learning)"}.`;
    }
}

async function postCloudConfig(body) {
    try {
        const res = await fetch("/api/sky/clouds/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error(JSON.stringify(data.detail || "Unknown error"));
        syncCloudConfigUI(data);
        return true;
    } catch (err) {
        showAlert(stationAlert, `❌ Failed to save cloud settings: ${err.message}`, "error", true);
        return false;
    }
}

if (roiInputs.length && currentPage === "tab-settings") {
    fetch("/api/sky/clouds/config")
        .then((res) => (res.ok ? res.json() : null))
        .then(syncCloudConfigUI)
        .catch(() => {});
    roiInputs.forEach((inp) =>
        inp.addEventListener("change", () => {
            const roi = [0, 0, 1, 1];
            roiInputs.forEach((el) => (roi[Number(el.dataset.roi)] = Number(el.value) / 100));
            if (roi.every(Number.isFinite)) postCloudConfig({ sky_roi: roi });
        })
    );
    if (btnCloudRefReset) {
        btnCloudRefReset.addEventListener("click", async () => {
            if (!confirm("Forget the learned clear-sky reference? Cloud cover is less accurate until it relearns (a few nights).")) return;
            if (await postCloudConfig({ reset_reference: true })) showAlert(stationAlert, "✅ Clear-sky reference reset.", "success", true);
        });
    }
}

// ------------------------------------------------------------------
