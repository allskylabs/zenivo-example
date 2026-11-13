# deploy/

Files for running Zenivo as a service. Nothing in this directory is read by the application.

| File | Use |
|------|-----|
| `zenivo.service` | systemd unit template for new installs. `install.sh` fills the `@PLACEHOLDERS@` and installs it. |
| `nginx-zenivo.example.conf` | Generic nginx reverse proxy with WebSocket, MJPEG and SSE settings. Edit `server_name` and the port. |
| `allsky-ng.service` | Live unit of the Raspberry Pi 4 (`allsky-ng`, `/home/pi/allsky-ng`, port 8095). Kept for reference; do not use for new installs. |
| `nginx-allsky.hoan.uk.conf` | Live nginx server block of that Pi (allsky.hoan.uk, behind cloudflared). |
| `allsky-adsb-feeder.service` | Optional unit that runs `tools/adsb_feeder.py` and pushes the aircraft list to a virtuallab3d endpoint. |
| `feeder.env.example` | Template for the feeder's `feeder.env` (URL, key, interval). Copy it, fill in the key, `chmod 600`. Never commit the real file. |

The two `allsky-*` units and `nginx-allsky.hoan.uk.conf` belong to hosts that are already running.
Their names are internal identifiers and stay unchanged. See [docs/INSTALL.md](../docs/INSTALL.md).
