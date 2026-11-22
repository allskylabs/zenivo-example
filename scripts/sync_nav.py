#!/usr/bin/env python3
"""Regenerate the shared chrome of every page from ONE definition: the sidebar + phone navigation (NAV), the Zenivo brand
mark + wordmark in the top bar (BRAND) and the brand/head links (title, description, icons, manifest, Open Graph: HEAD).

Usage: python3 scripts/sync_nav.py            # rewrite src/web/*.html
       python3 scripts/sync_nav.py --check    # exit 1 when a page is out of date (CI / pre-commit)

The blocks between `<!-- nav:start -->`/`<!-- nav:end -->`, `<!-- brand:start -->`/`<!-- brand:end -->` and
`<!-- head-brand:start -->`/`<!-- head-brand:end -->` in each page are replaced. Pages differ only in which
link is marked current (index.html leaves that to app.js through data-tab)."""
import os
import re
import sys

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

# (group title or None, items). item = (href, icon entity, label, kind, data-tab) ; kind: "tab" = page of index.html (app.js
# switches it), "link" = separate page, "blank" = new tab
NAV = [
    ("Sky", [("/community", "&#127760;", "Community", "link", ""),
             ("/live", "&#9673;", "Live", "tab", "tab-live"),
             ("/sky", "&#10024;", "Sky view", "link", ""),
             ("/flights3d", "&#9992;", "Flights 3D", "link", ""),
             ("/satellites3d", "&#128752;", "Satellites 3D", "link", ""),
             ("/overhead", "&#9737;", "Overhead", "link", "")]),
    ("Archive", [("/gallery", "&#9638;", "Gallery &amp; Events", "tab", "tab-gallery"),
                 ("/logs", "&#8801;", "Activity Log", "tab", "tab-logs")]),
    ("Camera", [("/focus", "&#9678;", "Focus", "tab", "tab-focus"),
                ("/calibration", "&#9680;", "Master Dark", "tab", "tab-calibration")]),
    ("Broadcast", [("/obs/dock", "&#9635;", "OBS Dock", "link", ""),
                   ("/obs", "&#8599;", "OBS Preview", "blank", "")]),
]
# Items only the admin sees (hidden for guests via data-admin; the server enforces the same list)
ADMIN_HREFS = {"/logs", "/focus", "/calibration", "/obs/dock"}
SETTINGS = [("image", "Image Output"), ("detection", "Detection"), ("station", "Station &amp; Optics"),
            ("skymask", "Sky Mask"), ("integrations", "Integrations"), ("community", "Community"),
            ("admin", "Admin")]
# Line icons (24x24, stroke = currentColor) for the phone tab bar and the Menu sheet tiles
ICONS = {
    "camera": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/>',
    "star": '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    "nav": '<polygon points="3 11 22 2 13 21 11 13 3 11"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20"/>',
    "pin": '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0z"/><circle cx="12" cy="10" r="3"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/>',
    "bars": '<path d="M12 20V10M18 20V4M6 20v-4"/>',
    "bolt": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "home": '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "play": '<polygon points="6 4 20 12 6 20 6 4"/>',
    "users": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
}


def icon(name, cls="m-svg", size=22):
    return (f'<svg class="{cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


# phone tab bar, same order as the sidebar: Community / Live / Sky / Archive / Menu (the rest lives in the Menu sheet)
MOBILE = [("/community", "pin", "Community", "/community"), ("/live", "camera", "Live", "/live,/,/index.html"),
          ("/sky", "star", "Sky", "/sky"), ("/gallery", "calendar", "Archive", "/gallery,/logs")]
MORE_PATHS = "/flights3d,/satellites3d,/overhead,/focus,/calibration,/settings,/obs"
# Menu sheet groups: (title, admin-only group, [(href, icon, label, tone, admin item, new tab)])
MENU = [
    ("Explore", False, [("/flights3d", "nav", "Flights 3D", "accent", False, False),
                        ("/satellites3d", "globe", "Satellites 3D", "accent", False, False),
                        ("/overhead", "nav", "Overhead", "accent", False, False)]),
    ("Camera", True, [("/focus", "search", "Focus", "violet", True, False),
                      ("/calibration", "camera", "Master dark", "violet", True, False),
                      ("/settings#image", "bars", "Image output", "violet", True, False),
                      ("/settings#detection", "bolt", "Detection", "violet", True, False)]),
    ("Station", True, [("/settings#station", "home", "Station &amp; optics", "ok", True, False),
                       ("/settings#skymask", "shield", "Sky mask", "ok", True, False),
                       ("/settings#integrations", "plus", "Integrations", "ok", True, False),
                       ("/settings#community", "users", "Sharing", "ok", True, False),
                       ("/settings#admin", "lock", "Admin", "ok", True, False),
                       ("/logs", "clock", "Activity log", "ok", True, False)]),
    ("Streaming", False, [("/obs/dock", "play", "OBS dock", "warn", True, False),
                          ("/obs", "play", "OBS preview", "warn", False, True)]),
]


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
             '<script src="/static/auth.js?v=1"></script>',
             '<script src="/static/brand/version.js?v=4" defer></script>',
             '<script src="/static/shell.js?v=1" defer></script>', "<!-- head-brand:end -->"]
    return "\n    ".join(lines)


