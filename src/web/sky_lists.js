/* Sky page: "Sun & Moon" and "Planets" cards (rise/set, altitude now, Moon phase) from GET /api/sky/bodies.
 * Classic script, self-contained: it adds a "Sun & planets" button and a panel to #stage, loads data only while the panel is open
 * (every 60 s, not while the tab is hidden) and shows times in the station's time zone (/api/station/config). */
(function () {
    "use strict";
    const stage = document.getElementById("stage");
    if (!stage) return;
    const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
    const DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    const dir = (az) => DIRS[Math.round((((az % 360) + 360) % 360) / 45) % 8];

    let tz = { name: null, offset_min: null };
    fetch("/api/station/config").then((r) => (r.ok ? r.json() : null)).then((c) => { if (c && c.timezone_info) tz = c.timezone_info; }).catch(() => {});
    function stationParts(ms) {
        const d = new Date(ms);
        if (tz.name) {
            try {
                const p = {};
                for (const x of new Intl.DateTimeFormat("en-GB", { timeZone: tz.name, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).formatToParts(d)) p[x.type] = x.value;
                return { day: +p.day, text: `${p.hour}:${p.minute}` };
            } catch (e) { /* unknown zone in this browser: fall back to the fixed offset */ }
        }
        const s = new Date(ms + (typeof tz.offset_min === "number" ? tz.offset_min : -new Date().getTimezoneOffset()) * 60000);
        const p2 = (n) => String(n).padStart(2, "0");
        return { day: s.getUTCDate(), text: `${p2(s.getUTCHours())}:${p2(s.getUTCMinutes())}` };
    }
    function when(sec, nowSec) {
        if (!sec) return "--";
        const a = stationParts(sec * 1000), n = stationParts(nowSec * 1000);
        return a.text + (a.day !== n.day ? " +1d" : "");
    }
    const state = (b, nowSec, rs) => {
        if (b.state === "always_up") return "always up";
        if (b.state === "never_rises") return "never rises";
        return when(b[rs], nowSec);
    };

    function moonSvg(angle, illum) {
        const r = 14, k = Math.cos((angle * Math.PI) / 180), rx = Math.abs(k) * r;
        const waxing = angle < 180, crescent = k > 0;
        const outer = waxing ? 1 : 0;
        const inner = waxing ? (crescent ? 0 : 1) : (crescent ? 1 : 0);
        const lit = `M${r} 0A${r} ${r} 0 0 ${outer} ${r} ${2 * r}A${rx.toFixed(2)} ${r} 0 0 ${inner} ${r} 0Z`;
        return `<svg class="skl-moon" viewBox="0 0 ${2 * r} ${2 * r}" width="30" height="30" role="img" aria-label="Moon, ${Math.round(illum * 100)}% lit"><circle cx="${r}" cy="${r}" r="${r}" class="skl-moon-dark"/><path d="${lit}" class="skl-moon-lit"/></svg>`;
    }

    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "skl-btn"; btn.setAttribute("aria-expanded", "false"); btn.setAttribute("aria-controls", "skl-panel");
    btn.textContent = "Sun & planets";
    const panel = document.createElement("div");
    panel.className = "skl-panel"; panel.id = "skl-panel"; panel.hidden = true;
    panel.setAttribute("role", "region"); panel.setAttribute("aria-label", "Sun, Moon and planets");
    panel.innerHTML = '<p class="skl-msg">Loading...</p>';
    stage.appendChild(btn); stage.appendChild(panel);

    function render(data) {
        const now = data.t, by = {};
        (data.bodies || []).forEach((b) => { by[b.name] = b; });
        const sun = by.Sun, moon = by.Moon;
        const planets = (data.bodies || []).filter((b) => b.name !== "Sun" && b.name !== "Moon");
        const dark = sun && sun.alt < -6;
        const sunTile = sun ? `<div class="skl-tile"><span class="skl-k">Sun</span>
            <span class="skl-l">Sunrise</span><b>${esc(state(sun, now, "rise"))}</b>
            <span class="skl-l">Sunset</span><b>${esc(state(sun, now, "set"))}</b></div>` : "";
        const moonTile = moon ? `<div class="skl-tile"><span class="skl-k">Moon</span>${moonSvg(moon.angle_deg, moon.illum)}
            <strong class="skl-ph">${esc(moon.phase)}</strong><span class="skl-sub">${Math.round(moon.illum * 100)}% illuminated</span>
            <span class="skl-row"><span class="skl-l">Rise</span><b>${esc(state(moon, now, "rise"))}</b></span>
            <span class="skl-row"><span class="skl-l">Set</span><b>${esc(state(moon, now, "set"))}</b></span></div>` : "";
        const rows = planets.map((p) => {
            const up = p.alt > 0;
            const tag = up ? (dark ? ["Visible", "ok"] : ["Up, daylight", ""]) : (p.state === "never_rises" ? ["Never rises", ""] : [`Rises ${when(p.rise, now)}`, ""]);
            const sub = up ? `Alt ${Math.round(p.alt)}° · ${dir(p.az)}${p.mag != null ? ` · mag ${p.mag < 0 ? "−" : ""}${Math.abs(p.mag).toFixed(1)}` : ""}` : "Below horizon";
            return `<li class="skl-planet"><span class="skl-dot skl-${esc(p.name.toLowerCase())}" aria-hidden="true"></span>
                <span class="skl-pt"><b>${esc(p.name)}</b><small>${esc(sub)}</small></span><span class="skl-tag ${tag[1]}">${esc(tag[0])}</span></li>`;
        }).join("");
        panel.innerHTML = `<section class="skl-card"><h2>Sun &amp; Moon</h2><div class="skl-grid">${sunTile}${moonTile}</div></section>
            <section class="skl-card"><h2>Planets</h2><ul class="skl-list">${rows}</ul></section>`;
    }
    let timer = null, busy = false;
    function load() {
        if (busy || document.hidden || panel.hidden) return;
        busy = true;
        fetch("/api/sky/bodies").then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
            .then(render)
            .catch(() => { if (!panel.querySelector(".skl-card")) panel.innerHTML = '<p class="skl-msg">Sun and planet times are not available right now.</p>'; })
            .finally(() => { busy = false; });
    }
    function setOpen(o) {
        panel.hidden = !o;
        btn.setAttribute("aria-expanded", o ? "true" : "false");
        clearInterval(timer);
        if (o) { load(); timer = setInterval(load, 60000); }
    }
    btn.addEventListener("click", () => setOpen(panel.hidden));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) { setOpen(false); btn.focus(); } });
})();
