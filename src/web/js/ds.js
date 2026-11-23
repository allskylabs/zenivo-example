/* Zenivo design system helpers (additive, no globals except window.DS). */
(function () {
    "use strict";
    var PREFS_KEY = "allsky.ui.prefs.v1";
    var ICON_URL = "/static/icons.svg";

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

    /* toast(msg, kind, ms): kind ok|warn|danger|error|info. Top-right under the topbar, max 380px.
     * Long or raw backend text is shortened to a title + one-line summary; the full text sits behind an expand button.
     * info/ok/warn auto-dismiss; errors stay until closed. Huge numbers are grouped / rounded. */
    function fmtNums(t) {
        return String(t).replace(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi, function (m, off, all) {
            if (/[\w.:/-]/.test(all.charAt(off - 1) || "") || /[\w:/]/.test(all.charAt(off + m.length) || "")) return m; /* ids, dates, times, paths */
            var n = Number(m);
            if (!isFinite(n)) return m;
            if (Math.abs(n) >= 1e12) return n.toExponential(2).replace("e+", "e");
            if (Math.abs(n) >= 1e4 || /\./.test(m)) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
            return m;
        });
    }
    function splitMsg(raw) {
        var txt = String(raw == null ? "" : raw).replace(/\s+/g, " ").trim();
        var title = "", rest = txt;
        var i = txt.indexOf(": ");
        if (i > 1 && i <= 32) { title = txt.slice(0, i); rest = txt.slice(i + 2); }
        var sum = fmtNums(rest);
        var cut = sum.search(/(?<=[.!?])\s/);
        if (cut > 0) sum = sum.slice(0, cut);
        if (sum.length > 90) sum = sum.slice(0, 88).replace(/\s+\S*$/, "") + "…";
        var long = !!title && sum !== rest || sum.length < rest.length;
        return { title: title, summary: sum, full: txt, long: long };
    }
    function toast(msg, kind, ms) {
        var box = document.querySelector(".ds-toasts");
        if (!box) {
            box = document.createElement("div");
            box.className = "ds-toasts";
            box.setAttribute("role", "status");
            box.setAttribute("aria-live", "polite");
            document.body.appendChild(box);
        }
        var k = kind === "error" ? "danger" : (kind || "info");
        var m = splitMsg(msg);
        var dup = box.querySelector('.ds-toast[data-key="' + k + "|" + m.full.slice(0, 120).replace(/"/g, "'") + '"]');
        if (dup) { dup.remove(); }
        while (box.children.length >= 4) box.firstChild.remove();
        var t = document.createElement("div");
        t.className = "ds-toast ds-toast--" + k;
        t.setAttribute("data-key", k + "|" + m.full.slice(0, 120).replace(/"/g, "'"));
        t.appendChild(icon({ ok: "circle-check", warn: "triangle-alert", danger: "circle-x", info: "info" }[k] || "info"));
        var body = document.createElement("div");
        body.className = "ds-toast__body";
        if (m.title) { var h = document.createElement("b"); h.className = "ds-toast__title"; h.textContent = m.title; body.appendChild(h); }
        var span = document.createElement("span");
        span.className = "ds-toast__sum";
        span.textContent = m.summary;
        body.appendChild(span);
        var raw = document.createElement("pre");
        raw.className = "ds-toast__raw";
        raw.hidden = true;
        raw.textContent = m.full;
        body.appendChild(raw);
        t.appendChild(body);
        var acts = document.createElement("div");
        acts.className = "ds-toast__acts";
        if (m.long) {
            var ex = document.createElement("button");
            ex.type = "button"; ex.className = "ds-toast__btn"; ex.setAttribute("aria-label", "Show details"); ex.setAttribute("aria-expanded", "false");
            ex.appendChild(icon("chevron-down"));
            ex.onclick = function () { var o = raw.hidden; raw.hidden = !o; ex.setAttribute("aria-expanded", String(o)); t.classList.toggle("is-open", o); };
            acts.appendChild(ex);
        }
        var cl = document.createElement("button");
        cl.type = "button"; cl.className = "ds-toast__btn"; cl.setAttribute("aria-label", "Dismiss");
        cl.appendChild(icon("x"));
        cl.onclick = function () { t.remove(); };
        acts.appendChild(cl);
        t.appendChild(acts);
        box.appendChild(t);
        if (k !== "danger") {
            var timer = setTimeout(function () { t.remove(); }, ms || 5000);
            t.addEventListener("mouseenter", function () { clearTimeout(timer); });
        }
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
