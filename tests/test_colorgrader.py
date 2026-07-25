"""Tests for the colour maths and file writers.

These deliberately avoid needing real video: the pieces that talk to ffmpeg are
thin, and the parts worth pinning down are the colour transforms and the LUT
file format.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from colorgrader.lut import read_cube, sample_lattice, write_cdl, write_cube
from colorgrader.match import (
    METHODS, build_transform, identity_transform, match_error,
)
from colorgrader.report import encode_png
from colorgrader.stats import (
    analyse, detect_content_box, lab_to_rgb, pick_reference, rgb_to_lab,
)


def make_frames(n=6, h=48, w=64, seed=0, gain=(1.0, 1.0, 1.0), bias=0.0):
    """Synthetic footage with a controllable colour cast."""
    rng = np.random.default_rng(seed)
    base = rng.random((n, h, w, 3)) * 0.6 + 0.2
    base = base * np.array(gain) + bias
    return np.clip(base * 255, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------
# Colour space round trips
# --------------------------------------------------------------------------

def test_lab_round_trip_is_lossless_enough():
    rng = np.random.default_rng(1)
    rgb = rng.random((5000, 3))
    assert np.allclose(lab_to_rgb(rgb_to_lab(rgb)), rgb, atol=1e-4)


def test_lab_known_values():
    # Pure white sits at L=100 with no chroma; black at L=0.
    white = rgb_to_lab(np.array([[1.0, 1.0, 1.0]]))[0]
    assert white[0] == pytest.approx(100.0, abs=0.1)
    assert np.allclose(white[1:], 0.0, atol=0.05)
    assert rgb_to_lab(np.array([[0.0, 0.0, 0.0]]))[0][0] == pytest.approx(0.0, abs=1e-6)


# --------------------------------------------------------------------------
# Letterbox detection
# --------------------------------------------------------------------------

def test_detect_content_box_finds_letterbox():
    frames = make_frames(h=100, w=100)
    frames[:, :20, :, :] = 0     # top bar
    frames[:, -15:, :, :] = 0    # bottom bar
    top, bottom, left, right = detect_content_box(frames)
    assert (top, bottom) == (20, 85)
    assert (left, right) == (0, 100)


def test_detect_content_box_leaves_clean_frames_alone():
    frames = make_frames(h=40, w=50)
    assert detect_content_box(frames) == (0, 40, 0, 50)


def test_detect_content_box_ignores_all_black_clip():
    frames = np.zeros((3, 40, 50, 3), np.uint8)
    assert detect_content_box(frames) == (0, 40, 0, 50)


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

@pytest.mark.parametrize("method", METHODS)
def test_matching_reduces_difference(method):
    warm = analyse("warm", make_frames(seed=2, gain=(1.25, 1.0, 0.75)))
    neutral = analyse("neutral", make_frames(seed=2))

    transform = build_transform(warm, neutral, method=method, strength=1.0)
    err = match_error(warm, neutral, transform)
    assert err["after"] < err["before"], f"{method} made the match worse"


@pytest.mark.parametrize("method", METHODS)
def test_transform_stays_in_range(method):
    a = analyse("a", make_frames(seed=3, gain=(1.3, 0.9, 0.7)))
    b = analyse("b", make_frames(seed=4, bias=0.1))
    out = build_transform(a, b, method=method).apply(
        np.random.default_rng(5).random((2000, 3))
    )
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_wb_is_a_pure_gain_and_leaves_saturation_alone():
    warm = analyse("warm", make_frames(seed=30, gain=(1.3, 1.0, 0.7)))
    neutral = analyse("neutral", make_frames(seed=30))
    tf = build_transform(warm, neutral, method="wb", strength=1.0)

    # A white-balance correction must be gain-only: no lift, no gamma, sat 1.
    lgg = tf.cdl.lift_gamma_gain()
    assert lgg["lift"] == [0.0, 0.0, 0.0]
    assert lgg["gamma"] == [1.0, 1.0, 1.0]
    assert lgg["saturation"] == 1.0
    # and it should still neutralise the cast it was given
    err = match_error(warm, neutral, tf)
    assert err["after"] < err["before"]


def test_wb_neutral_estimate_resists_a_saturated_block():
    """A big block of one colour (a feature wall, a hedge) must not hijack the
    white-balance estimate - that robustness is the whole point of the method."""
    from colorgrader.match import _neutral_estimate

    rng = np.random.default_rng(31)
    grey = rng.normal(0.5, 0.03, size=(40000, 3))          # near-neutral scene
    green = np.tile([0.05, 0.9, 0.05], (40000, 1))          # saturated intruder
    mixed = np.clip(np.vstack([grey, green]), 0, 1)

    est = _neutral_estimate(mixed)
    # Despite half the pixels being vivid green, the estimate stays near grey.
    assert abs(est[1] - est[0]) < 0.15 and abs(est[1] - est[2]) < 0.15
    plain_mean = mixed.mean(axis=0)
    assert est[1] < plain_mean[1]  # less green-biased than a naive grey-world


def test_matching_a_clip_to_itself_is_near_identity():
    st = analyse("same", make_frames(seed=6))
    out = build_transform(st, st, method="cdl", strength=1.0).apply(st.pixels_float)
    assert np.abs(out - st.pixels_float).mean() < 0.02


def test_strength_scales_the_correction():
    src = analyse("src", make_frames(seed=7, gain=(1.4, 1.0, 0.7)))
    ref = analyse("ref", make_frames(seed=7))
    probe = np.full((100, 3), 0.5)

    full = build_transform(src, ref, strength=1.0).apply(probe)
    half = build_transform(src, ref, strength=0.5).apply(probe)
    none = build_transform(src, ref, strength=0.0).apply(probe)

    assert np.allclose(none, probe, atol=1e-6)
    assert np.allclose(half, (probe + full) / 2, atol=1e-6)


def test_identity_transform_changes_nothing():
    probe = np.random.default_rng(8).random((500, 3))
    assert np.allclose(identity_transform().apply(probe), probe)


def test_unknown_method_is_rejected():
    st = analyse("x", make_frames(seed=9))
    with pytest.raises(ValueError, match="Unknown method"):
        build_transform(st, st, method="nonsense")


def test_pick_reference_chooses_the_middle_clip():
    # Three exposures: the middle one needs the least correction overall.
    clips = [
        analyse("dark", make_frames(seed=10, gain=(0.55, 0.55, 0.55))),
        analyse("mid", make_frames(seed=10)),
        analyse("bright", make_frames(seed=10, bias=0.3)),
    ]
    assert clips[pick_reference(clips)].name == "mid"


def test_pick_reference_handles_single_clip():
    assert pick_reference([analyse("only", make_frames(seed=11))]) == 0


# --------------------------------------------------------------------------
# LUT export
# --------------------------------------------------------------------------

def test_cube_has_correct_size_and_range(tmp_path):
    src = analyse("s", make_frames(seed=12, gain=(1.2, 1.0, 0.8)))
    ref = analyse("r", make_frames(seed=12))
    path = write_cube(build_transform(src, ref), tmp_path / "t.cube", size=17)

    size, values = read_cube(path)
    assert size == 17
    assert values.shape == (17**3, 3)
    assert values.min() >= 0.0 and values.max() <= 1.0


def test_cube_ordering_is_red_fastest(tmp_path):
    """The .cube spec has red varying fastest. Getting this backwards swaps the
    red and blue channels of every graded shot, so pin it down explicitly."""
    size = 5
    # A transform that reads only the red input, so ordering is observable.
    marker = identity_transform()
    marker.apply_fn = lambda rgb: np.stack(
        [rgb[..., 0], np.zeros_like(rgb[..., 0]), np.zeros_like(rgb[..., 0])],
        axis=-1,
    )
    _, values = read_cube(write_cube(marker, tmp_path / "o.cube", size=size))

    # First `size` entries must sweep red from 0 to 1.
    assert np.allclose(values[:size, 0], np.linspace(0, 1, size), atol=1e-6)
    # And entry `size` wraps back to red=0 as green steps up.
    assert values[size, 0] == pytest.approx(0.0, abs=1e-6)


def test_sample_lattice_matches_direct_evaluation():
    src = analyse("s", make_frames(seed=13, gain=(0.8, 1.1, 1.3)))
    ref = analyse("r", make_frames(seed=13))
    tf = build_transform(src, ref)

    values = sample_lattice(tf, size=9)
    # Corner at (r=1, g=0, b=0) is index 8 with red varying fastest.
    assert np.allclose(values[8], tf.apply(np.array([1.0, 0.0, 0.0])), atol=1e-6)


def test_bad_lut_size_is_rejected():
    with pytest.raises(ValueError, match="between 2 and 128"):
        sample_lattice(identity_transform(), size=1)


def test_cdl_written_for_correction_and_skipped_for_reference(tmp_path):
    src = analyse("s", make_frames(seed=14, gain=(1.3, 1.0, 0.7)))
    ref = analyse("r", make_frames(seed=14))

    written = write_cdl(build_transform(src, ref), tmp_path / "a.cdl", "shot_a")
    assert written is not None
    text = written.read_text()
    assert "<Slope>" in text and "shot_a" in text and "Saturation" in text

    skipped = write_cdl(identity_transform(), tmp_path / "b.cdl", "ref")
    assert skipped is None and not (tmp_path / "b.cdl").exists()


def test_cdl_id_is_xml_escaped(tmp_path):
    src = analyse("s", make_frames(seed=15, gain=(1.2, 1.0, 0.9)))
    ref = analyse("r", make_frames(seed=15))
    path = write_cdl(build_transform(src, ref), tmp_path / "c.cdl", 'a&b<c"')
    assert "&amp;" in path.read_text() and "a&b<" not in path.read_text()


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def test_png_encoder_produces_a_valid_file():
    png = encode_png(make_frames(n=1, h=8, w=12)[0])
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IHDR" in png and b"IDAT" in png and png.endswith(b"IEND\xae\x42\x60\x82")


def test_png_round_trips_through_a_real_decoder():
    """Verify against an independent decoder rather than trusting our own writer."""
    zlib_png = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    import io

    original = make_frames(n=1, h=16, w=24, seed=16)[0]
    decoded = np.array(zlib_png.open(io.BytesIO(encode_png(original))).convert("RGB"))
    assert np.array_equal(decoded, original)


# --------------------------------------------------------------------------
# GUI server logic (exercised without ffmpeg by injecting analysed clips)
# --------------------------------------------------------------------------

from colorgrader import server  # noqa: E402
from colorgrader.webui import INDEX_HTML  # noqa: E402


@pytest.fixture
def loaded_session():
    """Populate the server's in-memory session as if a folder had loaded."""
    stats = [
        analyse("neutral.mp4", make_frames(seed=20)),
        analyse("warm.mp4", make_frames(seed=20, gain=(1.3, 1.0, 0.7))),
        analyse("dark.mp4", make_frames(seed=20, bias=-0.12)),
    ]

    class _Info:  # stand-in for ffmpeg.ClipInfo; only .path/.stem are read
        def __init__(self, name):
            self.path = Path(name)
            self.stem = Path(name).stem

    server._SESSION.update(
        folder="/tmp/footage",
        infos=[_Info(s.name) for s in stats],
        stats=stats,
        frames=12,
    )
    yield stats
    server._SESSION.update(folder=None, infos=[], stats=[], frames=12)


