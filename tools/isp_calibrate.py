#!/usr/bin/env python3
"""Calibrate the RTS5869 ISP purple shading (MJPG path). Stop the allsky service first.

  isp_calibrate.py dark   # lens fully covered (opaque cap)
  isp_calibrate.py flat   # lens covered by a uniform white diffuser (white paper)
  isp_calibrate.py build  # writes data/calibration/isp_shading.npz from the two captures
Use the same ALLSKY_ISP_WB_K / exposure as the running service.
"""
import os, sys
import numpy as np
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["ALLSKY_ISP_CAL_OFF"] = "1"
CAL = os.path.join(ROOT, "data", "calibration")
SMALL = (324, 243)  # width, height of the stored maps


def grab(n=6):
    from src.hardware.driver import get_camera_driver
    from src.hardware import v4l2_mmap
    v4l2_mmap.configure_white_balance("manual", 1.0, 1.0)  # identity: calibrate the raw ISP output
    cam = get_camera_driver()
    cam.current_exposure_s = float(os.environ.get("ALLSKY_CAL_EXPOSURE", "0.0006"))
    acc, k = None, 0
    for i in range(n + 3):
        img, _ = cam.capture_frame()
        if i < 3:
            continue
        acc = img.astype(np.float32) if acc is None else acc + img.astype(np.float32)
        k += 1
    return acc / k


def main():
    mode = sys.argv[1]
    os.makedirs(CAL, exist_ok=True)
    if mode in ("dark", "flat"):
        img = grab()
        np.save(os.path.join(CAL, f"isp_{mode}.npy"), img)
        c = img[img.shape[0] // 2 - 50 : img.shape[0] // 2 + 50, img.shape[1] // 2 - 50 : img.shape[1] // 2 + 50]
        print(mode, "centre BGR mean", c.reshape(-1, 3).mean(0).round(1), "corner", img[:60, :60].reshape(-1, 3).mean(0).round(1))
    elif mode == "build":
        dark = np.load(os.path.join(CAL, "isp_dark.npy"))
        flat = np.load(os.path.join(CAL, "isp_flat.npy"))
        d = cv2.GaussianBlur(cv2.resize(dark, SMALL, interpolation=cv2.INTER_AREA), (0, 0), 3)
        f = cv2.GaussianBlur(cv2.resize(flat, SMALL, interpolation=cv2.INTER_AREA), (0, 0), 3) - d
        f = np.maximum(f, 1.0)
        h, w = f.shape[:2]
        centre = f[h // 2 - 12 : h // 2 + 12, w // 2 - 16 : w // 2 + 16].reshape(-1, 3).mean(0)
        ref = centre[1]  # green centre = reference; makes the white diffuser neutral everywhere
        gain = np.clip(ref / f, 0.5, 4.0).astype(np.float32)
        np.savez_compressed(os.path.join(CAL, "isp_shading.npz"), dark=d.astype(np.float32), gain=gain)
        print("saved; gain at corner BGR", gain[2, 2].round(2), "centre", gain[h // 2, w // 2].round(2))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
