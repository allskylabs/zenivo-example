/* Sky page: "Sun & planets" sheet (rise/set, altitude now, Moon phase) from GET /api/sky/bodies, rendered as ds rows into #sk-bodies-body.
 * Classic script, self-contained: loads data only while the sheet is open
 * (every 60 s, not while the tab is hidden) and shows times in the station's time zone (/api/station/config). */
(function () {
    "use strict";
    const sheet = document.getElementById("sk-bodies");
    const body = document.getElementById("sk-bodies-body");
    if (!sheet || !body) return;
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
        return `<svg class="sk-moon" viewBox="0 0 ${2 * r} ${2 * r}" width="30" height="30" role="img" aria-label="Moon, ${Math.round(illum * 100)}% lit"><circle cx="${r}" cy="${r}" r="${r}" class="sk-moon-dark"/><path d="${lit}" class="sk-moon-lit"/></svg>`;
    }

    const ic = (n) => `<svg class="ds-icon" aria-hidden="true"><use href="/static/icons.svg#i-${n}"></use></svg>`;
    const clock = (t) => `<span>${ic("clock")}${esc(t)}</span>`;
    let loaded = false;

    function render(data) {
        const now = data.t;
        const list = data.bodies || [];
        const sun = list.find((b) => b.name === "Sun"), moon = list.find((b) => b.name === "Moon");
        const planets = list.filter((b) => b.name !== "Sun" && b.name !== "Moon");
        const dark = sun && sun.alt < -6;
        const rows = [];
        if (sun) rows.push(`<li class="sk-body${sun.alt > 0 ? " is-up" : ""}">${ic("sun")}<span class="sk-body__t"><b>Sun</b><small>${sun.alt > 0 ? "Alt " + Math.round(sun.alt) + "° · " + dir(sun.az) : "Below horizon"}</small></span>
            <span class="sk-times"><span>${ic("arrow-up-right")}${esc(state(sun, now, "rise"))}</span><span>${ic("chevron-down")}${esc(state(sun, now, "set"))}</span></span></li>`);
        if (moon) rows.push(`<li class="sk-body${moon.alt > 0 ? " is-up" : ""}">${moonSvg(moon.angle_deg, moon.illum)}<span class="sk-body__t"><b>Moon</b><small>${esc(moon.phase)} · ${Math.round(moon.illum * 100)}%</small></span>
            <span class="sk-times"><span>${ic("arrow-up-right")}${esc(state(moon, now, "rise"))}</span><span>${ic("chevron-down")}${esc(state(moon, now, "set"))}</span></span></li>`);
        planets.forEach((p) => {
            const up = p.alt > 0;
            const sub = up ? `Alt ${Math.round(p.alt)}° · ${dir(p.az)}${p.mag != null ? ` · mag ${p.mag < 0 ? "−" : ""}${Math.abs(p.mag).toFixed(1)}` : ""}` : "Below horizon";
            const t = up ? (dark ? "" : "daylight") : (p.state === "never_rises" ? "never rises" : when(p.rise, now));
            rows.push(`<li class="sk-body${up && dark ? " is-up" : ""}">${ic(up && dark ? "eye" : "telescope")}<span class="sk-body__t"><b>${esc(p.name)}</b><small>${esc(sub)}</small></span>${t ? `<span class="sk-times">${up ? esc(t) : clock(t)}</span>` : ""}</li>`);
        });
        body.innerHTML = rows.length ? `<ul class="sk-bodies">${rows.join("")}</ul>` : empty("Nothing to show");
        loaded = true;
    }
    const empty = (t) => `<div class="ds-empty">${ic("triangle-alert")}<div class="ds-empty__title">${esc(t)}</div></div>`;
    let timer = null, busy = false;
    function load() {
        if (busy || document.hidden || sheet.getAttribute("data-open") !== "true") return;
        busy = true;
        fetch("/api/sky/bodies").then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
            .then(render)
            .catch(() => { if (!loaded) body.innerHTML = empty("Sun and planet times are not available right now"); })
            .finally(() => { busy = false; });
    }
    body.innerHTML = '<span class="ds-skeleton ds-skeleton--block" role="status" aria-label="Loading"></span>';
    new MutationObserver(() => {
        clearInterval(timer);
        if (sheet.getAttribute("data-open") === "true") { load(); timer = setInterval(load, 60000); }
    }).observe(sheet, { attributes: true, attributeFilter: ["data-open"] });
})();