def test_effective_strength_prefers_override():
    assert server._effective_strength(0, 0.85, {}) == 0.85
    assert server._effective_strength(2, 0.85, {"2": 0.4}) == 0.4     # JSON keys are strings
    assert server._effective_strength(2, 0.85, {2: 0.4}) == 0.4       # and ints work too
    assert server._effective_strength(1, 0.85, {"2": 0.4}) == 0.85    # unrelated override ignored
    assert server._effective_strength(2, 0.85, {"2": None}) == 0.85   # cleared override falls back


def test_thumb_uri_is_a_valid_png_data_uri():
    uri = server._thumb_uri(make_frames(n=1, h=8, w=8)[0])
    assert uri.startswith("data:image/png;base64,")
    import base64
    assert base64.b64decode(uri.split(",", 1)[1]).startswith(b"\x89PNG")


def test_preview_matches_and_reports_delta(loaded_session):
    result = server._preview("cdl", 1.0, reference=0, overrides={})
    assert result["reference"] == 0
    assert len(result["clips"]) == 3

    ref_clip = next(c for c in result["clips"] if c["i"] == 0)
    assert ref_clip["delta"] == {"before": 0.0, "after": 0.0}
    assert ref_clip["lgg"] is None

    for c in result["clips"]:
        assert c["after"].startswith("data:image/png;base64,")
        if c["i"] != 0:
            assert c["delta"]["after"] <= c["delta"]["before"]
            assert set(c["lgg"]) == {"lift", "gamma", "gain", "saturation"}


