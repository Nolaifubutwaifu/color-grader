"""A self-contained HTML report so you can see the match before trusting it.

Includes a minimal PNG writer so the whole tool needs nothing but numpy -
one less thing to install on an editing machine.
"""

from __future__ import annotations

import base64
import html
import struct
import zlib
from pathlib import Path

import numpy as np

from .match import Transform
from .stats import ClipStats


def encode_png(rgb: np.ndarray) -> bytes:
    """Encode a (h, w, 3) uint8 array as PNG bytes."""
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    h, w, _ = rgb.shape

    # Each scanline is prefixed with filter type 0 (None).
    raw = np.hstack([np.zeros((h, 1), np.uint8), rgb.reshape(h, w * 3)])
    compressed = zlib.compress(raw.tobytes(), 6)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def _data_uri(rgb: np.ndarray, max_width: int = 420) -> str:
    if rgb.shape[1] > max_width:
        step = int(np.ceil(rgb.shape[1] / max_width))
        rgb = rgb[::step, ::step]
    return "data:image/png;base64," + base64.b64encode(encode_png(rgb)).decode("ascii")


def _histogram_svg(pixels: np.ndarray, width: int = 420, height: int = 90) -> str:
    """A small RGB histogram drawn as inline SVG."""
    bins = 64
    paths = []
    peak = 1.0
    curves = []
    for c in range(3):
        hist, _ = np.histogram(pixels[:, c], bins=bins, range=(0.0, 1.0))
        curves.append(hist.astype(np.float64))
        peak = max(peak, hist.max())

    for c, (hist, colour) in enumerate(zip(curves, ("#ff5a5a", "#4ade80", "#5aa9ff"))):
        pts = []
        for i, v in enumerate(hist):
            x = i / (bins - 1) * width
            y = height - (v / peak) * (height - 4)
            pts.append(f"{x:.1f},{y:.1f}")
        d = f"M0,{height} L" + " L".join(pts) + f" L{width},{height} Z"
        paths.append(
            f'<path d="{d}" fill="{colour}" fill-opacity="0.35" '
            f'stroke="{colour}" stroke-width="1"/>'
        )

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'preserveAspectRatio="none" style="background:#111;border-radius:4px">'
        + "".join(paths)
        + "</svg>"
    )


_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; padding:32px; background:#0d0d10; color:#e8e8ea;
       font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
h1 { font-size:22px; margin:0 0 4px; letter-spacing:-0.01em; }
h2 { font-size:16px; margin:0 0 12px; letter-spacing:-0.01em; }
.sub { color:#8b8b96; margin:0 0 28px; }
.summary { background:#16161c; border:1px solid #26262f; border-radius:10px;
           padding:16px 20px; margin-bottom:28px; }
.summary dl { display:grid; grid-template-columns:auto 1fr; gap:4px 16px; margin:0; }
.summary dt { color:#8b8b96; } .summary dd { margin:0; }
.clip { background:#16161c; border:1px solid #26262f; border-radius:10px;
        padding:20px; margin-bottom:20px; }
.clip.ref { border-color:#3d6b3d; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:20px; }
.pane { min-width:0; }
.pane h3 { font-size:11px; text-transform:uppercase; letter-spacing:0.09em;
           color:#8b8b96; margin:0 0 8px; }
.pane img { width:100%; border-radius:6px; display:block; background:#000; }
.scope { margin-top:8px; }
table { border-collapse:collapse; width:100%; margin-top:16px; font-size:13px; }
th, td { text-align:left; padding:6px 10px; border-bottom:1px solid #26262f; }
th { color:#8b8b96; font-weight:500; }
td.num { font-variant-numeric:tabular-nums; }
.badge { display:inline-block; padding:2px 9px; border-radius:99px; font-size:11px;
         font-weight:600; letter-spacing:0.03em; }
.badge.ref { background:#1f3d1f; color:#84d184; }
.badge.ok { background:#1c2f45; color:#7db9f0; }
.note { color:#8b8b96; font-size:13px; margin-top:12px; }
code { background:#22222b; padding:1px 6px; border-radius:4px; font-size:12px; }
"""


def write_report(
    path: Path,
    stats: list[ClipStats],
    transforms: list[Transform],
    errors: list[dict],
    reference_index: int,
    method: str,
    strength: float,
) -> Path:
    """Render the before/after report."""
    sections = []
    for i, (st, tf, err) in enumerate(zip(stats, transforms, errors)):
        is_ref = i == reference_index
        before = st.thumbnail
        after = (
            before
            if is_ref
            else np.clip(tf.apply(before.astype(np.float64) / 255.0) * 255.0, 0, 255)
            .astype(np.uint8)
        )
        after_pixels = st.pixels if is_ref else tf.apply(st.pixels)

        badge = (
            '<span class="badge ref">REFERENCE</span>'
            if is_ref
            else f'<span class="badge ok">&Delta;E {err["before"]:.1f} '
                 f'&rarr; {err["after"]:.1f}</span>'
        )

        rows = ""
        if tf.cdl is not None and not is_ref:
            lgg = tf.cdl.lift_gamma_gain()
            for label in ("lift", "gamma", "gain"):
                r, g, b = lgg[label]
                rows += (
                    f"<tr><td>{label.title()}</td><td class='num'>{r:.4f}</td>"
                    f"<td class='num'>{g:.4f}</td><td class='num'>{b:.4f}</td></tr>"
                )
            rows += (
                f"<tr><td>Saturation</td><td class='num' colspan='3'>"
                f"{lgg['saturation']:.4f}</td></tr>"
            )
            rows = (
                "<table><tr><th>Control</th><th>R</th><th>G</th><th>B</th></tr>"
                + rows + "</table>"
            )

        notes = "".join(f'<p class="note">{html.escape(n)}</p>' for n in tf.notes)

        sections.append(f"""
<section class="clip {'ref' if is_ref else ''}">
  <h2>{html.escape(st.name)} &nbsp; {badge}</h2>
  <div class="grid">
    <div class="pane">
      <h3>Original</h3>
      <img src="{_data_uri(before)}" alt="original frame">
      <div class="scope">{_histogram_svg(st.pixels)}</div>
    </div>
    <div class="pane">
      <h3>{'Reference (unchanged)' if is_ref else 'Matched'}</h3>
      <img src="{_data_uri(after)}" alt="corrected frame">
      <div class="scope">{_histogram_svg(after_pixels)}</div>
    </div>
  </div>
  {rows}
  {notes}
</section>""")

    improved = [e for i, e in enumerate(errors) if i != reference_index]
    avg_before = np.mean([e["before"] for e in improved]) if improved else 0.0
    avg_after = np.mean([e["after"] for e in improved]) if improved else 0.0

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Colour match report</title><style>{_CSS}</style></head><body>
<h1>Colour match report</h1>
<p class="sub">{len(stats)} clip{'s' if len(stats) != 1 else ''} &middot;
matched to <strong>{html.escape(stats[reference_index].name)}</strong></p>
<div class="summary"><dl>
  <dt>Method</dt><dd>{html.escape(method)}</dd>
  <dt>Strength</dt><dd>{strength:.2f}</dd>
  <dt>Mean anchor &Delta;E</dt><dd>{avg_before:.2f} &rarr; {avg_after:.2f}</dd>
</dl></div>
{''.join(sections)}
<p class="note">&Delta;E compares the shadow, midtone and highlight anchors of
each clip against the reference&rsquo;s. It tells you whether the correction
landed where it aimed &mdash; it is not a judgement of whether the footage
looks good. Trust your eyes and the images above.</p>
</body></html>"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return path
