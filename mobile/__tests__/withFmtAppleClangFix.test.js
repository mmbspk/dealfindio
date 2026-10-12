'use strict';

/**
 * Regression tests for withFmtAppleClangFix.js
 *
 * Covers three scenarios:
 *   1. Patch applied once — RUBY_FIX injected at the right location
 *   2. Repeated run (idempotent) — applying twice produces identical output
 *   3. Unsupported Podfile layout — throws with a clear message
 */

const { injectFmtFix, PATCH_MARKER } = require('../plugins/withFmtAppleClangFix');

// Minimal Expo-generated Podfile that ends with the expected trailing pattern.
// The withFixPodsDeploymentTarget marker simulates a Podfile that already had
// that plugin applied, verifying both plugins compose correctly.
const PODFILE_BASE = `require File.join(File.dirname(\`node --print "require.resolve('expo/package.json')"\`), "scripts/autolinking")
require File.join(File.dirname(\`node --print "require.resolve('@react-native-community/cli-platform-ios/package.json')"\`), "native_modules/ios")

require 'json'
podfile_properties = JSON.parse(File.read(File.join(__dir__, 'Podfile.properties.json')))

ENV['RCT_NEW_ARCH_ENABLED'] = podfile_properties['newArchEnabled'] == 'true' ? '1' : '0'

platform :ios, podfile_properties['ios.deploymentTarget'] || '15.1'

install! 'cocoapods', :deterministic_uuids => false

target 'dealfindio' do
  use_expo_modules!
  config = use_native_modules!
  use_frameworks! :linkage => podfile_properties['ios.useFrameworks'].to_sym if podfile_properties['ios.useFrameworks']
  use_react_native!(
    :path => config[:reactNativePath],
    :hermes_enabled => podfile_properties['expo.jsEngine'] == 'hermes',
    :fabric_enabled => false,
  )
  post_install do |installer|
    react_native_post_install(
      installer,
      config[:reactNativePath],
      :mac_catalyst_enabled => false,
      :ccache_enabled => podfile_properties['apple.ccacheEnabled'] == 'true',
    )
    __apply_Xcode_12_5_M1_post_install_workaround(installer)
    # [withFixPodsDeploymentTarget] raise resource-bundle targets to project minimum.
    installer.pods_project.targets.each do |target|
      target.build_configurations.each do |config|
        tv = config.build_settings['IPHONEOS_DEPLOYMENT_TARGET']
        next if tv.nil?
        config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '15.1' if tv.to_f < 15.1
      end
    end
  end
end`;

// Variant without the deployment-target patch — tests the plugin in isolation.
const PODFILE_CLEAN = `require File.join(File.dirname(\`node --print "require.resolve('expo/package.json')"\`), "scripts/autolinking")
platform :ios, '15.1'
target 'dealfindio' do
  post_install do |installer|
    react_native_post_install(installer, config[:reactNativePath])
  end
end`;

describe('withFmtAppleClangFix — injectFmtFix', () => {
  // ── Test 1: patch applied once ────────────────────────────────────────────────
  it('injects the fmt consteval fix into an unpatched Podfile', () => {
    const result = injectFmtFix(PODFILE_BASE);

    // Marker present
    expect(result).toContain(PATCH_MARKER);

    // The flag is present in the injected Ruby
    expect(result).toContain('-DFMT_USE_CONSTEVAL=0');

    // Only the fmt pod is targeted
    expect(result).toContain("next unless target.name == 'fmt'");

    // File still ends correctly — post_install block is properly closed
    expect(result.trimEnd()).toMatch(/\n  end\nend$/);

    // The existing deployment-target patch is preserved
    expect(result).toContain('[withFixPodsDeploymentTarget]');
  });

  // ── Test 2: idempotent ────────────────────────────────────────────────────────
  it('does not apply the patch a second time when already present', () => {
    const once = injectFmtFix(PODFILE_BASE);
    const twice = injectFmtFix(once);

    // Output is byte-for-byte identical
    expect(twice).toBe(once);

    // The marker appears exactly once (not duplicated)
    const markerCount = (twice.split(PATCH_MARKER).length - 1);
    expect(markerCount).toBe(1);
  });

  // ── Test 3: unsupported Podfile layout ────────────────────────────────────────
  it('throws a clear error when the Podfile does not end with the expected pattern', () => {
    const brokenPodfile = `platform :ios, '15.1'\n# truncated — no post_install end`;

    expect(() => injectFmtFix(brokenPodfile)).toThrow('[withFmtAppleClangFix]');
    expect(() => injectFmtFix(brokenPodfile)).toThrow(/end.*end/i);
  });

  // ── Test 4: clean Podfile (no prior patches) ──────────────────────────────────
  it('works on a Podfile with no prior config-plugin patches', () => {
    const result = injectFmtFix(PODFILE_CLEAN);
    expect(result).toContain(PATCH_MARKER);
    expect(result).toContain('-DFMT_USE_CONSTEVAL=0');
    expect(result.trimEnd()).toMatch(/\n  end\nend$/);
  });

  // ── Test 5: deployment-target patch is preserved after fmt patch ──────────────
  it('preserves the withFixPodsDeploymentTarget content when composing both plugins', () => {
    const result = injectFmtFix(PODFILE_BASE);

    // Both markers present
    expect(result).toContain('[withFixPodsDeploymentTarget]');
    expect(result).toContain('[withFmtAppleClangFix]');

    // Both injected patches are inside the post_install block
    const postInstallContent = result.slice(
      result.indexOf('post_install do'),
      result.lastIndexOf('end')
    );
    expect(postInstallContent).toContain('IPHONEOS_DEPLOYMENT_TARGET');
    expect(postInstallContent).toContain('FMT_USE_CONSTEVAL');
  });
});
