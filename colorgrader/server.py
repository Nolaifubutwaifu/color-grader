"""Local web server behind `colorgrade gui`.

Deliberately stdlib-only (http.server): the GUI ships with the same numpy-only
install as the CLI, no web framework to pull in. It binds to localhost, holds
the analysed clips in memory so tuning the sliders never re-decodes video, and
reuses the exact same match/export code paths as the command line - the GUI is
a front end onto the library, not a second implementation of it.
"""

from __future__ import annotations

import base64
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from . import __version__
from .ffmpeg import (
    FFmpegError, collect_inputs, find_ffmpeg, find_ffprobe, grab_frames, probe,
)
from .lut import write_cdl, write_cube
from .match import build_transform, identity_transform, match_error
from .nle import write_instructions, write_resolve_script
from .render import EXTENSIONS, render
from .report import encode_png, write_report
from .stats import ClipStats, analyse, pick_reference
from .webui import INDEX_HTML

# Analysed clips live here between requests so slider moves stay instant.
# Single local user, so a module-level cache is all this needs.
_SESSION: dict = {"folder": None, "infos": [], "stats": [], "frames": 12}
_LOCK = threading.Lock()


def _thumb_uri(rgb: np.ndarray) -> str:
    """A uint8 or float (h,w,3) frame -> a PNG data URI for an <img>."""
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
    return "data:image/png;base64," + base64.b64encode(encode_png(rgb)).decode("ascii")


def _effective_strength(index: int, base: float, overrides: dict) -> float:
    """Per-clip override wins over the global slider when present."""
    key = str(index)
    if key in overrides and overrides[key] is not None:
        return float(overrides[key])
    if index in overrides and overrides[index] is not None:
        return float(overrides[index])
    return base


def _load(folder: str, frames: int) -> dict:
    ffmpeg = find_ffmpeg(None)
    ffprobe = find_ffprobe(ffmpeg)
    paths = collect_inputs([folder])
    if not paths:
        raise ValueError(f"No video files found in {folder!r}.")

    infos, stats = [], []
    for path in paths:
        try:
            info = probe(path, ffmpeg, ffprobe)
            sampled = grab_frames(info, ffmpeg, count=frames)
        except FFmpegError:
            continue  # unreadable clip: leave it out rather than fail the batch
        infos.append(info)
        stats.append(analyse(path.name, sampled))

    if not stats:
        raise ValueError("None of the files in that folder could be decoded.")

    with _LOCK:
        _SESSION.update(folder=str(Path(folder).expanduser()),
                        infos=infos, stats=stats, frames=frames)

    suggested = pick_reference(stats) if len(stats) > 1 else 0
    return {
        "version": __version__,
        "suggested_ref": suggested,
        "default_output": str(Path(folder).expanduser() / "colorgrade_out"),
        "clips": [
            {
                "i": i, "name": s.name,
                "width": info.width, "height": info.height,
                "exposure": round(s.exposure, 3), "saturation": round(s.saturation, 2),
                "before": _thumb_uri(s.thumbnail),
            }
            for i, (s, info) in enumerate(zip(stats, infos))
        ],
    }


def _preview(method: str, strength: float, reference: int, overrides: dict) -> dict:
    with _LOCK:
        stats: list[ClipStats] = _SESSION["stats"]
    if not stats:
        raise ValueError("No clips loaded yet.")
    reference = max(0, min(reference, len(stats) - 1))
    ref = stats[reference]

    clips = []
    for i, s in enumerate(stats):
        if i == reference:
            clips.append({
                "i": i, "after": _thumb_uri(s.thumbnail),
                "delta": {"before": 0.0, "after": 0.0}, "lgg": None,
            })
            continue
        strength_i = _effective_strength(i, strength, overrides)
        tf = build_transform(s, ref, method=method, strength=strength_i)
        after = tf.apply(s.thumbnail.astype(np.float64) / 255.0)
        clips.append({
            "i": i, "after": _thumb_uri(after),
            "delta": match_error(s, ref, tf),
            "lgg": tf.cdl.lift_gamma_gain() if tf.cdl else None,
        })
    return {"reference": reference, "clips": clips}


