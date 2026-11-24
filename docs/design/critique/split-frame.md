# Split frame (.ds-split) check, 2026-11-24
One frame for Flights 3D, Satellites 3D and Community: map card (flex:1, canvas only inside) + side list card 380px, 12px gap, own scroll, both fill to the footer. States expanded / rail (56px) / hidden (show button in map corner), key L, Esc to rail, remembered per page (`zenivo.split.<path>.v1`, `.m` on phones; default expanded desktop, rail phone). ≤900px: card stacks below the map.
Measured in real Chrome (iframes of the live page; map x+w x h | side x+w x h | canvas | gap):
- 1280: sat/flights main 244+632x720, side 888+380x720, canvas 630x718, gap 12, no horizontal scroll; community 256+608x704, side 876+380x704.
- 2000: sat/flights main 244+1352x720, side 1608+380x720, canvas 1350x718; community main 1328 wide, side 380.
- 390: map x=12 w=362, side x=12 w=362 (fixed: legacy `body.page-3d #list{left:12px}` rule removed; cached @import busted).
- Overhead, Sky, Live need no split (single-surface pages), unchanged.
Screenshots: not saved to the repo (Chrome tool returns no files); numbers above are the evidence.
