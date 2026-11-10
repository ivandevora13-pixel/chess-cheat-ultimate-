"""Screen capture: grab a screen region as an RGB numpy array.

The process runs DPI-aware (see main.py), so region coordinates are physical
pixels, matching what mss captures.
"""
from __future__ import annotations

import numpy as np
import mss


class ScreenCapture:
    def __init__(self):
        self._sct = None

    def grab_rgb(self, region) -> np.ndarray:
        """region: (x, y, w, h) -> uint8 array (h, w, 3) in RGB order."""
        if self._sct is None:
            self._sct = mss.mss()
        left, top, width, height = (int(region[0]), int(region[1]), int(region[2]), int(region[3]))
        if width <= 0 or height <= 0:
            raise ValueError("invalid capture region")
        raw = self._sct.grab({"left": left, "top": top, "width": width, "height": height})
        arr = np.frombuffer(raw.raw, dtype=np.uint8).reshape(raw.height, raw.width, 4)
        return arr[:, :, [2, 1, 0]].copy()

    def close(self):
        try:
            if self._sct is not None:
                self._sct.close()
        except Exception:
            pass
        self._sct = None