def _export(body: dict) -> dict:
    with _LOCK:
        stats: list[ClipStats] = _SESSION["stats"]
        infos = _SESSION["infos"]
        folder = _SESSION["folder"]
    if not stats:
        raise ValueError("No clips loaded yet.")

    method = body.get("method", "cdl")
    strength = float(body.get("strength", 0.85))
    overrides = body.get("overrides", {})
    reference = max(0, min(int(body.get("reference", 0)), len(stats) - 1))
    output = Path(body.get("output") or (Path(folder) / "colorgrade_out")).expanduser()
    do_render = bool(body.get("render"))
    codec = body.get("codec", "h264")

    ref = stats[reference]
    lut_dir, cdl_dir = output / "luts", output / "cdl"
    transforms, errors, lut_map = [], [], {}

    for i, s in enumerate(stats):
        if i == reference:
            transforms.append(identity_transform(s.name))
            errors.append({"before": 0.0, "after": 0.0, "improvement": 0.0})
            continue
        tf = build_transform(s, ref, method=method,
                             strength=_effective_strength(i, strength, overrides))
        transforms.append(tf)
        errors.append(match_error(s, ref, tf))
        stem, rstem = Path(s.name).stem, Path(ref.name).stem
        lut_map[s.name] = write_cube(
            tf, lut_dir / f"{stem}_to_{rstem}.cube",
            title=f"{stem} -> {rstem}",
        )
        write_cdl(tf, cdl_dir / f"{stem}.cdl", clip_id=stem)

    write_report(output / "report.html", stats, transforms, errors,
                 reference, method, strength)
    write_instructions(output / "HOW_TO_USE.md")
    write_resolve_script(output / "apply_in_resolve.py", lut_map)
    (output / "match.json").write_text(json.dumps({
        "version": __version__, "method": method, "strength": strength,
        "reference": ref.name,
        "clips": [
            {"name": s.name, "is_reference": i == reference,
             "delta_e": errors[i],
             "lut": str(lut_map[s.name]) if s.name in lut_map else None}
            for i, s in enumerate(stats)
        ],
    }, indent=2), encoding="utf-8")

    rendered = 0
    if do_render:
        ffmpeg = find_ffmpeg(None)
        for info, s in zip(infos, stats):
            if s.name not in lut_map:
                continue
            out = output / "rendered" / f"{info.stem}_matched{EXTENSIONS[codec]}"
            try:
                render(info.path, lut_map[s.name], out, ffmpeg,
                       codec=codec, overwrite=True)
                rendered += 1
            except FFmpegError:
                pass

    return {"output": str(output), "lut_count": len(lut_map), "rendered": rendered}


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = f"colorgrader/{__version__}"

    def log_message(self, *args):
        pass  # keep the console clean; errors still surface in responses

    def _send_json(self, obj: dict, status: int = 200):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send_json({"error": "Malformed request body."}, 400)

        try:
            if self.path == "/api/load":
                result = _load(body["folder"], int(body.get("frames", 12)))
            elif self.path == "/api/preview":
                result = _preview(
                    body.get("method", "cdl"), float(body.get("strength", 0.85)),
                    int(body.get("reference", 0)), body.get("overrides", {}),
                )
            elif self.path == "/api/export":
                result = _export(body)
            else:
                return self._send_json({"error": "Unknown endpoint."}, 404)
        except (ValueError, KeyError, FFmpegError, FileNotFoundError) as exc:
            return self._send_json({"error": str(exc)}, 400)
        except Exception as exc:  # never leak a stack trace to the browser
            return self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        self._send_json(result)


def serve(
    folder: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    open_browser: bool = True,
) -> None:
    """Start the GUI server and block until interrupted."""
    # Confirm ffmpeg is reachable before we advertise a URL that cannot work.
    find_ffmpeg(None)

    if folder:
        try:
            _load(folder, 12)
            print(f"loaded clips from {folder}")
        except Exception as exc:
            print(f"note: could not preload {folder}: {exc}")

    httpd = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}/"
    print(f"\n  colorgrader GUI running at {url}")
    print("  press Ctrl+C to stop\n")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()
