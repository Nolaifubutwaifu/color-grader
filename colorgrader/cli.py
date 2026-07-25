"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .ffmpeg import (
    ClipInfo, FFmpegError, FFmpegMissing, collect_inputs,
    find_ffmpeg, find_ffprobe, grab_frames, probe,
)
from .lut import write_cdl, write_cube
from .match import build_transform, identity_transform, match_error
from .nle import write_instructions, write_resolve_script
from .render import EXTENSIONS, render
from .report import write_report
from .stats import ClipStats, analyse, pick_reference


def _log(msg: str = "", quiet: bool = False) -> None:
    if not quiet:
        print(msg, file=sys.stderr, flush=True)


def _load_clips(
    paths: list[Path], ffmpeg_path: str, ffprobe_path: str | None,
    frames: int, quiet: bool,
) -> tuple[list[ClipInfo], list[ClipStats]]:
    """Probe and measure every clip, skipping ones that will not decode."""
    infos: list[ClipInfo] = []
    stats: list[ClipStats] = []
    for i, path in enumerate(paths, 1):
        _log(f"[{i}/{len(paths)}] reading {path.name}", quiet)
        try:
            info = probe(path, ffmpeg_path, ffprobe_path)
            sampled = grab_frames(info, ffmpeg_path, count=frames)
        except FFmpegError as exc:
            _log(f"    skipped: {exc}", quiet)
            continue
        infos.append(info)
        stats.append(analyse(path.name, sampled))
    return infos, stats


def _resolve_reference(
    stats: list[ClipStats], infos: list[ClipInfo], requested: str | None,
    ffmpeg_path: str, ffprobe_path: str | None, frames: int, quiet: bool,
) -> int:
    """Return the index of the reference clip in `stats`.

    The reference may be one of the inputs (matched by name), or an external
    clip that lives outside the batch - in which case it is analysed and
    prepended to `stats`/`infos` in place, so a fixed hero clip can anchor
    batch after batch without being copied into each folder.
    """
    if requested is None:
        return pick_reference(stats)

    want = Path(requested).name.lower()
    for i, s in enumerate(stats):
        if s.name.lower() == want or Path(s.name).stem.lower() == Path(want).stem:
            return i

    external = Path(requested).expanduser()
    if external.is_file():
        try:
            info = probe(external, ffmpeg_path, ffprobe_path)
            sampled = grab_frames(info, ffmpeg_path, count=frames)
        except FFmpegError as exc:
            raise SystemExit(f"Could not read reference clip {requested!r}: {exc}")
        stats.insert(0, analyse(external.name, sampled))
        infos.insert(0, info)
        _log(f"external reference: {external.name}", quiet)
        return 0

    raise SystemExit(
        f"Reference {requested!r} is neither one of the inputs nor a file that "
        f"exists. Inputs: " + ", ".join(s.name for s in stats)
    )


