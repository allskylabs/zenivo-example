/* /login: first-time setup (code + new password) or the password form. Same-site `next` only. */
(function () {
    const form = document.getElementById("login-form");
    const pw = document.getElementById("login-password");
    const repeat = document.getElementById("login-repeat");
    const code = document.getElementById("login-code");
    const err = document.getElementById("login-error");
    const submit = document.getElementById("login-submit");
    let setup = false;

    function safeNext() {
        const n = new URLSearchParams(location.search).get("next") || "/";
        return n.startsWith("/") && !n.startsWith("//") && !n.includes("\\") && !/[\u0000-\u001f]/.test(n) && !n.startsWith("/login") ? n : "/";
    }
    function show(msg) { err.textContent = msg; err.classList.toggle("hidden", !msg); }

    function mode(isSetup) {
        setup = isSetup;
        document.getElementById("login-title").textContent = isSetup ? "First-time setup" : "Admin sign-in";
        const lock = document.querySelector(".login-lock"); if (lock) lock.hidden = isSetup;
        document.getElementById("login-lead").textContent = isSetup
            ? "Enter the setup code, then choose a password of at least 10 characters."
            : "Settings and calibration need an admin session.";
        document.getElementById("field-code").hidden = !isSetup;
        document.getElementById("field-repeat").hidden = !isSetup;
        document.getElementById("login-password-label").textContent = isSetup ? "New password" : "Password";
        pw.autocomplete = isSetup ? "new-password" : "current-password";
        submit.textContent = isSetup ? "Set password and sign in" : "Sign in";
        (isSetup ? code : pw).focus();
    }

    fetch("/api/auth/status", { cache: "no-store" }).then((r) => r.json()).then((s) => {
        if (s.admin) { location.replace(safeNext()); return; }
        mode(!s.configured);
    }).catch(() => show("Cannot reach the station. Check the connection and reload."));

    form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        show("");
        if (setup) {
            if (!code.value.trim()) return show("Enter the setup code.");
            if (pw.value.length < 10) return show("The password needs at least 10 characters.");
            if (pw.value !== repeat.value) return show("The two passwords differ.");
        } else if (!pw.value) {
            return show("Enter the password.");
        }
        submit.disabled = true;
        try {
            const res = await fetch(setup ? "/api/auth/setup" : "/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(setup ? { code: code.value.trim(), password: pw.value } : { password: pw.value }),
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                window.zenivoAuth && window.zenivoAuth.set(true, true);
                location.replace(safeNext());
                return;
            }
            const d = data.detail;
            show(res.status === 429 ? String(d || "Too many attempts, wait a moment.")
                : Array.isArray(d) ? d.map((e) => e.msg).join("; ") : String(d || "Sign-in failed."));
            pw.value = "";
            if (repeat) repeat.value = "";
        } catch (e) {
            show("Cannot reach the station. Check the connection and try again.");
        } finally {
            submit.disabled = false;
        }
    });
})();
