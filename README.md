# colorgrader

Point it at a folder of clips. It samples frames from each one, measures their
colour, picks the most typical clip as a reference, and works out what every
other clip needs to look like it belongs in the same scene.

You get back `.cube` LUTs that load straight into **DaVinci Resolve** and
**Premiere Pro**, ASC CDL files, the lift/gamma/gain numbers if you would rather
turn the wheels yourself, and an HTML report showing before and after so you can
check the result before trusting it.

This is shot matching, not a creative look. It gets your footage to a consistent
starting point; the grade on top is still yours.

## Install

Needs Python 3.9+ and ffmpeg.

```bash
pip install -e .

# ffmpeg, if you do not have it:
#   macOS     brew install ffmpeg
#   Windows   winget install Gyan.FFmpeg
#   Linux     apt install ffmpeg
```

Check everything is wired up:

```bash
colorgrade doctor
```

## Use

Two ways in: a browser interface, or the command line.

### The interface

```bash
colorgrade gui /path/to/footage      # folder is optional; you can load one in the app
```

Opens a local app in your browser. Hit **Browse…** for a native folder dialog
(or type a path), and it lays every clip out with a **draggable before/after
wipe** on each one. Change the method,
drag the **strength** slider, or pick a different **reference** and every clip
re-matches live. Set a per-clip strength override when one shot needs a lighter
touch. Hit **Export** to write the LUTs, report and instructions — tick *bake
video* to get finished matched files too.

It runs entirely on your machine (localhost, no upload, no account), and it is
the same engine as the CLI below — just with a picture.

### The command line

```bash
colorgrade match /path/to/footage
```

That writes into `colorgrade_out/`:

```
report.html           before/after frames + histograms for every clip
luts/*.cube           one LUT per clip - this is the thing you load
cdl/*.cdl             same correction as ASC CDL (Resolve reads these)
match.json            every number, if you want to script against it
apply_in_resolve.py   applies all the LUTs to your Resolve timeline for you
HOW_TO_USE.md         step-by-step for both Resolve and Premiere
```

Open `report.html` first. If the match looks right, load the LUTs.

### Do you have to apply these by hand?

Mostly no.

**Baked files** — run with `--render` (or tick *bake video* in the GUI) and
colorgrader applies each correction with ffmpeg and hands you finished, matched
video files in `rendered/`. Drop them on your timeline — in Premiere or anything
else — and there is nothing left to grade. This is the zero-effort path, and the
one to use for Premiere.

**Resolve** — the generated `apply_in_resolve.py` applies every LUT across your
whole timeline automatically (Workspace › Console › Py3). Or drag a single
`.cube` onto a clip's first node.

**Premiere, keeping your originals** — Premiere's scripting can't reliably
assign a LUT, so this one is manual, but it is two clicks: Lumetri Color › Basic
Correction › Input LUT › Browse. Use *Input LUT*, not Creative › Look, so your
own adjustments stack on top of a corrected image.

Full step-by-step for both apps lands in `HOW_TO_USE.md` next to the LUTs.

### Options worth knowing

```bash
# Choose the clip everything else matches, instead of letting it pick
colorgrade match footage/ --reference hero_shot.mov

# Match to a hero clip that lives outside the folder (great for batches)
colorgrade match footage/ --reference /path/to/hero.mov

# Ease off if the match is too aggressive (default 0.85)
colorgrade match footage/ --strength 0.6

# Skip the NLE and just get graded files out
colorgrade match footage/ --render --codec prores

# Look at the footage without grading anything
colorgrade analyze footage/
```

| Flag | Default | |
|---|---|---|
| `--reference` | most typical clip | Clip to match everything to |
| `--strength` | `0.85` | 0 = untouched, 1 = full match |
| `--method` | `cdl` | `cdl`, `reinhard`, or `hist` |
| `--frames` | `12` | Frames sampled per clip |
| `--lut-size` | `33` | 3D LUT lattice size |
| `--render` | off | Also bake out video files |
| `--codec` | `h264` | `h264`, `h265`, or `prores` |

### The three methods

**`cdl`** (default) matches black point, midtone and white point per channel,
then aligns saturation. The most predictable of the three, and the only one
whose numbers map cleanly onto real grading controls. It was the only method
that improved every clip in testing, which is why it is the default.