def test_preview_override_changes_a_single_clip(loaded_session):
    at_zero = server._preview("cdl", 1.0, 0, {"1": 0.0})
    warm = next(c for c in at_zero["clips"] if c["i"] == 1)
    # Strength 0 on clip 1 means no correction, so it stays at its original delta.
    assert warm["delta"]["after"] == warm["delta"]["before"]


def test_preview_clamps_out_of_range_reference(loaded_session):
    assert server._preview("cdl", 0.85, reference=99, overrides={})["reference"] == 2


def test_export_writes_all_deliverables(tmp_path, loaded_session):
    out = tmp_path / "out"
    result = server._export({
        "output": str(out), "method": "cdl", "strength": 0.85,
        "reference": 0, "overrides": {}, "render": False,
    })
    assert result["lut_count"] == 2 and result["rendered"] == 0
    assert (out / "report.html").exists()
    assert (out / "HOW_TO_USE.md").exists()
    assert (out / "apply_in_resolve.py").exists()
    assert (out / "match.json").exists()
    assert len(list((out / "luts").glob("*.cube"))) == 2
    assert len(list((out / "cdl").glob("*.cdl"))) == 2


def test_pick_folder_reports_when_no_picker_available():
    # This test host has no Tk / no display, so the real subprocess must fail
    # with a friendly message rather than hanging or raising something opaque.
    with pytest.raises(RuntimeError, match="type or paste"):
        server._pick_folder()


