/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "LegacyMacFont.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <list>
#include <mutex>
#include <vector>

#include "hb.h"
#include "hb-ot.h"
#include "hb-subset.h"

namespace {

hb_blob_t* ReferenceTable(hb_face_t*, hb_tag_t aTag, void* aFont) {
  CFDataRef data = CGFontCopyTableForTag(static_cast<CGFontRef>(aFont), aTag);
  if (!data) {
    return hb_blob_get_empty();
  }
  auto length = CFDataGetLength(data);
  if (length > std::numeric_limits<unsigned>::max()) {
    CFRelease(data);
    return hb_blob_get_empty();
  }
  return hb_blob_create(reinterpret_cast<const char*>(CFDataGetBytePtr(data)),
                        length, HB_MEMORY_MODE_READONLY,
                        const_cast<void*>(static_cast<const void*>(data)),
                        [](void* aData) { CFRelease(aData); });
}

unsigned GetTableTags(const hb_face_t*, unsigned aStart, unsigned* aCount,
                      hb_tag_t* aTags, void* aFont) {
  CFArrayRef tags = CGFontCopyTableTags(static_cast<CGFontRef>(aFont));
  if (!tags) {
    if (aCount) {
      *aCount = 0;
    }
    return 0;
  }
  unsigned total = CFArrayGetCount(tags);
  if (aCount) {
    *aCount = std::min(*aCount, total - std::min(aStart, total));
    for (unsigned i = 0; i < *aCount; ++i) {
      aTags[i] = uintptr_t(CFArrayGetValueAtIndex(tags, aStart + i));
    }
  }
  CFRelease(tags);
  return total;
}

hb_face_t* GetFace(CGFontRef aFont) {
  static std::mutex mutex;
  static std::list<std::pair<CGFontRef, hb_face_t*>> faces;
  std::lock_guard<std::mutex> lock(mutex);
  auto found = std::find_if(
      faces.begin(), faces.end(),
      [aFont](const auto& aEntry) { return aEntry.first == aFont; });
  if (found != faces.end()) {
    faces.splice(faces.begin(), faces, found);
    return hb_face_reference(faces.front().second);
  }
  CGFontRetain(aFont);
  hb_face_t* face = hb_face_create_for_tables(
      ReferenceTable, aFont,
      [](void* aData) { CGFontRelease(static_cast<CGFontRef>(aData)); });
  hb_face_set_get_table_tags_func(face, GetTableTags, aFont, nullptr);
  hb_face_make_immutable(face);
  faces.emplace_front(aFont, face);
  if (faces.size() > 16) {
    hb_face_destroy(faces.back().second);
    faces.pop_back();
  }
  return hb_face_reference(face);
}

bool HasTable(hb_face_t* aFace, hb_tag_t aTag) {
  hb_blob_t* table = hb_face_reference_table(aFace, aTag);
  bool result = hb_blob_get_length(table) != 0;
  hb_blob_destroy(table);
  return result;
}

bool HasColor(hb_face_t* aFace) {
  return HasTable(aFace, HB_TAG('C', 'O', 'L', 'R')) ||
         HasTable(aFace, HB_TAG('s', 'b', 'i', 'x')) ||
         HasTable(aFace, HB_TAG('C', 'B', 'D', 'T')) ||
         HasTable(aFace, HB_TAG('S', 'V', 'G', ' '));
}

struct FontInstance {
  FontInstance(CGFontRef aSource, CGFontRef aFont,
               const std::vector<MomijiFontVariation>& aVariations)
      : source(CGFontRetain(aSource)),
        font(CGFontRetain(aFont)),
        variations(aVariations) {}
  ~FontInstance() {
    CGFontRelease(source);
    CGFontRelease(font);
  }
  CGFontRef source;
  CGFontRef font;
  std::vector<MomijiFontVariation> variations;
};

std::mutex sInstancesMutex;
std::list<FontInstance> sInstances;

bool Matches(const FontInstance& aInstance, CGFontRef aSource,
             const std::vector<MomijiFontVariation>& aVariations) {
  return aInstance.source == aSource &&
         aInstance.variations.size() == aVariations.size() &&
         std::equal(aVariations.begin(), aVariations.end(),
                    aInstance.variations.begin(),
                    [](const auto& a, const auto& b) {
                      return a.tag == b.tag && a.value == b.value;
                    });
}

hb_draw_funcs_t* GetDrawFuncs() {
  static hb_draw_funcs_t* funcs = [] {
    hb_draw_funcs_t* f = hb_draw_funcs_create();
    hb_draw_funcs_set_move_to_func(
        f,
        [](hb_draw_funcs_t*, void* aPath, hb_draw_state_t*, float x, float y,
           void*) {
          CGPathMoveToPoint(static_cast<CGMutablePathRef>(aPath), nullptr, x,
                            y);
        },
        nullptr, nullptr);
    hb_draw_funcs_set_line_to_func(
        f,
        [](hb_draw_funcs_t*, void* aPath, hb_draw_state_t*, float x, float y,
           void*) {
          CGPathAddLineToPoint(static_cast<CGMutablePathRef>(aPath), nullptr, x,
                               y);
        },
        nullptr, nullptr);
    hb_draw_funcs_set_quadratic_to_func(
        f,
        [](hb_draw_funcs_t*, void* aPath, hb_draw_state_t*, float cx, float cy,
           float x, float y, void*) {
          CGPathAddQuadCurveToPoint(static_cast<CGMutablePathRef>(aPath),
                                    nullptr, cx, cy, x, y);
        },
        nullptr, nullptr);
    hb_draw_funcs_set_cubic_to_func(
        f,
        [](hb_draw_funcs_t*, void* aPath, hb_draw_state_t*, float cx1,
           float cy1, float cx2, float cy2, float x, float y, void*) {
          CGPathAddCurveToPoint(static_cast<CGMutablePathRef>(aPath), nullptr,
                                cx1, cy1, cx2, cy2, x, y);
        },
        nullptr, nullptr);
    hb_draw_funcs_set_close_path_func(
        f,
        [](hb_draw_funcs_t*, void* aPath, hb_draw_state_t*, void*) {
          CGPathCloseSubpath(static_cast<CGMutablePathRef>(aPath));
        },
        nullptr, nullptr);
    hb_draw_funcs_make_immutable(f);
    return f;
  }();
  return funcs;
}

hb_font_t* GetOutlineFont(CTFontRef aFont) {
  CGFontRef cgFont = CTFontCopyGraphicsFont(aFont, nullptr);
  if (!cgFont) {
    return nullptr;
  }
  hb_face_t* face = GetFace(cgFont);
  CGFontRelease(cgFont);
  if (HasColor(face) || (!HasTable(face, HB_TAG('g', 'l', 'y', 'f')) &&
                         !HasTable(face, HB_TAG('C', 'F', 'F', ' ')))) {
    hb_face_destroy(face);
    return nullptr;
  }
  hb_font_t* font = hb_font_create(face);
  hb_face_destroy(face);
  hb_ot_font_set_funcs(font);
  unsigned upem = hb_face_get_upem(hb_font_get_face(font));
  hb_font_set_scale(font, upem, upem);
  return font;
}

}  // namespace

