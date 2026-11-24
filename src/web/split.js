/* Split frame behaviour (.ds-split): one toggle in the side card header, three states, remembered per page.
 *   expanded -> full side card · rail -> slim strip (icon + count) · hidden -> map card full width + floating "show" button.
 * Markup: .ds-split > .ds-split__main (+ .ds-split__show) + .ds-split__side > .ds-split__head (.ds-split__toggle, .ds-split__n[data-split-count]).
 * Key L cycles collapse / expand (not while typing). Esc inside the side card collapses it to the rail. Canvases are told through a window resize. */
(function () {
    "use strict";
    const sp = document.querySelector(".ds-split");
    if (!sp) return;
    const side = sp.querySelector(".ds-split__side"), main = sp.querySelector(".ds-split__main");
    const tog = sp.querySelector(".ds-split__toggle"), show = sp.querySelector(".ds-split__show");
    const KEY = "zenivo.split." + location.pathname.replace(/\W+/g, "") + ".v1";
    const phone = window.matchMedia("(max-width: 900px)");
    const STATES = ["expanded", "rail", "hidden"];
    const ico = (n) => '<svg class="ds-icon" aria-hidden="true"><use href="/static/icons.svg#i-' + n + '"></use></svg>';
    let state = "expanded", rz = 0;
    function def() { return phone.matches ? "rail" : "expanded"; }
    function load() { try { const v = localStorage.getItem(KEY + (phone.matches ? ".m" : "")); if (STATES.includes(v)) return v; } catch (e) { /* private mode */ } return def(); }
    function fire() { clearTimeout(rz); rz = setTimeout(() => window.dispatchEvent(new Event("resize")), 0); }
    function apply(s, persist) {
        state = s; sp.dataset.state = s;
        const open = s === "expanded";
        if (tog) {
            const lab = (open ? "Hide list" : "Show list") + " (L)";
            tog.setAttribute("aria-expanded", open ? "true" : "false"); tog.setAttribute("aria-label", lab); tog.title = lab;
            tog.innerHTML = ico(phone.matches ? (open ? "chevron-down" : "chevron-up") : (open ? "panel-right-close" : "panel-right-open"));
        }
        side.toggleAttribute("inert", s === "hidden");
        if (show) { show.setAttribute("aria-label", "Show list (L)"); show.title = "Show list (L)"; }
        if (persist) { try { localStorage.setItem(KEY + (phone.matches ? ".m" : ""), s); } catch (e) { /* private mode */ } }
        // let the card transition end, then once more at the end: ResizeObserver covers the frames in between
        fire(); setTimeout(fire, 320);
    }
    function mirror() {
        const n = side.querySelector("[data-split-count]"); if (!n || !show) return;
        const set = () => { const t = n.textContent.trim(); const b = show.querySelector(".ds-split__n"); if (b) b.textContent = t; };
        set(); if (window.MutationObserver) new MutationObserver(set).observe(n, { childList: true, characterData: true, subtree: true });
    }
    if (tog) tog.addEventListener("click", () => apply(state === "expanded" ? "rail" : "expanded", true));
    if (show) show.addEventListener("click", () => apply("expanded", true));
    // in the rail the whole strip expands the list (head has no other job there)
    side.addEventListener("click", (e) => { if (state === "rail" && !phone.matches && !e.target.closest(".ds-split__toggle")) apply("expanded", true); });
    const head = side.querySelector(".ds-split__head");
    if (head) head.addEventListener("click", (e) => { if (state === "rail" && phone.matches && !e.target.closest(".ds-split__toggle")) apply("expanded", true); });
    document.addEventListener("keydown", (e) => {
        const a = e.target; if (a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))) { if (e.key === "Escape" && side.contains(a) && state === "expanded") { apply("rail", true); a.blur(); } return; }
        if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
        if (e.key === "l" || e.key === "L") { e.preventDefault(); apply(state === "expanded" ? "hidden" : "expanded", true); }
        else if (e.key === "Escape" && side.contains(a) && state === "expanded") apply("rail", true);
    });
    if (window.ResizeObserver && main) new ResizeObserver(fire).observe(main);
    (phone.addEventListener ? phone.addEventListener("change", () => apply(load(), false)) : phone.addListener(() => apply(load(), false)));
    mirror();
    apply(load(), false);
})();
