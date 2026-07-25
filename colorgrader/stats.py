"""Colour measurement: space conversions, letterbox removal, clip statistics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Rec.709 luma weights, the same ones ASC CDL uses for its saturation term.
LUMA_709 = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)

# Percentile anchors we fit corrections against. Deliberately inset from 0/100
# so a few clipped speculars or a dead pixel cannot drag the whole grade.
SHADOW_PCT = 1.0
MID_PCT = 50.0
HIGHLIGHT_PCT = 99.0


# --------------------------------------------------------------------------
# Colour space conversions. Video is Rec.709, but its transfer curve is close
# enough to sRGB that using sRGB's here costs nothing for statistical matching
# and keeps the code readable.
# --------------------------------------------------------------------------

def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    rgb = np.clip(rgb, 0.0, 1.0)
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(lin: np.ndarray) -> np.ndarray:
    lin = np.clip(lin, 0.0, 1.0)
    return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)


_RGB_TO_XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])
_XYZ_TO_RGB = np.linalg.inv(_RGB_TO_XYZ)
_D65 = np.array([0.95047, 1.00000, 1.08883])


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """(..., 3) sRGB in 0-1 -> CIE Lab."""
    xyz = srgb_to_linear(rgb) @ _RGB_TO_XYZ.T / _D65
    eps, kappa = 216 / 24389, 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    return np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], axis=-1)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """CIE Lab -> sRGB in 0-1 (clipped)."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200
    eps, kappa = 216 / 24389, 24389 / 27
    f = np.stack([fx, fy, fz], axis=-1)
    f3 = f ** 3
    xyz = np.where(f3 > eps, f3, (116 * f - 16) / kappa) * _D65
    return np.clip(linear_to_srgb(xyz @ _XYZ_TO_RGB.T), 0.0, 1.0)


def luma(rgb: np.ndarray) -> np.ndarray:
    """Rec.709 luma of (..., 3) RGB, keeping the trailing axis."""
    return np.sum(rgb * LUMA_709, axis=-1, keepdims=True)


# --------------------------------------------------------------------------
# Letterboxing
# --------------------------------------------------------------------------

def detect_content_box(
    frames: np.ndarray, threshold: int = 18
) -> tuple[int, int, int, int]:
    """Find the non-letterboxed region as (top, bottom, left, right).

    Black bars would otherwise anchor every clip's shadow percentile at zero
    and make the black-point match meaningless. We only trim bars touching an
    edge, so an intentionally dark frame is left alone.
    """
    brightest = frames.max(axis=(0, 3))  # (h, w) - brightest any frame gets
    h, w = brightest.shape
    rows = brightest.max(axis=1) > threshold
    cols = brightest.max(axis=0) > threshold

    if not rows.any() or not cols.any():
        return 0, h, 0, w  # entire clip is near-black; do not crop

    top = int(np.argmax(rows))
    bottom = int(h - np.argmax(rows[::-1]))
    left = int(np.argmax(cols))
    right = int(w - np.argmax(cols[::-1]))

    # A crop that eats most of the frame is more likely a misdetection than a
    # real matte, so ignore it.
    if (bottom - top) < h * 0.25 or (right - left) < w * 0.25:
        return 0, h, 0, w
    return top, bottom, left, right


# --------------------------------------------------------------------------
# Clip statistics
# --------------------------------------------------------------------------

@dataclass
class ClipStats:
    """Everything we measure about one clip's colour."""

    name: str
    pixels: np.ndarray        # (n, 3) uint8, subsampled RGB. Stored small so a
                              # 200-clip batch fits in memory; convert with
                              # .pixels_float where 0-1 floats are needed.
    percentiles: np.ndarray   # (3, 3) rows = shadow/mid/highlight, cols = RGB
    lab_mean: np.ndarray      # (3,)
    lab_std: np.ndarray       # (3,)
    saturation: float         # mean Lab chroma
    exposure: float           # mean Rec.709 luma
    crop: tuple[int, int, int, int]
    thumbnail: np.ndarray     # (h, w, 3) uint8, representative frame

    @property
    def pixels_float(self) -> np.ndarray:
        """The subsampled pixels as (n, 3) float in 0-1, built on demand."""
        return self.pixels.astype(np.float64) / 255.0

    @property
    def shadow(self) -> np.ndarray:
        return self.percentiles[0]

    @property
    def mid(self) -> np.ndarray:
        return self.percentiles[1]

    @property
    def highlight(self) -> np.ndarray:
        return self.percentiles[2]

    def feature_vector(self) -> np.ndarray:
        """Compact description used to pick a reference clip."""
        return np.concatenate([
            self.percentiles.ravel(),
            self.lab_mean / np.array([100.0, 60.0, 60.0]),
            self.lab_std / np.array([50.0, 30.0, 30.0]),
        ])


def analyse(name: str, frames: np.ndarray, max_pixels: int = 400_000) -> ClipStats:
    """Measure a clip from its sampled frames."""
    top, bottom, left, right = detect_content_box(frames)
    cropped = frames[:, top:bottom, left:right, :]

    flat = cropped.reshape(-1, 3)
    if flat.shape[0] > max_pixels:
        # Deterministic stride sampling: same input always gives same grade.
        stride = flat.shape[0] // max_pixels + 1
        flat = flat[::stride]

    rgb = flat.astype(np.float64) / 255.0

    percentiles = np.percentile(rgb, [SHADOW_PCT, MID_PCT, HIGHLIGHT_PCT], axis=0)

    lab = rgb_to_lab(rgb)
    chroma = np.hypot(lab[:, 1], lab[:, 2])

    # Middle sample is usually more representative than the first frame.
    thumb = cropped[len(cropped) // 2]

    return ClipStats(
        name=name,
        pixels=flat,  # keep the uint8 subsample; floats are derived on demand
        percentiles=percentiles,
        lab_mean=lab.mean(axis=0),
        lab_std=lab.std(axis=0) + 1e-6,
        saturation=float(chroma.mean()),
        exposure=float((rgb * LUMA_709).sum(axis=1).mean()),
        crop=(top, bottom, left, right),
        thumbnail=thumb,
    )


def pick_reference(stats: list[ClipStats]) -> int:
    """Choose the most typical clip, so corrections stay as small as possible.

    Picking the clip nearest the group median beats picking the first or the
    prettiest: it minimises how far the rest of the footage has to be pushed.
    """
    if len(stats) == 1:
        return 0
    features = np.stack([s.feature_vector() for s in stats])
    median = np.median(features, axis=0)
    distances = np.linalg.norm(features - median, axis=1)
    return int(np.argmin(distances))
