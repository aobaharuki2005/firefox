# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Compare native glyph drawing and path filling without rebuilding Momiji.

Install ../requirements.txt in a Python 3 environment before running.
Outputs go to artifacts/legacy-fonts-native under the current directory.
"""

import ctypes as c
import hashlib
import json
import struct
import zlib
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.pens.basePen import BasePen


ROOT = Path(__file__).resolve().parent
OUT = Path.cwd() / "artifacts" / "legacy-fonts-native"
CF = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
CG = c.CDLL(
    "/System/Library/Frameworks/ApplicationServices.framework/"
    "Frameworks/CoreGraphics.framework/CoreGraphics"
)
CT = c.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
P = c.c_void_p
D = c.c_double
I = c.c_int
Z = c.c_size_t


class Point(c.Structure):
    _fields_ = [("x", D), ("y", D)]


class Rect(c.Structure):
    _fields_ = [("origin", Point), ("size", Point)]


def bind(lib, name, result, *args):
    fn = getattr(lib, name)
    fn.restype = result
    fn.argtypes = args
    return fn


release = bind(CF, "CFRelease", None, P)
data_create = bind(CF, "CFDataCreate", P, P, P, c.c_long)
provider_create = bind(CG, "CGDataProviderCreateWithCFData", P, P)
font_create = bind(CG, "CGFontCreateWithDataProvider", P, P)
ct_create = bind(CT, "CTFontCreateWithGraphicsFont", P, P, D, P, P)
get_glyph = bind(
    CT, "CTFontGetGlyphsForCharacters", c.c_bool, P,
    c.POINTER(c.c_uint16), c.POINTER(c.c_uint16), c.c_long,
)
draw_glyph = bind(
    CT, "CTFontDrawGlyphs", None, P,
    c.POINTER(c.c_uint16), c.POINTER(Point), Z, P,
)
create_path = bind(CT, "CTFontCreatePathForGlyph", P, P, c.c_uint16, P)
color_space = bind(CG, "CGColorSpaceCreateDeviceRGB", P)
context_create = bind(CG, "CGBitmapContextCreate", P, P, Z, Z, Z, Z, P, c.c_uint32)
set_color = bind(CG, "CGContextSetRGBFillColor", None, P, D, D, D, D)
fill_rect = bind(CG, "CGContextFillRect", None, P, Rect)
translate = bind(CG, "CGContextTranslateCTM", None, P, D, D)
add_path = bind(CG, "CGContextAddPath", None, P, P)
fill_path = bind(CG, "CGContextFillPath", None, P)
eo_fill_path = bind(CG, "CGContextEOFillPath", None, P)
set_smooth = bind(CG, "CGContextSetShouldSmoothFonts", None, P, c.c_bool)
set_aa = bind(CG, "CGContextSetShouldAntialias", None, P, c.c_bool)
context_release = bind(CG, "CGContextRelease", None, P)
path_release = bind(CG, "CGPathRelease", None, P)
scale = bind(CG, "CGContextScaleCTM", None, P, D, D)
begin_path = bind(CG, "CGContextBeginPath", None, P)
move_to = bind(CG, "CGContextMoveToPoint", None, P, D, D)
line_to = bind(CG, "CGContextAddLineToPoint", None, P, D, D)
curve_to = bind(CG, "CGContextAddCurveToPoint", None, P, D, D, D, D, D, D)
quad_to = bind(CG, "CGContextAddQuadCurveToPoint", None, P, D, D, D, D)
close_path = bind(CG, "CGContextClosePath", None, P)
contains = bind(CG, "CGPathContainsPoint", c.c_bool, P, P, Point, c.c_bool)


class ContextPen(BasePen):
    def __init__(self, glyphs, ctx):
        super().__init__(glyphs)
        self.ctx = ctx

    def _moveTo(self, pt):
        move_to(self.ctx, *pt)

    def _lineTo(self, pt):
        line_to(self.ctx, *pt)

    def _curveToOne(self, a, b, d):
        curve_to(self.ctx, *a, *b, *d)

    def _qCurveToOne(self, a, b):
        quad_to(self.ctx, *a, *b)

    def _closePath(self):
        close_path(self.ctx)


def png(path, width, height, data):
    def chunk(tag, body):
        return (
            struct.pack(">I", len(body)) + tag + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )
    rows = b"".join(
        b"\0" + data[y * width * 4:(y + 1) * width * 4]
        for y in range(height)
    )
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def render(source, size, mode):
    font = TTFont(ROOT / source)
    assert "fvar" not in font, "Hold the weight constant for this experiment"
    font.flavor = None
    ttf_path = OUT / (Path(source).stem + ".ttf")
    font.save(ttf_path)
    raw = ttf_path.read_bytes()
    data = data_create(None, raw, len(raw))
    provider = provider_create(data)
    cg_font = font_create(provider)
    assert cg_font, source
    ct_font = ct_create(cg_font, size, None, None)
    char = c.c_uint16(ord("络"))
    glyph = c.c_uint16()
    assert get_glyph(ct_font, c.byref(char), c.byref(glyph), 1), source
    width = height = size + 96
    storage = (c.c_ubyte * (width * height * 4))()
    space = color_space()
    ctx = context_create(storage, width, height, 8, width * 4, space, 0x4001)
    assert ctx
    set_aa(ctx, True)
    set_smooth(ctx, False)
    set_color(ctx, 1, 1, 1, 1)
    fill_rect(ctx, Rect(Point(0, 0), Point(width, height)))
    set_color(ctx, 0, 0, 0, 1)
    position = Point(32, 48)
    extracted_path_contains = None
    if mode == "glyph":
        draw_glyph(ct_font, c.byref(glyph), c.byref(position), 1, ctx)
    elif mode.startswith("raw-"):
        translate(ctx, position.x, position.y)
        factor = size / font["head"].unitsPerEm
        scale(ctx, factor, factor)
        begin_path(ctx)
        glyphs = font.getGlyphSet()
        glyphs[font.getBestCmap()[ord("络")]].draw(ContextPen(glyphs, ctx))
        (fill_path if mode == "raw-nonzero" else eo_fill_path)(ctx)
    else:
        path = create_path(ct_font, glyph, None)
        assert path
        extracted_path_contains = {
            str((x, y)): contains(
                path, None,
                Point(x * size / font["head"].unitsPerEm,
                      y * size / font["head"].unitsPerEm),
                False,
            )
            for x, y in [(495, 10), (805, 10), (650, 10), (650, 100)]
        }
        translate(ctx, position.x, position.y)
        add_path(ctx, path)
        (fill_path if mode == "nonzero" else eo_fill_path)(ctx)
        path_release(path)
    result = bytes(storage)
    # Raw bitmap rows correspond to the top of the image. Sample coordinates
    # are converted from the font's y-up coordinate system.
    samples = {}
    for name, x, y in [
        ("left_overlap", 495, 10),
        ("right_overlap", 805, 10),
        ("bottom_center", 650, 10),
        ("inside_mouth", 650, 100),
    ]:
        px = int(position.x + x * size / font["head"].unitsPerEm)
        py = height - 1 - int(position.y + y * size / font["head"].unitsPerEm)
        samples[name] = result[(py * width + px) * 4]
    name = f"{Path(source).stem}-{size}-{mode}"
    png(OUT / (name + ".png"), width, height, result)
    for obj in [ct_font, cg_font, provider, data, space]:
        release(obj)
    context_release(ctx)
    return result, {
        "image": name + ".png", "glyph_id": glyph.value, "size": size,
        "sha256_rgba": hashlib.sha256(result).hexdigest(),
        "sample_red_0_black_255_white": samples,
        "ct_extracted_path_contains_nonzero": extracted_path_contains,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    rasters = {}
    sources = [
        "instanced-200.woff2", "seam-fixed-200.woff2",
        "google-static-200.woff2",
    ]
    for source in sources:
        for size in [128, 512]:
            for mode in ["glyph", "nonzero", "evenodd", "raw-nonzero", "raw-evenodd"]:
                raw, result = render(source, size, mode)
                results.append(result)
                rasters[(source, size, mode)] = raw
    comparisons = []
    for size in [128, 512]:
        for mode in ["glyph", "nonzero", "evenodd", "raw-nonzero", "raw-evenodd"]:
            a = rasters[(sources[0], size, mode)]
            b = rasters[(sources[1], size, mode)]
            count = sum(a[i:i + 4] != b[i:i + 4] for i in range(0, len(a), 4))
            comparisons.append({
                "size": size, "mode": mode,
                "changed_pixels_original_vs_trimmed": count,
            })
    report = {
        "environment": "OS X 10.9.5; native CoreText/CoreGraphics; no Momiji rebuild",
        "conditions": "fixed weight 200; font smoothing off; antialiasing on",
        "results": results, "comparisons": comparisons,
    }
    (OUT / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
