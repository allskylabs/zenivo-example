"""
High-Fidelity Sony IMX335 + Realtek RTS5869 Hardware Simulator Fallback.

Emulates:
1. 8051 MCU XDATA RAM (64KB) with debug lock flag at XDATA[0xC78A] and UVC RAM exposure ceiling at XDATA[0xC96F].
2. Sony IMX335 I2C register bank (0x3000..0x3FFF), Group Hold latching (REGHOLD), and register math.
3. Realistic CMOS physical noise model:
   - Pedestal / Bias offset
   - Thermal dark current scaling with exposure time and gain
   - Hot / Defect pixel generation (DN >= 100 on long exposures)
   - Readout Gaussian noise and Poisson shot noise
4. Frame synthesis matching exact 2592x1944 BGR format.
5. Accurate V4L2 hardware timestamp simulation with hw_frame_delta error < 0.5% (mandate < 5%).
"""

import time
from typing import Dict, Any, Tuple
import numpy as np

from .constants import (
    SENSOR_WIDTH,
    SENSOR_HEIGHT,
    DEFAULT_HMAX,
    DEFAULT_VMAX,
    XDATA_UNLOCK_ADDR,
    XDATA_EXPOSURE_MAX_ADDR,
    REG_STANDBY,
    REG_REGHOLD,
    REG_VMAX_L,
    REG_VMAX_M,
    REG_VMAX_H,
    REG_HMAX_L,
    REG_HMAX_H,
    REG_SHR0_L,
    REG_SHR0_M,
    REG_SHR0_H,
    REG_PGC_L,
    REG_PGC_H,
)
from .driver_interface import CameraDriverInterface
from .registers import (
    calculate_exposure_registers,
    calculate_gain_register,
    decode_exposure_timing,
)