def test_pick_folder_returns_chosen_path(monkeypatch):
    class _Proc:
        returncode = 0
        stdout = "\n/Users/you/footage\n"

    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: _Proc())
    assert server._pick_folder("/start/here") == "/Users/you/footage"


def test_pick_folder_treats_cancel_as_empty(monkeypatch):
    class _Proc:
        returncode = 0
        stdout = "\n"  # dialog cancelled -> blank line only

    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: _Proc())
    assert server._pick_folder() == ""


def test_browse_endpoint_surfaces_picker_error():
    """The /api/browse route must turn a missing picker into a clean 400."""
    import io
    import json as _json

    class _FakeRequest(server._Handler):
        def __init__(self):  # bypass BaseHTTPRequestHandler's socket setup
            self.path = "/api/browse"
            self.headers = {"Content-Length": "2"}
            self.rfile = io.BytesIO(b"{}")
            self.wfile = io.BytesIO()
            self._status = None

        def send_response(self, code):
            self._status = code

        def send_header(self, *a):
            pass

        def end_headers(self):
            pass

    handler = _FakeRequest()
    handler.do_POST()
    assert handler._status == 400
    assert "folder path" in _json.loads(handler.wfile.getvalue())["error"]


from colorgrader import cli  # noqa: E402


def test_resolve_reference_matches_an_input_by_name_or_stem():
    stats = [analyse("neutral.mp4", make_frames(seed=1)),
             analyse("warm.mp4", make_frames(seed=2))]
    infos = [None, None]
    assert cli._resolve_reference(stats, infos, "warm.mp4", "ff", None, 12, True) == 1
    assert cli._resolve_reference(stats, infos, "warm", "ff", None, 12, True) == 1


def test_resolve_reference_prepends_an_external_hero(tmp_path, monkeypatch):
    stats = [analyse("a.mp4", make_frames(seed=3))]
    infos = ["a-info"]
    hero = tmp_path / "hero.mp4"
    hero.write_bytes(b"stub")  # just needs to exist; decode is monkeypatched
    monkeypatch.setattr(cli, "probe", lambda *a, **k: "hero-info")
    monkeypatch.setattr(cli, "grab_frames", lambda *a, **k: make_frames(seed=9))

    idx = cli._resolve_reference(stats, infos, str(hero), "ff", None, 12, True)
    assert idx == 0                     # external ref goes to the front
    assert stats[0].name == "hero.mp4"  # and everything else shifts down
    assert stats[1].name == "a.mp4"
    assert infos[0] == "hero-info"


def test_resolve_reference_rejects_unknown_name():
    stats = [analyse("a.mp4", make_frames(seed=4)),
             analyse("b.mp4", make_frames(seed=5))]
    with pytest.raises(SystemExit, match="neither"):
        cli._resolve_reference(stats, [None, None], "nope.mp4", "ff", None, 12, True)


def test_index_html_is_self_contained():
    # No external origins - the GUI must work offline on an editing machine.
    assert "<title>colorgrader</title>" in INDEX_HTML
    for marker in ("/api/load", "/api/preview", "/api/export"):
        assert marker in INDEX_HTML
    assert "http://" not in INDEX_HTML.replace("http://127.0.0.1", "")
    assert "src=\"http" not in INDEX_HTML and "cdn" not in INDEX_HTML.lower()
