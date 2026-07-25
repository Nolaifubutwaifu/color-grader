"""Reproduce the method comparison table in the README.

Generates synthetic clips from one source render, so every clip shows identical
content at any given frame. That makes a straight per-pixel Lab delta-E against
the reference a true measure of the match, rather than the percentile-anchor
metric the tool tunes against.

    python tests/benchmark.py [workdir]

Needs ffmpeg on PATH (or COLORGRADER_FFMPEG set).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from colorgrader.cli import main as cli_main
from colorgrader.ffmpeg import find_ffmpeg
from colorgrader.stats import rgb_to_lab

W, H, FRAMES = 640, 360, 25
METHODS = ("cdl", "hist", "reinhard")

# label -> ffmpeg filter chain simulating a different camera/lighting setup
VARIANTS = {
    "reference": None,
    "mild warm cast": "colorbalance=rm=0.22:gm=0.04:bm=-0.18,eq=brightness=0.05",
    "cool + flat": "colorbalance=rm=-0.18:bm=0.24,eq=contrast=0.78:brightness=0.06",
    "desaturated + dark": "eq=saturation=0.55:brightness=-0.08:contrast=1.15",
}


def slug(label: str) -> str:
    return label.replace(" + ", "_").replace(" ", "_")


def build_footage(ffmpeg: str, into: Path) -> None:
    into.mkdir(parents=True, exist_ok=True)
    base = into / "_base.mp4"
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=size={W}x{H}:rate=25:duration=4",
         "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(base)],
        check=True,
    )
    for label, vf in VARIANTS.items():
        cmd = [ffmpeg, "-v", "error", "-y", "-i", str(base)]
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
                str(into / f"{slug(label)}.mp4")]
        subprocess.run(cmd, check=True)
    base.unlink()


def read_frames(ffmpeg: str, path: Path) -> np.ndarray:
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-frames:v", str(FRAMES),
         "-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    n = len(out) // (W * H * 3)
    return np.frombuffer(out[: n * W * H * 3], np.uint8).reshape(n, H, W, 3)


def delta_e(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    return float(
        np.linalg.norm(
            rgb_to_lab(a[:n].astype(np.float64) / 255)
            - rgb_to_lab(b[:n].astype(np.float64) / 255),
            axis=-1,
        ).mean()
    )


def main() -> int:
    ffmpeg = find_ffmpeg(None)
    workdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
    footage = workdir / "footage"

    print(f"working in {workdir}\ngenerating footage...")
    build_footage(ffmpeg, footage)

    for method in METHODS:
        print(f"matching with {method}...")
        rc = cli_main([
            "match", str(footage),
            "-r", "reference.mp4",
            "--method", method, "--strength", "1.0",
            "-o", str(workdir / method),
            "--render", "--overwrite", "-q",
        ])
        if rc != 0:
            print(f"  {method} failed", file=sys.stderr)
            return rc

    ref = read_frames(ffmpeg, footage / "reference.mp4")
    clips = [c for c in VARIANTS if c != "reference"]

    header = f"\n| {'clip':<20} | {'before':>7} |" + "".join(
        f" {m:>8} |" for m in METHODS
    )
    print(header)
    print("|" + "-" * 22 + "|" + "-" * 9 + "|" + ("-" * 10 + "|") * len(METHODS))

    regressions = 0
    for label in clips:
        before = delta_e(read_frames(ffmpeg, footage / f"{slug(label)}.mp4"), ref)
        scores = {}
        for method in METHODS:
            rendered = workdir / method / "rendered" / f"{slug(label)}_matched.mp4"
            scores[method] = delta_e(read_frames(ffmpeg, rendered), ref)
        best = min(scores, key=scores.get)
        row = f"| {label:<20} | {before:>7.2f} |"
        for method in METHODS:
            mark = "*" if method == best else " "
            row += f" {scores[method]:>7.2f}{mark}|"
        print(row)
        if scores["cdl"] >= before:
            regressions += 1

    print("\n* = best for that clip")
    if regressions:
        print(f"WARNING: the default method regressed on {regressions} clip(s)",
              file=sys.stderr)
        return 1
    print("Default method (cdl) improved every clip.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
