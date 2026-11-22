/* Zenivo shell, shared by every page (classic script, deferred, generated into <head> by scripts/sync_nav.py).
 * Phone header (status dot, station name, clock) and the Menu sheet extras: Red vision state, Fullscreen, Share.
 * Opening/closing the sheet itself stays with each page's own handler (they all toggle `hidden` on #m-more-sheet);
 * this file only adds what the new Menu needs and never toggles the sheet except to close it after an action. */
(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    const sheet = $("m-more-sheet"), backdrop = $("m-sheet-backdrop"), moreBtn = $("m-more-btn");

    function closeSheet() {
        if (sheet) sheet.hidden = true;
        if (backdrop) backdrop.hidden = true;
        if (moreBtn) moreBtn.setAttribute("aria-expanded", "false");
    }

    // ---- station name (header + Menu) ----
    function setName(n) {
        ["m-head-name", "m-st-name"].forEach((id) => { const el = $(id); if (el) el.textContent = n; });
    }
    function loadName() {
        if (typeof window.zenivoStationConfig !== "function") return;
        window.zenivoStationConfig().then((c) => {
            const n = c && typeof c.location_name === "string" ? c.location_name.trim().slice(0, 60) : "";
            if (n && n !== "My station" && n !== "GPS Station" && n !== "Auto-Detected Station") setName(n);
        });
    }

    // ---- clock: the station's local time where the dashboard knows it, else the browser's ----
    function offsetLabel() {
        const m = -new Date().getTimezoneOffset();
        const h = Math.trunc(m / 60), r = Math.abs(m % 60);
        return "UTC" + (m < 0 ? "-" : "+") + Math.abs(h) + (r ? ":" + String(r).padStart(2, "0") : "");
    }
    function tickClock() {
        const el = $("m-head-clock");
        if (!el) return;
        const station = $("nav-local-clock");
        let txt;
        if (station && /\d\d:\d\d/.test(station.textContent) && typeof tzLabel === "string") txt = station.textContent.trim().slice(0, 5) + " " + tzLabel;
        else {
            const d = new Date();
            txt = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0") + " " + offsetLabel();
        }
        if (el.textContent !== txt) el.textContent = txt;
    }

    // ---- status dot: follows the Online/Offline text of the dashboard header when there is one ----
    function syncDot() {
        const s = $("nav-system-status"), dot = $("m-head-dot");
        if (!dot) return;
        const online = !s || /online/i.test(s.textContent || "") || !(s.textContent || "").trim();
        dot.classList.toggle("off", !online);
        const mdot = document.querySelector(".m-st-dot");
        if (mdot) mdot.classList.toggle("off", !online);
    }

    // ---- Red vision tile state follows body.night-mode, whichever page toggled it ----
    function syncRv() {
        const rv = $("m-rv-toggle"), st = $("m-rv-state");
        const on = document.body.classList.contains("night-mode");
        if (rv) { rv.classList.toggle("btn-primary", on); rv.setAttribute("aria-checked", on ? "true" : "false"); }
        if (st) st.textContent = on ? "On" : "Off";
    }

    // ---- Fullscreen: the live picture on the Live page, the whole page elsewhere ----
    function fullscreen() {
        const target = $("viewport-container") || document.documentElement;
        if (document.fullscreenElement) { document.exitFullscreen().catch(() => {}); return; }
        if (target.requestFullscreen) target.requestFullscreen().catch(() => {});
    }

    // ---- Share: native share sheet when there is one, else copy the link ----
    function toast(msg) {
        const el = document.createElement("div");
        el.className = "auth-toast";
        el.setAttribute("role", "status");
        el.textContent = msg;
        document.body.appendChild(el);
        setTimeout(() => el.remove(), 3500);
    }
    async function share() {
        const url = location.origin + (location.pathname === "/" ? "/live" : location.pathname);
        if (navigator.share) {
            try { await navigator.share({ title: document.title, url }); } catch (e) { /* cancelled */ }
            return;
        }
        try {
            await navigator.clipboard.writeText(url);
            toast("Link copied");
        } catch (e) {
            window.prompt("Copy this link", url);
        }
    }

    // ---- station chat widget (owner only; it renders nothing unless chat is on and the owner is signed in) ----
    function loadChat() {
        const v = "20261130d";
        const css = document.createElement("link");
        css.rel = "stylesheet"; css.href = "/static/css/chat.css?v=" + v;
        document.head.appendChild(css);
        const js = document.createElement("script");
        js.src = "/static/chat-widget.js?v=" + v; js.defer = true;
        document.head.appendChild(js);
    }

    function init() {
        loadChat();
        loadName();
        tickClock(); setInterval(tickClock, 5000);
        syncDot();
        const status = $("nav-system-status");
        if (status && window.MutationObserver) new MutationObserver(syncDot).observe(status, { childList: true, characterData: true, subtree: true });
        syncRv();
        if (window.MutationObserver) new MutationObserver(syncRv).observe(document.body, { attributes: true, attributeFilter: ["class"] });
        const fs = $("m-fs-btn"), sh = $("m-share-btn");
        if (fs) fs.addEventListener("click", () => { closeSheet(); fullscreen(); });
        if (sh) sh.addEventListener("click", () => { closeSheet(); share(); });
        if (sheet) sheet.addEventListener("click", (e) => { if (e.target.closest("a.m-tile")) closeSheet(); });
        // Esc and backdrop are handled by each page; make sure both exist even where a page predates them
        document.addEventListener("keydown", (e) => { if (e.key === "Escape" && sheet && !sheet.hidden) closeSheet(); });
        if (backdrop) backdrop.addEventListener("click", closeSheet);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
