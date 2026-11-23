#!/usr/bin/env python3
"""Design-system lint. Default: the new ds files (must be clean, exit 1 on violation).
`--report FILE...`: page-level report for legacy/new pages (never fails).
Rules: hex colours (only ds-tokens.css may define them), font-size < 12px (11px only as --fs-11 in tokens),
px border-radius outside tokens, z-index outside the named --z-* layers."""
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "src" / "web"
DS_FILES = [WEB / "css" / "ds-tokens.css", WEB / "css" / "ds-base.css", WEB / "css" / "ds-components.css"]
TOKENS = "ds-tokens.css"

HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b(?![-\w])")
FONT = re.compile(r"font-size\s*:\s*(\d+(?:\.\d+)?)px")
FONT_SH = re.compile(r"font\s*:\s*[^;]*?(\d+(?:\.\d+)?)px")
RADIUS = re.compile(r"border(?:-[a-z]+){0,2}-radius\s*:\s*([^;}]+)")
Z = re.compile(r"z-index\s*:\s*([^;}]+)")


def strip_comments(t: str) -> str:
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), t, flags=re.S)


def lint_text(text: str, name: str, is_tokens: bool = False):
    out = []
    for n, line in enumerate(strip_comments(text).split("\n"), 1):
        if not is_tokens and HEX.search(line) and not line.lstrip().startswith("<"):
            out.append((name, n, "hex colour (use a --c-* token)"))
        for m in list(FONT.finditer(line)) + list(FONT_SH.finditer(line)):
            if float(m.group(1)) < 12 and not (is_tokens and "--fs-11" in line):
                out.append((name, n, f"font-size {m.group(1)}px < 12px"))
        for m in RADIUS.finditer(line):
            v = m.group(1)
            if re.search(r"\d+px", v) and re.search(r"(?<![\w-])[1-9]\d*px", re.sub(r"var\([^)]*\)", "", v)):
                out.append((name, n, f"px radius '{v.strip()}' (use --r-*)"))
        for m in Z.finditer(line):
            v = m.group(1).strip()
            if not v.startswith("var(--z-") and v not in ("auto", "0"):
                out.append((name, n, f"z-index '{v}' outside named layers"))
    return out


def lint_ds():
    res = []
    for p in DS_FILES:
        res += lint_text(p.read_text(), p.name, p.name == TOKENS)
    return res


def main(argv):
    if argv and argv[0] == "--report":
        total = 0
        for f in argv[1:]:
            p = Path(f)
            v = lint_text(p.read_text(errors="ignore"), p.name)
            kinds = {}
            for _, _, msg in v:
                k = msg.split(" ")[0]
                kinds[k] = kinds.get(k, 0) + 1
            print(f"{p.name}: {len(v)} findings {kinds}")
            total += len(v)
        print(f"total {total} (report only)")
        return 0
    v = lint_ds()
    for name, n, msg in v:
        print(f"{name}:{n}: {msg}")
    print("ds-lint:", "FAIL" if v else "ok", f"({len(v)} violations)")
    return 1 if v else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
