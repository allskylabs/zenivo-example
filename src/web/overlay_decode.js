/* Zenivo: decoder and store for the packed overlay pushes of /ws/live ({type:"ov"}, src/api/overlay_pack.py).
 * Shared by the dashboard (app/05c-aircraft-overlay.js) and the Overhead list page (overhead.html).
 * Rows arrive as arrays in the field order below, only the rows that changed; rebuilt here into the objects the REST endpoints return. */
(function () {
    const FIELDS = {
        aircraft: ["icao", "callsign", "altitude_m", "speed_kmh", "heading_deg", "climb_m_min", "az_deg", "alt_deg", "distance_km", "fx", "fy", "vx", "vy", "fl"],
        satellites: ["norad", "name", "height_km", "az_deg", "alt_deg", "fx", "fy", "fl"],
        sondes: ["serial", "type", "altitude_m", "climb_m_s", "az_deg", "alt_deg", "distance_km", "fx", "fy", "vx", "vy", "fl"],
    };
    const store = {};   // layer -> { rows: Map(id -> object), meta: {} }
    // Applies one {type:"ov"} message; returns { ...meta, t, [layer]: rows[] } (aircraft nearest first) or null for an unknown layer
    function apply(msg) {
        const fields = FIELDS[msg.l];
        if (!fields) return null;
        let st = store[msg.l];
        if (msg.f || !st) st = store[msg.l] = { rows: new Map(), meta: {} };
        Object.entries(msg.m || {}).forEach(([k, v]) => { if (v === null) delete st.meta[k]; else st.meta[k] = v; });
        (msg.x || []).forEach((id) => st.rows.delete(id));
        (msg.u || []).forEach((r) => {
            const o = {};
            fields.forEach((f, i) => { o[f] = r[i]; });
            if (o.fx != null && o.fy != null) o.frame_xy = [o.fx / 10000, o.fy / 10000];
            if (o.vx != null && o.vy != null) o.frame_v = [o.vx / 10000, o.vy / 10000];
            o.in_view = !!(o.fl & 1); o.above_horizon = !!(o.fl & 2); o.visible = !!(o.fl & 4);
            st.rows.set(r[0], o);
        });
        const list = [...st.rows.values()];
        if (msg.l === "aircraft") list.sort((a, b) => (a.distance_km ?? 1e9) - (b.distance_km ?? 1e9));
        return { ...st.meta, t: msg.t, [msg.l]: list };
    }
    window.ZenivoOverlay = { FIELDS, store, apply };
})();
