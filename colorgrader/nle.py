"""Getting the results into DaVinci Resolve and Premiere Pro."""

from __future__ import annotations

import json
from pathlib import Path

RESOLVE_LUT_DIRS = {
    "macOS": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/",
    "Windows": r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT\ ".strip(),
    "Linux": "/opt/resolve/LUT/",
}

INSTRUCTIONS = """\
# How to use these files

The LUTs in `luts/` are *technical* corrections: they move each clip onto the
reference clip's colour. Apply them **first**, before any creative look, so the
look lands on footage that already matches.

The reference clip has no LUT - it is the thing everything else is matched to.

## DaVinci Resolve

Option A - per clip, no install needed:
  1. Color page, select a clip.
  2. Right-click the first node > LUT > Open LUT Browser, or drag the `.cube`
     from Finder/Explorer straight onto the node.

Option B - install them so they show up in the LUT list:
  1. Copy the `luts/` folder into Resolve's LUT directory:
{resolve_dirs}
  2. In Resolve: Project Settings > Color Management > Update Lists.
  3. Color page > LUTs > the folder you copied in.

Option C - apply them all automatically:
  Put your clips on a timeline in the order you like, then run
  `apply_in_resolve.py` (generated next to this file) from
  Workspace > Console > Py3, or via Resolve's Scripts menu. It matches clips by
  filename and drops each LUT on node 1.

ASC CDL: the `.cdl` files in `cdl/` are the same correction as slope/offset/
power values. Resolve reads them via right-click on a clip in the Media Pool >
ASC CDL, or the Color page's CDL import.

## Premiere Pro

  1. Select the clip, open the Lumetri Color panel.
  2. Basic Correction > Input LUT > Browse... > pick the clip's `.cube`.

Use Input LUT, not Creative > Look: Input LUT sits at the top of Lumetri's
chain, so your creative adjustments stack on top of the corrected image.

Premiere does not import ASC CDL. Use the `.cube` files, or type the
lift/gamma/gain numbers from the report into Lumetri's colour wheels.

## Dialling it back

If a match looks overcooked, re-run with a lower `--strength` (0.5-0.8 is a
good range) rather than fighting it downstream. You can also lower the LUT's
opacity: Resolve has a Key > Key Output Gain on the node; Premiere does not
expose LUT opacity, so re-exporting at a lower strength is the way there.
"""


def instructions_text() -> str:
    dirs = "\n".join(f"       {os}: {p}" for os, p in RESOLVE_LUT_DIRS.items())
    return INSTRUCTIONS.format(resolve_dirs=dirs)


RESOLVE_SCRIPT = '''\
"""Apply colorgrader LUTs to the clips on the current Resolve timeline.

Run from DaVinci Resolve: Workspace > Console > Py3, or drop this file in
Resolve's Scripts folder and pick it from Workspace > Scripts.

Clips are matched to LUTs by filename, so renaming source files after the
grade was computed will make them fall through as "no LUT".
"""

import os
import sys

# filename -> absolute .cube path, written out by colorgrader.
LUT_MAP = {lut_map}

NODE_INDEX = 1  # which node in the clip's grade receives the LUT


def get_resolve():
    try:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except ImportError:
        pass
    try:
        return resolve  # injected when running inside Resolve's console
    except NameError:
        pass
    sys.exit(
        "Could not reach the Resolve API. Run this from Resolve's console "
        "(Workspace > Console > Py3), and check that scripting is enabled in "
        "Preferences > System > General."
    )


def main():
    resolve_app = get_resolve()
    project = resolve_app.GetProjectManager().GetCurrentProject()
    if not project:
        sys.exit("No project open.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        sys.exit("No timeline open. Put your clips on a timeline first.")

    applied = skipped = failed = 0
    for track in range(1, timeline.GetTrackCount("video") + 1):
        for item in timeline.GetItemListInTrack("video", track) or []:
            name = os.path.basename(item.GetName() or "")
            lut = LUT_MAP.get(name)
            if not lut:
                print("  no LUT for %s (reference clip, or renamed?)" % name)
                skipped += 1
                continue
            if not os.path.exists(lut):
                print("  LUT file missing: %s" % lut)
                failed += 1
                continue
            if item.SetLUT(NODE_INDEX, lut):
                print("  applied %s -> %s" % (os.path.basename(lut), name))
                applied += 1
            else:
                print("  Resolve refused the LUT for %s" % name)
                failed += 1

    print("\\ndone: %d applied, %d skipped, %d failed" % (applied, skipped, failed))


main()
'''


def write_resolve_script(path: Path, lut_map: dict[str, Path]) -> Path:
    """Generate a Resolve script that applies each LUT to its clip."""
    serialisable = {k: str(Path(v).resolve()) for k, v in lut_map.items()}
    body = json.dumps(serialisable, indent=4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RESOLVE_SCRIPT.format(lut_map=body), encoding="utf-8")
    return path


def write_instructions(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(instructions_text(), encoding="utf-8")
    return path
