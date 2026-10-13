/**
 * Expo config plugin — UIScene lifecycle for iOS 26+ (Xcode 27) compatibility.
 *
 * WHY THIS IS NEEDED
 * ------------------
 * iOS 27 (built with Xcode 27 SDK) enforces `UIApplicationSceneManifest` in
 * Info.plist.  Without it the runtime rejects the app at launch with:
 *   "UIScene life cycle is required for apps built with this SDK."
 *
 * Expo SDK 54's `ExpoAppDelegate` has a TODO comment:
 *   // TODO: - Configuring and Discarding Scenes
 * Scene lifecycle is not yet implemented.  The generated `AppDelegate.swift`
 * creates `UIWindow(frame: UIScreen.main.bounds)` and calls `makeKeyAndVisible`
 * in `didFinishLaunchingWithOptions` — the pre-iOS-13 window-based pattern.
 *
 * THE FIX
 * -------
 * Two cooperating changes, both applied during `expo prebuild`:
 *
 *  1. Info.plist  — add `UIApplicationSceneManifest` with
 *     `UIApplicationSupportsMultipleScenes: false`.  This satisfies the
 *     iOS 27 presence check.
 *
 *  2. AppDelegate.swift patches (via withDangerousMod):
 *     a. Remove the `#if os(iOS)…#endif` window-creation block from
 *        `didFinishLaunchingWithOptions`.
 *     b. Add `application(_:configurationForConnecting:options:)` which
 *        designates AppDelegate itself as the scene delegate via
 *        `config.delegateClass = type(of: self)`.
 *     c. Add an `extension AppDelegate: UIWindowSceneDelegate` block with
 *        `scene(_:willConnectTo:options:)` that creates
 *        `UIWindow(windowScene:)` and calls `startReactNative`.
 *
 * The fix survives `expo prebuild --clean` because both changes are
 * re-applied by the plugin every time.
 */

