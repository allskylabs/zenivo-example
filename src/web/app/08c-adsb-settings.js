/* Zenivo dashboard (part of the former app.js): Settings: event detection, ADS-B, Flightradar24, weather balloons, daytime objects.
 * Classic script: continues 08-gallery-adsb.js; top-level declarations are shared with the other src/web/app/*.js files; load order is fixed in index.html. */

// Settings → Event detection
const eventToggles = [...document.querySelectorAll("input[data-event]")];

function syncEventConfigUI(data) {
    if (!data || !data.enabled) return;
    eventToggles.forEach((el) => { el.checked = !!data.enabled[el.dataset.event]; });
    const st = document.getElementById("evt-status");
    if (st) {
        st.textContent = data.night
            ? `Watching now: ${data.frames_compared} of ${data.frames_seen} frames compared tonight.`
            : "Daytime: only aircraft and flashes are watched (meteors and satellites need the dark).";
    }
}

async function loadEventConfig() {
    if (!eventToggles.length) return;
    try {
        const res = await fetch("/api/events/config");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        syncEventConfigUI(await res.json());
    } catch (err) {
        showAlert(stationAlert, `❌ Could not load event detection settings: ${err.message}`, "error", true);
    }
}

eventToggles.forEach((el) => {
    el.addEventListener("change", async () => {
        const ok = await settingsSave(el, async () => {
            syncEventConfigUI(await sendJson("/api/events/config", { [el.dataset.event]: el.checked }));
        });
        if (!ok) loadEventConfig();
    });
});
if (currentPage === "tab-settings") loadEventConfig();

// Settings → ADS-B flight matching
const adsbHost = document.getElementById("adsb-host");
const adsbPort = document.getElementById("adsb-port");
const adsbEnabled = document.getElementById("adsb-enabled");
const adsbStatus = document.getElementById("adsb-status");
const adsbPublic = document.getElementById("adsb-public-fallback");
const adsbSourceStatus = document.getElementById("adsb-source-status");
const adsbContribute = document.getElementById("adsb-contribute");
const adsbContributeStatus = document.getElementById("adsb-contribute-status");

const sondesEnabled = document.getElementById("sondes-enabled");

function syncAdsbUI(d) {
    if (!d || !adsbHost) return;
    if (document.activeElement !== adsbHost) adsbHost.value = d.host || "";
    if (document.activeElement !== adsbPort) adsbPort.value = d.port || 30003;
    adsbEnabled.checked = !!d.enabled;
    const st = d.status;
    let txt = !d.enabled ? "Off." : !st ? "Starting…"
        : st.connected ? `Connected: ${st.with_position} aircraft with position.`
        : st.last_error ? `Not connected: ${st.last_error}` : "Idle (reads the feed only at night with aircraft detection on).";
    if (d.enabled && !d.pointing_solved) txt += " Needs a star-solved camera pointing (plate solve) to match flights.";
    adsbStatus.textContent = txt;
    if (adsbPublic) {
        adsbPublic.checked = !!d.public_fallback;
        const ps = d.public_status || {};
        const src = d.source_in_use === "receiver" ? "your receiver"
            : d.source_in_use === "public" ? `public network (${ps.provider || "Zenivo network"})` : "none";
        let line = `Source in use: ${src}.`;
        if (d.public_fallback && ps.last_error) line += ` Last public error: ${ps.last_error}`;
        adsbSourceStatus.textContent = line;
    }
    if (adsbContribute) {
        adsbContribute.checked = !!d.contribute;
        const cs = d.contribute_status || {};
        const nh = document.getElementById("adsb-network-host");
        if (nh && d.network_host) nh.textContent = d.network_host;
        adsbContributeStatus.textContent = !d.contribute ? ""
            : !d.network_host ? "No network server: set a remote solver URL first."
            : cs.last_error ? `Last error: ${cs.last_error}`
            : cs.last_ok ? `Sending ${cs.last_sent} aircraft (${cs.posts} posts).` : "Waiting for your receiver.";
    }
    syncFr24UI(d);
    if (sondesEnabled) sondesEnabled.checked = d.radiosondes_enabled !== 0 && d.radiosondes_enabled !== false;
}

