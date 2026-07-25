"""Baking a LUT into a new video file with ffmpeg."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .ffmpeg import FFmpegError

# Sensible mezzanine-quality defaults per codec. ProRes is the friendlier
# choice for round-tripping back into Resolve or Premiere; h264 is for review
# copies you want to send someone.
CODEC_ARGS = {
    "h264": ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
             "-pix_fmt", "yuv420p"],
    "h265": ["-c:v", "libx265", "-crf", "20", "-preset", "medium",
             "-pix_fmt", "yuv420p"],
    "prores": ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"],
}

EXTENSIONS = {"h264": ".mp4", "h265": ".mp4", "prores": ".mov"}


def render(
    src: Path,
    lut_path: Path,
    out_path: Path,
    ffmpeg_path: str,
    codec: str = "h264",
    overwrite: bool = False,
) -> Path:
    """Apply a .cube LUT to `src` and write the result to `out_path`."""
    if codec not in CODEC_ARGS:
        raise ValueError(f"Unknown codec {codec!r}. Choose from: "
                         f"{', '.join(CODEC_ARGS)}")
    if out_path.exists() and not overwrite:
        raise FileExistsError(
            f"{out_path} already exists. Pass --overwrite to replace it."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ffmpeg's lut3d parser treats ':' and '\' as syntax, so escape the path.
    escaped = str(lut_path).replace("\\", "/").replace(":", r"\:")

    cmd = [
        ffmpeg_path, "-v", "error", "-y" if overwrite else "-n",
        "-i", str(src),
        "-vf", f"lut3d=file='{escaped}':interp=tetrahedral",
        *CODEC_ARGS[codec],
        "-c:a", "copy",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise FFmpegError(
            f"Rendering {src.name} failed:\n{res.stderr.strip()[:800]}"
        )
    return out_path
