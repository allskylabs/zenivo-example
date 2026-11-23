#!/usr/bin/env python3
"""Regenerate the shared chrome of every page from ONE definition: top bar + phone tab bar + one nav list (desktop sidebar = phone Menu sheet; TABS, MENU), the Zenivo
brand mark + wordmark (BRAND) and the head links (title, description, icons, manifest, Open Graph, ds css/js: HEAD).

Usage: python3 scripts/sync_nav.py            # rewrite src/web/*.html
       python3 scripts/sync_nav.py --check    # exit 1 when a page is out of date (CI / pre-commit)

The blocks between `<!-- chrome:start -->`/`<!-- chrome:end -->`, `<!-- brand:start -->`/`<!-- brand:end -->` and
`<!-- head-brand:start -->`/`<!-- head-brand:end -->` in each page are replaced. Pages differ only in which
link is marked current (index.html leaves that to app.js through data-tab)."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WEB = os.path.join(os.path.dirname(__file__), "..", "src", "web")
PAGES = {"index.html": None, "sky.html": "/sky", "overhead.html": "/overhead", "flights3d.html": "/flights3d", "satellites3d.html": "/satellites3d",
         "community.html": "/community", "login.html": "/login"}
NO_NAV = {"login.html"}  # standalone page without sidebar: only head and brand are generated

# Zenivo mark, inline so it follows the theme (ring = --accent, inner ring + star = --text-primary; see .brand-mark in style.css)
BRAND = [
    '<svg class="brand-mark" width="28" height="28" viewBox="0 0 64 64" aria-hidden="true">'
    '<path class="bm-ring" d="M36.2 6.35A26 26 0 1 1 27.8 6.35" fill="none" stroke-width="6"/>'
    '<path class="bm-tick" d="M32 4.6V9.6" fill="none" stroke-width="2.2" stroke-linecap="round"/>'
    '<circle class="bm-inner" cx="32" cy="32" r="18.5" fill="none" stroke-width="3.5"/>'
    '<polygon class="bm-star" points="38.00,23.80 38.85,25.84 41.04,26.01 39.37,27.44 39.88,29.59 38.00,28.44 36.12,29.59 '
    '36.63,27.44 34.96,26.01 37.15,25.84"/></svg>',
    '<h1 class="brand-name">Zenivo</h1>',
]
TAGLINE = "All-sky observatory software"
# <title> and <meta description> per page
HEAD = {
    "index.html": ("Zenivo — All-sky observatory", "Zenivo: live view, calibration, sky overlay and event detection for your all-sky camera."),
    "sky.html": ("Sky view — Zenivo", "Interactive planetarium of the sky above your Zenivo all-sky station."),
    "overhead.html": ("Overhead — Zenivo", "Aircraft, satellites and weather balloons above your all-sky station, as lists and a sky radar."),
    "flights3d.html": ("Flights 3D — Zenivo", "3D map of the aircraft your all-sky station hears over ADS-B."),
    "satellites3d.html": ("Satellites 3D — Zenivo", "3D map of the satellites above your all-sky station."),
    "login.html": ("Sign in — Zenivo", "Sign in to the admin controls of your Zenivo all-sky station."),
    "community.html": ("Community — Zenivo", "Map of the Zenivo all-sky stations that share themselves with the community."),
}

# Admin-only destinations (hidden for guests via data-admin; the server enforces the same list)
ADMIN_HREFS = {"/logs", "/focus", "/calibration", "/obs/dock"}

# Phone tab bar (<1024px): (href, icon, label, paths that mark it current). The 5th slot opens the nav as a sheet.
TABS = [("/live", "camera", "Live", "/live,/,/index.html"), ("/sky", "sparkles", "Sky", "/sky"),
        ("/overhead", "radio", "Overhead", "/overhead"), ("/gallery", "archive", "Archive", "/gallery")]
# Navigation list, ONE definition for the desktop sidebar and the phone Menu sheet:
# (section title or None, admin-only group, [(href, icon, label, admin item, new tab)])
MENU = [
    (None, False, [("/live", "camera", "Live", False, False), ("/sky", "sparkles", "Sky", False, False),
                   ("/overhead", "radio", "Overhead", False, False), ("/gallery", "archive", "Archive", False, False),
                   ("/community", "users", "Community", False, False)]),
    ("Explore", False, [("/flights3d", "plane", "Flights 3D", False, False),
                        ("/satellites3d", "satellite", "Satellites 3D", False, False)]),
    ("Camera", True, [("/focus", "telescope", "Focus", False, False),
                      ("/calibration", "moon-star", "Master dark", False, False),
                      ("/settings#image", "sliders-horizontal", "Image output", False, False),
                      ("/settings#detection", "crosshair", "Detection", False, False)]),
    ("Station", True, [("/settings#station", "map-pin", "Station &amp; optics", False, False),
                       ("/settings#skymask", "shield", "Sky mask", False, False),
                       ("/settings#integrations", "plug", "Integrations", False, False),
                       ("/settings#community", "share-2", "Sharing", False, False),
                       ("/logs", "activity", "Activity log", False, False),
                       ("/settings#admin", "key-round", "Admin", False, False)]),
    ("Streaming", False, [("/obs/dock", "film", "OBS dock", True, False),
                          ("/obs", "arrow-up-right", "OBS", False, True)]),
]
MORE_PATHS = ",".join(sorted({h.split("#")[0] for _, _, items in MENU for h, *_ in items} - {t[0] for t in TABS}))


def ic(name):
    return f'<svg class="ds-icon" aria-hidden="true"><use href="/static/icons.svg#i-{name}"></use></svg>'


def render_brand():
    return "\n".join(["<!-- brand:start (generated by scripts/sync_nav.py) -->"] + ["            " + b for b in BRAND]
                      + ["            <!-- brand:end -->"])


def render_head(name):
    title, desc = HEAD[name]
    img = "/static/brand/og-image.png"
    lines = ["<!-- head-brand:start (generated by scripts/sync_nav.py) -->", f"<title>{title}</title>",
             f'<meta name="description" content="{desc}">', '<meta name="theme-color" content="#070a13">',
             '<link rel="icon" href="/static/brand/favicon.svg" type="image/svg+xml">',
             '<link rel="icon" href="/favicon.ico" sizes="any">',
             '<link rel="apple-touch-icon" href="/static/brand/apple-touch-icon.png">',
             '<link rel="manifest" href="/site.webmanifest">',
             '<meta property="og:site_name" content="Zenivo">', '<meta property="og:type" content="website">',
             f'<meta property="og:title" content="{title}">', f'<meta property="og:description" content="{desc}">',
             f'<meta property="og:image" content="{img}">', '<meta name="twitter:card" content="summary_large_image">',
             '<script>try{if(localStorage.getItem("zenivo.nav")==="0")document.documentElement.classList.add("zs-collapsed")}catch(e){}</script>',
             # old hash deep links (/#archive, /#sky ...) open the page's own URL before first paint
             '<script>(function(){var p=location.pathname.replace(/\\/+$/,"")||"/";if(p!=="/"&&p!=="/live"&&p!=="/index.html")return;'
             'var h=location.hash.replace(/^#\\/?/,"").toLowerCase(),M={archive:"/gallery",gallery:"/gallery",events:"/gallery",sky:"/sky",overhead:"/overhead",community:"/community",'
             'flights:"/flights3d",flights3d:"/flights3d",satellites:"/satellites3d",satellites3d:"/satellites3d",settings:"/settings",calibration:"/calibration",logs:"/logs",activity:"/logs",focus:"/focus",live:"/live"},'
             'S="image detection station skymask integrations community admin".split(" "),t=M[h];if(h==="community")t="/community";'
             'else if(S.indexOf(h)>-1)t="/settings#"+h;if(t&&t!==p)location.replace(t)})()</script>',
             '<link rel="stylesheet" href="/static/css/ds-tokens.css?v=20261123a">',
             '<link rel="stylesheet" href="/static/css/ds-components.css?v=20261204c">',
             '<link rel="stylesheet" href="/static/css/shell.css?v=20261204c">',
             '<script src="/static/auth.js?v=20261204c"></script>',
             '<script src="/static/js/ds.js?v=20261123a" defer></script>',
             '<script src="/static/brand/version.js?v=4" defer></script>',
             '<script src="/static/shell.js?v=20261204c" defer></script>', "<!-- head-brand:end -->"]
    return "\n    ".join(lines)


def render(current, is_index):
    o = ['<!-- chrome:start (generated by scripts/sync_nav.py: edit TABS / MENU there, not here) -->',
         '    <header class="ds-topbar" id="topbar">',
         '        <a class="zt-brand" href="/live" aria-label="Zenivo">', '            ' + render_brand(), '        </a>',
         '        <div class="ds-topbar__title"><span class="zt-name" id="m-head-name">Zenivo</span></div>',
         '        <span class="zt-meta" id="sensor-subtitle"></span>',
         '        <span class="zt-meta" id="nav-camera-mode"></span>',
         '        <span class="zt-meta" id="nav-fps"></span>',
         '        <span class="ds-sr-only" id="nav-system-status" role="status">Online</span>',
         '        <div class="zt-cluster">',
         '            <span class="zt-status" id="zt-status" tabindex="0" role="img" aria-label="Online" title="Online"><i class="pulse-indicator" id="status-pulse"></i></span>',
         '            <span class="zt-clocks" id="zt-clocks" tabindex="0" title="UTC --:--:--">' + ic("clock") + '<b class="zt-local" id="nav-local-clock">--:--</b><span class="zt-utc"><b id="nav-utc-clock">--:--:--</b></span><span class="ds-sr-only" id="nav-local-label">Local</span></span>',
         '            <span class="ds-topbar__clock" id="m-head-clock" hidden></span>',
         '            <button class="ds-btn ds-btn--ghost ds-btn--icon" type="button" id="btn-night-vision" aria-pressed="false" '
         f'aria-label="Red night vision" title="Red night vision">{ic("moon-star")}</button>',
         '        </div>',
         '    </header>', '',
         '    <nav class="ds-tabbar" aria-label="Main navigation">']
    for h, i, t, p in TABS:
        o.append(f'        <a class="ds-tab" href="{h}" data-paths="{p}">{ic(i)}<span>{t}</span></a>')
    o += [f'        <button class="ds-tab" type="button" data-ds-open="zs-nav" data-paths="{MORE_PATHS}" aria-haspopup="dialog">'
          f'{ic("menu")}<span>Menu</span></button>', '    </nav>', '',
          '    <aside class="ds-sheet zs" id="zs-nav" role="dialog" aria-modal="true" aria-label="Menu">',
          '        <div class="ds-sheet__scrim" data-ds-close></div>', '        <div class="zs__panel">',
          '            <div class="zs__head"><div class="ds-sheet__title"><span id="m-st-name">Zenivo</span></div>'
          f'<button class="ds-btn ds-btn--ghost ds-btn--icon" type="button" data-ds-close aria-label="Close">{ic("x")}</button></div>',
          '            <nav class="zs__nav" aria-label="Sections">']
    for title, admin_grp, items in MENU:
        o.append(f'                <section class="zs__group"{" data-admin" if admin_grp else ""}>')
        if title:
            o.append(f'                    <h3 class="zs__title">{title}</h3>')
        for h, i, label, adm, blank in items:
            path, _, frag = h.partition("#")
            attrs = f' data-paths="{path}"' + (f' data-hash="{frag}"' if frag else "") + (" data-admin" if adm else "")
            attrs += ' target="_blank" rel="noopener"' if blank else ""
            plain = label.replace("&amp;", "&")
            badge = '<b class="zt-badge" id="log-error-badge" hidden>0</b>' if h == "/logs" and is_index else ""
            o.append(f'                    <a class="zs__item" href="{h}"{attrs} data-tip="{plain}" aria-label="{plain}">'
                     f'{ic(i)}<span class="zs__label">{label}</span>{badge}</a>')
        o.append('                </section>')
    o += ['            </nav>',
          '            <div class="zs__foot">',
          '                <div class="zs__utils">',
          f'                    <button class="zs__item" type="button" id="m-fs-btn" data-tip="Fullscreen" aria-label="Fullscreen">{ic("maximize")}<span class="zs__label">Fullscreen</span></button>',
          f'                    <button class="zs__item" type="button" id="m-share-btn" data-tip="Share" aria-label="Share">{ic("share-2")}<span class="zs__label">Share</span></button>',
          f'                    <button class="zs__item" type="button" id="m-chat-btn" data-tip="Chat" aria-label="Chat">{ic("message-circle")}<span class="zs__label">Chat</span></button>',
          '                </div>',
          f'                <a class="zs__item" href="/login" data-auth-link data-tip="Sign in" aria-label="Sign in">{ic("log-in")}<span class="zs__label" data-auth-label>Sign in</span></a>',
          '                <div class="zs__meta"><small class="zs__ver" id="app-version" data-app-version>Zenivo</small>'
          f'<button class="zs__toggle" type="button" id="zs-toggle" aria-expanded="true" aria-controls="zs-nav" aria-label="Collapse menu" data-tip="Collapse menu">{ic("chevrons-left")}</button></div>',
          '            </div>', '        </div>', '    </aside>', '    <!-- chrome:end -->']
    return "\n".join(o)


def patch(text, current, is_index):
    block = render(current, is_index)
    return re.sub(r"<!-- chrome:start.*?<!-- chrome:end -->", lambda m: block, text, flags=re.S)


def patch_page(name, text, current, is_index):
    if name not in NO_NAV:
        text = patch(text, current, is_index)
    text = re.sub(r"<!-- brand:start.*?<!-- brand:end -->", lambda m: render_brand(), text, flags=re.S)
    return re.sub(r"<!-- head-brand:start.*?<!-- head-brand:end -->", lambda m: render_head(name), text, flags=re.S)


def main() -> int:
    stale = []
    for name, cur in PAGES.items():
        # index.html is generated (scripts/build_index.py): its shared chrome lives in pages/00-shell.html
        path = os.path.join(WEB, "pages/00-shell.html" if name == "index.html" else name)
        old = open(path, encoding="utf-8").read()
        new = patch_page(name, old, cur, cur is None)
        if new != old:
            stale.append(name)
            if "--check" not in sys.argv:
                open(path, "w", encoding="utf-8").write(new)
    if "--check" not in sys.argv:
        import build_index
        build_index.main()
    print(("stale: " if "--check" in sys.argv else "updated: ") + (", ".join(stale) or "none"))
    return 1 if stale and "--check" in sys.argv else 0


if __name__ == "__main__":
    sys.exit(main())