async function loadAdsb() {
    try {
        const res = await fetch("/api/adsb");
        if (res.ok) syncAdsbUI(await res.json());
    } catch (err) { /* optional feature */ }
}

// Local receiver: host, port and the switch save themselves (host may be empty = receiver off)
if (adsbHost) {
    autosave(adsbHost.closest("fieldset"), async () => {
        syncAdsbUI(await sendJson("/api/adsb", { host: adsbHost.value.trim(), port: Number(adsbPort.value) || 30003, enabled: adsbEnabled.checked }));
        setTimeout(loadAdsb, 3000);
    }, { selector: "#adsb-host, #adsb-port, #adsb-enabled" });
}
if (adsbPublic) {
    autosave(adsbPublic.closest("fieldset"), async () => {
        syncAdsbUI(await sendJson("/api/adsb", { public_fallback: adsbPublic.checked }));
        setTimeout(loadAdsb, 3000);
    });
}
if (adsbContribute) {
    autosave(adsbContribute.closest(".field"), async () => {
        syncAdsbUI(await sendJson("/api/adsb", { contribute: adsbContribute.checked }));
        setTimeout(loadAdsb, 3000);
    });
}
if (currentPage === "tab-settings") loadAdsb();

// Settings → Flightradar24: switch, optional account (e-mail + password, or a subscription token). The token and the
// password are write-only: the server only says whether a token is set. They are sent only when typed here.
const fr24Enabled = document.getElementById("fr24-enabled");
const fr24Email = document.getElementById("fr24-email");
const fr24Password = document.getElementById("fr24-password");
const fr24Token = document.getElementById("fr24-token");
const fr24TokenState = document.getElementById("fr24-token-state");
const fr24Clear = document.getElementById("btn-fr24-clear-token");
const fr24Status = document.getElementById("fr24-status");

let fr24Loaded = false;
function syncFr24UI(d) {
    if (!fr24Enabled || !d) return;
    fr24Loaded = true;
    fr24Enabled.checked = !!d.fr24_enabled;
    if (document.activeElement !== fr24Email) fr24Email.value = d.fr24_email || "";
    const has = !!d.fr24_has_token;
    fr24TokenState.textContent = has ? "Token saved" : "";
    fr24Clear.hidden = !has;
    fr24Token.placeholder = has ? "Paste a new token to replace it" : "";
    const f = d.fr24 || {};
    let txt;
    if (!d.fr24_enabled) txt = "Off.";
    else {
        txt = has || f.authenticated ? "Using your Flightradar24 account (subscription feed)." : "Using the public feed (no account).";
        if (f.last_error) txt += ` Last error: ${f.last_error}`;
        else if (f.last_fetch_age_s !== null && f.last_fetch_age_s !== undefined) txt += ` Last update ${Math.round(f.last_fetch_age_s)} s ago, ${f.global_total || 0} aircraft worldwide.`;
    }
    fr24Status.textContent = txt;
}

async function saveFr24(withSecrets) {
    const body = { fr24_enabled: fr24Enabled.checked };
    if (withSecrets) {
        const em = fr24Email.value.trim();
        if (em && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)) throw new Error("that is not an e-mail address");
        body.fr24_email = em;
    }
    // The password and token leave this page only on the explicit button (never on autosave: a browser's
    // password manager can fill these fields by itself, and a filled field must not be sent anywhere)
    const pw = withSecrets ? fr24Password.value : "";
    const tk = withSecrets ? fr24Token.value.trim() : "";
    if (!fr24Loaded) throw new Error("still loading, try again");
    if (pw && !body.fr24_email) throw new Error("enter the account e-mail with the password");
    if (pw) body.fr24_password = pw;
    if (tk) body.fr24_token = tk;
    const data = await sendJson("/api/adsb", body);
    if (withSecrets) {
        fr24Password.value = "";
        fr24Token.value = "";
    }
    syncAdsbUI(data);
    if (data.fr24_login && !data.fr24_login.ok) throw new Error(`Flightradar24 sign-in failed: ${data.fr24_login.message}`);
}