const { withInfoPlist, withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

// ── Constants ──────────────────────────────────────────────────────────────────

const PATCH_MARKER = '// # [withIosSceneLifecycle]';

/**
 * Expo SDK 54 generates `var window` with internal (module) access.
 * `UIWindowSceneDelegate` is a `public` protocol, so the `window` property it
 * requires must also be `public`.  Without this, Swift emits:
 *   error: property 'window' must be declared public because it matches a
 *          requirement in public protocol 'UIWindowSceneDelegate'
 */
const WINDOW_DECL_OLD = '  var window: UIWindow?';
const WINDOW_DECL_NEW = '  public var window: UIWindow?';

/**
 * The exact window-creation block Expo SDK 54 generates inside
 * `didFinishLaunchingWithOptions`.  Matched including the leading blank line
 * so the replacement leaves a single clean comment line.
 */
const WINDOW_SETUP_OLD =
  '\n\n#if os(iOS) || os(tvOS)\n' +
  '    window = UIWindow(frame: UIScreen.main.bounds)\n' +
  '    factory.startReactNative(\n' +
  '      withModuleName: "main",\n' +
  '      in: window,\n' +
  '      launchOptions: launchOptions)\n' +
  '#endif';

/**
 * Replacement comment that documents why the block was removed.
 * The extra blank line mirrors the blank line we consumed from WINDOW_SETUP_OLD.
 */
const WINDOW_SETUP_NEW =
  '\n\n  ' + PATCH_MARKER + ': window creation deferred to UIWindowSceneDelegate.scene(_:willConnectTo:)';

/**
 * Scene-configuration callback to insert at the end of the AppDelegate class
 * body (before its closing `}`).  Designates AppDelegate as the UIWindowScene
 * delegate so iOS calls `scene(_:willConnectTo:options:)` on it.
 */
const CONFIG_FOR_CONNECTING = [
  '',
  '  ' + PATCH_MARKER + ': required by iOS 26+ SDK — returns a scene configuration pointing',
  '  ' + PATCH_MARKER + ': to AppDelegate itself as UIWindowSceneDelegate.',
  '  public func application(',
  '    _ application: UIApplication,',
  '    configurationForConnecting connectingSceneSession: UISceneSession,',
  '    options: UIScene.ConnectionOptions',
  '  ) -> UISceneConfiguration {',
  '    let config = UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)',
  '    config.delegateClass = type(of: self)',
  '    return config',
  '  }',
].join('\n');

/**
 * UIWindowSceneDelegate extension that creates the app's main window once a
 * UIWindowScene is available.  `reactNativeFactory` was set up earlier in
 * `didFinishLaunchingWithOptions`; we simply call through to it here.
 */
const SCENE_DELEGATE_EXTENSION = [
  PATCH_MARKER + ': UIWindowSceneDelegate wires the React Native window into',
  PATCH_MARKER + ': the iOS scene created by UIKit after app launch.',
  'extension AppDelegate: UIWindowSceneDelegate {',
  '  public func scene(',
  '    _ scene: UIScene,',
  '    willConnectTo session: UISceneSession,',
  '    options connectionOptions: UIScene.ConnectionOptions',
  '  ) {',
  '    guard let windowScene = scene as? UIWindowScene,',
  '          let factory = reactNativeFactory else { return }',
  '    window = UIWindow(windowScene: windowScene)',
  '    factory.startReactNative(withModuleName: "main", in: window, launchOptions: nil)',
  '  }',
  '}',
].join('\n');

/**
 * Unique boundary string between AppDelegate and ReactNativeDelegate in the
 * Expo SDK 54 generated file.  We insert scene methods here.
 */
const CLASS_TRANSITION = '\n}\n\nclass ReactNativeDelegate';

// ── Core patch function ────────────────────────────────────────────────────────

/**
 * Apply UIScene lifecycle patches to the contents of AppDelegate.swift.
 * Idempotent: returns `contents` unchanged if the patch marker is already
 * present.  Throws with a `[withIosSceneLifecycle]` prefix if the expected
 * source patterns are absent (guards against future template changes).
 *
 * @param {string} contents  Raw text of AppDelegate.swift
 * @returns {string}         Patched file contents
 */
function patchAppDelegate(contents) {
  // ── Step 0 — make window property public (independent of full-patch marker) ──
  //
  // This step runs even when the file was already patched by an older version of
  // this plugin that pre-dates the access-control fix.  It is safe to re-run:
  // if WINDOW_DECL_OLD is absent and WINDOW_DECL_NEW is present, the replace is
  // skipped; if neither is present the file has changed in an unexpected way.
  const hasInternalWindow = contents.includes(WINDOW_DECL_OLD);
  const hasPublicWindow = contents.includes(WINDOW_DECL_NEW);

  if (!hasInternalWindow && !hasPublicWindow) {
    throw new Error(
      '[withIosSceneLifecycle]: expected "var window: UIWindow?" declaration not found in ' +
        'AppDelegate.swift.  The Expo SDK 54 template may have changed — review withIosSceneLifecycle.js.'
    );
  }

  let patched = hasInternalWindow
    ? contents.replace(WINDOW_DECL_OLD, WINDOW_DECL_NEW)
    : contents;

  // ── Steps 1–2 — scene lifecycle insertion (gated by full-patch marker) ───────
  //
  // If the marker is already present the scene lifecycle blocks were injected by
  // a previous prebuild run.  Skip to avoid duplicating them.
  if (patched.includes(PATCH_MARKER)) return patched;

  if (!patched.includes(WINDOW_SETUP_OLD)) {
    throw new Error(
      '[withIosSceneLifecycle]: expected UIWindow creation block not found in AppDelegate.swift. ' +
        'The Expo SDK 54 template may have changed — review withIosSceneLifecycle.js.'
    );
  }

  if (!patched.includes(CLASS_TRANSITION)) {
    throw new Error(
      '[withIosSceneLifecycle]: expected class boundary (AppDelegate → ReactNativeDelegate) ' +
        'not found in AppDelegate.swift.'
    );
  }

  // 1. Remove the window-creation block from didFinishLaunchingWithOptions
  patched = patched.replace(WINDOW_SETUP_OLD, WINDOW_SETUP_NEW);

  // 2. Insert configurationForConnecting inside AppDelegate, then add the
  //    UIWindowSceneDelegate extension right after the class closes.
  patched = patched.replace(
    CLASS_TRANSITION,
    CONFIG_FOR_CONNECTING + '\n}\n\n' + SCENE_DELEGATE_EXTENSION + '\n\nclass ReactNativeDelegate'
  );

  return patched;
}

// ── Expo config-plugin functions ───────────────────────────────────────────────

function withSceneLifecycleInfoPlist(config) {
  return withInfoPlist(config, (cfg) => {
    if (!cfg.modResults.UIApplicationSceneManifest) {
      cfg.modResults.UIApplicationSceneManifest = {
        UIApplicationSupportsMultipleScenes: false,
      };
    }
    return cfg;
  });
}

function withSceneLifecycleAppDelegate(config) {
  return withDangerousMod(config, [
    'ios',
    async (cfg) => {
      const appDelegatePath = path.join(
        cfg.modRequest.platformProjectRoot,
        cfg.modRequest.projectName,
        'AppDelegate.swift'
      );

      if (!fs.existsSync(appDelegatePath)) {
        throw new Error(
          `[withIosSceneLifecycle]: AppDelegate.swift not found at ${appDelegatePath}`
        );
      }

      const original = fs.readFileSync(appDelegatePath, 'utf8');
      const patched = patchAppDelegate(original);

      if (patched !== original) {
        fs.writeFileSync(appDelegatePath, patched, 'utf8');
      }

      return cfg;
    },
  ]);
}

function withIosSceneLifecycle(config) {
  config = withSceneLifecycleInfoPlist(config);
  config = withSceneLifecycleAppDelegate(config);
  return config;
}

// ── Exports ────────────────────────────────────────────────────────────────────

module.exports = withIosSceneLifecycle;
module.exports.patchAppDelegate = patchAppDelegate;
module.exports.PATCH_MARKER = PATCH_MARKER;
module.exports.WINDOW_DECL_OLD = WINDOW_DECL_OLD;
module.exports.WINDOW_DECL_NEW = WINDOW_DECL_NEW;
module.exports.WINDOW_SETUP_OLD = WINDOW_SETUP_OLD;
module.exports.CLASS_TRANSITION = CLASS_TRANSITION;
