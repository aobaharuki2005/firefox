/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef MOZILLA_GFX_LEGACYMACFONT_H_
#define MOZILLA_GFX_LEGACYMACFONT_H_

#include <ApplicationServices/ApplicationServices.h>
#include <CoreText/CoreText.h>
#include <stddef.h>
#include <stdint.h>

struct MomijiFontVariation {
  uint32_t tag;
  float value;
};

extern "C" {
bool momiji_legacy_variations_enabled();
bool momiji_legacy_outlines_enabled();
CGFontRef momiji_legacy_create_font(CGFontRef aFont,
                                    const MomijiFontVariation* aVariations,
                                    size_t aCount);
CGPathRef momiji_legacy_create_glyph_path(CTFontRef aFont, CGGlyph aGlyph,
                                          const CGAffineTransform* aTransform);
bool momiji_legacy_draw_glyphs(CTFontRef aFont, const CGGlyph* aGlyphs,
                               const CGPoint* aPositions, size_t aCount,
                               CGContextRef aContext);
}

#endif
