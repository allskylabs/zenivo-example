#!/usr/bin/env python3
"""Assemble src/web/index.html from src/web/pages/*.html (sorted by file name): 00-shell (head, topbar, navigation, <main>),
10-live ... 60-logs (one <section class="tab-content"> per page) and 90-tail (</main>, sheets, scripts).

index.html is GENERATED and committed (the API serves it as is): edit the partials, then run this script.
Usage: python3 scripts/build_index.py          # rewrite src/web/index.html
       python3 scripts/build_index.py --check  # exit 1 when index.html is out of date (CI)"""
import glob
import os
import sys

WEB = os.path.join(os.path.dirname(__file__), "..", "src", "web")


def assemble() -> str:
    parts = sorted(glob.glob(os.path.join(WEB, "pages", "*.html")))
    return "".join(open(p, encoding="utf-8").read() for p in parts)


def main() -> int:
    out = os.path.join(WEB, "index.html")
    new = assemble()
    old = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
    if new == old:
        print("index.html: up to date")
        return 0
    if "--check" in sys.argv:
        print("index.html: STALE (run scripts/build_index.py)")
        return 1
    open(out, "w", encoding="utf-8").write(new)
    print("index.html: rebuilt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
