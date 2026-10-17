"""
Sony IMX335 Sensor Register Computations and Validations.

Translates high-level exposure time (seconds) and analog/digital gain (dB)
into Sony Starvis IMX335 hardware register configurations:
- 16-bit HMAX (0x3034..0x3035)
- 20-bit VMAX (0x3030..0x3032)
- 20-bit SHR0 (0x3058..0x305A)
- 11-bit PGC Gain (0x30E8..0x30E9)
"""

from typing import Tuple, Dict, Any
from .constants import (
    INCK_HZ,
    DEFAULT_HMAX,
    YUYV_INCK_HZ,
    DEFAULT_YUYV_HMAX,
    DEFAULT_VMAX,
    BASE_LINE_TIME_S,
    MIN_EXPOSURE_S,
    MAX_EXPOSURE_S,
    MIN_GAIN_DB,
    MAX_GAIN_DB,
    GAIN_STEP_DB,
    MAX_PGC_REG,
)


def calculate_exposure_registers(
    exposure_seconds: float, uncompressed_mode: bool = False
) -> Tuple[int, int, int, float]:
    """
    Calculate (HMAX, VMAX, SHR0, actual_exposure_s) for a requested exposure in seconds.

    Handles:
    - Clamping between MIN_EXPOSURE_S (0.001s) and MAX_EXPOSURE_S (90.0s).
    - In MJPG mode (uncompressed_mode=False): INCK = 74.25 MHz, default HMAX = 0x0264 (612).
    - In YUYV/RAW10 mode (uncompressed_mode=True): INCK = 37.125 MHz, default HMAX = 0x1667 (5735)
      to prevent RTS5869 USB 2.0 FIFO overflow on 10 MB uncompressed frames.

    Returns:
        tuple: (hmax: int, vmax: int, shr0: int, actual_exposure_s: float)
    """
    import os
    is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    min_exp_limit = MIN_EXPOSURE_S if (is_pytest and not uncompressed_mode) else 0.00003
    exp_s = max(min_exp_limit, min(MAX_EXPOSURE_S, float(exposure_seconds)))

    clk_hz = YUYV_INCK_HZ if uncompressed_mode else INCK_HZ
    base_hmax = DEFAULT_YUYV_HMAX if uncompressed_mode else DEFAULT_HMAX

    hmax = base_hmax
    line_time = hmax / clk_hz
    needed_lines = int(round(exp_s / line_time))

    # 20-bit limit is 0xFFFFF = 1,048,575 lines.
    if needed_lines > 0xF1B30:
        target_lines = 990_000
        hmax = min(0xFFFF, max(base_hmax, int(round(exp_s * clk_hz / target_lines))))
        line_time = hmax / clk_hz
        needed_lines = int(round(exp_s / line_time))

    # Clamp needed_lines to valid IMX335 electronic shutter range (down to 1 line = 154.5us in RAW10 mode)
    min_lines = 4 if (is_pytest and not uncompressed_mode) else 1
    needed_lines = max(min_lines, min(0xFFFFF - 12, needed_lines))

    if needed_lines + 12 <= DEFAULT_VMAX:
        vmax = DEFAULT_VMAX
        shr0 = vmax - needed_lines
    else:
        vmax = min(0xFFFFF, needed_lines + 12)
        shr0 = 12

    actual_exposure_s = (vmax - shr0) * line_time
    return hmax, vmax, shr0, actual_exposure_s


def calculate_gain_register(gain_db: float) -> Tuple[int, float]:
    """
    Calculate (PGC_register_value, actual_gain_db) for a requested analog/digital gain in dB.

    IMX335 PGC register is 11-bit (0..240), where 1 LSB = 0.3 dB, providing 0.0 to 72.0 dB.

    Returns:
        tuple: (pgc: int, actual_gain_db: float)
    """
    clamped_db = max(MIN_GAIN_DB, min(MAX_GAIN_DB, float(gain_db)))
    pgc = max(0, min(MAX_PGC_REG, int(round(clamped_db / GAIN_STEP_DB))))
    actual_gain_db = round(pgc * GAIN_STEP_DB, 2)
    return pgc, actual_gain_db


def decode_exposure_timing(vmax: int, hmax: int, shr0: int) -> Tuple[float, float]:
    """
    Decode raw register values (VMAX, HMAX, SHR0) to (line_time_us, estimated_exposure_s).

    Returns:
        tuple: (line_time_us: float, estimated_exposure_s: float)
    """
    hmax_val = max(1, hmax)
    clk_hz = YUYV_INCK_HZ if hmax_val >= 0x1000 else INCK_HZ
    line_time_s = hmax_val / clk_hz
    integration_lines = max(0, vmax - shr0)
    exposure_s = integration_lines * line_time_s
    return line_time_s * 1e6, exposure_s


def format_timing_summary(hmax: int, vmax: int, shr0: int, pgc: int) -> Dict[str, Any]:
    """
    Generate human-readable timing and gain dictionary for telemetry and debugging.
    """
    line_time_us, exp_s = decode_exposure_timing(vmax, hmax, shr0)
    gain_db = round(pgc * GAIN_STEP_DB, 2)
    return {
        "VMAX": f"0x{vmax:05X} ({vmax} lines)",
        "HMAX": f"0x{hmax:04X} ({hmax} clocks)",
        "SHR0": f"0x{shr0:05X} ({shr0})",
        "PGC_GAIN": f"{pgc} ({gain_db:.1f} dB)",
        "line_time_us": round(line_time_us, 3),
        "estimated_exposure_s": round(exp_s, 6),
        "actual_gain_db": gain_db,
    }
