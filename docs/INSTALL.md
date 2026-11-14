# Installing Zenivo

This guide installs Zenivo on a Raspberry Pi (Raspberry Pi OS) or on a Debian/Ubuntu mini-PC or
laptop. After it you have a service that starts at boot and a web UI on a TCP port.

Contents: [Requirements](#requirements) | [Install with install.sh](#install-with-installsh) |
[Install by hand](#install-by-hand) | [Camera access](#camera-access) | [systemd](#systemd-service) |
[nginx](#nginx-reverse-proxy) | [ADS-B feeder](#adsb-feeder-service) | [Upgrading](#upgrading) |
[Existing installs](#existing-installs) | [Uninstall](#uninstall)

## Requirements

| Item | Requirement |
|------|-------------|
| OS | Raspberry Pi OS or Debian/Ubuntu (64-bit recommended) |
| Python | 3.10 or newer (`python3 --version`) |
| Memory | 1.8 GB or more. Tested on a Raspberry Pi 4 with 1.8 GB usable RAM |
| Disk | 8 GB free for the default retention policy (`max_data_gb` 8, `min_free_gb` 3) |
| Packages | `python3-venv`, `python3-pip`, `git` or `rsync`, `ffmpeg` (timelapse), `curl` |
| Pi CSI camera | `python3-picamera2` (apt) |
| Network | Outbound HTTPS for satellite orbits (CelesTrak), weather balloons (SondeHub), airports and aircraft data, plus the optional sync target, remote plate solver, Flightradar24 and community sharing (all off by default). Every destination is listed in [TRANSPARENCY.md](TRANSPARENCY.md#1-outbound-connections-made-by-the-server) |

## Install with install.sh

1. Install the system packages.

   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv python3-pip git ffmpeg curl
   # Raspberry Pi CSI camera only:
   sudo apt install -y python3-picamera2
   ```

2. Set the time zone. Zenivo names archive folders and frames by the **local** date and stamps
   every time with its offset, so the system time zone must be correct before the first run.

   ```bash
   timedatectl list-timezones | grep -i <your city>
   sudo timedatectl set-timezone Europe/Berlin     # example
   timedatectl                                     # check "Time zone"
   ```

3. Get the code.

   ```bash
   git clone https://github.com/hoanb1/zenivo.git ~/zenivo
   cd ~/zenivo
   ```

4. Preview the installer, then run it.

   ```bash
   ./install.sh --dry-run
   ./install.sh
   ```

   `install.sh` checks for Python 3.10+, creates `.venv`, installs the dependencies and installs and
   starts a systemd unit named `zenivo`. It never touches `data/`.

   | Option | Default | Meaning |
   |--------|---------|---------|
   | `--dir PATH` | the repo directory | Install directory (must contain `run_allsky.py`) |
   | `--port N` | `8080` | Port of the web UI and API |
   | `--host ADDR` | `127.0.0.1` | Bind address of the installed service. Use `0.0.0.0` to open it on the LAN (plain HTTP sends the admin password unencrypted: prefer a TLS reverse proxy) |
   | `--camera NAME` | `auto` | `auto`, `rts5869`, `picamera`, `v4l2` or `simulator` |
   | `--user NAME` | current user | User that runs the service |
   | `--service-name NAME` | `zenivo` | systemd unit name |
   | `--no-systemd` | off | Only create the venv and install dependencies |
   | `--dry-run` | off | Print every action and change nothing |

   On a Raspberry Pi with `python3-picamera2` installed, the installer creates the venv with
   `--system-site-packages` so the venv can import the apt-provided `picamera2`.

5. Open `http://<host>:8080/`. Verify the version:

   ```bash
   curl http://localhost:8080/api/version
   ```

   Expected output: `{"product":"Zenivo","version":"1.0.0"}`.

6. Set the admin password. Until you do, nobody can change settings. Read the one-time setup code on the
   station and enter it at `http://<host>:8080/login` together with a password of 10 characters or more:

   ```bash
   cat data/admin_setup_code.txt        # inside the install directory
   # or, without the browser:
   .venv/bin/python run_allsky.py --set-admin-password
   ```

7. In **Settings > Station & Optics**, enter latitude, longitude and elevation. The factory default is a
   neutral "My station" at 0, 0. Wrong coordinates shift aircraft, satellites and planets in every
   overlay.

Visitors who are not signed in can watch (with the station location rounded) but cannot change anything;
see [SECURITY.md](SECURITY.md) and [TRANSPARENCY.md](TRANSPARENCY.md) (what Zenivo sends and stores). Use `--host 127.0.0.1` with a reverse proxy or tunnel over HTTPS.

## Install by hand

Use this if you do not want `install.sh`.

```bash
cd ~/zenivo
# On a Pi with picamera2 from apt, add --system-site-packages:
python3 -m venv .venv                # or: python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

`requirements.txt` lists every runtime dependency: numpy, Pillow, pillow-avif-plugin, sgp4, urllib3, cryptography, the web
framework (FastAPI, uvicorn, websockets), requests, httpx and OpenCV. Zenivo opens no GUI windows, so the headless OpenCV
build is enough. Test tools (pytest) are in `requirements-dev.txt`.

Test it in the foreground:

```bash
.venv/bin/python run_allsky.py --camera auto --port 8080
```

The server binds to `127.0.0.1` (this machine only) by default. Add `--host 0.0.0.0` to open it on the LAN.

Stop it with Ctrl+C, then install the service as described below.

## Camera access

| Camera | What to do |
|--------|-----------|
| USB IMX335 (RTS5869, `0bda:5869`) | The user needs read/write access to `/dev/video*`: `sudo usermod -aG video <user>`, then log in again. The shipped unit adds the `video` group itself. Check with `lsusb \| grep 0bda:5869` and `ls -l /dev/video*` |
| Raspberry Pi CSI | `sudo apt install python3-picamera2`, create the venv with `--system-site-packages`, enable the camera in `raspi-config` if needed. Test with `rpicam-hello --list-cameras` |
| Other UVC webcam | Same `video` group as the USB camera. Use `--camera v4l2` |
| None | `--camera simulator` |

The repo ships no udev rules. If the camera is not ready at boot, Zenivo keeps retrying in the
background (a watchdog tries again every 30 s) and switches over once it opens.

Add `--device /dev/videoN` to the unit's `ExecStart` if the wrong video node is chosen.

## systemd service

`install.sh` renders [`deploy/zenivo.service`](../deploy/zenivo.service) and writes it to
`/etc/systemd/system/<service-name>.service`. To do the same by hand, copy the template, replace
`@USER@`, `@INSTALL_DIR@`, `@HOST@`, `@PORT@` and `@CAMERA@`, and run:

```bash
sudo cp zenivo.service /etc/systemd/system/zenivo.service
sudo systemctl daemon-reload
sudo systemctl enable --now zenivo
```

Useful commands:

```bash
systemctl status zenivo
journalctl -u zenivo -f
tail -f ~/zenivo/data/logs/allsky.log     # rotating file log (5 MB x 3), survives reboots
sudo systemctl restart zenivo
```

The unit runs with `Nice=5` and `MemoryMax=700M`. Raise `MemoryMax` on hosts with more RAM. The
template starts with `--sync-target ""` (sync is off by default anyway); see
[CONFIGURATION.md](CONFIGURATION.md#command-line-flags).

## nginx reverse proxy

Optional. Use it for TLS, a domain name or access control.

```bash
sudo apt install -y nginx
sudo cp deploy/nginx-zenivo.example.conf /etc/nginx/sites-available/zenivo
sudo nano /etc/nginx/sites-available/zenivo        # set server_name and the port
sudo ln -s /etc/nginx/sites-available/zenivo /etc/nginx/sites-enabled/zenivo
sudo nginx -t && sudo systemctl reload nginx
```

Run Zenivo with `--host 127.0.0.1` when nginx is the only entry point. The settings that matter:

| Setting | Why |
|---------|-----|
| `proxy_http_version 1.1`, `Upgrade` and `Connection` headers | The live view uses a WebSocket (`/ws/live`) |
| `proxy_buffering off` | MJPEG (`/api/stream/mjpeg`) and SSE (`/api/stream/sse`) are endless streams |
| `proxy_read_timeout 3600s`, `proxy_send_timeout 3600s` | Streams stay open for a long time |
| `client_max_body_size 20m` | Uploads from the UI |
| `X-Forwarded-Proto` | Zenivo runs uvicorn with proxy headers trusted, so it sees the original scheme and client |

Zenivo has its own single-admin login (see [SECURITY.md](SECURITY.md)); guests can watch but not change anything. Serve it over TLS (or a tunnel such as Cloudflare Tunnel) if the host is reachable from the internet, so the session cookie is Secure.

## ADS-B feeder service

Optional. `tools/adsb_feeder.py` reads the local aircraft list from Zenivo and pushes it to a
ingest endpoint of your choice every few seconds.

1. Create `feeder.env` next to the code from the template. Never commit this file.

   ```bash
   cp deploy/feeder.env.example ~/zenivo/feeder.env
   chmod 600 ~/zenivo/feeder.env
   nano ~/zenivo/feeder.env      # VLAB_URL, VLAB_KEY, VLAB_INTERVAL
   ```

2. Adapt `deploy/zenivo-adsb-feeder.service` (an example unit): replace `/home/pi/zenivo`
   with your install directory, `User=pi` with your user, and `After=zenivo.service` with your
   service name. Set `ALLSKY_URL` in `feeder.env` if Zenivo does not listen on `http://127.0.0.1:8095`.

3. Install and start it.

   ```bash
   sudo cp deploy/zenivo-adsb-feeder.service /etc/systemd/system/zenivo-adsb-feeder.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now zenivo-adsb-feeder
   ```

The unit starts only when `feeder.env` exists. The feeder sends the aircraft list without the
station position and camera pointing.

## Upgrading

Back up first. Never overwrite `data/`.

```bash
# 1. Back up the configuration (small). Back up data/ only if you want the images.
cp ~/zenivo/data/station_config.json ~/station_config.json.bak-$(date +%Y%m%d%H%M%S)

# 2a. Upgrade a git checkout
cd ~/zenivo && git pull

# 2b. Or deploy from another machine: sync the code only, never data/
rsync -ac --exclude data/ --exclude __pycache__ --exclude '*.pyc' \
    ./src ./run_allsky.py user@host:/home/user/zenivo/

# 3. Update dependencies if requirements.txt changed
~/zenivo/.venv/bin/python -m pip install -r ~/zenivo/requirements.txt

# 4. Restart once
sudo systemctl restart zenivo

# 5. Verify
curl http://localhost:8080/api/version
journalctl -u zenivo -n 30 --no-pager
```

If the HTML pages change, the server tags scripts and styles with the version and a hash of the
file, so browsers pick up the new files without a hard refresh. Restart the unit once per deploy.

## Existing installs

Installs from before 2026-11-13 used the unit and folder name `allsky-ng`; since then both are `zenivo`
(`~/zenivo`), and a legacy symlink `~/allsky-ng` -> `zenivo` keeps old scripts working. Ports are unchanged.

To upgrade a host from a checkout on another machine, use the rsync flow above, then restart the existing unit:

```bash
rsync -ac --exclude data/ --exclude __pycache__ --exclude '*.pyc' \
    ./src ./run_allsky.py pi@<pi-host>:/home/pi/zenivo/
ssh pi@<pi-host> 'sudo systemctl restart zenivo'
```

`install.sh` never stops or restarts a unit with a different name. If a host still has an older
`allsky-ng` or `allsky-web` unit, the installer warns about it and leaves it untouched: stop and
disable it yourself if it uses the same port or camera, and run `install.sh` with `--dir` pointing
at the existing folder (this keeps `data/`).

Internal identifiers keep the old "allsky" name: logger names `allsky.*`, the browser storage key
`allsky.ui.prefs.v1`, upload paths `/uploads/allsky/...`, the sync API `/api/allsky/ingest/*` and
the environment variables `ALLSKY_*`.

## Uninstall

```bash
sudo systemctl disable --now zenivo
sudo rm /etc/systemd/system/zenivo.service
sudo systemctl daemon-reload
# Optional: nginx and the feeder
sudo rm -f /etc/nginx/sites-enabled/zenivo /etc/nginx/sites-available/zenivo && sudo systemctl reload nginx
sudo systemctl disable --now zenivo-adsb-feeder 2>/dev/null; sudo rm -f /etc/systemd/system/zenivo-adsb-feeder.service
# Remove the code. This deletes data/ too (frames, darks, station_config.json): copy it first if you need it.
rm -rf ~/zenivo
```
