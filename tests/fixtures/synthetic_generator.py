"""
Synthetic Astronomical Image & Data Generator for E2E AllSky Testing.

Generates realistic astronomical frames matching the Sony Starvis IMX335 (2592x1944)
under diverse observational conditions:
1. Dark Frames with Poisson/Gaussian noise, temperature scaling, and hot pixels (DN >= 100).
2. Pristine Night Sky with rotating star fields, realistic point spread functions (PSF).
3. Cloudy Sky with multi-scale atmospheric haze and star obscuration.
4. Transient Meteor Streaks with asymmetric light curves and high aspect ratios (AR >= 4.5).
5. False-Positive Generators: Aircraft strobe beads and multi-frame persistent satellite tracks.
6. 24-Hour Diurnal/Nocturnal Cycle Sequence.
"""

import math
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timezone, timedelta
import numpy as np
import cv2


class SyntheticAllSkyGenerator:
    """
    High-fidelity synthetic frame generator for IMX335 AllSky observatory verification.
    """

    DEFAULT_WIDTH = 2592
    DEFAULT_HEIGHT = 1944

    def __init__(self, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT, seed: int = 42):
        self.width = width
        self.height = height
        self.center_x = width // 2
        self.center_y = height // 2
        self.horizon_radius = int(min(width, height) * 0.48)
        self.rng = np.random.default_rng(seed)

    def generate_fisheye_mask(self) -> np.ndarray:
        """Create a circular fisheye boundary mask (1 inside horizon, 0 outside)."""
        y, x = np.ogrid[:self.height, :self.width]
        dist_from_center = np.sqrt((x - self.center_x) ** 2 + (y - self.center_y) ** 2)
        mask = (dist_from_center <= self.horizon_radius).astype(np.uint8)
        return mask

    def generate_dark_frame(
        self,
        exposure_s: float = 10.0,
        gain_db: float = 36.0,
        temp_c: float = 25.0,
        num_hot_pixels: int = 300,
        fixed_hot_coords: Optional[np.ndarray] = None,
        pedestal: int = 10,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Generate a synthetic raw dark frame with realistic thermal and read noise.

        Returns:
            Tuple of:
            - frame: np.ndarray (H, W, 3) uint8 BGR
            - hot_pixel_mask: np.ndarray (H, W) bool (pixels >= 100)
            - hot_coords: np.ndarray (N, 2) [y, x] coordinates
        """
        # Thermal scaling: dark current doubles roughly every 7C
        thermal_factor = 2.0 ** ((temp_c - 20.0) / 7.0)
        thermal_mean = 0.4 * exposure_s * thermal_factor * (10.0 ** (gain_db / 40.0))
        read_noise_std = 2.2 * (10.0 ** (gain_db / 60.0))

        # Base noise floor
        noise = self.rng.normal(pedestal + thermal_mean, read_noise_std, (self.height, self.width))
        frame_luma = np.clip(noise, 0, 95).astype(np.uint8)

        # Generate or reuse hot pixel coordinates
        if fixed_hot_coords is None:
            y_coords = self.rng.integers(10, self.height - 10, size=num_hot_pixels)
            x_coords = self.rng.integers(10, self.width - 10, size=num_hot_pixels)
            hot_coords = np.column_stack((y_coords, x_coords))
        else:
            hot_coords = fixed_hot_coords

        # Inject hot pixels with DN >= 100 (up to 255 saturated)
        for y, x in hot_coords:
            base_val = 110 + int(exposure_s * 4.5)
            val = min(255, base_val + int(self.rng.integers(10, 100)))
            frame_luma[y, x] = val

        # Convert to 3-channel BGR
        frame_bgr = cv2.merge([frame_luma, frame_luma, frame_luma])

        # Hot pixel boolean mask
        hot_mask = frame_luma >= 100

        return frame_bgr, hot_mask, hot_coords

    def generate_dark_stack(
        self,
        count: int = 9,
        exposure_s: float = 10.0,
        gain_db: float = 36.0,
        temp_c: float = 25.0,
        num_hot_pixels: int = 300,
    ) -> Tuple[List[np.ndarray], np.ndarray]:
        """
        Generate a sequence of N dark frames sharing static hot pixels but independent temporal noise.
        Injects a single transient cosmic ray in one frame to verify median rejection.
        """
        cy1, cx1 = int(self.height * 0.4), int(self.width * 0.4)
        cy2, cx2 = cy1 + 15, cx1 + 20

        y_coords = self.rng.integers(10, self.height - 10, size=num_hot_pixels)
        x_coords = self.rng.integers(10, self.width - 10, size=num_hot_pixels)
        fixed_coords = np.column_stack((y_coords, x_coords))

        # Avoid placing static hot pixels inside the transient cosmic ray test region
        mask_outside = ~((y_coords >= cy1 - 5) & (y_coords <= cy2 + 5) & (x_coords >= cx1 - 5) & (x_coords <= cx2 + 5))
        fixed_coords = fixed_coords[mask_outside]

        stack = []
        for i in range(count):
            frame, _, _ = self.generate_dark_frame(
                exposure_s=exposure_s,
                gain_db=gain_db,
                temp_c=temp_c,
                fixed_hot_coords=fixed_coords,
            )
            # Inject cosmic ray in frame index 2 (transient streak)
            if i == 2:
                cy1, cx1 = int(self.height * 0.4), int(self.width * 0.4)
                cy2, cx2 = cy1 + 15, cx1 + 20
                cv2.line(frame, (cx1, cy1), (cx2, cy2), (255, 255, 255), 2)
            stack.append(frame)

        true_hot_mask = np.zeros((self.height, self.width), dtype=bool)
        for y, x in fixed_coords:
            true_hot_mask[y, x] = True

        return stack, true_hot_mask

    def generate_night_sky(
        self,
        num_stars: int = 150,
        rotation_deg: float = 0.0,
        bg_luma: int = 12,
        noise_std: float = 2.5,
        inject_hot_pixels: bool = False,
        seed: Optional[int] = None,
    ) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """
        Generate a pristine night sky with point-spread function (PSF) stars
        rotated by rotation_deg around the zenith (celestial pole).
        """
        rng = np.random.default_rng(seed) if seed is not None else self.rng
        # Background sky glow + Poisson/Gaussian noise
        bg = rng.normal(bg_luma, noise_std, (self.height, self.width))
        bg = np.clip(bg, 0, 255).astype(np.float32)

        # 3 channels with slight astronomical sky tint (dark indigo)
        b_ch = bg * 1.15
        g_ch = bg * 0.95
        r_ch = bg * 0.85

        stars_meta = []
        rad = math.radians(rotation_deg)
        cos_r = math.cos(rad)
        sin_r = math.sin(rad)

        # Distribute stars in polar coordinates within horizon
        star_radii = rng.uniform(20, self.horizon_radius * 0.95, size=num_stars)
        star_angles = rng.uniform(0, 2 * math.pi, size=num_stars)
        magnitudes = rng.exponential(scale=1.5, size=num_stars)  # more faint than bright

        for r, theta, mag in zip(star_radii, star_angles, magnitudes):
            # Rotate star position
            x0 = r * math.cos(theta)
            y0 = r * math.sin(theta)
            xr = x0 * cos_r - y0 * sin_r + self.center_x
            yr = x0 * sin_r + y0 * cos_r + self.center_y

            ix = int(round(xr))
            iy = int(round(yr))

            if 2 <= ix < self.width - 2 and 2 <= iy < self.height - 2:
                # Peak intensity inversely proportional to magnitude
                peak = min(255.0, 70.0 + 185.0 / (1.0 + 0.6 * mag))
                fwhm_sigma = max(0.8, min(2.0, 1.2 + 0.2 * (3.0 - mag)))

                # Render 5x5 Gaussian PSF
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        dist2 = dx * dx + dy * dy
                        val = peak * math.exp(-dist2 / (2.0 * fwhm_sigma * fwhm_sigma))
                        b_ch[iy + dy, ix + dx] += val
                        g_ch[iy + dy, ix + dx] += val * 0.98
                        r_ch[iy + dy, ix + dx] += val * 0.92

                stars_meta.append({
                    "x": ix,
                    "y": iy,
                    "peak": peak,
                    "magnitude": float(mag),
                })

        # Apply fisheye horizon mask
        mask = self.generate_fisheye_mask()
        b_ch = np.clip(b_ch, 0, 255).astype(np.uint8) * mask
        g_ch = np.clip(g_ch, 0, 255).astype(np.uint8) * mask
        r_ch = np.clip(r_ch, 0, 255).astype(np.uint8) * mask

        frame = cv2.merge([b_ch, g_ch, r_ch])

        if inject_hot_pixels:
            # Add hot pixels for end-to-end calibration testing
            y_hot = self.rng.integers(10, self.height - 10, size=200)
            x_hot = self.rng.integers(10, self.width - 10, size=200)
            for yh, xh in zip(y_hot, x_hot):
                frame[yh, xh] = [240, 240, 240]

        return frame, stars_meta

    def generate_cloudy_sky(
        self,
        cloud_cover_pct: float = 75.0,
        num_stars: int = 150,
        seed: int = 42,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Generate a night sky with realistic cloud cover using low-frequency Perlin-like noise.
        Clouds elevate background luma (reflecting city light pollution) and obscure stars.
        """
        base_night, stars = self.generate_night_sky(num_stars=num_stars, seed=seed)

        # Generate low-frequency cloud noise pattern via downsampled blur
        low_res_h = self.height // 32
        low_res_w = self.width // 32
        rng = np.random.default_rng(seed + 100)
        coarse_noise = rng.uniform(0.0, 1.0, (low_res_h, low_res_w)).astype(np.float32)

        # Smooth upscale to full resolution
        cloud_map = cv2.resize(coarse_noise, (self.width, self.height), interpolation=cv2.INTER_CUBIC)
        cloud_map = cv2.GaussianBlur(cloud_map, (101, 101), 0)

        # Threshold cloud mask based on desired coverage
        target_fraction = cloud_cover_pct / 100.0
        threshold_val = np.quantile(cloud_map, 1.0 - target_fraction)
        cloud_intensity = np.clip((cloud_map - threshold_val) * 3.5, 0.0, 1.0)

        # Cloud glow in urban night sky: +30..60 DN brownish/amber light pollution
        cloud_glow = (cloud_intensity * 55.0).astype(np.uint8)

        # Obscure stars under clouds: opacity attenuates underlying starlight
        star_attenuation = np.clip(1.0 - (cloud_intensity * 1.6), 0.0, 1.0)

        result_channels = []
        for c in range(3):
            tint = 1.1 if c == 0 else (1.0 if c == 1 else 0.8)  # slight amber skyglow
            ch = base_night[:, :, c].astype(np.float32) * star_attenuation + (cloud_glow * tint)
            result_channels.append(np.clip(ch, 0, 255).astype(np.uint8))

        cloudy_frame = cv2.merge(result_channels)
        mask = self.generate_fisheye_mask()
        cloudy_frame = cv2.bitwise_and(cloudy_frame, cloudy_frame, mask=mask)

        # Calculate actual obscured star count
        visible_stars = 0
        for s in stars:
            sx, sy = s["x"], s["y"]
            if cloud_intensity[sy, sx] < 0.45:
                visible_stars += 1

        meta = {
            "requested_cloud_pct": cloud_cover_pct,
            "actual_cloud_fraction": float(np.mean(cloud_intensity > 0.1)),
            "total_stars": len(stars),
            "visible_stars": visible_stars,
            "mean_cloud_luma": float(np.mean(cloud_glow)),
        }

        return cloudy_frame, meta

    def generate_meteor_frame(
        self,
        base_frame: Optional[np.ndarray] = None,
        start_pt: Optional[Tuple[int, int]] = None,
        end_pt: Optional[Tuple[int, int]] = None,
        streak_length: int = 160,
        angle_deg: float = 35.0,
        peak_brightness: int = 250,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Inject a realistic meteor streak into a night frame.
        The streak features:
        - Straight trajectory
        - Asymmetric brightness envelope (gradual brightening, terminal flare, rapid extinction)
        - Very high aspect ratio (AR >= 4.5)
        """
        if base_frame is None:
            frame, _ = self.generate_night_sky(num_stars=120)
        else:
            frame = base_frame.copy()

        if start_pt is None:
            x1 = self.center_x - 100
            y1 = self.center_y - 200
        else:
            x1, y1 = start_pt

        rad = math.radians(angle_deg)
        if end_pt is None:
            x2 = int(x1 + streak_length * math.cos(rad))
            y2 = int(y1 + streak_length * math.sin(rad))
        else:
            x2, y2 = end_pt

        # Sample points along the streak
        num_samples = int(math.hypot(x2 - x1, y2 - y1))
        streak_overlay = np.zeros((self.height, self.width), dtype=np.float32)

        for i in range(num_samples):
            t = i / max(1, num_samples)
            # Asymmetrical light curve: t^2 up to 0.85, then sharp drop
            if t < 0.85:
                intensity = peak_brightness * (0.2 + 0.8 * (t / 0.85) ** 1.8)
            else:
                intensity = peak_brightness * (1.0 - (t - 0.85) / 0.15)

            curr_x = int(round(x1 + t * (x2 - x1)))
            curr_y = int(round(y1 + t * (y2 - y1)))

            # 3x3 point spread along the streak
            if 1 <= curr_x < self.width - 1 and 1 <= curr_y < self.height - 1:
                streak_overlay[curr_y, curr_x] = max(streak_overlay[curr_y, curr_x], intensity)
                streak_overlay[curr_y - 1, curr_x] = max(streak_overlay[curr_y - 1, curr_x], intensity * 0.45)
                streak_overlay[curr_y + 1, curr_x] = max(streak_overlay[curr_y + 1, curr_x], intensity * 0.45)
                streak_overlay[curr_y, curr_x - 1] = max(streak_overlay[curr_y, curr_x - 1], intensity * 0.45)
                streak_overlay[curr_y, curr_x + 1] = max(streak_overlay[curr_y, curr_x + 1], intensity * 0.45)

        # Blend streak into frame
        streak_uint8 = np.clip(streak_overlay, 0, 255).astype(np.uint8)
        for c in range(3):
            frame[:, :, c] = np.maximum(frame[:, :, c], streak_uint8)

        metadata = {
            "type": "meteor",
            "start_pixel": [x1, y1],
            "end_pixel": [x2, y2],
            "length_pixels": float(num_samples),
            "angle_deg": angle_deg,
            "peak_brightness": int(peak_brightness),
            "estimated_aspect_ratio": round(num_samples / 2.5, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return frame, metadata

    def generate_aircraft_frame(
        self,
        base_frame: Optional[np.ndarray] = None,
        num_beads: int = 7,
        spacing_px: int = 35,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Generate a false-positive aircraft track: collinear periodic strobes (beads).
        Used to verify false-positive rejection filters.
        """
        if base_frame is None:
            frame, _ = self.generate_night_sky(num_stars=120)
        else:
            frame = base_frame.copy()

        start_x = self.center_x - 150
        start_y = self.center_y - 80
        beads = []

        for i in range(num_beads):
            bx = start_x + i * spacing_px
            by = start_y + int(i * spacing_px * 0.4)
            # Red / white alternating strobes
            color = (255, 255, 255) if i % 2 == 0 else (50, 50, 255)
            cv2.circle(frame, (bx, by), 3, color, -1)
            beads.append([bx, by])

        metadata = {
            "type": "aircraft",
            "bead_count": num_beads,
            "beads": beads,
            "periodic_strobe": True,
        }
        return frame, metadata

    def generate_diurnal_sequence(
        self,
        num_frames: int = 24,
    ) -> List[Tuple[datetime, np.ndarray, float]]:
        """
        Generate a 24-step diurnal/nocturnal cycle spanning midday -> sunset -> night -> dawn.
        Returns:
            List of (timestamp, frame, solar_elevation_deg)
        """
        base_time = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
        sequence = []

        for hour in range(num_frames):
            ts = base_time + timedelta(hours=hour)
            # Solar elevation sinusoidal model: +65 deg at 12:00, -65 deg at 00:00
            angle_rad = (hour / 24.0) * 2.0 * math.pi
            solar_elev = 65.0 * math.cos(angle_rad)

            if solar_elev > 0.0:
                # Daylight: bright zenith, blue atmosphere
                sky_luma = int(min(200, 110 + solar_elev * 1.3))
                frame = np.full((self.height, self.width, 3), (sky_luma + 20, sky_luma, sky_luma - 30), dtype=np.uint8)
            elif solar_elev > -12.0:
                # Twilight: gradient orange / deep blue
                sky_luma = int(max(18, 60 + solar_elev * 3.5))
                frame = np.full((self.height, self.width, 3), (sky_luma + 30, sky_luma + 5, sky_luma + 10), dtype=np.uint8)
            else:
                # Astronomical Night: dark sky with stars
                frame, _ = self.generate_night_sky(
                    num_stars=140,
                    rotation_deg=hour * 15.0,  # 15 deg per hour sidereal rotation
                    seed=hour,
                )

            mask = self.generate_fisheye_mask()
            frame = cv2.bitwise_and(frame, frame, mask=mask)
            sequence.append((ts, frame, round(solar_elev, 2)))

        return sequence