CGFontRef momiji_legacy_create_font(CGFontRef aFont,
                                    const MomijiFontVariation* aVariations,
                                    size_t aCount) {
  if (!aFont || (aCount && !aVariations)) {
    return nullptr;
  }
  for (size_t i = 0; i < aCount; ++i) {
    if (!std::isfinite(aVariations[i].value)) {
      return nullptr;
    }
  }
  hb_face_t* face = GetFace(aFont);
  unsigned axisCount = hb_ot_var_get_axis_count(face);
  if (!axisCount || HasColor(face)) {
    hb_face_destroy(face);
    return nullptr;
  }
  std::vector<hb_ot_var_axis_info_t> axes(axisCount);
  hb_ot_var_get_axis_infos(face, 0, &axisCount, axes.data());
  std::vector<MomijiFontVariation> locations;
  locations.reserve(axisCount);
  for (const auto& axis : axes) {
    float value = axis.default_value;
    for (size_t i = 0; i < aCount; ++i) {
      if (aVariations[i].tag == axis.tag) {
        value = aVariations[i].value;
      }
    }
    locations.push_back(
        {axis.tag, std::max(axis.min_value, std::min(value, axis.max_value))});
  }
  {
    std::lock_guard<std::mutex> lock(sInstancesMutex);
    auto found = std::find_if(sInstances.begin(), sInstances.end(),
                              [&](const auto& aInstance) {
                                return Matches(aInstance, aFont, locations);
                              });
    if (found != sInstances.end()) {
      sInstances.splice(sInstances.begin(), sInstances, found);
      hb_face_destroy(face);
      return CGFontRetain(sInstances.front().font);
    }
  }
  hb_subset_input_t* input = hb_subset_input_create_or_fail();
  if (!input) {
    hb_face_destroy(face);
    return nullptr;
  }
  hb_set_invert(hb_subset_input_glyph_set(input));
  hb_set_invert(hb_subset_input_unicode_set(input));
  hb_set_invert(hb_subset_input_set(input, HB_SUBSET_SETS_LAYOUT_FEATURE_TAG));
  hb_subset_input_set_flags(
      input, HB_SUBSET_FLAGS_RETAIN_GIDS | HB_SUBSET_FLAGS_NOTDEF_OUTLINE |
                 HB_SUBSET_FLAGS_PASSTHROUGH_UNRECOGNIZED |
                 HB_SUBSET_FLAGS_DOWNGRADE_CFF2);
  bool ok = true;
  for (const auto& location : locations) {
    ok &= hb_subset_input_pin_axis_location(input, face, location.tag,
                                            location.value);
  }
  hb_face_t* instance = ok ? hb_subset_or_fail(face, input) : nullptr;
  hb_subset_input_destroy(input);
  hb_face_destroy(face);
  if (!instance) {
    return nullptr;
  }
  hb_blob_t* blob = hb_face_reference_blob(instance);
  unsigned length = 0;
  const char* bytes = hb_blob_get_data(blob, &length);
  CFDataRef data =
      length ? CFDataCreate(kCFAllocatorDefault,
                            reinterpret_cast<const UInt8*>(bytes), length)
             : nullptr;
  CGDataProviderRef provider =
      data ? CGDataProviderCreateWithCFData(data) : nullptr;
  CGFontRef result =
      provider ? CGFontCreateWithDataProvider(provider) : nullptr;
  if (provider) {
    CGDataProviderRelease(provider);
  }
  if (data) {
    CFRelease(data);
  }
  hb_blob_destroy(blob);
  hb_face_destroy(instance);
  if (result) {
    std::lock_guard<std::mutex> lock(sInstancesMutex);
    sInstances.emplace_front(aFont, result, locations);
    if (sInstances.size() > 8) {
      sInstances.pop_back();
    }
  }
  return result;
}

