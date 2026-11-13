# deploy/

Files for running Zenivo as a service. Nothing in this directory is read by the application.

| File | Use |
|------|-----|
| `zenivo.service` | systemd unit template for new installs. `install.sh` fills the `@PLACEHOLDERS@` and installs it. |
| `nginx-zenivo.example.conf` | Generic nginx reverse proxy with WebSocket, MJPEG and SSE settings. Edit `server_name` and the port. |
| `zenivo-pi.service` | Live unit of the Raspberry Pi 4 (`zenivo`, `/home/pi/zenivo`, port 8095). Kept for reference; do not use for new installs. On the Pi, the drop-ins `/etc/systemd/system/zenivo.service.d/10-hoan-oom.conf` (`OOMScoreAdjust=300`) and `20-hoan-memory.conf` (`MemoryHigh=550M`, `MemoryMax=700M`) add to it. |
| `nginx-allsky.hoan.uk.conf` | Live nginx server block of that Pi (allsky.hoan.uk, behind cloudflared). It proxies by port 8095; the site file on the Pi is still named `allsky-ng`. |
| `zenivo-adsb-feeder.service` | Optional unit (live on the Pi as `zenivo-adsb-feeder`) that runs `tools/adsb_feeder.py` and pushes the aircraft list to a virtuallab3d endpoint. |
| `feeder.env.example` | Template for the feeder's `feeder.env` (URL, key, interval). Copy it, fill in the key, `chmod 600`. Never commit the real file. |

`zenivo-pi.service`, `zenivo-adsb-feeder.service` and `nginx-allsky.hoan.uk.conf` describe a host that is already running.
The legacy symlink `/home/pi/allsky-ng` points to `/home/pi/zenivo` for old scripts. See [docs/INSTALL.md](../docs/INSTALL.md).