class SimulatedIMX335Controller(CameraDriverInterface):
    """
    High-fidelity simulator for RTS5869 + IMX335 camera subsystem.
    """

    def __init__(
        self,
        width: int = SENSOR_WIDTH,
        height: int = SENSOR_HEIGHT,
        seed: int = 42,
        simulate_real_sleep: bool = False,
        sky: bool = False,
    ):
        self.width = width
        self.height = height
        self.seed = seed
        self.simulate_real_sleep = simulate_real_sleep
        self.is_open = False
        # Optional synthetic night sky (stars + faint sky glow) so the live view is not just black noise.
        # Off by default so register/dark-frame tests keep seeing a pure dark frame; the service simulator turns it on.
        self.sky = bool(sky)
        self.lens_covered = False  # set by the dark calibration: a covered lens sees no sky
        self._stars = self._make_stars() if self.sky else None

        # 64KB 8051 XDATA RAM
        self.xdata = bytearray(0x10000)
        # Stock firmware starts with XDATA[0xC78A] = 0 (locked)
        self.xdata[XDATA_UNLOCK_ADDR] = 0
        # Stock firmware caps exposure at 5000 (0x00001388 = 0.5s in 100us units)
        self.xdata[XDATA_EXPOSURE_MAX_ADDR : XDATA_EXPOSURE_MAX_ADDR + 4] = (5000).to_bytes(4, "big")

        # Active IMX335 I2C Registers
        self.active_registers: Dict[int, int] = {}
        # Shadow registers (buffered during REGHOLD = 1)
        self.shadow_registers: Dict[int, int] = {}
        self.reghold = False

        # Exposure and Gain state
        self.current_exposure_s = 0.1
        self.current_gain_db = 0.0

        # Virtual hardware clock
        self.virtual_timestamp = time.time()
        self.rng = np.random.RandomState(seed)

        # Generate persistent hot pixel coordinate template
        # Approx 0.15% hot pixels on sensor (7,500 hot pixels out of 5M)
        num_hot_pixels = int(self.width * self.height * 0.0015)
        self.hot_pixel_ys = self.rng.randint(0, self.height, size=num_hot_pixels)
        self.hot_pixel_xs = self.rng.randint(0, self.width, size=num_hot_pixels)
        # Inherent defect severity factors (1.0 to 5.0)
        self.hot_pixel_severities = self.rng.uniform(1.2, 4.5, size=num_hot_pixels)

        # Initialize default registers
        self._init_default_registers()

    def _make_stars(self, count: int = 420) -> np.ndarray:
        """Fixed star field inside a round image circle: columns x, y (px from the centre), magnitude."""
        rng = np.random.default_rng(self.seed + 7)
        radius = 0.48 * min(self.width, self.height)
        r = radius * np.sqrt(rng.random(count))
        a = rng.random(count) * 2.0 * np.pi
        # Star counts grow about 2.5x per magnitude: draw magnitudes 0..6.2 with that law
        mag = np.log10(1.0 + rng.random(count) * (10.0 ** (0.4 * 6.2) - 1.0)) / 0.4
        return np.stack([r * np.cos(a), r * np.sin(a), mag], axis=1).astype(np.float32)

    def _add_sky(self, plane: np.ndarray, exposure_s: float, gain_factor: float) -> None:
        """Add faint sky glow and Gaussian stars scaled by exposure and gain; the field drifts with the Earth's spin."""
        h, w = plane.shape
        radius = 0.48 * min(w, h)
        yy, xx = np.ogrid[:h, :w]
        rr2 = (xx - w / 2.0) ** 2 + (yy - h / 2.0) ** 2
        inside = rr2 < radius * radius
        # Sky glow: a little brighter towards the horizon, grows with exposure x gain, black outside the circle
        glow = 0.25 * exposure_s * gain_factor * (0.6 + 0.4 * rr2 / (radius * radius))
        # Always a night sky, at any wall-clock time; the service runs the auto exposure in night mode while
        # the simulator is the camera (DayNightAutoExposure.force_night), so no daylight glow is needed.
        plane += np.where(inside, glow, 0.0).astype(np.float32)
        ang = time.time() * 7.292e-5  # sidereal rate, rad/s
        c, s = np.cos(ang), np.sin(ang)
        sx = self._stars[:, 0] * c - self._stars[:, 1] * s + w / 2.0
        sy = self._stars[:, 0] * s + self._stars[:, 1] * c + h / 2.0
        sigma = max(1.4, 1.6 * w / 2592.0 * 1.6)
        reach = int(np.ceil(4 * sigma))
        k = 60.0 * exposure_s * gain_factor
        for x, y, m in zip(sx, sy, self._stars[:, 2]):
            peak = k * 10.0 ** (-0.4 * (float(m) - 2.0)) / (2.0 * np.pi * sigma * sigma)
            if peak < 0.5:
                continue
            x0, y0 = int(round(x)), int(round(y))
            xs, xe, ys, ye = max(0, x0 - reach), min(w, x0 + reach + 1), max(0, y0 - reach), min(h, y0 + reach + 1)
            if xs >= xe or ys >= ye:
                continue
            gx = np.exp(-((np.arange(xs, xe) - x) ** 2) / (2 * sigma * sigma))
            gy = np.exp(-((np.arange(ys, ye) - y) ** 2) / (2 * sigma * sigma))
            plane[ys:ye, xs:xe] += peak * np.outer(gy, gx).astype(np.float32)

    def _init_default_registers(self):
        self.active_registers[REG_STANDBY] = 0x00
        self.active_registers[REG_REGHOLD] = 0x00

        # Default VMAX = 4044 (0x000FCC)
        self.active_registers[REG_VMAX_L] = DEFAULT_VMAX & 0xFF
        self.active_registers[REG_VMAX_M] = (DEFAULT_VMAX >> 8) & 0xFF
        self.active_registers[REG_VMAX_H] = (DEFAULT_VMAX >> 16) & 0x0F

        # Default HMAX = 612 (0x0264)
        self.active_registers[REG_HMAX_L] = DEFAULT_HMAX & 0xFF
        self.active_registers[REG_HMAX_H] = (DEFAULT_HMAX >> 8) & 0xFF

        # Default SHR0 = 12 (0x00000C)
        self.active_registers[REG_SHR0_L] = 12
        self.active_registers[REG_SHR0_M] = 0
        self.active_registers[REG_SHR0_H] = 0

        # Default PGC Gain = 0 (0 dB)
        self.active_registers[REG_PGC_L] = 0
        self.active_registers[REG_PGC_H] = 0

    def open(self, device_path: str = "/dev/video0") -> bool:
        """Simulate opening camera device."""
        self.is_open = True
        return True

    def close(self) -> None:
        """Simulate closing camera device."""
        self.is_open = False

    def is_locked(self) -> bool:
        """Check if debug access is locked (XDATA[0xC78A] == 0)."""
        return self.xdata[XDATA_UNLOCK_ADDR] == 0

    def unlock_extension_unit(self) -> bool:
        """Emulate XDATA[0xC78A] = 1 and raising RAM ceiling to 90.0s."""
        self.xdata[XDATA_UNLOCK_ADDR] = 1
        # Patch RAM ceiling to 900,000 (90.0s)
        self.xdata[XDATA_EXPOSURE_MAX_ADDR : XDATA_EXPOSURE_MAX_ADDR + 4] = (900_000).to_bytes(4, "big")
        return True

    def read_xdata(self, addr: int, length: int = 1) -> bytes:
        """Read 8051 XDATA RAM. Requires unlock."""
        if self.is_locked():
            raise PermissionError("RTS5869 XDATA read denied: debug access locked (XDATA[0xC78A] == 0)")
        return bytes(self.xdata[addr : addr + length])

    def write_xdata(self, addr: int, data: bytes | list[int]) -> None:
        """Write 8051 XDATA RAM. Opcode 0x82 is permitted even when locked."""
        raw = bytes(data)
        self.xdata[addr : addr + len(raw)] = raw

    def read_sensor_reg(self, reg: int) -> int:
        """Read Sony IMX335 I2C register. Requires unlock."""
        if self.is_locked():
            raise PermissionError("IMX335 I2C read denied: debug access locked (XDATA[0xC78A] == 0)")
        return self.active_registers.get(reg, 0x00)

    def write_sensor_reg(self, reg: int, val: int) -> None:
        """
        Write Sony IMX335 I2C register with Group Hold support.
        If REGHOLD == 1, writes are staged in shadow registers.
        """
        val = val & 0xFF
        if reg == REG_REGHOLD:
            if val == 0x01:
                self.reghold = True
                self.shadow_registers = self.active_registers.copy()
            elif val == 0x00:
                if self.reghold:
                    self.active_registers.update(self.shadow_registers)
                    self.shadow_registers.clear()
                    self.reghold = False
            self.active_registers[REG_REGHOLD] = val
            return

        if self.reghold:
            self.shadow_registers[reg] = val
        else:
            self.active_registers[reg] = val

    def get_firmware_info(self) -> Dict[str, Any]:
        """Return simulated firmware and hardware identification."""
        return {
            "vendor": "REALSIL_SIM",
            "vid_pid": "0bda:5869",
            "fw_version": "0x0001",
            "hw_version": "0x1060",
            "chip_revision": "0x4258",
            "build_date": "07/27/26",
            "spi_flash_jedec": "308512",
        }

    def set_hardware_exposure(self, exposure_s: float, gain_db: float) -> Dict[str, Any]:
        """
        Program hardware exposure and gain registers via group hold sequence.
        """
        self.unlock_extension_unit()

        hmax, vmax, shr0, actual_exp = calculate_exposure_registers(exposure_s)
        pgc, actual_gain = calculate_gain_register(gain_db)

        # Execute Group Hold Sequence
        self.write_sensor_reg(REG_REGHOLD, 0x01)
        self.write_sensor_reg(REG_HMAX_L, hmax & 0xFF)
        self.write_sensor_reg(REG_HMAX_H, (hmax >> 8) & 0xFF)
        self.write_sensor_reg(REG_VMAX_L, vmax & 0xFF)
        self.write_sensor_reg(REG_VMAX_M, (vmax >> 8) & 0xFF)
        self.write_sensor_reg(REG_VMAX_H, (vmax >> 16) & 0x0F)
        self.write_sensor_reg(REG_SHR0_L, shr0 & 0xFF)
        self.write_sensor_reg(REG_SHR0_M, (shr0 >> 8) & 0xFF)
        self.write_sensor_reg(REG_SHR0_H, (shr0 >> 16) & 0x0F)
        self.write_sensor_reg(REG_PGC_L, pgc & 0xFF)
        self.write_sensor_reg(REG_PGC_H, (pgc >> 8) & 0x07)
        self.write_sensor_reg(REG_REGHOLD, 0x00)

        self.current_exposure_s = actual_exp
        self.current_gain_db = actual_gain

        return {
            "requested_exposure_s": exposure_s,
            "actual_exposure_s": round(actual_exp, 6),
            "gain_db": actual_gain,
            "VMAX": vmax,
            "HMAX": hmax,
            "SHR0": shr0,
            "PGC": pgc,
        }

    def capture_frame(self) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Synthesize a realistic 2592x1944 BGR frame reflecting current exposure and gain.
        Computes accurate hw_frame_delta matching the actual exposure time.
        """
        if not self.is_open:
            self.open()

        # Retrieve active registers
        vmax = (
            self.active_registers.get(REG_VMAX_L, 0)
            | (self.active_registers.get(REG_VMAX_M, 0) << 8)
            | ((self.active_registers.get(REG_VMAX_H, 0) & 0x0F) << 16)
        )
        hmax = self.active_registers.get(REG_HMAX_L, 0) | (self.active_registers.get(REG_HMAX_H, 0) << 8)
        shr0 = (
            self.active_registers.get(REG_SHR0_L, 0)
            | (self.active_registers.get(REG_SHR0_M, 0) << 8)
            | ((self.active_registers.get(REG_SHR0_H, 0) & 0x0F) << 16)
        )
        pgc = self.active_registers.get(REG_PGC_L, 0) | ((self.active_registers.get(REG_PGC_H, 0) & 0x07) << 8)

        _, actual_exp = decode_exposure_timing(vmax, hmax, shr0)
        gain_db = pgc * 0.3

        # Simulate frame timing delta (< 0.1% jitter)
        jitter_ratio = self.rng.uniform(-0.0005, +0.0005)
        hw_delta_ms = (actual_exp * 1000.0) * (1.0 + jitter_ratio)

        if self.simulate_real_sleep and actual_exp > 0.05:
            time.sleep(min(actual_exp, 2.0))

        self.virtual_timestamp += hw_delta_ms / 1000.0

        # Synthesize frame image (H=1944, W=2592, C=3)
        # 1. Base pedestal / bias offset: ~16 DN
        bias = 16.0

        # 2. Thermal dark current: scales with exposure time and analog gain
        # Gain factor: 10^(gain_db / 20)
        gain_factor = 10.0 ** (gain_db / 20.0)
        dark_rate = 0.8  # DN per second per unit gain
        dark_current = actual_exp * dark_rate * (1.0 + gain_factor * 0.1)

        # 3. Readout and thermal shot noise (Gaussian approximation)
        noise_sigma = np.sqrt(3.0 + dark_current * 0.5) * (1.0 + gain_factor * 0.05)
        background = bias + dark_current
        noise = self.rng.normal(0, noise_sigma, (self.height, self.width)).astype(np.float32)

        plane = (background + noise).astype(np.float32)
        if self._stars is not None and not self.lens_covered:
            self._add_sky(plane, actual_exp, gain_factor)
        plane = np.clip(plane, 0, 255)
        frame_bgr = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        frame_bgr[:, :, 0] = plane.astype(np.uint8)  # Blue
        frame_bgr[:, :, 1] = plane.astype(np.uint8)  # Green
        frame_bgr[:, :, 2] = plane.astype(np.uint8)  # Red

        # 4. Inject Hot Pixels on long exposures or high gain
        # Hot pixels intensify with exposure time and gain
        if actual_exp >= 1.0 or gain_db >= 20.0:
            exp_factor = min(actual_exp / 10.0, 3.0)
            gain_multiplier = 1.0 + (gain_db / 30.0)
            hot_dn = np.clip(
                background + 70.0 * self.hot_pixel_severities * exp_factor * gain_multiplier,
                100,  # Ensure defect pixels cross the DN >= 100 threshold
                255,
            ).astype(np.uint8)

            frame_bgr[self.hot_pixel_ys, self.hot_pixel_xs, 0] = hot_dn
            frame_bgr[self.hot_pixel_ys, self.hot_pixel_xs, 1] = hot_dn
            frame_bgr[self.hot_pixel_ys, self.hot_pixel_xs, 2] = hot_dn

        metadata = {
            "hw_frame_delta": round(hw_delta_ms, 2),
            "actual_exposure_s": round(actual_exp, 6),
            "gain_db": round(gain_db, 2),
            "VMAX": vmax,
            "HMAX": hmax,
            "SHR0": shr0,
            "PGC": pgc,
            "timestamp": round(self.virtual_timestamp, 4),
            "simulator": True,
        }

        return frame_bgr, metadata
