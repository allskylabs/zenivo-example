// Settings home: grouped ds-row list (the whole page on phones, the left pane on desktop). One-line current values are read from
// what the page already loaded (no extra requests; a row without a cheap value has no sub line). Rows are plain links, so the
// hash-driven sections and admin gating keep working untouched.
(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    const text = (id) => { const e = $(id); return e ? (e.textContent || "").trim() : ""; };
    const val = (id) => { const e = $(id); return e ? (e.value || "").trim() : ""; };
    const icon = (n) => `<svg class="ds-icon" aria-hidden="true"><use href="/static/icons.svg#i-${n}"></use></svg>`;
    // [group, rows]; row = [href, section, icon, title, keywords, admin, sub()]
    const GROUPS = [
        ["Camera", [
            ["/focus", "", "crosshair", "Focus", "lens sharpness assistant hfr", true, () => { const tr = document.querySelector("#settings-focus-history tbody tr"); if (!tr) return ""; const c = tr.cells; return [c[3] && c[3].textContent, c[2] && c[2].textContent].map((x) => (x || "").trim()).filter((x) => x && x !== "—").join(" · "); }],
            ["/calibration", "", "moon", "Master dark", "calibration dark frames hot pixels", true, () => ""],
            ["/settings#image", "image", "image", "Image output", "camera resolution orientation flip mirror white balance capture", true, () => [...new Set([text("camera-current-badge").replace(/^--$/, ""), text("output-res-badge")].filter(Boolean))].join(" · ")],
            ["/settings#detection", "detection", "eye", "Detection", "events meteors aircraft flashes sensitivity", true, () => { const n = document.querySelectorAll("input[data-event]:checked").length; return n ? `${n} on` : ""; }],
        ]],
        ["Station", [
            ["/settings#station", "station", "telescope", "Station & optics", "name location latitude longitude lens field of view pointing azimuth", true, () => val("station-name")],
            ["/settings#skymask", "skymask", "layers", "Sky mask", "mask horizon trees buildings polygon", true, () => ""],
        ]],
        ["Integrations", [
            ["/settings#integrations", "integrations", "plug", "Integrations", "ads-b aircraft fr24 sondehub radiosonde weather satellites", true, () => ""],
            ["/settings#community", "community", "users", "Community sharing", "community share map public", true, () => { const e = $("community-enabled"); return e ? (e.checked ? "On" : "Off") : ""; }],
        ]],
        ["Streaming", [
            ["/obs/dock", "", "radio", "OBS dock", "obs streaming control panel", true, () => ""],
            ["/obs", "", "play", "OBS preview", "obs streaming preview", false, () => ""],
        ]],
        ["Admin", [
            ["/logs", "", "activity", "Activity log", "log errors events system", true, () => ""],
            ["/settings#admin", "admin", "lock", "Admin", "password update restart sign out version service location precision", true, () => {
                const v = text("app-version"), a = $("upd-auto");
                return [/\d/.test(v) ? v.replace(/^Zenivo\s*/, "") : "", a ? (a.checked ? "auto-update" : "") : ""].filter(Boolean).join(" · ");
            }],
        ]],
    ];
    const root = $("settings-home"), box = $("sh-groups");
    if (!root || !box) return;
    const esc = (v) => String(v).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    box.innerHTML = GROUPS.map(([g, rows]) => `<section class="sh-group"${rows.every((r) => r[5]) ? " data-admin" : ""} aria-label="${esc(g)}"><h2 class="sh-title">${esc(g)}</h2><div class="ds-card ds-card--flush"><div class="ds-list">` +
        rows.map(([href, sec, ic, title, kw, adm]) =>
            `<a class="ds-row sh-row" href="${href}"${sec ? ` data-section="${sec}"` : ""}${adm ? " data-admin" : ""} data-k="${esc((title + " " + kw).toLowerCase())}"${href === "/obs" ? ' target="_blank" rel="noopener"' : ""}>` +
            `<span class="ds-row__icon">${icon(ic)}</span><span class="ds-row__body"><span class="ds-row__title">${esc(title)}</span><span class="ds-row__sub" hidden></span></span>` +
            `<span class="ds-row__chev">${icon("chevron-right")}</span></a>`).join("") + "</div></div></section>").join("");
    const rowEls = [...box.querySelectorAll(".sh-row")];
    function values() {
        let i = 0;
        GROUPS.forEach(([, rows]) => rows.forEach((r) => {
            let v = ""; try { v = r[6](); } catch (e) { v = ""; }
            const s = rowEls[i++].querySelector(".ds-row__sub"); s.textContent = v; s.hidden = !v;
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
    function sync() {
        const sub = sec.getAttribute("data-sub");
        rowEls.forEach((a) => { if (a.dataset.section && a.dataset.section === sub) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
        values();
    }
    new MutationObserver(sync).observe(sec, { attributes: true, attributeFilter: ["data-sub"] });
    sync();
    setInterval(() => { if (!document.hidden && root.offsetParent) values(); }, 2500);
})();
