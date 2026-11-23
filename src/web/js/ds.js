/* Zenivo design system helpers (additive, no globals except window.DS). */
(function () {
    "use strict";
    var PREFS_KEY = "allsky.ui.prefs.v1";
    var ICON_URL = "/static/icons.svg?v=20261128c";

    function icon(name, cls) {
        var s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        s.setAttribute("class", "ds-icon" + (cls ? " " + cls : ""));
        s.setAttribute("aria-hidden", "true");
        var u = document.createElementNS("http://www.w3.org/2000/svg", "use");
        u.setAttribute("href", ICON_URL + "#i-" + name);
        s.appendChild(u);
        return s;
    }

    /* theme: "red" = Red Vision (same attribute and prefs key as the legacy site) */
    function setTheme(red, noPersist) {
        if (red) document.documentElement.setAttribute("data-theme", "red");
        else document.documentElement.removeAttribute("data-theme");
        if (noPersist) return;
        try {
            var p = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
            p.nightVision = !!red;
            localStorage.setItem(PREFS_KEY, JSON.stringify(p));
        } catch (e) { /* storage unavailable */ }
    }
    function getTheme() {
        return document.documentElement.getAttribute("data-theme") === "red";
    }

    /* sheet: open(el) / close(el); Esc and scrim close; focus returns to the opener */
    var lastFocus = null;
    function openSheet(el) {
        lastFocus = document.activeElement;
        el.setAttribute("data-open", "true");
        var f = el.querySelector("button, a, [tabindex]");
        if (f) f.focus();
    }
    function closeSheet(el) {
        el.removeAttribute("data-open");
        if (lastFocus && lastFocus.focus) lastFocus.focus();
    }
    document.addEventListener("click", function (e) {
        var t = e.target.closest("[data-ds-open]");
        if (t) { var s = document.getElementById(t.getAttribute("data-ds-open")); if (s) openSheet(s); return; }
        var c = e.target.closest("[data-ds-close]");
        if (c) { var p = c.closest(".ds-sheet"); if (p) closeSheet(p); }
    });
    document.addEventListener("keydown", function (e) {
        if (e.key !== "Escape") return;
        var s = document.querySelector(".ds-sheet[data-open='true']");
        if (s) closeSheet(s);
    });

    /* toast(msg, kind): kind ok|warn|danger|info; auto-dismiss, announced via aria-live */
    function toast(msg, kind, ms) {
        var box = document.querySelector(".ds-toasts");
        if (!box) {
            box = document.createElement("div");
            box.className = "ds-toasts";
            box.setAttribute("role", "status");
            box.setAttribute("aria-live", "polite");
            document.body.appendChild(box);
        }
        var k = kind || "info";
        var t = document.createElement("div");
        t.className = "ds-toast ds-toast--" + k;
        t.appendChild(icon({ ok: "circle-check", warn: "triangle-alert", danger: "circle-x", info: "info" }[k] || "info"));
        var span = document.createElement("span");
        span.textContent = msg;
        t.appendChild(span);
        box.appendChild(t);
        setTimeout(function () { t.remove(); }, ms || 3500);
    }

    /* switch: <label class=ds-switch><input type=checkbox role=switch>...; nothing to wire (native checkbox) */
    /* tab bar: mark the current tab */
    function setTab(root, id) {
        root.querySelectorAll(".ds-tab").forEach(function (t) {
            if (t.getAttribute("data-tab") === id) t.setAttribute("aria-current", "page");
            else t.removeAttribute("aria-current");
        });
    }

    window.DS = { icon: icon, setTheme: setTheme, getTheme: getTheme, openSheet: openSheet, closeSheet: closeSheet, toast: toast, setTab: setTab };
})();