if (fr24Enabled) {
    autosave(fr24Enabled.closest("fieldset"), () => saveFr24(false));
    const fr24Signin = document.getElementById("btn-fr24-signin");
    if (fr24Signin) fr24Signin.addEventListener("click", async () => {
        await settingsSave(fr24Signin, () => saveFr24(true));
    });
    fr24Clear.addEventListener("click", async () => {
        await settingsSave(fr24Clear, async () => {
            syncAdsbUI(await sendJson("/api/adsb", { fr24_token: "" }));
        });
    });
}

// Settings → Weather balloons (SondeHub): one switch, saved on change
if (sondesEnabled) {
    autosave(sondesEnabled.closest("fieldset"), async () => {
        syncAdsbUI(await sendJson("/api/adsb", { radiosondes_enabled: sondesEnabled.checked }));
    });
}

// Settings → Satellites (CelesTrak groups, refresh, source) and the Data sources overview
const satBlock = document.getElementById("sat-block");
const satEnabled = document.getElementById("sat-enabled");
const satRefresh = document.getElementById("sat-refresh");
const satUrl = document.getElementById("sat-url");
const satStatus = document.getElementById("sat-status");
const satGroupBoxes = [...document.querySelectorAll("[data-sat-group]")];

function syncSatUI(d) {
    if (!d || !satBlock) return;
    satEnabled.checked = !!d.enabled;
    satGroupBoxes.forEach((b) => { b.checked = (d.groups || []).includes(b.dataset.satGroup); });
    satRefresh.value = String(d.refresh_h);
    satUrl.value = d.tle_url || "";
    const s = d.status || {};
    const age = s.fetched_at ? `updated ${Math.round((Date.now() / 1000 - s.fetched_at) / 3600)} h ago` : "not downloaded yet";
    satStatus.textContent = (s.fetching ? "Downloading… " : "") + `${s.count || 0} satellites, ${age}` +
        (s.error ? `. Last error: ${s.error}` : "");
}

async function loadSat() {
    if (!satBlock) return;
    try {
        const res = await fetch("/api/satellites/config");
        if (res.ok) syncSatUI(await res.json());
    } catch (err) { /* admin only */ }
}

if (satBlock) {
    autosave(satBlock, async () => {
        const groups = satGroupBoxes.filter((b) => b.checked).map((b) => b.dataset.satGroup);
        if (!groups.length) throw new Error("keep at least one group");
        syncSatUI(await sendJson("/api/satellites/config", {
            enabled: satEnabled.checked, groups, refresh_h: Number(satRefresh.value), tle_url: satUrl.value.trim(),
        }));
        setTimeout(() => { loadSat(); loadDataSources(); }, 4000);
    }, { selector: "#sat-enabled, [data-sat-group], #sat-refresh, #sat-url" });
    document.getElementById("sat-refresh-now").addEventListener("click", async (ev) => {
        ev.currentTarget.disabled = true;
        try {
            const d = await sendJson("/api/satellites/refresh", {});
            satStatus.textContent = d.started ? "Downloading…" : "A download is already running.";
            [3000, 9000].forEach((ms) => setTimeout(() => { loadSat(); loadDataSources(); }, ms));
        } catch (err) {
            satStatus.textContent = `Could not start the download: ${err.message}`;
        }
        ev.currentTarget.disabled = false;
    });
}

const dataSourcesList = document.getElementById("data-sources-list");
const STATE_LABEL = { ok: "Working", waiting: "Waiting", error: "Problem", off: "Off" };

async function loadDataSources() {
    if (!dataSourcesList) return;
    try {
        const res = await fetch("/api/data-sources");
        if (!res.ok) throw new Error(res.status === 401 ? "sign in as admin" : `HTTP ${res.status}`);
        const d = await res.json();
        dataSourcesList.innerHTML = "";
        d.sources.forEach((s) => {
            const row = document.createElement("div");
            row.className = "ds-row";
            const dot = document.createElement("span");
            dot.className = `ds-dot ds-${s.state}`;
            dot.title = STATE_LABEL[s.state] || s.state;
            const main = document.createElement("div");
            main.className = "ds-main";
            const name = document.createElement("strong");
            name.textContent = s.name;
            const detail = document.createElement("small");
            detail.textContent = [STATE_LABEL[s.state], s.detail, s.host].filter(Boolean).join(" · ");
            main.append(name, detail);
            row.append(dot, main);
            dataSourcesList.appendChild(row);
        });
    } catch (err) {
        dataSourcesList.textContent = `Could not load the data sources: ${err.message}`;
    }
}
const dsRefresh = document.getElementById("data-sources-refresh");
if (dsRefresh) dsRefresh.addEventListener("click", () => { loadDataSources(); loadSat(); loadAdsb(); });
if (currentPage === "tab-settings") { loadSat(); loadDataSources(); }

