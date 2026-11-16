# First run: set up your station

Do this once after [installing](INSTALL.md). It takes about 15 minutes, plus one clear night for the
pointing check. Every step says where to click and how to see that it worked. Open the web UI
(`http://<host>:8080/`, or your own address) and sign in as admin first.

| # | Step | Where | Why it matters |
|---|------|-------|----------------|
| 1 | Set the admin password | `/login` | Nothing can be changed until you do |
| 2 | Set the system time zone | the machine | Archive folders and frame names use the local date |
| 3 | Pick the camera | Settings > Image Output > Camera | Only needed with several cameras |
| 4 | Enter the station position | Settings > Station & Optics | Aircraft, satellites and planets are drawn from it |
| 5 | Describe sensor and lens | Settings > Station & Optics | Maps pixels to sky angles |
| 6 | Set the camera mounting and check it on a clear night | Settings > Station & Optics | Overlays only line up when the pointing is right |
| 7 | Orientation and image output | Settings > Image Output | Mirror/flip, resolution, white balance, load on small hosts |
| 8 | Sky mask | Settings > Sky Mask | Keeps trees and buildings out of the star search and cloud analysis |
| 9 | Optional services | Settings > Integrations, Community | ADS-B receiver, sync, sharing (all off by default) |
| 10 | Storage and dark frame | Settings > Admin, Master Dark | Disk use, hot pixels |

## 1. Admin password

Read the one-time code on the machine, open `/login`, enter the code and choose a password (10 to 256
characters):

```bash
cat ~/zenivo/data/admin_setup_code.txt
```