def side_item(href, ic, label, kind, tab, current, is_index):
    cls = "tab-btn side-item" if kind == "tab" else "side-item side-link"
    if current == href and not is_index:
        cls += " active"
    attrs = f' data-tab="{tab}"' if kind == "tab" else ""
    attrs += " data-admin" if href in ADMIN_HREFS else ""
    attrs += ' aria-current="page"' if current == href and not is_index else ""
    attrs += ' target="_blank" rel="noopener"' if kind == "blank" else ""
    badge = ' <span class="log-badge" id="log-error-badge" hidden>0</span>' if href == "/logs" and is_index else ""
    return (f'                <a class="{cls}" href="{href}"{attrs}><span class="side-ic">{ic}</span>'
            f'<span class="side-txt">{label}{badge}</span></a>')


def render(current, is_index):
    o = ['<!-- nav:start (generated by scripts/sync_nav.py: edit NAV there, not here) -->',
         '    <aside class="sidebar" id="sidebar">', '        <nav class="tab-bar side-nav" aria-label="Main navigation">']
    for title, items in NAV:
        o.append('            <div class="side-group">' if title else '            <div class="side-group side-group-home">')
        if title:
            o.append(f'                <div class="side-group-title">{title}</div>')
        o += [side_item(*it, current, is_index) for it in items]
        o.append('            </div>')
    o += ['            <div class="side-group side-group-settings" data-admin>', '                <div class="side-group-title">Settings</div>',
          '                <a class="tab-btn side-item" href="/settings" data-tab="tab-settings"><span class="side-ic">&#9881;</span>'
          '<span class="side-txt">Settings</span></a>', '                <div class="side-sub-list" id="settings-subnav">']
    o += [f'                    <a class="side-sub" href="/settings#{k}" data-sub="{k}">{t}</a>' for k, t in SETTINGS]
    o += ['                </div>', '            </div>', '        </nav>',
          '        <div class="side-foot"><span id="app-version" data-app-version>Zenivo</span>'
          '<br><a class="side-auth" href="/login" data-auth-link>Sign in</a></div>', '    </aside>', '',
          '    <!-- Phone navigation (<=768px): 4 tabs + Menu sheet, same routes as the sidebar -->',
          '    <nav class="m-tabbar" id="m-tabbar" aria-label="Mobile navigation">']
    o += [f'        <a class="m-tab" href="{h}" data-mpath="{p}"><span class="m-ic">{icon(i)}</span><span class="m-txt">{t}</span></a>'
          for h, i, t, p in MOBILE]
    o += [f'        <button class="m-tab" type="button" id="m-more-btn" aria-haspopup="dialog" aria-expanded="false" '
          f'aria-controls="m-more-sheet" data-mpath="{MORE_PATHS}"><span class="m-ic">{icon("menu")}</span><span class="m-txt">Menu</span></button>',
          '    </nav>', '    <div class="m-sheet-backdrop" id="m-sheet-backdrop" hidden></div>',
          '    <div class="m-sheet" id="m-more-sheet" role="dialog" aria-modal="true" aria-label="Menu" hidden>',
          '        <div class="m-sheet-grip" aria-hidden="true"></div>',
          '        <div class="m-st"><i class="m-st-dot" aria-hidden="true"></i><div class="m-st-txt"><strong class="m-st-name" id="m-st-name">Zenivo</strong>'
          '<span class="m-st-sub">Online &middot; <span id="app-version-m" data-app-version>Zenivo</span></span></div>'
          '<a class="m-signin" href="/login" data-auth-link>Sign in</a></div>',
          '        <div class="m-quick">',
          '            <button class="m-qtile m-sheet-rv" type="button" id="m-rv-toggle" role="switch" aria-checked="false"><b>Red vision</b><span id="m-rv-state">Off</span></button>',
          '            <button class="m-qtile" type="button" id="m-fs-btn"><b>Fullscreen</b><span>Live view</span></button>',
          '            <button class="m-qtile" type="button" id="m-share-btn"><b>Share</b><span id="m-share-sub">Station link</span></button>',
          '        </div>']
    for title, admin_grp, items in MENU:
        o.append(f'        <section class="m-group"{" data-admin" if admin_grp else ""}>')
        o.append(f'            <h3 class="m-sheet-title">{title}{"<span>admin</span>" if admin_grp else ""}</h3>')
        o.append('            <div class="m-grid">')
        for h, ic, label, tone, adm, blank in items:
            path = h.split("#")[0]
            attrs = (f' data-mpath="{path}"' if "#" not in h else "") + (" data-admin" if adm and not admin_grp else "")
            attrs += ' target="_blank" rel="noopener"' if blank else ""
            o.append(f'                <a class="m-tile m-sheet-item tone-{tone}" href="{h}"{attrs}>{icon(ic, "m-svg", 20)}<span>{label}</span></a>')
        o += ['            </div>', '        </section>']
    o += ['    </div>', '    <!-- nav:end -->']
    return "\n".join(o)


