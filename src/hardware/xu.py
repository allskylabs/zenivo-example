"""
UVC Extension Unit (XU) Interface for Realtek RTS5869 (0bda:5869).

Directly accesses RTS5869 internal 8051 MCU RAM/ROM and IMX335 I2C bus via
Linux UVCIOC_CTRL_QUERY ioctls.
"""

import ctypes
import fcntl
import glob
import os
from typing import List, Dict, Any, Optional

from .constants import (
    UVCIOC_CTRL_QUERY,
    UVC_SET_CUR,
    UVC_GET_CUR,
    RTS5869_XU_UNIT_ID,
    XU_SELECTOR_CMD,
    XU_SELECTOR_DATA,
)


class UvcXuControlQuery(ctypes.Structure):
    _fields_ = [
        ("unit", ctypes.c_uint8),
        ("selector", ctypes.c_uint8),
        ("query", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("data", ctypes.POINTER(ctypes.c_uint8)),
    ]


def find_rts5869_device_node() -> Optional[str]:
    """
    Locate the primary capture /dev/videoX node for Realtek 0bda:5869.
    Returns path e.g. '/dev/video0', or None if not found.
    """
    for sys_path in sorted(glob.glob("/sys/class/video4linux/video*")):
        dev_name = os.path.basename(sys_path)
        uevent_path = os.path.join(sys_path, "device", "..", "uevent")
        if not os.path.exists(uevent_path):
            continue
        try:
            with open(uevent_path, "r", encoding="utf-8", errors="ignore") as fp:
                content = fp.read().lower()
            if "product=bda/5869" in content:
                # Confirm it is the primary video capture node (index 0)
                index_path = os.path.join(sys_path, "index")
                if os.path.exists(index_path):
                    with open(index_path, "r", encoding="utf-8") as ifp:
                        if ifp.read().strip() != "0":
                            continue
                return f"/dev/{dev_name}"
        except Exception:
            continue
    return None


class RTS5869XUController:
    """
    Low-level UVC Extension Unit controller for Realtek RTS5869 ISP.
    """

    def __init__(self, fd: int):
        self.fd = fd

    def xu_set(self, selector: int, payload: List[int] | bytes):
        """Send an 8-byte control query with UVC_SET_CUR."""
        buf = (ctypes.c_uint8 * 8)(*payload[:8])
        q = UvcXuControlQuery(
            unit=RTS5869_XU_UNIT_ID,
            selector=selector,
            query=UVC_SET_CUR,
            size=8,
            data=buf,
        )
        fcntl.ioctl(self.fd, UVCIOC_CTRL_QUERY, q)

    def xu_get(self, selector: int) -> bytes:
        """Receive an 8-byte control query with UVC_GET_CUR."""
        buf = (ctypes.c_uint8 * 8)()
        q = UvcXuControlQuery(
            unit=RTS5869_XU_UNIT_ID,
            selector=selector,
            query=UVC_GET_CUR,
            size=8,
            data=buf,
        )
        fcntl.ioctl(self.fd, UVCIOC_CTRL_QUERY, q)
        return bytes(buf)

    def reset_state_machine(self):
        """Reset Extension Unit state machine by clearing internal flags."""
        try:
            self.xu_get(XU_SELECTOR_CMD)
        except Exception:
            pass
        try:
            self.xu_set(XU_SELECTOR_CMD, [0xFF, 0, 0, 0, 0, 0, 0, 0])
        except Exception:
            pass

    def unlock_xdata(self):
        """
        Set XDATA[0xC78A] = 1 via opcode 0x82.
        Bypasses debug lock and allows reading XDATA, ROM, and sensor I2C registers.
        """
        self.reset_state_machine()
        # Opcode 0x82: b0=0x00, opcode=0x82, addr=0xC78A (lo=0x8A, hi=0xC7), len=1
        self.xu_set(XU_SELECTOR_CMD, [0x00, 0x82, 0x8A, 0xC7, 1, 0, 0, 0])
        self.xu_set(XU_SELECTOR_DATA, [0x01, 0, 0, 0, 0, 0, 0, 0])

    def patch_exposure_ceiling(self, max_val_100us: int = 900_000):
        """
        Patch UVC CT_EXPOSURE_TIME_ABSOLUTE_CONTROL MAX in XDATA[0xC96F..0xC972].
        Default 900,000 corresponds to 90.0 seconds (900000 * 100 microseconds).
        """
        # Convert to 4 bytes big-endian
        val_bytes = max_val_100us.to_bytes(4, byteorder="big")
        self.xu_set(XU_SELECTOR_CMD, [0x00, 0x82, 0x6F, 0xC9, 4, 0, 0, 0])
        self.xu_set(XU_SELECTOR_DATA, list(val_bytes) + [0, 0, 0, 0])

    def enable_bayer_raw_mode(self):
        """
        Switch Realtek RTS5869 ISP pipeline into 10-bit Bayer RAW (RGGB) bypass mode:
        - XDATA[0xC90F] = 0x02: Disable ISP auto-loops (AE/AWB/CCM/Gamma) & JPEG encoder
        - XDATA[0x8000..0x8009] = 10 0F 00 00 01 8E 23 40 10 06: Route raw 10-bit CSI-2 words
        - XDATA[0x8037] = 0x01: Bypass YUV conversion
        """
        self.unlock_xdata()
        self.write_xdata(0xC90F, [0x02])
        self.write_xdata(0x8000, [0x10, 0x0F, 0x00, 0x00, 0x01, 0x8E, 0x23, 0x40])
        self.write_xdata(0x8008, [0x10, 0x06])
        self.write_xdata(0x8037, [0x01])

    def disable_bayer_raw_mode(self):
        """Restore standard ISP demosaic and YUV/MJPG mode."""
        self.restore_isp_mjpg_mode()

    def restore_isp_mjpg_mode(self):
        """
        Ensure Realtek RTS5869 ISP demosaic and YUV/MJPEG pipeline registers are active
        (XDATA[0xC90F]=0x00, XDATA[0x8000..0x8009], XDATA[0x8037]=0x00) so the ISP never
        gets stuck in RAW bypass or disabled-ISP state after long exposures.
        """
        self.unlock_xdata()
        self.write_xdata(0xC90F, [0x00])
        self.write_xdata(0x8000, [0x10, 0xFF, 0x00, 0x78, 0x01, 0x00, 0x63, 0x00])
        self.write_xdata(0x8008, [0x14, 0x00])
        self.write_xdata(0x8037, [0x00])

    def read_xdata(self, addr: int, length: int = 8) -> bytes:
        """
        Read up to 8 bytes from 8051 XDATA RAM [0x0000..0xFFFF] via opcode 0xC2.
        Requires XDATA[0xC78A] == 1.
        """
        rlen = min(8, max(1, length))
        self.xu_set(XU_SELECTOR_CMD, [0x00, 0xC2, addr & 0xFF, (addr >> 8) & 0xFF, rlen, 0, 0, 0])
        return self.xu_get(XU_SELECTOR_DATA)[:rlen]

    def write_xdata(self, addr: int, data: bytes | List[int]):
        """
        Write up to 8 bytes to 8051 XDATA RAM [0x0000..0xFFFF] via opcode 0x82.
        """
        raw = bytes(data)
        length = min(8, len(raw))
        padded = list(raw[:length]) + [0] * (8 - length)
        self.xu_set(XU_SELECTOR_CMD, [0x00, 0x82, addr & 0xFF, (addr >> 8) & 0xFF, length, 0, 0, 0])
        self.xu_set(XU_SELECTOR_DATA, padded)

    def read_sensor_reg(self, reg: int) -> int:
        """
        Read 1 byte from Sony IMX335 sensor I2C register [0x3000..0x3FFF] via opcode 0xCA.
        Requires XDATA[0xC78A] == 1.
        """
        self.xu_set(XU_SELECTOR_CMD, [0x00, 0xCA, reg & 0xFF, (reg >> 8) & 0xFF, 2, 0, 0, 0])
        return self.xu_get(XU_SELECTOR_DATA)[0]

    def write_sensor_reg(self, reg: int, val: int):
        """
        Write 1 byte to Sony IMX335 sensor I2C register [0x3000..0x3FFF] via opcode 0x8A.
        """
        self.xu_set(XU_SELECTOR_CMD, [0x00, 0x8A, reg & 0xFF, (reg >> 8) & 0xFF, 2, 0, 0, 0])
        self.xu_set(XU_SELECTOR_DATA, [val & 0xFF, 0x00, 0, 0, 0, 0, 0, 0])

    def get_firmware_info(self) -> Dict[str, Any]:
        """
        Query firmware/hardware info via opcodes 0xCD, 0xD0, and BootROM 0xC1.
        """
        self.reset_state_machine()
        self.xu_set(XU_SELECTOR_CMD, [0x00, 0xCD, 0x00, 0x00, 8, 0, 0, 0])
        cd0 = self.xu_get(XU_SELECTOR_DATA)

        self.xu_set(XU_SELECTOR_CMD, [0x00, 0xCD, 0x02, 0x00, 8, 0, 0, 0])
        cd2 = self.xu_get(XU_SELECTOR_DATA)

        self.xu_set(XU_SELECTOR_CMD, [0x00, 0xCD, 0x08, 0x00, 8, 0, 0, 0])
        cd8 = self.xu_get(XU_SELECTOR_DATA)

        self.xu_set(XU_SELECTOR_CMD, [0x00, 0xD0, 0x00, 0x00, 8, 0, 0, 0])
        vendor = self.xu_get(XU_SELECTOR_DATA).rstrip(b"\x00").decode("ascii", errors="ignore")

        self.xu_set(XU_SELECTOR_CMD, [0x02, 0xC1, 0x00, 0x00, 8, 0, 0, 0])
        jedec = self.xu_get(XU_SELECTOR_DATA)[:3]

        vid = cd0[0] | (cd0[1] << 8)
        pid = cd0[2] | (cd0[3] << 8)
        fw_ver = cd0[4] | (cd0[5] << 8)
        hw_ver = (cd0[6] << 8) | cd0[7]
        rev = (cd8[6] << 8) | cd8[7]

        return {
            "vendor": vendor,
            "vid_pid": f"{vid:04x}:{pid:04x}",
            "fw_version": f"0x{fw_ver:04x}",
            "hw_version": f"0x{hw_ver:04x}",
            "chip_revision": f"0x{rev:04x}",
            "build_date": cd2.decode("ascii", errors="ignore"),
            "spi_flash_jedec": jedec.hex(),
        }
