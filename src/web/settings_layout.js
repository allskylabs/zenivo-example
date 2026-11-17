// Settings layout helpers (phase 4). Pure presentation: every original input stays in the DOM as the source of truth.
//  - segmented control drawn over a small fixed <select>: a click sets the select and fires its own change event, so the existing autosave runs;
//    the buttons follow the select when other code changes its value.
//  - "header switch": the enable checkbox of a block is moved (not copied) into a card header.
//  - range inputs get a --pct custom property so the track can show a filled part.
(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    const segs = [];

    function segmented(selectId, labelOf) {
        const sel = $(selectId);
        if (!sel || sel.dataset.segDone) return;
        sel.dataset.segDone = "1";
        const wrap = document.createElement("div");
        wrap.className = "seg seg-bound";
        wrap.setAttribute("role", "radiogroup");
        const lab = sel.id && document.querySelector('label[for="' + sel.id + '"]');
        if (lab) wrap.setAttribute("aria-label", lab.textContent.trim());
        [...sel.options].forEach((o) => {
            const b = document.createElement("button");
            b.type = "button";
            b.setAttribute("role", "radio");
            b.dataset.value = o.value;
            b.textContent = labelOf ? labelOf(o) : o.textContent;
            b.title = o.textContent;
            b.addEventListener("click", () => {
                if (sel.disabled || sel.value === o.value) return;
                sel.value = o.value;
                sel.dispatchEvent(new Event("change", { bubbles: true }));
                paint();
            });
            wrap.appendChild(b);
        });
        sel.classList.add("seg-src");
        sel.insertAdjacentElement("afterend", wrap);
        const paint = () => {
            wrap.querySelectorAll("button").forEach((b) => {
                const on = b.dataset.value === sel.value;
                b.classList.toggle("active", on);
                b.setAttribute("aria-checked", on ? "true" : "false");
                b.disabled = sel.disabled;
            });
        };
        paint();
        segs.push(paint);
    }

    function headerSwitch(fs) {
        const box = $(fs.dataset.hsSwitch);
        const card = fs.closest(".card");
        if (!box || !card || card.querySelector(".card-header-switch")) return;
        const text = (box.closest("label") || box.parentElement).textContent.trim();
        const head = document.createElement("div");
        head.className = "card-header card-header-switch";
        const tx = document.createElement("div");
        tx.className = "hs-tx";
        const h = document.createElement("h2");
        h.textContent = fs.dataset.hsTitle || "";
        const sm = document.createElement("small");
        sm.textContent = text;
        tx.append(h, sm);
        const srcField = box.closest(".field");
        box.setAttribute("aria-label", text);
        head.append(tx, box);
        fs.parentElement.insertBefore(head, fs.parentElement.querySelector(".settings-grid") || fs);
        if (srcField) srcField.classList.add("ds-moved");
    }

    function paintRange(r) {
        const min = Number(r.min || 0), max = Number(r.max || 100), v = Number(r.value);
        const pct = max > min ? Math.min(100, Math.max(0, ((v - min) / (max - min)) * 100)) : 0;
        if (r.style.getPropertyValue("--pct") !== pct + "%") r.style.setProperty("--pct", pct + "%");
    }

    function init() {
        segmented("wx-refresh");
        segmented("wb-mode");
        segmented("dayobj-sensitivity", (o) => o.textContent.replace(/\s*\(.*$/, ""));
        document.querySelectorAll("[data-hs-switch]").forEach(headerSwitch);
        const ranges = [...document.querySelectorAll('input[type="range"]')];
        ranges.forEach((r) => { paintRange(r); r.addEventListener("input", () => paintRange(r)); });
        // other scripts set .value without events (auto exposure, saved settings): follow them while the page is visible
        setInterval(() => {
            if (document.hidden) return;
            segs.forEach((p) => p());
            ranges.forEach(paintRange);
        }, 700);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
