# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import ctypes as c
import hashlib
import importlib.util
import io
import json
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.ttLib.sfnt import SFNTWriter
from fontTools.varLib.instancer import instantiateVariableFont


parser = argparse.ArgumentParser()
parser.add_argument("--fixtures", type=Path, required=True)
parser.add_argument("--library", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location(
    "native_platform", args.fixtures / "native_overlap_probe.py"
)
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)
lib = c.CDLL(str(args.library.resolve()))


class Variation(c.Structure):
    _fields_ = [("tag", c.c_uint32), ("value", c.c_float)]


create_instance = n.bind(
    lib, "momiji_legacy_create_font", n.P, n.P, c.POINTER(Variation), n.Z
)
draw_fixed = n.bind(
    lib, "momiji_legacy_draw_glyphs", c.c_bool, n.P,
    c.POINTER(c.c_uint16), c.POINTER(n.Point), n.Z, n.P
)
create_raw_path = n.bind(
    lib, "momiji_legacy_create_glyph_path", n.P, n.P, c.c_uint16, n.P
)
copy_tags = n.bind(n.CG, "CGFontCopyTableTags", n.P, n.P)
array_count = n.bind(n.CF, "CFArrayGetCount", c.c_long, n.P)
array_get = n.bind(n.CF, "CFArrayGetValueAtIndex", n.P, n.P, c.c_long)
copy_table = n.bind(n.CG, "CGFontCopyTableForTag", n.P, n.P, c.c_uint32)
data_length = n.bind(n.CF, "CFDataGetLength", c.c_long, n.P)
data_bytes = n.bind(n.CF, "CFDataGetBytePtr", n.P, n.P)


def cg_font(font):
    font.flavor = None
    stream = io.BytesIO()
    font.save(stream)
    raw = stream.getvalue()
    data = n.data_create(None, raw, len(raw))
    provider = n.provider_create(data)
    result = n.font_create(provider)
    n.release(provider)
    n.release(data)
    assert result
    return result


def export_font(font):
    tags = copy_tags(font)
    stream = io.BytesIO()
    writer = SFNTWriter(stream, array_count(tags), sfntVersion="\x00\x01\x00\x00")
    for i in range(array_count(tags)):
        tag = array_get(tags, i)
        data = copy_table(font, tag)
        writer[tag.to_bytes(4, "big").decode("latin1")] = c.string_at(
            data_bytes(data), data_length(data)
        )
        n.release(data)
    writer.close()
    n.release(tags)
    return stream.getvalue()


def render(font, fixed, name, positions=None, matrix=None):
    size = 128
    width = height = 224
    storage = (c.c_ubyte * (width * height * 4))()
    space = n.color_space()
    ctx = n.context_create(storage, width, height, 8, width * 4, space, 0x4001)
    n.set_smooth(ctx, False)
    n.set_aa(ctx, True)
    n.set_color(ctx, 1, 1, 1, 1)
    n.fill_rect(ctx, n.Rect(n.Point(0, 0), n.Point(width, height)))
    n.set_color(ctx, 0, 0, 0, 1)
    ct = n.ct_create(font, size, None, None)
    glyph = c.c_uint16()
    char = c.c_uint16(ord("络"))
    assert n.get_glyph(ct, c.byref(char), c.byref(glyph), 1)
    positions = positions or [(32, 48)]
    glyphs = (c.c_uint16 * len(positions))(*([glyph.value] * len(positions)))
    points = (n.Point * len(positions))(*(n.Point(*p) for p in positions))
    if matrix:
        class Matrix(c.Structure):
            _fields_ = [(k, n.D) for k in ["a", "b", "c", "d", "tx", "ty"]]
        n.bind(n.CG, "CGContextSetTextMatrix", None, n.P, Matrix)(ctx, Matrix(*matrix))
    if fixed == "path":
        path = create_raw_path(ct, glyph.value, None)
        assert path
        n.translate(ctx, positions[0][0], positions[0][1])
        n.add_path(ctx, path)
        n.fill_path(ctx)
        n.path_release(path)
    elif fixed:
        assert draw_fixed(ct, glyphs, points, len(positions), ctx)
    else:
        n.draw_glyph(ct, glyphs, points, len(positions), ctx)
    result = bytes(storage)
    n.png(args.out / f"{name}.png", width, height, result)
    n.context_release(ctx)
    n.release(space)
    n.release(ct)
    return result


