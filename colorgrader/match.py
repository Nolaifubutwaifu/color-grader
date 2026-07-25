"""Deriving a colour correction that moves one clip onto another.

Every method produces a Transform: a plain function from RGB to RGB over
0-1 floats. Everything downstream (LUT export, baking, previews) only talks
to that interface, so adding a method never touches the rest of the tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .stats import ClipStats, LUMA_709, lab_to_rgb, luma, rgb_to_lab

METHODS = ("cdl", "reinhard", "hist")


@dataclass
class Transform:
    """A colour correction plus a human-readable account of what it does."""

    name: str
    apply_fn: object
    strength: float = 1.0
    description: str = ""
    cdl: "CDLValues | None" = None
    notes: list[str] = field(default_factory=list)

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        """Apply to (..., 3) float RGB in 0-1, honouring `strength`."""
        out = np.clip(self.apply_fn(np.clip(rgb, 0.0, 1.0)), 0.0, 1.0)
        if self.strength >= 1.0:
            return out
        return np.clip(rgb, 0.0, 1.0) * (1 - self.strength) + out * self.strength

    @property
    def is_identity(self) -> bool:
        return self.strength <= 0.0


@dataclass
class CDLValues:
    """ASC CDL parameters: out = (in * slope + offset) ** power, then sat."""

    slope: np.ndarray
    offset: np.ndarray
    power: np.ndarray
    saturation: float

    def as_dict(self) -> dict:
        return {
            "slope": [round(float(v), 6) for v in self.slope],
            "offset": [round(float(v), 6) for v in self.offset],
            "power": [round(float(v), 6) for v in self.power],
            "saturation": round(float(self.saturation), 6),
        }

    def lift_gamma_gain(self) -> dict:
        """Restate CDL as the lift/gamma/gain wheels colourists actually turn.

        Approximate by construction - CDL and LGG are different models - but
        close enough to dial in by hand when you would rather not use the LUT.
        """
        gain = self.slope + self.offset
        lift = self.offset
        gamma = 1.0 / np.clip(self.power, 1e-3, None)
        return {
            "lift": [round(float(v), 4) for v in lift],
            "gamma": [round(float(v), 4) for v in gamma],
            "gain": [round(float(v), 4) for v in gain],
            "saturation": round(float(self.saturation), 4),
        }


def _apply_saturation(rgb: np.ndarray, sat: float) -> np.ndarray:
    y = luma(rgb)
    return y + (rgb - y) * sat


def _fit_cdl(src: ClipStats, ref: ClipStats) -> CDLValues:
    """Fit slope/offset/power per channel from the percentile anchors.

    Slope and offset come from the straight line through the shadow and
    highlight anchors; power then bends the midtone onto the reference's.
    """
    s_lo, s_mid, s_hi = src.shadow, src.mid, src.highlight
    r_lo, r_mid, r_hi = ref.shadow, ref.mid, ref.highlight

    span = np.maximum(s_hi - s_lo, 1e-4)
    slope = (r_hi - r_lo) / span
    # Keep the correction sane on clips with almost no contrast to stretch.
    slope = np.clip(slope, 0.2, 5.0)
    offset = r_lo - s_lo * slope

    linear_mid = np.clip(s_mid * slope + offset, 1e-4, 1.0)
    target_mid = np.clip(r_mid, 1e-4, 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        power = np.log(target_mid) / np.log(linear_mid)
    power = np.where(np.isfinite(power), power, 1.0)
    power = np.clip(power, 0.25, 4.0)

    sat = float(np.clip(ref.saturation / max(src.saturation, 1e-6), 0.3, 3.0))
    return CDLValues(slope=slope, offset=offset, power=power, saturation=sat)


def _cdl_transform(src: ClipStats, ref: ClipStats) -> Transform:
    cdl = _fit_cdl(src, ref)

    def fn(rgb: np.ndarray) -> np.ndarray:
        out = np.clip(rgb * cdl.slope + cdl.offset, 0.0, 1.0) ** cdl.power
        return _apply_saturation(out, cdl.saturation)

    return Transform(
        name="cdl",
        apply_fn=fn,
        description=(
            "ASC CDL fit: matches black point, midtone and white point per "
            "channel, then aligns overall saturation."
        ),
        cdl=cdl,
    )


def _reinhard_transform(src: ClipStats, ref: ClipStats) -> Transform:
    """Classic Reinhard mean/std transfer, done in Lab.

    Gentler than histogram matching and rarely bands, but it moves the whole
    image together, so a clip whose subject differs from the reference's can
    drift.
    """
    scale = ref.lab_std / src.lab_std
    scale = np.clip(scale, 0.4, 2.5)

    def fn(rgb: np.ndarray) -> np.ndarray:
        lab = rgb_to_lab(rgb)
        out = (lab - src.lab_mean) * scale + ref.lab_mean
        out[..., 0] = np.clip(out[..., 0], 0.0, 100.0)
        return lab_to_rgb(out)

    return Transform(
        name="reinhard",
        apply_fn=fn,
        description=(
            "Lab mean/standard-deviation transfer: aligns average lightness "
            "and colour cast plus their spread."
        ),
        cdl=_fit_cdl(src, ref),  # reported for reference, not applied
        notes=["CDL numbers shown are an approximation of this transform."],
    )


def _hist_transform(src: ClipStats, ref: ClipStats) -> Transform:
    """Per-channel CDF matching: the most literal match available.

    Strongest of the three and the one most likely to introduce banding on
    gradients, so it pairs well with a reduced --strength.
    """
    levels = np.arange(256) / 255.0
    tables = np.empty((3, 256))
    for c in range(3):
        # For each source level, find the reference level with the same rank.
        tables[c] = np.interp(src.cdf[c], ref.cdf[c], levels)

    def fn(rgb: np.ndarray) -> np.ndarray:
        out = np.empty_like(rgb)
        for c in range(3):
            out[..., c] = np.interp(rgb[..., c], levels, tables[c])
        return out

    return Transform(
        name="hist",
        apply_fn=fn,
        description=(
            "Per-channel histogram matching: reshapes each channel's full "
            "tonal distribution onto the reference's."
        ),
        cdl=_fit_cdl(src, ref),  # reported for reference, not applied
        notes=["CDL numbers shown are an approximation of this transform."],
    )


_BUILDERS = {
    "cdl": _cdl_transform,
    "reinhard": _reinhard_transform,
    "hist": _hist_transform,
}


def identity_transform(name: str = "reference") -> Transform:
    return Transform(
        name=name,
        apply_fn=lambda rgb: rgb,
        strength=1.0,
        description="Reference clip - left untouched.",
    )


def build_transform(
    src: ClipStats, ref: ClipStats, method: str = "cdl", strength: float = 1.0
) -> Transform:
    if method not in _BUILDERS:
        raise ValueError(f"Unknown method {method!r}. Choose from: {', '.join(METHODS)}")
    transform = _BUILDERS[method](src, ref)
    transform.strength = float(np.clip(strength, 0.0, 1.0))
    return transform


def match_error(src: ClipStats, ref: ClipStats, transform: Transform) -> dict:
    """How far apart two clips look, before and after correction.

    Mean Lab delta-E between the two clips' percentile anchors. Not a
    perceptual score for the images themselves - it measures whether the
    tonal anchors we aimed at actually landed.
    """
    def delta(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.linalg.norm(rgb_to_lab(a) - rgb_to_lab(b), axis=-1).mean())

    before = delta(src.percentiles, ref.percentiles)
    after = delta(transform.apply(src.percentiles), ref.percentiles)
    return {
        "before": round(before, 2),
        "after": round(after, 2),
        "improvement": round(before - after, 2),
    }
