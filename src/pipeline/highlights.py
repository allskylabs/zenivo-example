"""
Highlight desaturation: neutralise the false colours of clipped highlights.

When the sun is in the frame the sensor clips around it even at its shortest exposure. The colour
channels clip at different levels, and the ISP's white balance gains and colour matrix then turn the
clipped pixels into vivid orange / green / cyan fringes on sunlit cloud edges. Their real colour is
lost, and it is close to white, so pixels that are both bright and strongly coloured are blended
towards a neutral grey at their brightest channel (never darker), with smooth ramps so no edge appears.
Ordinary bright sky and cloud are only weakly coloured, so they (and the colour cloud cover relies on)
are left alone.

A few uint8 OpenCV operations on the tiles that hold such pixels; nothing at night or in a plain sky.
"""

import cv2
import numpy as np

# Ramps on the brightest channel and on the chroma (brightest minus darkest channel): desaturation starts
# at *_START and is full at *_FULL; the pixel weight is the product of both.
# Measured on IMX708 NoIR day frames: sky / cloud ~100-210 DN with chroma <= ~50 (99.9th percentile),
# sun fringes 140-255 DN with chroma up to ~135.
HIGHLIGHT_START = 140
HIGHLIGHT_FULL = 200
CHROMA_START = 32
CHROMA_FULL = 64
# Near clipping (this ramp on the brightest channel) a pixel is neutralised whatever its chroma, so the faint
# tint left inside the sun disc does not show as a contour next to neutralised fringes
CLIP_START = 225
CLIP_FULL = 250
# Only tiles of this size (px) that hold a bright, strongly coloured pixel are processed
TILE = 64

_LUT_CACHE: dict = {}


def _ramp_lut(start: int, full: int) -> np.ndarray:
    lut = _LUT_CACHE.get((start, full))
    if lut is None:
        t = np.clip((np.arange(256, dtype=np.float32) - start) / max(1, full - start), 0.0, 1.0)
        lut = _LUT_CACHE[(start, full)] = np.round(t * t * (3.0 - 2.0 * t) * 255.0).astype(np.uint8)  # smoothstep
    return lut


def _max_chroma(block: np.ndarray):
    b, g, r = cv2.split(block)
    mx = cv2.max(cv2.max(b, g), r)
    return (b, g, r), mx, cv2.subtract(mx, cv2.min(cv2.min(b, g), r))


def _desaturate_block(block: np.ndarray, lut_bright: np.ndarray, lut_chroma: np.ndarray, lut_clip: np.ndarray) -> None:
    chans, mx, chroma = _max_chroma(block)
    w = cv2.max(cv2.multiply(cv2.LUT(mx, lut_bright), cv2.LUT(chroma, lut_chroma), scale=1.0 / 255.0), cv2.LUT(mx, lut_clip))
    # Saturating uint8 ops (SIMD in OpenCV): out = ch + (max - ch) * w / 255
    block[...] = cv2.merge([cv2.add(ch, cv2.multiply(cv2.subtract(mx, ch), w, scale=1.0 / 255.0)) for ch in chans])


def desaturate_highlights(
    frame_bgr: np.ndarray,
    start: int = HIGHLIGHT_START,
    full: int = HIGHLIGHT_FULL,
    chroma_start: int = CHROMA_START,
    chroma_full: int = CHROMA_FULL,
    tile: int = TILE,
) -> np.ndarray:
    """Blend bright, strongly coloured pixels of a uint8 BGR frame towards neutral, in place; returns the frame."""
    if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3 or frame_bgr.dtype != np.uint8:
        return frame_bgr
    h, w = frame_bgr.shape[:2]
    # Find candidate areas on a 1/4 nearest-neighbour view (~1/16 of the work), then process only the
    # tiles that hold them
    small = cv2.resize(frame_bgr, (max(1, w // 4), max(1, h // 4)), interpolation=cv2.INTER_NEAREST)
    _, mx, chroma = _max_chroma(small)
    cand = ((mx > start) & (chroma > chroma_start)).astype(np.uint8)
    if not cand.any():  # night, or a plain sky: no further work
        return frame_bgr
    cand = cv2.dilate(cand, np.ones((3, 3), np.uint8))  # +4 px: covers pixels the subsampling skipped
    st = max(1, tile // 4)
    rows, cols = -(-cand.shape[0] // st), -(-cand.shape[1] // st)
    padded = np.zeros((rows * st, cols * st), np.uint8)
    padded[: cand.shape[0], : cand.shape[1]] = cand
    cells = padded.reshape(rows, st, cols, st).max(axis=(1, 3))
    luts = (_ramp_lut(start, full), _ramp_lut(chroma_start, chroma_full), _ramp_lut(CLIP_START, CLIP_FULL))
    for ty in range(rows):
        row = cells[ty]
        tx = 0
        while tx < cols:  # one block per run of candidate tiles in this tile row
            if not row[tx]:
                tx += 1
                continue
            t0 = tx
            while tx < cols and row[tx]:
                tx += 1
            _desaturate_block(frame_bgr[ty * tile:(ty + 1) * tile, t0 * tile:min(w, tx * tile)], *luts)
    return frame_bgr