def cmd_match(args: argparse.Namespace) -> int:
    quiet = args.quiet
    ffmpeg_path = find_ffmpeg(args.ffmpeg)
    ffprobe_path = find_ffprobe(ffmpeg_path)

    paths = collect_inputs(args.inputs)
    if not paths:
        raise SystemExit("No video files found in the given paths.")

    infos, stats = _load_clips(paths, ffmpeg_path, ffprobe_path, args.frames, quiet)
    if not stats:
        raise SystemExit("No clips could be decoded.")

    ref_index = _resolve_reference(
        stats, infos, args.reference, ffmpeg_path, ffprobe_path, args.frames, quiet
    )
    # With an external reference a single input clip is a valid batch; without
    # one we need at least two clips so there is something to match against.
    if len(stats) < 2:
        raise SystemExit(
            "Nothing to match: give at least two clips, or one clip plus an "
            "external --reference to match it to."
        )
    reference = stats[ref_index]
    _log(f"\nreference: {reference.name}", quiet)
    _log(f"strength: {args.strength:.2f}\n", quiet)

    outdir = Path(args.output).expanduser()
    lut_dir, cdl_dir = outdir / "luts", outdir / "cdl"

    transforms, errors, lut_map = [], [], {}
    for st in stats:
        if st is reference:
            transforms.append(identity_transform(st.name))
            errors.append({"before": 0.0, "after": 0.0, "improvement": 0.0})
            _log(f"  {st.name}: reference, no correction", quiet)
            continue

        tf = build_transform(st, reference, strength=args.strength)
        err = match_error(st, reference, tf)
        transforms.append(tf)
        errors.append(err)

        stem = Path(st.name).stem
        lut_path = write_cube(
            tf, lut_dir / f"{stem}_to_{Path(reference.name).stem}.cube",
            size=args.lut_size, title=f"{stem} -> {Path(reference.name).stem}",
        )
        write_cdl(tf, cdl_dir / f"{stem}.cdl", clip_id=stem)
        lut_map[st.name] = lut_path
        _log(
            f"  {st.name}: dE {err['before']:.1f} -> {err['after']:.1f}  "
            f"({lut_path.name})",
            quiet,
        )

    report_path = write_report(
        outdir / "report.html", stats, transforms, errors,
        ref_index, args.strength,
    )
    write_instructions(outdir / "HOW_TO_USE.md")
    write_resolve_script(outdir / "apply_in_resolve.py", lut_map)

    (outdir / "match.json").write_text(
        json.dumps(
            {
                "version": __version__,
                "strength": args.strength,
                "reference": reference.name,
                "clips": [
                    {
                        "name": st.name,
                        "is_reference": i == ref_index,
                        "delta_e": errors[i],
                        "lut": str(lut_map[st.name]) if st.name in lut_map else None,
                        "cdl": (
                            transforms[i].cdl.as_dict()
                            if transforms[i].cdl and i != ref_index else None
                        ),
                        "lift_gamma_gain": (
                            transforms[i].cdl.lift_gamma_gain()
                            if transforms[i].cdl and i != ref_index else None
                        ),
                    }
                    for i, st in enumerate(stats)
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if args.render:
        _log("\nrendering matched files...", quiet)
        render_dir = outdir / "rendered"
        for info, st, tf in zip(infos, stats, transforms):
            if st.name not in lut_map:
                continue
            out = render_dir / f"{info.stem}_matched{EXTENSIONS[args.codec]}"
            try:
                render(info.path, lut_map[st.name], out, ffmpeg_path,
                       codec=args.codec, overwrite=args.overwrite)
                _log(f"  wrote {out.name}", quiet)
            except (FFmpegError, FileExistsError) as exc:
                _log(f"  {info.name}: {exc}", quiet)

    _log(f"\nDone. Open {report_path} to check the match.", quiet)
    _log(f"Then read {outdir / 'HOW_TO_USE.md'} to get it into "
         f"Resolve or Premiere.", quiet)
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    quiet = args.quiet
    ffmpeg_path = find_ffmpeg(args.ffmpeg)
    ffprobe_path = find_ffprobe(ffmpeg_path)

    paths = collect_inputs(args.inputs)
    if not paths:
        raise SystemExit("No video files found in the given paths.")

    _, stats = _load_clips(paths, ffmpeg_path, ffprobe_path, args.frames, quiet)
    if not stats:
        raise SystemExit("No clips could be decoded.")

    print(f"\n{'clip':<38} {'exposure':>9} {'saturation':>11} "
          f"{'black':>7} {'white':>7}")
    print("-" * 76)
    for st in stats:
        print(
            f"{st.name[:38]:<38} {st.exposure:>9.3f} {st.saturation:>11.2f} "
            f"{st.shadow.mean():>7.3f} {st.highlight.mean():>7.3f}"
        )

    if len(stats) > 1:
        print(f"\nsuggested reference: {stats[pick_reference(stats)].name}")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    # Imported lazily so the CLI stays snappy and http.server is only pulled
    # in when someone actually wants the interface.
    from .server import serve

    folder = args.folder
    if folder is None and args.inputs:
        folder = args.inputs[0]
    serve(folder=folder, host=args.host, port=args.port,
          open_browser=not args.no_browser)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    try:
        ffmpeg_path = find_ffmpeg(args.ffmpeg)
    except FFmpegMissing as exc:
        print(f"ffmpeg:  NOT FOUND\n\n{exc}")
        return 1
    print(f"ffmpeg:  {ffmpeg_path}")
    print(f"ffprobe: {find_ffprobe(ffmpeg_path) or 'not found (optional)'}")
    try:
        import numpy
        print(f"numpy:   {numpy.__version__}")
    except ImportError:
        print("numpy:   NOT FOUND - run `pip install numpy`")
        return 1
    print("\nAll good.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="colorgrade",
        description="Match the colour of a set of video clips to each other, "
                    "and export the correction for DaVinci Resolve and Premiere Pro.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("inputs", nargs="+",
                       help="Video files, or folders of video files.")
        p.add_argument("--frames", type=int, default=12, metavar="N",
                       help="Frames sampled per clip (default: 12).")
        p.add_argument("--ffmpeg", metavar="PATH",
                       help="Path to ffmpeg, if it is not on your PATH.")
        p.add_argument("-q", "--quiet", action="store_true",
                       help="Suppress progress output.")

    m = sub.add_parser("match", help="Match clips and export LUTs (the main command).")
    common(m)
    m.add_argument("-o", "--output", default="colorgrade_out", metavar="DIR",
                   help="Where to write results (default: colorgrade_out).")
    m.add_argument("-r", "--reference", metavar="CLIP",
                   help="Clip everything else should match. "
                        "Default: the most typical clip of the set.")
    m.add_argument("--strength", type=float, default=0.85, metavar="0-1",
                   help="How far to push toward the reference (default: 0.85).")
    m.add_argument("--lut-size", type=int, default=33, metavar="N",
                   help="3D LUT lattice size (default: 33).")
    m.add_argument("--render", action="store_true",
                   help="Also bake the correction into new video files.")
    m.add_argument("--codec", choices=tuple(EXTENSIONS), default="h264",
                   help="Codec for --render (default: h264).")
    m.add_argument("--overwrite", action="store_true",
                   help="Replace existing rendered files.")
    m.set_defaults(func=cmd_match)

    a = sub.add_parser("analyze", help="Report each clip's colour without grading.")
    common(a)
    a.set_defaults(func=cmd_analyze)

    g = sub.add_parser("gui", help="Open the interface in your browser.")
    g.add_argument("inputs", nargs="*",
                   help="Optional folder of clips to preload.")
    g.add_argument("--folder", metavar="DIR",
                   help="Folder of clips to preload (same as positional).")
    g.add_argument("--port", type=int, default=8000, help="Port (default: 8000).")
    g.add_argument("--host", default="127.0.0.1", help="Bind address.")
    g.add_argument("--no-browser", action="store_true",
                   help="Do not open a browser automatically.")
    g.set_defaults(func=cmd_gui)

    d = sub.add_parser("doctor", help="Check that ffmpeg and numpy are usable.")
    d.add_argument("--ffmpeg", metavar="PATH")
    d.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FFmpegMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (FFmpegError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
