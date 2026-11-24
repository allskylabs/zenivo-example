# STATE: imx335-allsky (Zenivo)

Updated 2026-11-24 by the PM (handoff to Antigravity, see docs/handoff/to-agy.md). Release tag v1.3.0-beta.6 (pushed; no GitHub release, as for beta.5). Live on sky.hoan.uk (mini-PC, IMX335) and allsky.hoan.uk (Pi, imx708): 1.3.0b6, md5 checked on both hosts. Backups: mini-PC ~/zenivo-bak-*.tgz, Pi ~/zenivo-src-bak-*.tgz and zenivo-web-bak-*.tgz.

## Owner decisions
- **UI: full clean rebuild on the ds system, no more patching the old UI.** Mockups (23 pages) in docs/design/mockups/ and the owner's opnai account (permanent, never edit).
- **Icons first WITH short visible labels.** Never hide status, values, names or menu labels in a tooltip; long text goes behind an info icon. Text >= 12 px.
- **One function, one control per page** (dedupe rule, docs/design/ui-critique.md Update 10b): no second link/button for the same destination; the sidebar owns navigation, Settings list owns the rest.
- **Map/3D/Community pages share one split frame** (map card + side list card, list hideable: expanded / rail / hidden); nothing overlays the map. Dark surface + light icons on every control.
- Deploys: static web = rsync to Pi + mini-PC from main HEAD only, md5 check, no restart. Python changes need `systemctl restart zenivo` (Pi ~1.5 min, mini-PC ~1 min). Never reboot the mini-PC.
- Hosts: mini-PC `ssh hoan@minipcv6.hoan.uk` (192.168.6.15 times out from the laptop); Pi `ssh -J hoan@minipcv6.hoan.uk pi@100.127.30.11` (tailnet). 192.168.3.24 is NOT the Pi.

## Done (all on main, released as 1.3.0-beta.6)
- UI rebuild on ds for all pages: Live, Overhead, Sign-in, Archive + event viewer, Sky, Community, Logs, Flights/Satellites, Settings (+ Stream view / Stream control, one sky mask).
- Sidebar (collapsible, Ctrl+B, no Admin item: Settings > Admin only), topbar chips, object info card, split frame + hideable list, map controls theme, Community marker projection fix.
- Mobile chat: FAB visible above the tab bar, input keeps focus on send (no keyboard flicker).
- Community: Seed stations list + Auto discovery (periodic fetch of peers' station lists, SSRF-safe, cap 200).
- Cloud cover rewrite: day colour/spread method, night star clarity capped by moon, temporal median + hysteresis, per-condition audit 38.6% -> 88.6% agreement (docs/design/cloud-audit.md); Pi imx708 31/33.
- Earlier: focus peaking, star-boost grain fix, events detector gates + owner labels.

## In progress
Nothing running. Workers kept idle: Worker-15 (backend, deploy), Worker-17 (UI). Max 3 workers at once.

## Open / next
- Verify on a real phone: chat keyboard behaviour, Menu sheet, 390 px layouts of the split pages (screenshots not saved in docs/design/critique/).
- Community stations list shows stale duplicate entries (e.g. Jasper, Arkansas 8 days ago; Hanoi, Vietnam 4 days ago) and lists this station's own seed with a stale "answered 253 h ago": dedupe by station id and hide self.
- Cloud: Pi has no clear-sky frame yet, revisit WB/blue test on the first clear day; known misses: Pi 2026-11-19 23:00 (68 vs 90), 11-22 15:00 hazy (50 vs 85).
- Detector: measure event gates on real new events after a few days and tune from owner labels; night moon exclusion not done. YOLO not needed now; if classification is wanted later, a small classifier on event crops, never per-frame.
- **Skyview not rebuilt via vite:** deps `astrophoto` and `hoan.uk/client/node_modules` are missing on the laptop and the runners; bundle patched by hand plus source (tools/skyview/src).
- Legacy: some pages still load style.css + redesign*.css (ds_lint --report); red mode on every page, a11y pass, loading/empty/error states not verified.
- Optional: zenivo.service stop takes ~90 s then SIGKILL (not investigated); ui-design skill wording still says "text only as short label/tooltip" (outdated vs owner rule).

## Branches / worktrees
- main = released; origin/main pushed up to the release docs commit.
- `fix/reticle-first-paint` (worktree strange-maxwell): 1 unmerged docs commit (Facebook group cover image); merge or drop on the owner's word.
- Worktrees kind-bassi-a793d8 (Worker-17) and wonderful-cannon-863002 (Worker-15) kept; others removed 2026-11-24.

## Blockers
- Claude quota nearly out; work continues on Antigravity (docs/handoff/to-agy.md).