def patch(text, current, is_index):
    block = render(current, is_index)
    if "<!-- nav:start" in text:
        return re.sub(r"<!-- nav:start.*?<!-- nav:end -->", lambda m: block, text, flags=re.S)
    a = text.index('<aside class="sidebar"')
    a = text.rfind("\n", 0, a) + 1
    # first-time migration: also swallow the old phone navigation comment, bar and sheet
    cut = text.index("m-rv-toggle", a)
    e = text.index("</div>", text.index("</button>", cut)) + len("</div>")
    start = text.rfind("<!-- Sidebar", 0, a + 1)
    a = start if start != -1 and a - start < 400 else a
    return text[:a] + "    " + block + text[e:]


MHEAD = ('<!-- mhead:start (generated by scripts/sync_nav.py) -->\n'
         '        <div class="m-head"><i class="m-head-dot" id="m-head-dot" aria-hidden="true"></i><span class="m-head-name" id="m-head-name">Zenivo</span>'
         '<span class="m-head-clock" id="m-head-clock"></span></div>\n        <!-- mhead:end -->')


def patch_mhead(text):
    """Phone header (one line: status dot, station name, clock): generated into every page's <header class="navbar">."""
    if "<!-- mhead:start" in text:
        return re.sub(r"<!-- mhead:start.*?<!-- mhead:end -->", lambda m: MHEAD, text, flags=re.S)
    tag = '<header class="navbar">'
    i = text.find(tag)
    return text if i < 0 else text[:i + len(tag)] + "\n        " + MHEAD + text[i + len(tag):]


def patch_page(name, text, current, is_index):
    if name not in NO_NAV:
        text = patch_mhead(patch(text, current, is_index))
    text = re.sub(r"<!-- brand:start.*?<!-- brand:end -->", lambda m: render_brand(), text, flags=re.S)
    return re.sub(r"<!-- head-brand:start.*?<!-- head-brand:end -->", lambda m: render_head(name), text, flags=re.S)


def main() -> int:
    stale = []
    for name, cur in PAGES.items():
        path = os.path.join(WEB, name)
        old = open(path, encoding="utf-8").read()
        new = patch_page(name, old, cur, cur is None)
        if new != old:
            stale.append(name)
            if "--check" not in sys.argv:
                open(path, "w", encoding="utf-8").write(new)
    print(("stale: " if "--check" in sys.argv else "updated: ") + (", ".join(stale) or "none"))
    return 1 if stale and "--check" in sys.argv else 0


if __name__ == "__main__":
    sys.exit(main())
