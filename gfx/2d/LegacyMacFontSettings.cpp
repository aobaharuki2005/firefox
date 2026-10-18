/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "LegacyMacFont.h"
#include "mozilla/StaticPrefs_gfx.h"
#include "nsCocoaFeatures.h"

bool momiji_legacy_variations_enabled() {
  return mozilla::StaticPrefs::
             gfx_font_rendering_legacy_macos_variations_AtStartup() &&
         !nsCocoaFeatures::OnHighSierraOrLater();
}

bool momiji_legacy_outlines_enabled() {
  return mozilla::StaticPrefs::
             gfx_font_rendering_legacy_macos_outlines_AtStartup() &&
         !nsCocoaFeatures::OnYosemiteOrLater();
}