Details and recovery (`--set-admin-password`, `--reset-admin`) are in [SECURITY.md](SECURITY.md#first-start-and-the-setup-code).
Guests can look at the live view but cannot change anything.

## 2. Time zone

```bash
timedatectl list-timezones | grep -i <your city>
sudo timedatectl set-timezone Europe/Berlin    # example
timedatectl                                    # check "Time zone", then restart Zenivo
```

Check: the `Local` clock at the top of the page shows your local time.

## 3. Camera

Settings > Image Output > **Camera**. The list shows every camera Zenivo can see (IMX335 USB, Pi camera,
ZWO ASI, QHY, webcams). Choose one; the choice is saved and used at start when the service runs with
`--camera auto`. **Rescan** looks again after you plug a camera in; when a ZWO or QHY camera is plugged in but not offered, the line under Rescan (and `grep "Camera scan" data/allsky.log`) says why: missing `zwoasi` package, missing vendor SDK, or missing udev rules. `scripts/install_camera_sdk.sh zwo` (or `qhy`) fixes all three; it asks before using sudo (only the udev rules need it). ZWO and QHY need their vendor SDK first,
see [INSTALL.md](INSTALL.md#camera-access).

Check: the Live page shows the picture within a few seconds.

## 4. Station position (Settings > Station & Optics)

Open <https://sky.hoan.uk/settings#station> on the public demo, or `/settings#station` on your own station.

1. **Station name**: free text, shown in the page header and, if you share, in the community list.
2. **Latitude, Longitude, Elevation**: decimal degrees (north and east are positive) and metres above sea level. Use
   a map or phone app for the exact spot. **Locate via GPS / IP** fills them in, but without browser GPS your public
   IP address goes to a geolocation service, and an IP lookup is only accurate to the town. Prefer typing the values.
3. Press **Save**.

Check: aircraft and satellites match what you see. A position that is a few kilometres off barely moves the
stars but shifts aircraft by many degrees, so a wrong position shows up first as aircraft that do not line up.

## 5. Sensor and lens

In the same card:

- **Sensor** and **Pixel size (µm)**: from the sensor data sheet (IMX335: 2.0 µm).
- **Lens type**: fisheye (all-sky, round image) or rectilinear (a normal lens).
- **Focal length (mm)**, **Diagonal / Horizontal / Vertical FOV (°)**: from the lens data sheet. Give the focal
  length or the field of view, the others follow.
- **Image circle (px)**, **Optical center X / Y (px)**, **Horizon radius R90 (px)**: for a fisheye, the size and
  centre of the round image and the radius where the horizon (90° from the zenith) falls. Sensible defaults are
  filled in; the plate solve in step 6 refines them.

Coordinates here are in native sensor pixels. Mirror and flip are applied for you.

## 6. Camera mounting and pointing check

- **North position angle (°)**, **Zenith tilt (°)**, **Azimuth offset (°)**: where the top of the picture points,
  and how far the camera is tilted off straight up. A manual edit takes effect in all overlays at once.
- On the first **clear night**, press **Capture & Auto Plate-Solve** (quick first, deep if the quick one fails). The
  solver identifies bright stars, then stores the pointing and refines the lens. **Solver status** shows the result.
- If the automatic solve is wrong, use **Align by stars** (Settings > Sky Mask): click four or more stars you can see,
  press **Identify**, then **Apply**.

Check: on the Live page switch on **Stars**, **Labels** and **Grid**. The marks must sit on the real stars across the
whole frame, also near the edge. If they are rotated or mirrored, fix **Orientation** in step 7 and solve again.

## 7. Orientation and image output (Settings > Image Output)

- **Orientation**: mirror, flip and 180° rotation. Set them so north, east and the sky overlay read correctly.
- **Master frame** (output resolution): the size saved in the archive. Smaller frames cost less CPU and disk.
- **White balance**: automatic or manual R/B gains, for colour sensors.
- **Performance** (Raspberry Pi and other small hosts): raise the time between captures and lower the analysis
  width if the machine runs hot. See [CONFIGURATION.md](CONFIGURATION.md#performance-profile-for-small-hosts).
- **Night star boost**: optional, only changes the live view.

## 8. Sky mask (Settings > Sky Mask)

The mask tells the star search and the cloud analysis where the open sky is.

1. Press **✨ Auto-detect** (best at night, with the camera focused). Zenivo proposes sky polygons (green) and
   obstructions inside them (red) from the current frame.
2. Check the outline. Draw what is missing with **＋ Sky polygon** and **＋ Obstruction** (click points on the frame,
   **↶ Undo point** removes the last one), or **🗑 Clear all** and start again.
3. Press **✔ Apply mask**. Nothing is stored until then.

## 9. Optional services (all off until you switch them on)

- **ADS-B receiver** (Settings > Integrations): host and port of an SBS-1 feed (usually port 30003), or the public
  fallback. See [CONFIGURATION.md](CONFIGURATION.md#integrations).
- **Detection** (Settings > Detection): which events to log (meteors, aircraft, satellites, flashes, clouds) and the
  sky region the detectors look at.
- **Community** (Settings > Community): share a preview with other stations. Default off; choose the position
  precision under Admin first. See [COMMUNITY.md](COMMUNITY.md).
- **Network access**: the installer listens on `127.0.0.1` only. Put a TLS reverse proxy in front before opening
  it to the internet ([INSTALL.md](INSTALL.md#nginx-reverse-proxy)).

## 10. Storage and dark frame

- **Retention**: `max_data_gb` and `min_free_gb` decide when old frames are deleted, see
  [CONFIGURATION.md](CONFIGURATION.md#retention-and-storage). The defaults suit an 8 GB budget.
- **Master Dark** (`/calibration`): cover the lens and capture a dark frame once; it removes hot pixels. Redo it
  when the camera temperature changes a lot.

## Final check

```bash
curl http://localhost:8080/api/version
```

- Live shows a sharp picture and the clocks are right.
- Stars, labels and grid overlay the real stars; aircraft match what you see.
- Settings > Station & Optics > **Solver status** reports a successful solve.
- Gallery fills with frames after a few minutes; **Activity Log** shows no repeated errors.

Something wrong? Read [CONFIGURATION.md](CONFIGURATION.md) and [SECURITY.md](SECURITY.md), then open an issue with
your version, camera and the Activity Log lines (remove your position and any tokens).
