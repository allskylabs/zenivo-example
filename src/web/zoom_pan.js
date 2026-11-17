/* Zenivo zoom + pan for stored pictures and videos (Archive frame viewer, lightbox, event viewer, timelapse tiles).
 * Hand-written on purpose: the UI is a no-build classic-script bundle with a size budget, so no pinch-zoom library (the Live view keeps its own code).
 *
 *   const zp = ZenivoZoomPan.attach(containerEl, targetEl, opts);   // returns { reset, destroy, zoomed, scale, zoomAt, update }
 *
 * containerEl clips (overflow hidden) and must be the same size as targetEl's layout box; targetEl is what gets the CSS transform
 * (an <img>/<video>, or a wrapper holding a picture plus the marks drawn over it, so they stay registered).
 * opts: pill 'always' (default) | 'hover' | false · wheel 'plain' (default) | 'ctrl' (needs Ctrl/Cmd, keeps page scroll on inline tiles)
 *       active(): bool, keys + - 0 also work while this returns true (modal viewers); otherwise only with focus or hover in the container
 *       onChange(scale) */
(function (g) {
    "use strict";
    const MIN = 1, MAX = 8, TAP_ZOOM = 3, STEP = 1.5;

    function attach(container, el, opts) {
        opts = opts || {};
        if (!container || !el) return null;
        const isVideo = el.tagName === "VIDEO";
        let z = 1, x = 0, y = 0;                      // scale and translate (px, transform-origin 0 0)
        const pointers = new Map();
        let pinch = null, lastTap = null, moved = 0, multi = false, swallow = false, downAt = null, controlsOn = false;

        container.classList.add("zp-clip");
        container.style.touchAction = "pan-y";        // page still scrolls vertically at 1x; zoomed it becomes "none"
        if (!container.hasAttribute("tabindex")) container.tabIndex = 0;
        if (!container.hasAttribute("aria-label")) container.setAttribute("aria-label", "Zoomable picture: scroll or pinch to zoom, drag to pan, keys + - 0");
        el.style.transformOrigin = "0 0";
        el.draggable = false;

        // ---- pill: - / % (reset) / + (and play/pause for a zoomed video, whose native controls would be scaled out of reach)
        let pill = null, lvl = null, playBtn = null;
        if (opts.pill !== false) {
            pill = document.createElement("div");
            pill.className = "zoom-controls zp-pill" + (opts.pill === "hover" ? " zp-pill-hover" : "");
            pill.setAttribute("role", "group");
            pill.setAttribute("aria-label", "Zoom");
            pill.innerHTML = '<button type="button" class="zoom-btn" data-zp="out" aria-label="Zoom out">−</button>'
                + '<button type="button" class="zoom-btn zoom-level" data-zp="reset" aria-label="Reset zoom">100%</button>'
                + '<button type="button" class="zoom-btn" data-zp="in" aria-label="Zoom in">+</button>'
                + (isVideo ? '<button type="button" class="zoom-btn zp-play" data-zp="play" aria-label="Play or pause">⏯</button>' : "");
            lvl = pill.querySelector(".zoom-level");
            playBtn = pill.querySelector(".zp-play");
            pill.addEventListener("click", (e) => {
                const b = e.target.closest("[data-zp]");
                if (!b) return;
                e.stopPropagation();
                const k = b.dataset.zp;
                if (k === "in") zoomAt(z * STEP, container.clientWidth / 2, container.clientHeight / 2);
                else if (k === "out") zoomAt(z / STEP, container.clientWidth / 2, container.clientHeight / 2);
                else if (k === "reset") reset();
                else if (k === "play") togglePlay();
            });
            container.appendChild(pill);
        }

        function togglePlay() { if (isVideo) { if (el.paused) el.play().catch(() => {}); else el.pause(); } }

        function apply() {
            const w = container.clientWidth, h = container.clientHeight;
            x = Math.min(0, Math.max(w * (1 - z), x));   // the picture never leaves the frame
            y = Math.min(0, Math.max(h * (1 - z), y));
            const on = z > 1.001;
            el.style.transform = on ? `translate(${x}px, ${y}px) scale(${z})` : "";
            el.style.willChange = on ? "transform" : "";
            container.classList.toggle("zp-zoomed", on);
            container.style.touchAction = on ? "none" : "pan-y";
            if (lvl) lvl.textContent = `${Math.round(z * 100)}%`;
            if (isVideo) {   // native controls are inside the video and would be scaled; hide them while zoomed
                if (on && el.controls) { controlsOn = true; el.controls = false; }
                else if (!on && controlsOn) { controlsOn = false; el.controls = true; }
                if (playBtn) playBtn.hidden = !on;
            }
            if (opts.onChange) opts.onChange(z);
        }

        // zoom keeping the point (px, py), in container coordinates, fixed on screen
        function zoomAt(nz, px, py) {
            nz = Math.min(MAX, Math.max(MIN, nz));
            const k = nz / z;
            x = px - (px - x) * k;
            y = py - (py - y) * k;
            z = nz;
            apply();
        }
        function reset() { z = 1; x = 0; y = 0; pointers.clear(); pinch = null; container.classList.remove("zp-panning"); apply(); }

        const local = (e) => { const r = container.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
        const onControls = (e) => {   // bottom strip of an un-zoomed video = its native controls: leave them alone
            if (!isVideo || z > 1.001 || !el.controls) return false;
            const r = container.getBoundingClientRect();
            return e.clientY > r.bottom - 56;
        };

        function onWheel(e) {
            if (opts.wheel === "ctrl" && !(e.ctrlKey || e.metaKey)) return;
            if (e.deltaY > 0 && z <= 1.001) return;     // scrolling down at 1x is the page's business
            e.preventDefault();
            const [px, py] = local(e);
            zoomAt(z * Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : e.ctrlKey ? 0.01 : 0.0015)), px, py);
        }

        function onDown(e) {
            if (e.target.closest(".zp-pill") || (e.pointerType === "mouse" && e.button !== 0) || onControls(e)) return;
            if (pointers.size === 0) { moved = 0; multi = false; swallow = false; downAt = [e.clientX, e.clientY]; }
            else multi = true;
            pointers.set(e.pointerId, [e.clientX, e.clientY]);
            if (pointers.size === 2) {
                const [a, b] = [...pointers.values()];
                pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), z };
            }
            if (z > 1.001 || pointers.size > 1) { try { container.setPointerCapture(e.pointerId); } catch (_) { /* gone */ } }
        }
        function onMove(e) {
            const prev = pointers.get(e.pointerId);
            if (!prev) return;
            const cur = [e.clientX, e.clientY];
            pointers.set(e.pointerId, cur);
            moved = Math.max(moved, Math.hypot(cur[0] - downAt[0], cur[1] - downAt[1]));
            const r = container.getBoundingClientRect();
            if (pointers.size === 2 && pinch) {
                const [a, b] = [...pointers.values()];
                zoomAt(pinch.z * Math.hypot(a[0] - b[0], a[1] - b[1]) / Math.max(1, pinch.d), (a[0] + b[0]) / 2 - r.left, (a[1] + b[1]) / 2 - r.top);
            } else if (pointers.size === 1 && z > 1.001) {
                if (moved > 4) container.classList.add("zp-panning");
                x += cur[0] - prev[0];
                y += cur[1] - prev[1];
                apply();
            }
        }
        function onUp(e) {
            if (!pointers.delete(e.pointerId)) return;
            if (pointers.size < 2) pinch = null;
            if (pointers.size > 0) return;
            container.classList.remove("zp-panning");
            const wasDrag = moved > 6 || multi;
            swallow = wasDrag;                           // the click that follows a drag or pinch must not open/close anything
            if (wasDrag || e.type === "pointercancel") { lastTap = null; return; }
            const now = Date.now(), [px, py] = local(e);
            if (lastTap && now - lastTap.t < 320 && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 30) {
                lastTap = null;
                swallow = true;
                if (isVideo && z > 1.001) togglePlay();   // undo the pause the first tap of the pair caused
                zoomAt(z > 1.001 ? 1 : TAP_ZOOM, px, py);
                return;
            }
            lastTap = { t: now, x: e.clientX, y: e.clientY };
            if (isVideo && z > 1.001) togglePlay();      // a plain tap on a zoomed video plays / pauses it
        }
        function onClick(e) {
            if (!swallow || e.target.closest(".zp-pill")) return;
            swallow = false;
            e.stopPropagation();
            e.preventDefault();
        }

        function keyActive() {
            const a = document.activeElement;
            return container.contains(a) || container.matches(":hover") || (opts.active ? opts.active() : false);
        }
        function onKey(e) {
            if (e.ctrlKey || e.metaKey || e.altKey || (e.target.closest && e.target.closest("input, select, textarea")) || !keyActive()) return;
            const cx = container.clientWidth / 2, cy = container.clientHeight / 2;
            if (e.key === "+" || e.key === "=") zoomAt(z * STEP, cx, cy);
            else if (e.key === "-" || e.key === "_") zoomAt(z / STEP, cx, cy);
            else if (e.key === "0") reset();
            else return;
            e.preventDefault();
        }
        const onDrag = (e) => e.preventDefault();

        container.addEventListener("wheel", onWheel, { passive: false });
        container.addEventListener("pointerdown", onDown);
        container.addEventListener("pointermove", onMove);
        container.addEventListener("pointerup", onUp);
        container.addEventListener("pointercancel", onUp);
        container.addEventListener("click", onClick, true);
        container.addEventListener("dragstart", onDrag);
        document.addEventListener("keydown", onKey);
        const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => { if (z > 1.001) apply(); }) : null;
        if (ro) ro.observe(container);
        apply();

        return {
            reset, zoomAt, update: apply,
            zoomed: () => z > 1.001,
            scale: () => z,
            destroy() {
                reset();
                container.removeEventListener("wheel", onWheel);
                container.removeEventListener("pointerdown", onDown);
                container.removeEventListener("pointermove", onMove);
                container.removeEventListener("pointerup", onUp);
                container.removeEventListener("pointercancel", onUp);
                container.removeEventListener("click", onClick, true);
                container.removeEventListener("dragstart", onDrag);
                document.removeEventListener("keydown", onKey);
                if (ro) ro.disconnect();
                if (pill) pill.remove();
                container.classList.remove("zp-clip");
                container.style.touchAction = "";
            },
        };
    }

    g.ZenivoZoomPan = { attach, MIN, MAX };
})(window);
