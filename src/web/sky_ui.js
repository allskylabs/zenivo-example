/* Sky page chrome: icon rail, layers sheet, time bar/sheet and the tap-for-info card, driven by the planetarium core (window.__sky.core).
 * The bundle's own panels (.sv-ui) are hidden by css/sky.css; drawing, projection and the first-paint guards stay in the bundle. */
(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    const stage = $("stage");
    if (!stage) return;
    const ic = (n) => `<svg class="ds-icon" aria-hidden="true"><use href="/static/icons.svg#i-${n}"></use></svg>`;
    const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

    /* [layer key, icon, short label] per group (same keys and grouping as the bundle) */
    const GROUPS = [
        ["Sky", "sparkles", [["stars", "star", "Stars"], ["milkyWay", "sparkles", "Milky Way"], ["daylightAtmosphere", "cloud", "Daylight"], ["meteors", "zap", "Meteors"]]],
        ["Objects", "telescope", [["moon", "sun", "Sun, Moon"], ["planets", "telescope", "Planets"], ["starLabels", "type", "Star names"], ["deepSkyObjects", "crosshair", "Messier"]]],
        ["Constellations", "star", [["constellationLines", "activity", "Lines"], ["constellationLabels", "type", "Names"]]],
        ["Live data", "radio", [["camera", "camera", "Camera frame"], ["cameraImage", "image", "Camera photo"], ["aircraft", "plane", "Aircraft"], ["satellites", "satellite", "Satellites"], ["sondes", "balloon", "Sondes"], ["events", "bell", "Events"]]],
        ["Horizon", "compass", [["cardinalDirections", "compass", "N E S W"], ["seeThroughGround", "eye", "Below horizon"]]],
        ["Lines and grids", "layout-grid", [["ecliptic", "activity", "Ecliptic"], ["equator", "minus", "Equator"], ["meridian", "map", "Meridian"], ["altAzGrid", "layout-grid", "Alt-az grid"], ["raGrid", "layout-grid", "RA grid"], ["decGrid", "layout-grid", "Dec grid"], ["polarAxis", "crosshair", "Poles"], ["calendarPoints", "calendar-days", "Seasons"]]],
    ];
    const SPEEDS = [1, 10, 60, 300, 1000];
    const STEPS = [[-86400000, "−1 d"], [-3600000, "−1 h"], [-600000, "−10 m"], [600000, "+10 m"], [3600000, "+1 h"], [86400000, "+1 d"]];

    /* loading / error state until the planetarium is ready */
    const state = $("sk-state");
    function showState(kind) {
        if (!state) return;
        if (kind === "ready") { state.hidden = true; return; }
        state.hidden = false;
        state.innerHTML = kind === "error"
            ? `<div class="ds-empty">${ic("triangle-alert")}<div class="ds-empty__title">Sky view unavailable</div><button class="ds-btn" type="button" id="sk-retry" aria-label="Reload" title="Reload">${ic("rotate-ccw")}</button></div>`
            : `<span class="ds-skeleton ds-skeleton--block" role="status" aria-label="Loading sky"></span>`;
        const r = $("sk-retry"); if (r) r.addEventListener("click", () => location.reload());
    }
    showState("loading");

    function build(core) {
        const sheets = [$("sk-layers"), $("sk-bodies"), $("sk-timesheet")];
        const rail = { layers: $("sk-b-layers"), bodies: $("sk-b-bodies"), mode: $("sk-b-mode") };
        /* sheets that dock on desktop: reflect open state on the opener */
        const sync = () => sheets.forEach((s) => { const b = document.querySelector(`[data-ds-open="${s.id}"]`); if (b) b.setAttribute("aria-expanded", s.getAttribute("data-open") === "true" ? "true" : "false"); });
        sheets.forEach((s) => new MutationObserver(sync).observe(s, { attributes: true, attributeFilter: ["data-open"] }));

        /* layers sheet: grouped icon toggles */
        const grid = $("sk-layers-body");
        grid.innerHTML = GROUPS.map(([title, icon, items]) =>
            `<div class="sk-group">${ic(icon)}<span>${esc(title)}</span></div><div class="sk-grid">` +
            items.map(([k, i, l]) => `<button class="sk-tog" type="button" data-layer="${k}" aria-pressed="false" aria-label="${esc(l)}" title="${esc(l)}">${ic(i)}<span>${esc(l)}</span></button>`).join("") + "</div>").join("");
        grid.addEventListener("click", (e) => {
            const b = e.target.closest("[data-layer]"); if (!b) return;
            core.setLayer(b.dataset.layer, b.getAttribute("aria-pressed") !== "true");
            refresh();
        });

        /* time sheet: date, speed, steps */
        $("sk-speeds").innerHTML = SPEEDS.map((s) => `<button class="sk-chip" type="button" data-speed="${s}" aria-pressed="false" aria-label="Speed ${s}x">${s}×</button>`).join("");
        $("sk-steps").innerHTML = STEPS.map(([ms, l]) => `<button class="sk-chip" type="button" data-step="${ms}" aria-label="Shift ${l}">${l}</button>`).join("");
        const dt = $("sk-date");
        dt.addEventListener("change", () => { const v = Date.parse(dt.value); if (!Number.isNaN(v)) core.setTime(v); });
        $("sk-speeds").addEventListener("click", (e) => { const b = e.target.closest("[data-speed]"); if (b) { core.setSpeed(Number(b.dataset.speed)); refresh(); } });
        $("sk-steps").addEventListener("click", (e) => { const b = e.target.closest("[data-step]"); if (b) core.shiftTime(Number(b.dataset.step)); });

        /* time bar */
        $("sk-t-back").addEventListener("click", () => core.shiftTime(-3600000));
        $("sk-t-fwd").addEventListener("click", () => core.shiftTime(3600000));
        $("sk-t-now").addEventListener("click", () => { core.goNow(); refresh(); });
        $("sk-t-play").addEventListener("click", () => { core.setSpeed(core.speed === 0 ? 1 : 0); refresh(); });
        rail.mode.addEventListener("click", async () => { try { await window.__sky.setMode(core.mode === "3d" ? "eye" : "3d"); } catch (e) { /* mode unavailable */ } refresh(); });

        const pad = (n) => String(n).padStart(2, "0");
        function refresh() {
            grid.querySelectorAll("[data-layer]").forEach((b) => b.setAttribute("aria-pressed", core.layers[b.dataset.layer] ? "true" : "false"));
            const d = new Date(core.simMs);
            if (document.activeElement !== dt) dt.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
            $("sk-clock-t").textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
            $("sk-clock-d").textContent = d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
            const live = core.live || core.isNow;
            $("sk-clock-live").hidden = !live;
            const playing = core.speed !== 0;
            const pb = $("sk-t-play");
            pb.setAttribute("aria-pressed", playing ? "true" : "false");
            const lbl = playing ? "Pause" : "Play"; pb.setAttribute("aria-label", lbl); pb.title = lbl;
            pb.querySelector("use").setAttribute("href", playing ? "#sk-pause" : "/static/icons.svg#i-play");
            $("sk-speeds").querySelectorAll("[data-speed]").forEach((b) => b.setAttribute("aria-pressed", playing && Number(b.dataset.speed) === core.speed ? "true" : "false"));
            rail.mode.setAttribute("aria-pressed", core.mode === "3d" ? "true" : "false");
        }
        core.on("change", refresh); refresh(); setInterval(refresh, 1000);

        /* tap-for-info card */
        const card = $("sk-card");
        const kindIcon = (d) => { const s = `${d.kind} ${d.title}`.toLowerCase(); return /moon/.test(s) ? "moon" : /sun/.test(s) ? "sun" : /sat|iss/.test(s) ? "satellite" : /aircraft|flight|plane/.test(s) ? "plane" : /planet|mars|venus|jupiter|saturn|mercury/.test(s) ? "telescope" : "star"; };
        function renderCard() {
            const h = core.selectedHit;
            if (!core.selected || !h) { card.hidden = true; return; }
            const d = core.inspect(h);
            card.hidden = false;
            card.innerHTML = `<div class="oc-head"><span class="oc-chip">${ic(kindIcon(d))}</span><div class="oc-who"><h2 class="oc-title">${esc(d.title)}</h2><p class="oc-sub">${esc(d.kind)} · ${ic(d.above ? "eye" : "minus")}<span class="ds-sr-only">${d.above ? "above horizon" : "below horizon"}</span></p></div>` +
                `<button class="oc-btn" type="button" data-act="close" aria-label="Close" title="Close">${ic("x")}</button></div>` +
                `<div class="oc-stats">${(d.rows || []).map(([a, b]) => `<div><small>${esc(a)}</small><b>${esc(b)}</b></div>`).join("")}</div>` +
                (d.note ? `<p class="sk-note">${esc(d.note)}</p>` : "") +
                (core.mode !== "3d" ? `<div class="oc-actions"><button class="oc-btn" type="button" data-act="goto" aria-label="Centre on it" title="Centre on it">${ic("crosshair")}</button></div>` : "");
            card.dataset.az = d.az; card.dataset.alt = d.alt;
        }
        card.addEventListener("click", (e) => {
            const b = e.target.closest("[data-act]"); if (!b) return;
            if (b.dataset.act === "close") core.select(null);
            else core.goTo(Number(card.dataset.az), Number(card.dataset.alt));
        });
        core.on("select", renderCard);
        setInterval(() => { if (core.selected) renderCard(); }, 1000);
        showState("ready");
    }

    let tries = 0;
    (function wait() {
        const core = window.__sky && window.__sky.core;
        if (core && typeof core.on === "function") { try { build(core); } catch (e) { console.error(e); showState("error"); } return; }
        if (++tries > 300) { showState("error"); return; }
        setTimeout(wait, 100);
    })();
})();
