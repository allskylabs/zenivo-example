"""
Standard Camera Driver Interface definition from PROJECT.md.
"""

import abc
from typing import Tuple, Dict, Any, Optional
import numpy as np


class CameraDriverInterface(abc.ABC):
    """
    Standard Camera Driver Interface contract from PROJECT.md.
    """

    @abc.abstractmethod
    def open(self, device_path: str = "/dev/video0") -> bool:
        """Open camera device or initialize simulator."""
        ...

    @abc.abstractmethod
    def unlock_extension_unit(self) -> bool:
        """Unlock XDATA[0xC78A]=1 and patch RAM exposure ceiling to 90.0s."""
        ...

    @abc.abstractmethod
    def set_hardware_exposure(self, exposure_s: float, gain_db: float) -> Dict[str, Any]:
        """Configure hardware exposure timing and sensor analog/digital gain."""
        ...

    @abc.abstractmethod
    def capture_frame(self) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Capture and return (image_bgr_2592x1944, metadata_dict) where metadata_dict contains:
        'hw_frame_delta': float (ms),
        'actual_exposure_s': float,
        'gain_db': float,
        'VMAX': int, 'HMAX': int, 'SHR0': int, 'PGC': int,
        'timestamp': float
        """
        ...

    @abc.abstractmethod
    def close(self) -> None:
        """Release device resources."""
        ...
