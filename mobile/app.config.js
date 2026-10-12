/**
 * Dynamic Expo config.
 *
 * Overrides two things from app.json that can't be expressed in static JSON:
 *
 *   1. apiUrl — resolved from EXPO_PUBLIC_API_URL (set in .env.local) or
 *      defaulting to http://localhost:8010.  iOS Simulator reaches the Mac
 *      host at `localhost`; Android Emulator requires 10.0.2.2; physical
 *      devices need the Mac's LAN IP.  See docs/local-mobile-development.md.
 *
 *   2. plugins — adds expo-build-properties (explicit iOS deployment target)
 *      and withFixPodsDeploymentTarget (post_install hook that bumps resource-
 *      bundle targets ignored by the global platform directive).
 */

// @ts-check
const baseConfig = require('./app.json');

/** @type {import('@expo/config').ExpoConfig} */
const config = {
  ...baseConfig.expo,
  extra: {
    ...baseConfig.expo.extra,
    // EXPO_PUBLIC_API_URL may be set in mobile/.env.local (gitignored).
    // Fall back to localhost so iOS Simulator works out of the box.
    apiUrl:
      process.env.EXPO_PUBLIC_API_URL ||
      'http://localhost:8010',
  },
  plugins: [
    ...baseConfig.expo.plugins,
    // Declare the iOS deployment target explicitly so it shows up in
    // Podfile.properties.json alongside the generated platform directive.
    [
      'expo-build-properties',
      {
        ios: {
          deploymentTarget: '15.1',
        },
      },
    ],
    // Post-install hook that raises resource-bundle targets (which inherit
    // their deployment target from the podspec, not from `platform :ios`)
    // to the project minimum.  Required for Xcode 27 which rejects < 15.0.
    './plugins/withFixPodsDeploymentTarget',
    // Post-install hook that disables consteval in the fmt pod for Apple
    // Clang 21+ (Xcode 27) compatibility.  fmt 9.x FMT_STRING() uses consteval
    // in a way Apple Clang 21 rejects.  Ref: fmtlib/fmt#4740, RN#55601.
    './plugins/withFmtAppleClangFix',
  ],
};

module.exports = { expo: config };
