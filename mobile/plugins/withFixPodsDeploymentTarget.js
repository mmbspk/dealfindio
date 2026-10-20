/**
 * Expo config plugin — fix CocoaPods resource-bundle deployment targets.
 *
 * WHY THIS EXISTS
 * ---------------
 * Some podspecs (notably RNCAsyncStorage v2.x in non-Fabric / Old-Arch mode)
 * declare `s.platforms = { :ios => "9.0" }` for their resource-bundle targets.
 * CocoaPods writes that version directly to the resource-bundle target's
 * IPHONEOS_DEPLOYMENT_TARGET in the generated Pods.xcodeproj.  The global
 * `platform :ios, '15.1'` directive does NOT override it, and neither does
 * react_native_post_install (which skips _resources targets).
 *
 * Xcode 26+ enforces a hard minimum of 15.0 and exits with code 65 when it
 * finds any target below that range.
 *
 * FIX
 * ---
 * We inject a few lines of Ruby into the EXISTING post_install block, right
 * after the react_native_post_install() call.  CocoaPods rejects multiple
 * post_install blocks, so we must inject into the single existing one.
 *
 * DURABILITY
 * ----------
 * withDangerousMod re-applies the patch every time `expo prebuild` regenerates
 * the Podfile, including `--clean` runs.  No manual editing required.
 */

const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

const MIN_IOS = '15.1';
const PATCH_MARKER = '# [withFixPodsDeploymentTarget]';

// Ruby snippet inserted inside the existing post_install block, after
// react_native_post_install() runs.  Uses 4-space indent to match the block.
const RUBY_FIX = `
    ${PATCH_MARKER} raise resource-bundle targets to project minimum.
    # CocoaPods writes the podspec platform to resource-bundle targets and does
    # not apply the global platform directive.  Xcode 27 rejects < 15.0.
    installer.pods_project.targets.each do |target|
      target.build_configurations.each do |config|
        tv = config.build_settings['IPHONEOS_DEPLOYMENT_TARGET']
        next if tv.nil?
        config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '${MIN_IOS}' if tv.to_f < ${parseFloat(MIN_IOS)}
      end
    end`;

/**
 * Inserts RUBY_FIX before the closing `end` of the post_install block.
 *
 * Expo generates Podfiles that end with:
 *   ...
 *   post_install do |installer|
 *     react_native_post_install(...)
 *   end          ← 2-space indent, closes post_install
 * end            ← 0-space indent, closes target block
 *
 * We match that trailing "\n  end\nend" pattern and inject before it.
 * The pattern is unique in Expo-generated Podfiles.
 */
function injectIntoPodfile(contents) {
  if (contents.includes(PATCH_MARKER)) {
    return contents; // Idempotent — already patched.
  }

  // Match the end of post_install block + end of target block at EOF.
  // Allow optional trailing whitespace / newlines.
  const TRAILING_RE = /(\n  end\nend\s*)$/;
  if (!TRAILING_RE.test(contents)) {
    throw new Error(
      '[withFixPodsDeploymentTarget] Podfile does not end with the expected ' +
        '"  end\\nend" pattern.  The Expo Podfile template may have changed.  ' +
        'Please report this at https://github.com/mmbspk/dealfindio/issues.'
    );
  }

  return contents.replace(TRAILING_RE, RUBY_FIX + '$1');
}

/** @type {(config: import('@expo/config-plugins').ExpoConfig) => import('@expo/config-plugins').ExpoConfig} */
function withFixPodsDeploymentTarget(config) {
  return withDangerousMod(config, [
    'ios',
    (modConfig) => {
      const podfilePath = path.join(
        modConfig.modRequest.platformProjectRoot,
        'Podfile'
      );
      const contents = fs.readFileSync(podfilePath, 'utf8');
      fs.writeFileSync(podfilePath, injectIntoPodfile(contents));
      return modConfig;
    },
  ]);
}

module.exports = withFixPodsDeploymentTarget;
