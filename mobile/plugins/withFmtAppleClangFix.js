/**
 * Expo config plugin — fix fmt consteval incompatibility with Apple Clang 21+ (Xcode 27).
 *
 * WHY THIS EXISTS
 * ---------------
 * React Native bundles fmt 9.1.0 (via RCT-Folly).  In fmt 9.x the FMT_STRING()
 * macro expands to a `consteval` function call.  Apple Clang 21 (Xcode 27) tightened
 * its enforcement of the C++20 `consteval` standard in a way that rejects the
 * FMT_STRING macro with:
 *
 *   Pods/fmt/include/fmt/format-inl.h:59:24 error:
 *   'call to consteval function ... is not a constant expression'
 *
 * This is a known upstream issue:
 *   • fmtlib/fmt#4740
 *   • facebook/react-native#55601
 *   • expo/expo#44229
 *
 * FIX
 * ---
 * Set -DFMT_USE_CONSTEVAL=0 for the `fmt` pod only.  This tells fmt to use the
 * `constexpr` branch instead of `consteval`, which Apple Clang 21 accepts.
 *
 * The fix is NARROW: only the `fmt` pod target is touched; all other pods,
 * the global C++ standard, and the rest of the project are left unchanged.
 *
 * DURABILITY
 * ----------
 * withDangerousMod re-injects the post_install hook every time `expo prebuild`
 * regenerates the Podfile, including `--clean` runs.  No manual editing required.
 *
 * INJECTION STRATEGY
 * ------------------
 * CocoaPods rejects multiple post_install blocks.  We inject our Ruby snippet
 * into the EXISTING block using the same TRAILING_RE pattern as
 * withFixPodsDeploymentTarget.  Each plugin carries its own idempotency marker
 * so they compose safely regardless of execution order.
 */

const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

const PATCH_MARKER = '# [withFmtAppleClangFix]';

// Ruby snippet inserted inside the existing post_install block.
// Uses 4-space indent to match the surrounding block.
const RUBY_FIX = `
    ${PATCH_MARKER} disable consteval in fmt for Apple Clang 21+ / Xcode 27 compatibility.
    # fmt 9.x FMT_STRING() expands to a consteval call that Apple Clang 21 rejects.
    # Ref: fmtlib/fmt#4740, facebook/react-native#55601, expo/expo#44229
    # Targets only the fmt pod — no other pod or the C++ standard is changed.
    installer.pods_project.targets.each do |target|
      next unless target.name == 'fmt'
      fmt_root = installer.sandbox.root.to_s + '/fmt'
      header_candidates = [
        fmt_root + '/include/fmt/base.h',
        fmt_root + '/include/fmt/core.h',
      ]
      has_header = header_candidates.any? { |h| File.exist?(h) }
      unless has_header
        raise '[withFmtAppleClangFix] fmt pod present but base.h/core.h not found. ' \\
              'Upstream fmt layout may have changed — update this plugin. ' \\
              'Run: make ios-clean'
      end
      target.build_configurations.each do |config|
        flags = config.build_settings['OTHER_CPLUSPLUSFLAGS']
        next if flags.is_a?(String) && flags.include?('-DFMT_USE_CONSTEVAL=0')
        next if flags.is_a?(Array) && flags.any? { |f| f.include?('-DFMT_USE_CONSTEVAL=0') }
        existing = flags.nil? ? '$(inherited)' : (flags.is_a?(Array) ? flags.join(' ') : flags.to_s)
        config.build_settings['OTHER_CPLUSPLUSFLAGS'] = "#{existing} -DFMT_USE_CONSTEVAL=0"
      end
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
 * We match that trailing "\n  end\nend" pattern (same as withFixPodsDeploymentTarget)
 * and inject before it.  Since both plugins restore the captured trailing pattern via
 * $1, the end-of-file shape is preserved for any subsequent plugin in the chain.
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
