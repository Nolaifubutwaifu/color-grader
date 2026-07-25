"""Locating ffmpeg, probing clips, and pulling frames out as numpy arrays.

Deliberately does not require ffprobe: plenty of people have an ffmpeg binary
sitting around without the rest of the suite, so we fall back to scraping
``ffmpeg -i``'s stderr banner when ffprobe is missing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".mxf", ".avi", ".mkv", ".webm",
    ".mts", ".m2ts", ".braw", ".r3d", ".prores", ".dv", ".wmv",
}


class FFmpegError(RuntimeError):
    pass


class FFmpegMissing(FFmpegError):
    pass


def find_ffmpeg(explicit: str | None = None) -> str:
    """Return a usable ffmpeg binary path, or raise FFmpegMissing."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("COLORGRADER_FFMPEG"):
        candidates.append(os.environ["COLORGRADER_FFMPEG"])
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(found)
    # imageio-ffmpeg ships a static build; handy on machines with no system ffmpeg.
    try:
        import imageio_ffmpeg

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass

    for cand in candidates:
        if cand and (Path(cand).exists() or shutil.which(cand)):
            return cand
    raise FFmpegMissing(
        "Could not find ffmpeg. Install it (macOS: `brew install ffmpeg`, "
        "Windows: `winget install Gyan.FFmpeg`), or point at it with "
        "--ffmpeg /path/to/ffmpeg or the COLORGRADER_FFMPEG env var."
    )


def find_ffprobe(ffmpeg_path: str) -> str | None:
    found = shutil.which("ffprobe")
    if found:
        return found
    sibling = Path(ffmpeg_path).with_name("ffprobe" + Path(ffmpeg_path).suffix)
    if sibling.exists():
        return str(sibling)
    return None


@dataclass
class ClipInfo:
    path: Path
    width: int
    height: int
    duration: float

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        return self.path.stem


_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_DIM_RE = re.compile(r"Video:.*?[,\s](\d{2,5})x(\d{2,5})[,\s]")


def probe(path: Path, ffmpeg_path: str, ffprobe_path: str | None) -> ClipInfo:
    """Get width/height/duration for a clip."""
    if ffprobe_path:
        cmd = [
            ffprobe_path, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "default=noprint_wrappers=1:nokey=0",
            str(path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            fields = {}
            for line in res.stdout.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    fields[k.strip()] = v.strip()
            try:
                return ClipInfo(
                    path=path,
                    width=int(fields["width"]),
                    height=int(fields["height"]),
                    duration=float(fields.get("duration", "0") or 0),
                )
            except (KeyError, ValueError):
                pass  # fall through to the stderr-scraping path

    # Fallback: ffmpeg with no output writes a descriptive banner to stderr.
    res = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True,
    )
    err = res.stderr
    dim = _DIM_RE.search(err)
    if not dim:
        raise FFmpegError(f"No video stream found in {path.name}.")
    width, height = int(dim.group(1)), int(dim.group(2))

    duration = 0.0
    dur = _DUR_RE.search(err)
    if dur:
        duration = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))
    return ClipInfo(path=path, width=width, height=height, duration=duration)


def _target_size(info: ClipInfo, max_width: int) -> tuple[int, int]:
    """Scale down for analysis, preserving aspect, keeping both dims even."""
    if info.width <= max_width:
        w, h = info.width, info.height
    else:
        w = max_width
        h = max(2, round(info.height * (max_width / info.width)))
    return (w - w % 2 or 2, h - h % 2 or 2)


def sample_timestamps(duration: float, count: int) -> list[float]:
    """Evenly spaced sample points, trimming the head/tail where slates,
    fades and handles live."""
    if duration <= 0:
        return [0.0] * count
    head = min(0.5, duration * 0.05)
    tail = min(0.5, duration * 0.05)
    usable = max(duration - head - tail, 0.0)
    if usable <= 0 or count == 1:
        return [duration / 2] * count
    return [head + usable * (i + 0.5) / count for i in range(count)]


def grab_frames(
    info: ClipInfo,
    ffmpeg_path: str,
    count: int = 12,
    max_width: int = 384,
) -> np.ndarray:
    """Return sampled frames as uint8 (n, h, w, 3) RGB.

    Each frame is a separate input-seek decode, which is fast even on long
    clips and avoids decoding footage we are going to throw away.
    """
    w, h = _target_size(info, max_width)
    frames = []
    for ts in sample_timestamps(info.duration, count):
        cmd = [
            ffmpeg_path, "-v", "error",
            "-ss", f"{ts:.3f}",
            "-i", str(info.path),
            "-frames:v", "1",
            "-vf", f"scale={w}:{h}",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True)
        expected = w * h * 3
        if res.returncode != 0 or len(res.stdout) < expected:
            continue
        buf = np.frombuffer(res.stdout[:expected], dtype=np.uint8)
        frames.append(buf.reshape(h, w, 3))

    if not frames:
        raise FFmpegError(
            f"Could not decode any frames from {info.name}. "
            "The file may be corrupt or in a codec this ffmpeg build lacks."
        )
    return np.stack(frames)


def collect_inputs(paths: list[str]) -> list[Path]:
    """Expand a mix of files and directories into a sorted list of clips."""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            out.extend(
                c for c in sorted(p.iterdir())
                if c.is_file() and c.suffix.lower() in VIDEO_EXTENSIONS
            )
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(f"No such file or directory: {raw}")
    # De-dupe while keeping order.
    seen, unique = set(), []
    for p in out:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique
