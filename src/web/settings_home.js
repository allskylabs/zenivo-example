// Phone settings home (/settings without #section): grouped list, one-line current values read from what the page already loaded
// (no extra requests; a row without a cheap value has no sub line), and a search field. The rows are plain links, so the existing
// hash-driven sections, sub-pages and admin gating keep working untouched.
(function () {
    "use strict";
    const I = {
        star: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
        clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>', camera: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/>',
        bolt: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>', home: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
        shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>', plus: '<path d="M12 5v14M5 12h14"/>',
        users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
        play: '<polygon points="6 4 20 12 6 20 6 4"/>', lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
        search: '<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/>',
    };
    // [group, rows]; row = [href, icon, tone, title, keywords, admin, sub()]
    const $ = (id) => document.getElementById(id);
    const text = (id) => { const e = $(id); return e ? (e.textContent || "").trim() : ""; };
    const val = (id) => { const e = $(id); return e ? (e.value || "").trim() : ""; };
    const GROUPS = [
        ["Camera", [
            ["/focus", "search", "violet", "Focus", "lens sharpness assistant hfr", true, () => { const t = text("settings-focus-history"); return /no focus saved/i.test(t) ? "" : t.split("\n")[0].slice(0, 60); }],
            ["/calibration", "clock", "violet", "Master dark", "calibration dark frames hot pixels", true, () => ""],
            ["/settings#image", "camera", "violet", "Image output", "camera resolution orientation flip mirror", true, () => [...new Set([text("camera-current-badge").replace(/^--$/, ""), text("output-res-badge")].filter(Boolean))].join(" · ")],
            ["/settings#detection", "bolt", "violet", "Detection", "events meteors aircraft flashes sensitivity", true, () => { const n = document.querySelectorAll("input[data-event]:checked").length; return n ? `${n} event type${n > 1 ? "s" : ""} on` : ""; }],
        ]],
        ["Station", [
            ["/settings#station", "home", "ok", "Station & optics", "name location latitude longitude lens field of view pointing azimuth", true, () => val("station-name")],
            ["/settings#skymask", "shield", "ok", "Sky mask", "mask horizon trees buildings polygon", true, () => ""],
        ]],
        ["Integrations", [
            ["/settings#integrations", "plus", "ok", "Integrations", "ads-b aircraft fr24 sondehub radiosonde weather satellites", true, () => ""],
            ["/settings#community", "users", "ok", "Community sharing", "community share map public", true, () => { const e = $("community-enabled"); return e ? (e.checked ? "Sharing on" : "Off") : ""; }],
        ]],
        ["Streaming", [
            ["/obs/dock", "play", "warn", "OBS dock", "obs streaming control panel", true, () => ""],
            ["/obs", "play", "warn", "OBS preview", "obs streaming preview", false, () => ""],
        ]],
        ["Admin", [
            ["/settings#admin", "lock", "accent", "Admin", "password update restart sign out version service location precision", true, () => {
                const v = text("app-version"), a = $("upd-auto");
                return [/\d/.test(v) ? v.replace(/^Zenivo\s*/, "Version ") : "", a ? (a.checked ? "auto update on" : "auto update off") : ""].filter(Boolean).join(" · ");
            }],
        ]],
    ];
    const root = $("settings-home"), box = $("sh-groups");
    if (!root || !box) return;
    const esc = (v) => String(v).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    box.innerHTML = GROUPS.map(([g, rows]) => `<section class="sh-group"${rows.every((r) => r[5]) ? " data-admin" : ""}><h2 class="sh-title">${g}</h2><div class="sh-card">` + rows.map(([href, ic, tone, title, kw, adm]) =>
        `<a class="sh-row tone-${tone}" href="${href}"${adm ? " data-admin" : ""} data-k="${esc((title + " " + kw).toLowerCase())}"${href === "/obs" ? ' target="_blank" rel="noopener"' : ""}>` +
        `<span class="sh-ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${I[ic]}</svg></span>` +
        `<span class="sh-tx"><b>${esc(title)}</b><small></small></span><span class="sh-chev" aria-hidden="true">&rsaquo;</span></a>`).join("") + "</div></section>").join("");
    const rowEls = [...box.querySelectorAll(".sh-row")];
    function values() {
        let i = 0;
        GROUPS.forEach(([, rows]) => rows.forEach((r) => {
            let v = ""; try { v = r[6](); } catch (e) { v = ""; }
            const s = rowEls[i++].querySelector("small"); s.textContent = v; s.hidden = !v;
        }));
    }
    function filter() {
        const q = $("sh-q").value.trim().toLowerCase();
        let any = false;
        rowEls.forEach((a) => { const hit = !q || a.dataset.k.includes(q); a.hidden = !hit; any = any || hit; });
        box.querySelectorAll(".sh-group").forEach((g) => { g.hidden = ![...g.querySelectorAll(".sh-row")].some((a) => !a.hidden); });
        $("sh-none").hidden = any;
    }
    $("sh-q").addEventListener("input", filter);
    const sec = $("tab-settings");
    function sync() { const on = sec.getAttribute("data-sub") === "home"; root.hidden = !on; if (on) values(); }
    new MutationObserver(sync).observe(sec, { attributes: true, attributeFilter: ["data-sub"] });
    sync();
    setInterval(() => { if (!root.hidden && !document.hidden) values(); }, 2500);
})();