// Settings → Detection → daytime objects (birds, insects, aircraft)
const dayObj = {
    group: document.getElementById("dayobj-group"),
    enabled: document.getElementById("dayobj-enabled"),
    classes: document.querySelectorAll("[data-dayobj-class]"),
    sens: document.getElementById("dayobj-sensitivity"),
    days: document.getElementById("dayobj-days"),
    mb: document.getElementById("dayobj-mb"),
    minA: document.getElementById("dayobj-min-area"),
    maxA: document.getElementById("dayobj-max-area"),
    contrast: document.getElementById("dayobj-contrast"),
    status: document.getElementById("dayobj-status"),
};
const DAYOBJ_NAMES = { plane: "aircraft", bird: "birds", insect: "insects", object: "other" };

function syncDayObjUI(d) {
    const c = d && d.config;
    if (!c || !dayObj.group) return;
    const set = (el, v) => { if (el && document.activeElement !== el) el.value = v; };
    dayObj.enabled.checked = !!c.enabled;
    dayObj.classes.forEach((el) => { el.checked = c.classes.includes(el.dataset.dayobjClass); });
    dayObj.sens.value = c.sensitivity;
    set(dayObj.days, c.retention_days);
    set(dayObj.mb, c.max_mb);
    set(dayObj.minA, c.min_blob_area);
    set(dayObj.maxA, c.max_blob_area);
    set(dayObj.contrast, c.min_contrast_dn === null ? "" : c.min_contrast_dn);
    if (d.presets && d.presets[c.sensitivity]) dayObj.contrast.placeholder = `${d.presets[c.sensitivity].min_contrast_dn} (from sensitivity)`;
}

async function loadDayObjStatus() {
    if (!dayObj.group) return;
    try {
        const res = await fetch("/api/day-objects/status");
        if (!res.ok) return;
        const st = await res.json();
        syncDayObjUI(st);
        const parts = [];
        if (!st.config.enabled) parts.push("Off.");
        else if (st.tracker && st.tracker.raining) parts.push("Rain on the dome: recording paused.");
        const midnight = new Date();
        midnight.setHours(0, 0, 0, 0);
        const r = await fetch(`/api/day-objects?t0=${midnight.getTime() / 1000}&t1=${Date.now() / 1000}&limit=20000`);
        if (r.ok) {
            const counts = {};
            ((await r.json()).tracks || []).forEach((t) => { counts[t.cls] = (counts[t.cls] || 0) + 1; });
            const txt = Object.keys(DAYOBJ_NAMES).filter((k) => counts[k]).map((k) => `${counts[k]} ${DAYOBJ_NAMES[k]}`).join(", ");
            parts.push(`Today: ${txt || "nothing recorded yet"}.`);
        }
        dayObj.status.textContent = ` ${parts.join(" ")}`;
    } catch (err) { /* optional feature */ }
}

if (dayObj.group) {
    autosave(dayObj.group, async () => {
        const body = {
            enabled: dayObj.enabled.checked,
            classes: [...dayObj.classes].filter((el) => el.checked).map((el) => el.dataset.dayobjClass),
            sensitivity: dayObj.sens.value,
            retention_days: Number(dayObj.days.value),
            max_mb: Number(dayObj.mb.value),
            min_blob_area: Number(dayObj.minA.value),
            max_blob_area: Number(dayObj.maxA.value),
            min_contrast_dn: dayObj.contrast.value.trim() === "" ? null : Number(dayObj.contrast.value),
        };
        for (const k of ["retention_days", "max_mb", "min_blob_area", "max_blob_area"]) {
            if (!Number.isFinite(body[k]) || body[k] <= 0) throw new Error("enter a number");
        }
        syncDayObjUI(await sendJson("/api/day-objects/config", body));
        loadDayObjStatus();
    });
    if (currentPage === "tab-settings") {
        loadDayObjStatus();
        setInterval(loadDayObjStatus, 60000);
    }
}