source = TTFont(args.fixtures / "actual-browser-luo.woff2")
base = cg_font(source)
records = []
for weight in [200, 400, 500, 550, 700]:
    variation = Variation(int.from_bytes(b"wght", "big"), weight)
    instance = create_instance(base, c.byref(variation), 1)
    assert instance, weight
    raw = export_font(instance)
    (args.out / f"instance-{weight}.ttf").write_bytes(raw)
    actual = TTFont(io.BytesIO(raw))
    expected = instantiateVariableFont(source, {"wght": weight}, inplace=False)
    assert "fvar" not in actual
    assert actual["maxp"].numGlyphs == source["maxp"].numGlyphs
    max_error = 0
    for gid, name in enumerate(source.getGlyphOrder()):
        if source["glyf"][name].isComposite():
            continue
        got_name = actual.getGlyphOrder()[gid]
        exp_points = expected["glyf"][name].getCoordinates(expected["glyf"])[0]
        got_points = actual["glyf"][got_name].getCoordinates(actual["glyf"])[0]
        assert len(exp_points) == len(got_points), (weight, gid)
        for p, q in zip(exp_points, got_points):
            max_error = max(max_error, abs(p[0] - q[0]), abs(p[1] - q[1]))
        exp_advance = expected["hmtx"][name][0]
        got_advance = actual["hmtx"][got_name][0]
        assert abs(exp_advance - got_advance) <= 1, (weight, gid, exp_advance, got_advance)
    assert max_error <= 1, (weight, max_error)
    native = render(instance, False, f"weight-{weight}-native")
    repaired = render(instance, True, f"weight-{weight}-repaired")
    records.append({
        "weight": weight,
        "max_coordinate_error_font_units": max_error,
        "native_nonwhite_pixels": sum(native[i] < 255 for i in range(0, len(native), 4)),
        "repaired_nonwhite_pixels": sum(repaired[i] < 255 for i in range(0, len(repaired), 4)),
        "repaired_sha256": hashlib.sha256(repaired).hexdigest(),
    })
    assert records[-1]["native_nonwhite_pixels"] > 0
    n.release(instance)
assert len({r["repaired_sha256"] for r in records}) == len(records)

original = cg_font(TTFont(args.fixtures / "instanced-200.woff2"))
trimmed = cg_font(TTFont(args.fixtures / "seam-fixed-200.woff2"))
broken = render(original, False, "overlap-before")
fixed = render(original, True, "overlap-after")
control = render(trimmed, True, "overlap-control")
assert fixed == control
assert fixed != broken
assert render(original, "path", "overlap-raw-path") == fixed
for positions, matrix in [
    ([(32.25, 48.5)], [1, 0, 0, 1, 0, 0]),
    ([(32, 48), (70, 48)], [0.8, 0, 0.15, 0.8, 0, 0]),
]:
    a = render(original, True, "transform-original", positions, matrix)
    b = render(trimmed, True, "transform-control", positions, matrix)
    assert a == b
bad = Variation(int.from_bytes(b"wght", "big"), float("nan"))
assert not create_instance(base, c.byref(bad), 1)
assert not create_instance(base, None, 1)
assert not create_instance(original, None, 0)
for font in [base, original, trimmed]:
    n.release(font)
report = {
    "weights": records,
    "overlap_fixed_weight_200": {
        "matches_trimmed_control_byte_for_byte": True,
        "changed_pixels": sum(broken[i:i+4] != fixed[i:i+4] for i in range(0, len(fixed), 4)),
        "fractional_and_transformed_multiglyph_controls_passed": True,
    },
}
(args.out / "results.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
