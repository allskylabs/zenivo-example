/* Fills every [data-app-version] element (sidebar footer, phone "More" sheet) with "Zenivo v<version>" from /api/version. */
(function () {
    function fill() {
        var els = document.querySelectorAll("[data-app-version]");
        if (!els.length || !window.fetch) return;
        fetch("/api/version", { cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (j) {
                if (!j || !j.version) return;
                els.forEach(function (el) { el.textContent = (j.product || "Zenivo") + " v" + j.version; });
            })
            .catch(function () { /* offline: keep the plain product name */ });
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fill); else fill();
})();

/* One shared /api/station/config request per page load (the name here, the time zone in 01-core.js). */
window.zenivoStationConfig = function () {
    if (!window.__zsc) window.__zsc = fetch("/api/station/config", { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    return window.__zsc;
};

/* A station that has a name of its own shows it in the header and the tab title ("Hanoi" / "Live — Hanoi · Zenivo"). */
(function () {
    function apply() {
        if (!window.fetch) return;
        window.zenivoStationConfig()
            .then(function (c) {
                var n = c && typeof c.location_name === "string" ? c.location_name.trim().slice(0, 60) : "";
                if (!n || n === "My station" || n === "GPS Station" || n === "Auto-Detected Station") return;
                document.querySelectorAll(".brand-name").forEach(function (el) { el.textContent = n; el.title = "Zenivo station: " + n; });
                if (!/ · Zenivo$/.test(document.title)) {
                    var page = /^Zenivo\b/.test(document.title) ? "" : document.title.replace(/\s*[—-]\s*Zenivo.*$/, "");
                    document.title = (page ? page + " · " : "") + n + " · Zenivo";
                }
            })
            .catch(function () { });
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply); else apply();
})();
