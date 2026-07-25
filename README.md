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
wipe** on each one. Drag the **strength** slider or pick a different
**reference** and every clip re-matches live. Set a per-clip strength override
when one shot needs a lighter touch. Hit **Export** to write the LUTs, report
and instructions — tick *bake
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
| `--frames` | `12` | Frames sampled per clip |
| `--lut-size` | `33` | 3D LUT lattice size |
| `--render` | off | Also bake out video files |
| `--codec` | `h264` | `h264`, `h265`, or `prores` |

### How the match works

There is one matching method, because it is the one that actually holds up.
It fits an **ASC CDL** correction from each clip's measured tonal anchors:
the black point, midtone and white point, **per channel**. Matching those three
points on each of red, green and blue is what corrects a colour cast — a warm
clip has its red curve sitting high and its blue low, and pulling all three
channels onto the reference's neutralises it. A gentle saturation nudge finishes
the match. The result exports cleanly as a `.cube` LUT, as ASC CDL, and as
lift/gamma/gain numbers, because it *is* those controls.

Earlier versions shipped three more methods — histogram matching, a Reinhard
mean/standard-deviation transfer, and a standalone white-balance gain. Testing
retired all three. The distribution methods only win when every clip contains
*identical* content (a synthetic ideal real footage never meets) and otherwise
overcook — forcing one image's histogram onto another's blows out saturation and
bands gradients. The white-balance gain looked promising but could not reliably
*measure* a cast from arbitrary footage: with no shared grey reference, blind
estimators return a near-neutral gain even on a clearly warm clip, and sometimes
make the match worse. Per-channel CDL sidesteps that by matching the anchors it
can actually measure. See the commit history if you want the evidence.

Measured by `python tests/benchmark.py`, as mean Lab ΔE against the reference
(lower is better) — the correction improves every clip:

| clip | before | after |
|---|---|---|
| mild warm cast | 1.85 | 1.10 |
| cool + flat | 4.12 | 2.91 |
| desaturated + dark | 39.47 | 12.67 |

Those clips are all derived from one source render, so this is a true per-pixel
measure rather than the anchor metric the tool tunes against.

## Mixed lighting — a social feed shot indoors and out

When some clips were shot inside (warm) and some outside (cool) and you want the
whole set to feel like one cohesive feed, this is the recipe:

```bash
colorgrade gui /footage        # or: colorgrade match /footage -r hero.mov
```

1. **Pick a hero that has the look you want the feed to have** — usually a
   well-lit indoor clip with flattering skin and hair tones. Everything else
   gets pulled toward it.
2. **Start at a moderate strength (0.6–0.8).** The per-channel match neutralises
   the indoor/outdoor cast well; easing off keeps it from over-reaching on a
   clip whose content differs a lot from the hero. Push toward 1.0 for clips
   that are genuinely the same setup.
3. **Judge by eye, not by the ΔE number.** An outdoor clip will always show a
   higher residual ΔE than an indoor one, because it genuinely contains
   different things — that residual is *content*, not colour, and no colour
   correction removes it. What matters is whether the whites and skin now read
   the same warmth across clips. The report's side-by-side is there for exactly
   this.
4. **Dial back the odd stubborn clip.** If one outdoor shot still looks pushed,
   drop its **per-clip strength override** in the GUI until it sits right. If a
   shot is lit so differently that no strength looks right, it is faster to nudge
   its temperature by hand in Premiere than to force a statistical match — the
   tool won't pretend otherwise.
5. Optional: once everything is *balanced*, add one creative look on top in
   Premiere (a single Lumetri look across all clips) to give the feed its brand
   feel. Balance first, look second.

Skin and hair are the subject of a salon feed, so that is what to watch in the
report: if faces read consistently warm across inside and outside, you are done.

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

# End-to-end: generates footage, grades it, and measures the result
# through ffmpeg's own LUT application
python tests/benchmark.py
```

The colour maths lives in `stats.py` (measurement) and `match.py` (correction).
The correction is a `Transform` — a function from RGB to RGB. Everything
downstream — LUT export, ffmpeg baking, report previews — only talks to that
interface, so an alternative correction would be one function and one entry in
`_BUILDERS`, without touching the rest of the tool.
