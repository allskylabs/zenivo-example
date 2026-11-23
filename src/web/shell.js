/* Zenivo shell, shared by every page (classic deferred script, linked from <head> by scripts/sync_nav.py).
 * Drives the generated topbar / tab bar / navigation list on top of ds.js: sidebar collapse, active item, red vision,
 * station name, clocks, status dot, fullscreen, share and the owner chat widget. */
(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    const DSx = window.DS || {};
    const PREFS = "allsky.ui.prefs.v1";

    // ---- active tab / nav item from data-paths (+ data-hash for /settings sections) ----
    function norm(p) { p = p.replace(/\/index\.html$/, "/").replace(/\/+$/, ""); return p || "/"; }
    function markActive() {
        const here = norm(location.pathname);
        document.querySelectorAll("[data-paths]").forEach((el) => {
            let hit = el.dataset.paths.split(",").some((p) => norm(p.trim()) === here);
            if (hit && el.dataset.hash) hit = location.hash.slice(1) === el.dataset.hash;
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
        document.querySelectorAll("#btn-night-vision").forEach((b) => b.setAttribute("aria-pressed", on ? "true" : "false"));
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
        const txt = (id) => { const e = $(id); return e ? e.textContent.trim() : ""; };
        const stT = txt("nav-system-status") || "Online";
        const stEl = $("zt-status");
        const tip = [stT, txt("sensor-subtitle"), txt("nav-camera-mode"), txt("nav-fps").replace(/^\s*·\s*/, "")].filter(Boolean).join(" · ");
        if (stEl && stEl.title !== tip) { stEl.title = tip; stEl.setAttribute("aria-label", tip); }
        const stT2 = $("zt-status-t");
        if (stT2 && stT2.textContent !== stT) stT2.textContent = stT;
        const camEl = $("zt-cam");
        if (camEl) {
            const sen = txt("sensor-subtitle").split("·")[0].trim() || "IMX335";
            const ex = ((($("telem-exp") || {}).textContent) || "").replace(/\s+/g, " ").trim();
            const ct = ex && !/^--/.test(ex) ? sen + " · " + ex : sen;
            if (camEl.textContent !== ct) camEl.textContent = ct;
        }
        const clEl = $("zt-clocks");
        const lab = txt("nav-local-label");
        const ctip = "UTC " + txt("nav-utc-clock") + (lab ? " · " + lab : "");
        if (clEl && clEl.title !== ctip) { clEl.title = ctip; clEl.setAttribute("aria-label", ctip); }
        const utc = $("nav-utc-clock");
        // the dashboard (core.js) drives the UTC/local clocks itself; other pages get a plain UTC tick
        if (utc && typeof window.updateClock !== "function") {
            const d = new Date();
            const lc = $("nav-local-clock");
            if (lc) lc.textContent = hhmm(d);
            const ll = $("nav-local-label");
            if (ll) ll.textContent = "Local (" + offsetLabel().replace("UTC", "GMT") + ")";
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

    const NAV = "zenivo.nav", DESKTOP = window.matchMedia("(min-width: 1024px)");
    function closeMenu() { const s = $("zs-nav"); if (s && DSx.closeSheet && s.hasAttribute("data-open")) DSx.closeSheet(s); }

    // ---- sidebar: expanded / collapsed (persisted), dialog semantics only while it is the phone sheet ----
    function setCollapsed(on, persist) {
        document.documentElement.classList.toggle("zs-collapsed", on);
        const t = $("zs-toggle");
        if (t) {
            const lab = (on ? "Expand menu" : "Collapse menu") + " (Ctrl+B)";
            t.setAttribute("aria-expanded", on ? "false" : "true"); t.setAttribute("aria-label", lab); t.title = lab;
            const eg = $("zs-edge"); if (eg) eg.title = lab;
        }
        document.querySelectorAll(".zs__item").forEach((el) => {
            const tip = el.getAttribute("aria-label");
            if (el.closest(".zs__utils")) return;
            if (on && tip) el.title = tip; else el.removeAttribute("title");
        });
        if (persist) { try { localStorage.setItem(NAV, on ? "0" : "1"); } catch (e) { /* private mode */ } }
        // camera / map canvases size themselves from the window: tell them the content box changed
        window.dispatchEvent(new Event("resize"));
    }
    function syncRole() {
        const z = $("zs-nav");
        if (!z) return;
        if (DESKTOP.matches) { z.removeAttribute("role"); z.removeAttribute("aria-modal"); z.removeAttribute("data-open"); }
        else { z.setAttribute("role", "dialog"); z.setAttribute("aria-modal", "true"); }
    }
    function initNav() {
        const z = $("zs-nav");
        if (!z) return;
        // utilities always carry a tooltip (icon-only), items only while collapsed
        z.querySelectorAll(".zs__utils .zs__item").forEach((el) => { el.title = el.getAttribute("aria-label") || ""; });
        const col = document.documentElement.classList.contains("zs-collapsed");
        setCollapsed(col, false);
        const t = $("zs-toggle");
        const flip = () => setCollapsed(!document.documentElement.classList.contains("zs-collapsed"), true);
        if (t) t.addEventListener("click", flip);
        const eg = $("zs-edge");
        if (eg) eg.addEventListener("click", flip);
        document.addEventListener("keydown", (e) => {
            if (!DESKTOP.matches || !(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey || e.key.toLowerCase() !== "b") return;
            const a = e.target; if (a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))) return;
            e.preventDefault(); flip();
        });
        syncRole();
        (DESKTOP.addEventListener ? DESKTOP.addEventListener("change", syncRole) : DESKTOP.addListener(syncRole));
        z.addEventListener("click", (e) => { if (e.target.closest("a.zs__item")) closeMenu(); });
        window.addEventListener("hashchange", markActive);
        initTip(z);
    }

    // Collapsed-rail tooltip: one fixed bubble (the scrolling nav would clip a CSS one).
    function initTip(z) {
        const tip = document.createElement("div");
        tip.className = "zs-tip"; tip.setAttribute("role", "tooltip"); tip.hidden = true;
        document.body.appendChild(tip);
        const hide = () => { tip.hidden = true; };
        const show = (el) => {
            if (!document.documentElement.classList.contains("zs-collapsed") || !DESKTOP.matches) return hide();
            tip.textContent = el.getAttribute("data-tip"); tip.hidden = false;
            const r = el.getBoundingClientRect();
            tip.style.left = (r.right + 8) + "px";
            tip.style.top = Math.max(4, r.top + (r.height - tip.offsetHeight) / 2) + "px";
        };
        z.addEventListener("mouseover", (e) => { const el = e.target.closest("[data-tip]"); el ? show(el) : hide(); });
        z.addEventListener("focusin", (e) => { const el = e.target.closest("[data-tip]"); el ? show(el) : hide(); });
        z.addEventListener("mouseleave", hide); z.addEventListener("focusout", hide);
        z.addEventListener("click", hide);
        const n = z.querySelector(".zs__nav"); if (n) n.addEventListener("scroll", hide, { passive: true });
    }

    function init() {
        markActive();
        applyRv(readPref(), false);
        const nv = $("btn-night-vision"), fs = $("m-fs-btn"), sh = $("m-share-btn"), ch = $("m-chat-btn");
        if (nv) nv.addEventListener("click", () => applyRv(!rvOn(), true));
        if (fs) fs.addEventListener("click", () => { closeMenu(); fullscreen(); });
        if (sh) sh.addEventListener("click", () => { closeMenu(); share(); });
        if (ch) ch.addEventListener("click", () => { closeMenu(); const l = document.querySelector(".zcw-launch"); if (l) l.click(); });
        initNav();
        loadChat(); loadName();
        tickClock(); setInterval(tickClock, 1000);
        syncDot();
        const st = $("nav-system-status");
        if (st && window.MutationObserver) new MutationObserver(syncDot).observe(st, { childList: true, characterData: true, subtree: true });
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
