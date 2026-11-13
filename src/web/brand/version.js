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
