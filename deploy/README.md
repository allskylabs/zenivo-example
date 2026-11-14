# deploy/

Files for running Zenivo as a service. They are examples to adapt (user, paths, ports, host names); nothing in this directory is read by the application.

| File | Use |
|------|-----|
| `zenivo.service` | systemd unit template for new installs. `install.sh` fills the `@PLACEHOLDERS@` and installs it. |
| `nginx-zenivo.example.conf` | Generic nginx reverse proxy with WebSocket, MJPEG and SSE settings. Edit `server_name` and the port. |
| `zenivo-pi.service` | Example unit for a Raspberry Pi 4 behind a reverse proxy (`/home/pi/zenivo`, port 8095, a sync target and extra prune directories). For new installs use `zenivo.service` through `install.sh`. Memory limits (`OOMScoreAdjust`, `MemoryHigh`, `MemoryMax`) can be added in a systemd drop-in. |
| `zenivo-adsb-feeder.service` | Optional example unit that runs `tools/adsb_feeder.py` and pushes the aircraft list to an ingest endpoint you configure. |
| `feeder.env.example` | Template for the feeder's `feeder.env` (URL, key, interval). Copy it, fill in the key, `chmod 600`. Never commit the real file. |

Installations from before 2026-11-13 used the name `allsky-ng`; a symlink `~/allsky-ng` -> `~/zenivo` keeps old scripts working. See [docs/INSTALL.md](../docs/INSTALL.md).
