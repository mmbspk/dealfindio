/**
 * Expo config plugin — fix fmt consteval incompatibility with Apple Clang 21+ (Xcode 27).
 *
 * WHY -DFMT_USE_CONSTEVAL=0 DID NOT WORK
 * ----------------------------------------
 * fmt/base.h detects consteval support via a plain #if/#elif chain with NO outer
 * #ifndef FMT_USE_CONSTEVAL guard:
 *
 *   #elif defined(__apple_build_version__) && __apple_build_version__ < 14000029L
 *     #define FMT_USE_CONSTEVAL 0   ← only fires for Apple Clang < Xcode 14
 *   ...
 *   #elif defined(__cpp_consteval)
 *     #define FMT_USE_CONSTEVAL 1   ← fires for Apple Clang 21 (build 21000334)
 *
 * Xcode 27 Apple Clang 21 has __apple_build_version__ = 21000334.
 * 21000334 < 14000029 is FALSE → the Apple exclusion is skipped.
 * __cpp_consteval IS defined → FMT_USE_CONSTEVAL is set to 1 unconditionally.
 * A -D compiler flag defined before the header is then silently overridden by
 * the #define at line 127 of base.h.
 *
 * THE FIX
 * -------
 * Patch fmt/base.h in the post_install hook to widen the Apple Clang exclusion
 * from "< Xcode 14" to "all Apple Clang":
 *
 *   BEFORE:
 *     #elif defined(__apple_build_version__) && __apple_build_version__ < 14000029L
 *   AFTER:
 *     #elif defined(__apple_build_version__)  // [withFmtAppleClangFix] ...
 *
 * This disables consteval for all Apple Clang versions — using constexpr instead
 * of consteval is safe: the compiler still evaluates at compile time, it is just
 * no longer forced to.
 *
 * The same root cause is documented in:
 *   fmtlib/fmt#4740, facebook/react-native#55601, expo/expo#44229
 *
 * DURABILITY
 * ----------
 * withDangerousMod re-injects the post_install hook every time `expo prebuild`
 * regenerates the Podfile (including --clean runs). pod install then re-applies
 * the patch to the freshly downloaded base.h. Idempotent: the marker comment
 * in base.h prevents double-patching.
 *
 * INJECTION STRATEGY
 * ------------------
 * CocoaPods rejects multiple post_install blocks. We inject into the EXISTING
 * block using the same TRAILING_RE pattern as withFixPodsDeploymentTarget.
 */

const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

const PATCH_MARKER = '# [withFmtAppleClangFix]';

// The exact string we are looking for in base.h.
// Verified against fmt 9.1.0 as shipped with RN 0.81.5.
const BASE_H_OLD_GUARD =
  '#elif defined(__apple_build_version__) && __apple_build_version__ < 14000029L';

// Ruby snippet injected into the existing post_install block (4-space indent).
// At pod-install time it patches fmt/base.h to remove the version threshold so
// the Apple Clang exclusion fires unconditionally.
const RUBY_FIX = `
    ${PATCH_MARKER} patch fmt/base.h — widen Apple Clang consteval exclusion to cover Clang 21+ (Xcode 27).
    # fmt 9.x enables consteval for Apple Clang >= __apple_build_version__ 14000029 (Xcode 14).
    # Xcode 27 Clang 21 (build 21000334) is above that threshold so consteval is enabled,
    # but Apple Clang 21 rejects FMT_STRING() in format-inl.h:59/60/1387 with:
    #   'call to consteval function is not a constant expression'
    # The guard has no #ifndef wrapper so -DFMT_USE_CONSTEVAL=0 is overridden by line 127.
    # Patch: remove the version threshold; #elif defined(__apple_build_version__) matches
    # all Apple Clang, disabling consteval regardless of Xcode version.
    # Ref: fmtlib/fmt#4740, facebook/react-native#55601, expo/expo#44229
    _fmt_base_h = installer.sandbox.root.to_s + '/fmt/include/fmt/base.h'
    if File.exist?(_fmt_base_h)
      _content = File.read(_fmt_base_h)
      unless _content.include?('${PATCH_MARKER}')
        _old = '#elif defined(__apple_build_version__) && __apple_build_version__ < 14000029L'
        _new = '#elif defined(__apple_build_version__) // ${PATCH_MARKER}: exclude all Apple Clang (Clang 21+/Xcode 27 re-broke consteval)'
        if _content.include?(_old)
          File.write(_fmt_base_h, _content.sub(_old, _new))
          puts '${PATCH_MARKER}: patched fmt/base.h — Apple Clang consteval exclusion now covers Clang 21+ (Xcode 27)'
        else
          raise '${PATCH_MARKER}: expected Apple Clang guard not found in fmt/base.h. fmt layout changed — update mobile/plugins/withFmtAppleClangFix.js and run: make ios-clean'
        end
      end
    else
      puts '${PATCH_MARKER}: fmt/base.h not found — fmt pod not installed (run: make ios-clean)'
    end`;

/**
 * Inserts RUBY_FIX before the closing `end` of the post_install block.
 *
 * Expo generates Podfiles that end with:
 *   post_install do |installer|
 *     react_native_post_install(...)
 *   end          ← 2-space indent, closes post_install
 * end            ← 0-space indent, closes target block
 *
 * Both this plugin and withFixPodsDeploymentTarget use the same TRAILING_RE.
 * Since each restores the captured trailing `$1`, the end-of-file shape is
 * preserved for any subsequent plugin in the chain.
 *
 * @param {string} contents  Raw Podfile text
 * @returns {string}         Patched Podfile text
 * @throws {Error}           If the Podfile does not have the expected structure
 */
function injectFmtFix(contents) {
  if (contents.includes(PATCH_MARKER)) {
    return contents; // Idempotent — already patched.
  }

  const TRAILING_RE = /(\n  end\nend\s*)$/;
  if (!TRAILING_RE.test(contents)) {
    throw new Error(
      '[withFmtAppleClangFix] Podfile does not end with the expected ' +
        '"  end\\nend" pattern.  The Expo Podfile template may have changed.  ' +
        'Please report this at https://github.com/mmbspk/dealfindio/issues.'
    );
  }

  return contents.replace(TRAILING_RE, RUBY_FIX + '$1');
}

/** @type {(config: import('@expo/config-plugins').ExpoConfig) => import('@expo/config-plugins').ExpoConfig} */
function withFmtAppleClangFix(config) {
  return withDangerousMod(config, [
    'ios',
    (modConfig) => {
      const podfilePath = path.join(
        modConfig.modRequest.platformProjectRoot,
        'Podfile'
      );
      const contents = fs.readFileSync(podfilePath, 'utf8');
      fs.writeFileSync(podfilePath, injectFmtFix(contents));
      return modConfig;
    },
  ]);
}

module.exports = withFmtAppleClangFix;
module.exports.injectFmtFix = injectFmtFix;
module.exports.PATCH_MARKER = PATCH_MARKER;
module.exports.BASE_H_OLD_GUARD = BASE_H_OLD_GUARD;