CGPathRef momiji_legacy_create_glyph_path(CTFontRef aFont, CGGlyph aGlyph,
                                          const CGAffineTransform* aTransform) {
  if (!aFont) {
    return nullptr;
  }
  hb_font_t* font = GetOutlineFont(aFont);
  if (!font) {
    return nullptr;
  }
  CGMutablePathRef path = CGPathCreateMutable();
  CGPathRef result = nullptr;
  if (path && hb_font_draw_glyph_or_fail(font, aGlyph, GetDrawFuncs(), path)) {
    CGFloat scale =
        CTFontGetSize(aFont) / hb_face_get_upem(hb_font_get_face(font));
    CGAffineTransform matrix =
        CGAffineTransformScale(CTFontGetMatrix(aFont), scale, scale);
    if (aTransform) {
      matrix = CGAffineTransformConcat(matrix, *aTransform);
    }
    result = CGPathCreateCopyByTransformingPath(path, &matrix);
  }
  if (path) {
    CGPathRelease(path);
  }
  hb_font_destroy(font);
  return result;
}

bool momiji_legacy_draw_glyphs(CTFontRef aFont, const CGGlyph* aGlyphs,
                               const CGPoint* aPositions, size_t aCount,
                               CGContextRef aContext) {
  if (!aFont || !aContext || (aCount && (!aGlyphs || !aPositions))) {
    return false;
  }
  hb_font_t* font = GetOutlineFont(aFont);
  if (!font) {
    return false;
  }
  unsigned upem = hb_face_get_upem(hb_font_get_face(font));
  std::vector<CGMutablePathRef> paths;
  paths.reserve(aCount);
  bool ok = true;
  for (size_t i = 0; i < aCount; ++i) {
    CGMutablePathRef path = CGPathCreateMutable();
    paths.push_back(path);
    if (!path ||
        !hb_font_draw_glyph_or_fail(font, aGlyphs[i], GetDrawFuncs(), path)) {
      ok = false;
      break;
    }
  }
  if (ok) {
    CGFloat scale = CTFontGetSize(aFont) / upem;
    CGAffineTransform textMatrix = CGContextGetTextMatrix(aContext);
    CGAffineTransform fontMatrix = CTFontGetMatrix(aFont);
    for (size_t i = 0; i < aCount; ++i) {
      CGContextSaveGState(aContext);
      CGContextConcatCTM(aContext, textMatrix);
      CGContextTranslateCTM(aContext, aPositions[i].x, aPositions[i].y);
      CGContextConcatCTM(aContext, fontMatrix);
      CGContextScaleCTM(aContext, scale, scale);
      CGContextAddPath(aContext, paths[i]);
      CGContextFillPath(aContext);
      CGContextRestoreGState(aContext);
    }
  }
  for (auto path : paths) {
    if (path) {
      CGPathRelease(path);
    }
  }
  hb_font_destroy(font);
  return ok;
}
