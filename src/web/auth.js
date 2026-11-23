/* Zenivo sign-in state, shared by every page (classic script, loaded in <head>).
 * - marks <html>/<body> with `is-admin` from the last known state at once (sessionStorage) so admin-only items do not flash,
 *   then confirms with /api/auth/status; the server enforces everything, this only decides what to show;
 * - turns every API reply "401 + code auth" into one toast with a sign-in link;
 * - fills the Sign in / Sign out links ([data-auth-link]). */
(function () {
    const KEY = "zenivo.admin";
    const root = document.documentElement;
    const auth = (window.zenivoAuth = { admin: false, configured: true, ready: false });
    try { auth.admin = sessionStorage.getItem(KEY) === "1"; } catch (e) { /* storage blocked */ }
    root.classList.toggle("is-admin", auth.admin);

    function paint() {
        root.classList.toggle("is-admin", auth.admin);
        if (document.body) document.body.classList.toggle("is-admin", auth.admin);
        const here = encodeURIComponent(location.pathname + location.hash);
        document.querySelectorAll("[data-auth-link]").forEach((a) => {
            const label = auth.admin ? "Sign out" : "Sign in", t = a.querySelector("[data-auth-label]");
            if (t) { t.textContent = label; a.setAttribute("aria-label", label); if (a.title) a.title = label; const u = a.querySelector("use"); if (u) u.setAttribute("href", u.getAttribute("href").replace(/#i-.*/, auth.admin ? "#i-log-out" : "#i-log-in")); }
            else a.textContent = label;
            a.setAttribute("href", auth.admin ? "#signout" : `/login?next=${here}`);
        });
    }

    function set(admin, configured) {
        auth.admin = !!admin;
        if (typeof configured === "boolean") auth.configured = configured;
        auth.ready = true;
        try { sessionStorage.setItem(KEY, auth.admin ? "1" : "0"); } catch (e) { /* ignore */ }
        paint();
        document.dispatchEvent(new CustomEvent("zenivo-auth", { detail: auth }));
    }
    auth.set = set;

    auth.signOut = async function () {
        try { await fetch("/api/auth/logout", { method: "POST" }); } catch (e) { /* offline: the cookie stays until it expires */ }
        set(false);
        location.href = ["/settings", "/calibration", "/focus", "/logs"].includes(location.pathname) ? "/live" : location.pathname + location.search;
    };

    let toastAt = 0;
    auth.toast = function () {
        const now = Date.now();
        if (now - toastAt < 6000) return;
        toastAt = now;
        const el = document.createElement("div");
        el.className = "auth-toast";
        el.setAttribute("role", "status");
        el.append("Sign in as admin to change this. ");
        const a = document.createElement("a");
        a.href = `/login?next=${encodeURIComponent(location.pathname + location.hash)}`;
        a.textContent = "Sign in";
        el.append(a);
        (document.body || root).appendChild(el);
        setTimeout(() => el.remove(), 6000);
    };

    // One minimal wrapper: a 401 with code "auth" shows the toast; callers still get the untouched response
    const nativeFetch = window.fetch.bind(window);
    window.fetch = function (...args) {
        return nativeFetch(...args).then((res) => {
            if (res.status === 401) {
                res.clone().json().then((b) => { if (b && b.code === "auth") { set(false); auth.toast(); } }).catch(() => {});
            }
            return res;
        });
    };

    document.addEventListener("click", (ev) => {
        const a = ev.target.closest && ev.target.closest("[data-auth-link]");
        if (a && auth.admin) { ev.preventDefault(); auth.signOut(); }
    });
    document.addEventListener("DOMContentLoaded", paint);

    nativeFetch("/api/auth/status", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((s) => { if (s) set(s.admin, s.configured); })
        .catch(() => {});
})();
