/* Zenivo shell, shared by every page (classic deferred script, linked from <head> by scripts/sync_nav.py).
 * Drives the generated topbar / tab bar / Menu sheet on top of ds.js: active tab, red vision, station name,
 * clocks, status dot, fullscreen, share and the owner chat widget. */
(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    const DSx = window.DS || {};
    const PREFS = "allsky.ui.prefs.v1";

    // ---- active tab / tile from data-paths ----
    function norm(p) { p = p.replace(/\/index\.html$/, "/").replace(/\/+$/, ""); return p || "/"; }
    function markActive() {
        const here = norm(location.pathname);
        document.querySelectorAll("[data-paths]").forEach((el) => {
            const hit = el.dataset.paths.split(",").some((p) => norm(p.trim()) === here);
            if (hit) el.setAttribute("aria-current", "page"); else el.removeAttribute("aria-current");
        });
    }

    // ---- red vision ----
    function readPref() { try { return !!(JSON.parse(localStorage.getItem(PREFS) || "{}").nightVision); } catch (e) { return false; } }
    function writePref(on) {
        try { const o = JSON.parse(localStorage.getItem(PREFS) || "{}"); o.nightVision = on; localStorage.setItem(PREFS, JSON.stringify(o)); } catch (e) { /* private mode */ }
    }
    function applyRv(on, persist) {
        document.body.classList.toggle("night-mode", on);
        if (DSx.setTheme) DSx.setTheme(on, true);
        document.querySelectorAll("#btn-night-vision, #m-rv-toggle").forEach((b) => b.setAttribute("aria-pressed", on ? "true" : "false"));
        if (persist) writePref(on);
        window.dispatchEvent(new CustomEvent("zenivo:rv", { detail: { on } }));
    }
    const rvOn = () => document.body.classList.contains("night-mode");

    // ---- station name ----
    function setName(n) { ["m-head-name", "m-st-name"].forEach((id) => { const el = $(id); if (el) el.textContent = n; }); }
    function loadName() {
        if (typeof window.zenivoStationConfig !== "function") return;
        window.zenivoStationConfig().then((c) => {
            const n = c && typeof c.location_name === "string" ? c.location_name.trim().slice(0, 60) : "";
            if (n && n !== "My station" && n !== "GPS Station" && n !== "Auto-Detected Station") setName(n);
        });
    }

    // ---- clocks ----
    function offsetLabel() {
        const m = -new Date().getTimezoneOffset();
        const h = Math.trunc(m / 60), r = Math.abs(m % 60);
        return "UTC" + (m < 0 ? "-" : "+") + Math.abs(h) + (r ? ":" + String(r).padStart(2, "0") : "");
    }
    const hhmm = (d) => String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    function tickClock() {
        const el = $("m-head-clock");
        if (el) {
            const st = $("nav-local-clock");
            const txt = st && /\d\d:\d\d/.test(st.textContent) && typeof tzLabel === "string"
                ? st.textContent.trim().slice(0, 5) + " " + tzLabel
                : hhmm(new Date()) + " " + offsetLabel();
            if (el.textContent !== txt) el.textContent = txt;
        }
        const utc = $("nav-utc-clock");
        // the dashboard (core.js) drives the UTC/local clocks itself; other pages get a plain UTC tick
        if (utc && typeof window.updateClock !== "function") {
            const d = new Date();
            utc.textContent = String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0") + ":" + String(d.getUTCSeconds()).padStart(2, "0");
        }
    }

    // ---- status dot ----
    function syncDot() {
        const s = $("nav-system-status"), dot = $("status-pulse");
        if (!dot) return;
        const t = s ? (s.textContent || "").trim() : "";
        dot.classList.toggle("off", !!t && !/online/i.test(t));
    }

    // ---- fullscreen / share ----
    function fullscreen() {
        const target = $("viewport-container") || document.documentElement;
        if (document.fullscreenElement) { document.exitFullscreen().catch(() => {}); return; }
        if (target.requestFullscreen) target.requestFullscreen().catch(() => {});
    }
    async function share() {
        const url = location.origin + (location.pathname === "/" ? "/live" : location.pathname);
        if (navigator.share) { try { await navigator.share({ title: document.title, url }); } catch (e) { /* cancelled */ } return; }
        try { await navigator.clipboard.writeText(url); DSx.toast ? DSx.toast("Link copied", "ok") : null; }
        catch (e) { window.prompt("Copy this link", url); }
    }

    // ---- station chat widget ----
    function loadChat() {
        const v = "20261130h";
        const css = document.createElement("link");
        css.rel = "stylesheet"; css.href = "/static/css/chat.css?v=" + v;
        document.head.appendChild(css);
        const js = document.createElement("script");
        js.src = "/static/chat-widget.js?v=" + v; js.defer = true;
        document.head.appendChild(js);
    }

    function closeMenu() { const s = $("m-more-sheet"); if (s && DSx.closeSheet) DSx.closeSheet(s); }

    function init() {
        markActive();
        applyRv(readPref(), false);
        const nv = $("btn-night-vision"), rv = $("m-rv-toggle"), fs = $("m-fs-btn"), sh = $("m-share-btn");
        [nv, rv].forEach((b) => b && b.addEventListener("click", () => { applyRv(!rvOn(), true); if (b === rv) closeMenu(); }));
        if (fs) fs.addEventListener("click", () => { closeMenu(); fullscreen(); });
        if (sh) sh.addEventListener("click", () => { closeMenu(); share(); });
        const ch = $("m-chat-btn");
        if (ch) ch.addEventListener("click", () => { closeMenu(); const l = document.querySelector(".zcw-launch"); if (l) l.click(); });
        const sheet = $("m-more-sheet");
        if (sheet) sheet.addEventListener("click", (e) => { if (e.target.closest("a.ds-tile")) closeMenu(); });
        loadChat(); loadName();
        tickClock(); setInterval(tickClock, 1000);
        syncDot();
        const st = $("nav-system-status");
        if (st && window.MutationObserver) new MutationObserver(syncDot).observe(st, { childList: true, characterData: true, subtree: true });
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