**`hist`** reshapes each channel's full tonal distribution onto the reference's.
The most literal match, and it scored best on test footage where every clip
shared identical content — the ideal case for it. Real shoots are not that case:
forcing a wide landscape's distribution onto a close-up is exactly how histogram
matching goes wrong, and it is the most likely of the three to band on skies and
gradients. Worth trying when your clips really are the same setup. Pair it with a
lower `--strength`.

**`reinhard`** matches average lightness and colour cast, plus their spread, in
Lab. It handles a heavily desaturated clip better than anything else here, but on
clips that already nearly match it can make things slightly *worse* — matching
standard deviations has nothing useful left to do at that point and starts
amplifying differences instead. Reach for it when one clip is badly off and the
rest are fine; check the report afterwards.

Measured by `python tests/benchmark.py`, as mean Lab ΔE against the reference
(lower is better, **bold** is best per row):

| clip | before | `cdl` | `hist` | `reinhard` |
|---|---|---|---|---|
| mild warm cast | 1.85 | 1.10 | **0.85** | 2.24 |
| cool + flat | 4.12 | 2.83 | **1.51** | 3.38 |
| desaturated + dark | 39.47 | 11.90 | 8.55 | **6.78** |

Those clips are all derived from one source render, so the comparison is a true
per-pixel measure rather than the anchor metric the tool tunes against. Take it
as a rough guide to each method's character, not a ranking — your footage does
not have identical content across shots.

## Matching a lot of clips to one hero

Each clip's correction depends only on that clip and the reference — never on
the other clips in the run. So matching in batches to a fixed hero clip gives
**byte-identical** LUTs to matching everything at once; how you split the work
does not change the result.

Point `--reference` at a hero clip that lives *outside* the folder and it never
has to be copied into each batch:

```bash
# one clip, or a folder, or many folders — all matched to the same hero
colorgrade match batch_01/ -r /footage/hero.mov -o out/batch_01
colorgrade match batch_02/ -r /footage/hero.mov -o out/batch_02
# ...
```

Give each batch its own `-o` so their reports and Resolve scripts don't
overwrite each other. (You can also just point it at the whole folder in one
go — a couple hundred clips takes a few minutes.)

One thing this does *not* fix: if your clips span genuinely different scenes or
lighting, one hero can't be right for all of them. Matching neutralises each
clip toward the hero, so a shot lit differently will be dragged toward the
hero's look. Use one hero per lighting setup — a folder (and a hero) per scene.

## Why `--strength` defaults to 0.85

A full-strength match is usually a little too much. Clips differ partly because
they were shot differently and partly because they *contain* different things —
a wide of a field and a close-up of a face genuinely have different colour
distributions, and forcing them into agreement drags the face somewhere strange.
Backing off slightly keeps the correction honest. Push it to `1.0` when your
clips really are the same setup.

## Things to know

- **The LUTs go first in the chain.** They are technical corrections. Apply them
  before any creative look, so the look lands on footage that already matches.
- **Log and raw footage:** the LUTs are built from the clip as ffmpeg decodes it.
  If you are shooting **log** (Sony S-Log2/S-Log3, Canon C-Log, etc.), apply your
  conversion to Rec.709 first, then generate the LUTs from the converted footage
  — otherwise the correction is fitted to a curve you are about to replace.
  Standard-dynamic-range profiles do **not** need this: Sony's Movie gamma
  (a7III Picture Profile 1) and the Cine gammas are already display-referred, so
  feed them in as-is. The rule is log vs. not-log, not the profile number.
- **One grade per file.** If a single file contains several very different
  setups, it gets one average correction. Split it into separate clips first.
- **Letterboxed footage** is handled — black bars are detected and excluded
  before measuring, so they do not drag the black point.
- **Matching is statistical.** It compares colour distributions, not subjects.
  It cannot know that the red thing in one shot is a jacket and in another is a
  wall. The report exists so you can catch that.

## Development

```bash
pip install pytest
python -m pytest tests/ -q

# End-to-end: generates footage, grades it with every method, and measures
# the result through ffmpeg's own LUT application
python tests/benchmark.py
```

The colour maths lives in `stats.py` (measurement) and `match.py` (correction).
Every method produces the same thing: a function from RGB to RGB. Everything
downstream — LUT export, ffmpeg baking, report previews — only talks to that
interface, so a new matching method means one function and one entry in
`_BUILDERS`.
