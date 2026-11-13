# Zenivo brand guide

This guide covers the name, the logo, the colours, the type and the voice of Zenivo. The design
tokens in `src/web/tokens.css` are the single source for colours in the product.

## Name

- The product is **Zenivo**. Capital Z, lowercase rest.
- Never write "ZENIVO", "zenivo" in running text, or "Zenivo NG". Use lowercase `zenivo` only where
  a technical name requires it: the Python package, the command, the systemd unit, file names.
- Tagline: **All-sky observatory software**. Use it as written, without a final period in titles.
- "all-sky" is the generic term for the camera and the sky view and is spelled with a hyphen.
  Internal identifiers keep the old name and are not rebranded: logger names `allsky.*`, the
  browser storage key `allsky.ui.prefs.v1`, `/uploads/allsky/...`, `/api/allsky/ingest/*`, the
  `ALLSKY_*` environment variables and the script name `run_allsky.py`.

## Logo

The mark is two concentric rings and one star, the view of a fisheye all-sky lens. The outer ring is
Zenivo Blue with a small tick at north. The inner ring and the star are white (Starlight). The
tick marks north, so keep the mark upright.

Files in `src/web/brand/` (served at `/static/brand/`):

| File | Use |
|------|-----|
| `mark.svg` | The mark alone, in colour, on dark backgrounds. Sidebar, app icon contexts |
| `mark-mono.svg` | One colour (`currentColor`). Print, stamps, single-colour UI, watermarks |
| `logo-horizontal.svg` | Mark and wordmark on dark backgrounds (Deep Navy, Ink, photos of the night sky). README, docs, splash |
| `logo-horizontal-light.svg` | Mark and wordmark on white or light backgrounds (uses the darker blue `#0ea5e9`) |
| `favicon.svg`, `favicon.ico` | Browser tab icon |
| `icon-192.png`, `icon-512.png` | Web app icons referenced by `site.webmanifest` |
| `apple-touch-icon.png` | 180 x 180 icon for iOS home screens |
| `og-image.png` | 1200 x 630 social preview image |
| `site.webmanifest` | Web app manifest (name, theme colour, icons) |

### Clear space and minimum size

- Keep clear space around the logo equal to one quarter of the mark's height on every side
  (16 units on the 64-unit artboard). Nothing else may enter this area.
- Minimum size: the mark at **16 px** wide, the horizontal logo at **96 px** wide. Below 96 px use
  the mark alone.

### Don'ts

- Do not recolour the rings or the star, except to the one-colour `mark-mono.svg`.
- Do not stretch, rotate, skew, outline or add shadows or glows.
- Do not remove the north tick or the star, and do not add a second star.
- Do not place the dark-background logo on a light background, or the reverse. Use the matching file.
- Do not set the wordmark in another typeface. Use the SVG files.
- Do not put the logo on busy backgrounds with low contrast.

## Colour

| Name | Hex | Token | Use |
|------|-----|-------|-----|
| Zenivo Blue | `#38bdf8` | `--brand-blue`, `--accent` | Primary accent: links, active states, logo ring, charts |
| Zenivo Blue (hover) | `#0284c7` | `--accent-hover` | Hover and pressed state of accent elements |
| Deep Navy | `#070a13` | `--brand-navy`, `--bg-primary` | Page background, icon tile |
| Ink | `#0d1322` | `--brand-ink`, `--bg-card` | Cards and raised surfaces |
| Card sub | `#131d31` | `--bg-card-sub` | Inputs and nested surfaces |
| Border | `#1e293b` | `--border-color` | Dividers and outlines |
| Starlight | `#f1f5f9` | `--brand-starlight`, `--text-primary` | Primary text, logo inner ring and star |
| Secondary text | `#94a3b8` | `--text-secondary` | Labels and secondary copy |
| Muted | `#64748b` | `--text-muted` | Hints and disabled text |
| Aurora | `#10b981` | `--brand-aurora`, `--success` | Success, online, clear sky |
| Amber | `#f59e0b` | `--brand-amber`, `--warning` | Warnings |
| Meteor | `#ef4444` | `--brand-meteor`, `--danger` | Errors and destructive actions |

The accent tint for selected backgrounds is `--accent-bg` (Zenivo Blue at 16 % opacity).
Use colour for meaning, not decoration: green, amber and red mean good, caution and problem.
Never rely on colour alone; add text or an icon.

### Red Vision

Red Vision is a real alternate palette for observers who protect their dark adaptation. It is set
by `data-theme="red"` on the page and replaces the tokens; it is not a CSS filter. In this theme
nothing on screen is blue or green.

| Token | Value |
|-------|-------|
| `--bg-primary` | `#0a0000` |
| `--bg-card` | `#140404` |
| `--bg-card-sub` | `#1c0707` |
| `--border-color` | `#3a0e0e` |
| `--text-primary` | `#ff6b6b` |
| `--text-secondary` | `#cc4444` |
| `--text-muted` | `#8a2e2e` |
| `--accent` | `#ff4d4d` |
| `--accent-hover` | `#cc3333` |
| `--success` | `#d94a4a` |
| `--warning` | `#ff7a5c` |
| `--danger` | `#ff2d2d` |

Any new component must read these tokens, never hard-coded hex values, so it works in both themes.

## Typography

| Role | Font | Use |
|------|------|-----|
| UI | Inter (fallbacks: system sans-serif) | Headings, labels, body text. Base size 14 px, minimum 12 px |
| Numbers and telemetry | JetBrains Mono (fallbacks: system monospace) | Exposure, gain, coordinates, times, counters |

Tokens: `--font-sans`, `--font-mono`, `--font-size-body`, `--font-size-min`. Fonts are not bundled;
the stacks fall back to the system fonts. Use the mono face for values that change, so digits
do not jump.

## Voice

- Plain and precise. Say what the software does and what the reader can do about it.
- Always show units: `0.25 s`, `12 dB`, `21.0285 deg N`, `8 GB`. Write the unit after a space.
- Use numbers, not adjectives: "uses 0.4 s minimum interval", not "very fast".
- Short sentences, active voice, present tense. Address the reader as "you" in docs and UI help.
- Errors say what failed, why, and what to do next.
- No marketing language, no exclamation marks, no emoji in documentation.
- Dates are ISO 8601 (`2026-11-13`); times carry the offset where it matters (`06:34:17+07:00`).
