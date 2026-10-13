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
 *        returns a `UISceneConfiguration` WITHOUT setting `delegateClass`.
 *        When `delegateClass` is nil and no `UISceneDelegateClassName` is
 *        present in Info.plist, UIKit falls back to the existing app-delegate
 *        object (documented Apple behaviour), so the same AppDelegate instance
 *        that set up `reactNativeFactory` in `didFinishLaunchingWithOptions`
 *        will receive `scene(_:willConnectTo:options:)`.
 *        WARNING: setting `config.delegateClass = type(of: self)` would cause
 *        UIKit to create a NEW AppDelegate instance as the scene delegate.
 *        That new instance has `reactNativeFactory = nil`, so the guard in
 *        `scene(_:willConnectTo:options:)` silently returns and React Native
 *        never starts.
 *     c. Add an `extension AppDelegate: UIWindowSceneDelegate` block with
 *        `scene(_:willConnectTo:options:)` that creates
 *        `UIWindow(windowScene:)` and calls `startReactNative`.
 *        `RCTReactNativeFactory.startReactNativeWithModuleName:inWindow:`
 *        calls `[window makeKeyAndVisible]` itself — no additional call needed.
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
 * Retroactive fix for files already patched by an older version of this plugin
 * that incorrectly set `config.delegateClass = type(of: self)`.
 *
 * Setting delegateClass causes UIKit to instantiate a NEW AppDelegate as the
 * scene delegate.  That new instance has reactNativeFactory = nil, so the
 * guard in scene(_:willConnectTo:) silently returns and React Native never
 * starts → black screen.
 *
 * The correct behaviour is to return the UISceneConfiguration with no
 * delegateClass set.  UIKit then uses the existing app-delegate object
 * (documented Apple fallback) which already holds reactNativeFactory.
 */
const DELEGATE_CLASS_FIXUP_OLD =
  '    let config = UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)\n' +
  '    config.delegateClass = type(of: self)\n' +
  '    return config';
const DELEGATE_CLASS_FIXUP_NEW =
  '    return UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)';

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
 * body (before its closing `}`).
 *
 * IMPORTANT: `delegateClass` is intentionally NOT set.  When delegateClass is
 * nil and no UISceneDelegateClassName is in Info.plist, UIKit uses the
 * existing app-delegate object as scene delegate (documented Apple fallback).
 * That object already holds reactNativeFactory from didFinishLaunchingWithOptions.
 * Setting delegateClass would create a fresh AppDelegate with nil factory.
 */
const CONFIG_FOR_CONNECTING = [
  '',
  '  ' + PATCH_MARKER + ': required by iOS 26+ SDK.  Returns configuration without',
  '  ' + PATCH_MARKER + ': delegateClass so UIKit reuses this AppDelegate instance',
  '  ' + PATCH_MARKER + ': (which holds reactNativeFactory) as UIWindowSceneDelegate.',
  '  public func application(',
  '    _ application: UIApplication,',
  '    configurationForConnecting connectingSceneSession: UISceneSession,',
  '    options: UIScene.ConnectionOptions',
  '  ) -> UISceneConfiguration {',
  '    #if DEBUG',
  '    NSLog("[dealfindio:scene] configurationForConnecting — self=%p factory=%@",',
  '          unsafeBitCast(self, to: UInt.self), reactNativeFactory != nil ? "set" : "NIL")',
  '    #endif',
  '    return UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)',
  '  }',
].join('\n');

/**
 * UIWindowSceneDelegate extension that creates the app's main window once a
 * UIWindowScene is available.  `reactNativeFactory` was set up earlier in
 * `didFinishLaunchingWithOptions`; we simply call through to it here.
 *
 * RCTReactNativeFactory.startReactNativeWithModuleName:inWindow:launchOptions:
 * (RCTReactNativeFactory.mm:83-95) unconditionally calls [window makeKeyAndVisible].
 * No additional makeKeyAndVisible call is needed from our code.
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
  '    #if DEBUG',
  '    NSLog("[dealfindio:scene] scene willConnectTo — self=%p factory=%@",',
  '          unsafeBitCast(self, to: UInt.self), reactNativeFactory != nil ? "set" : "NIL")',
  '    #endif',
  '    guard let windowScene = scene as? UIWindowScene,',
  '          let factory = reactNativeFactory else { return }',
  '    window = UIWindow(windowScene: windowScene)',
  '    factory.startReactNative(withModuleName: "main", in: window, launchOptions: nil)',
  '    #if DEBUG',
  '    NSLog("[dealfindio:scene] startReactNative done — rootVC=%@ isKey=%d isHidden=%d",',
  '          String(describing: window?.rootViewController),',
  '          window?.isKeyWindow == true ? 1 : 0, window?.isHidden == true ? 1 : 0)',
  '    #endif',
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

  // ── Step 0.5 — retroactively remove config.delegateClass = type(of: self) ────
  //
  // Older versions of this plugin (before the scene-delegate instance fix)
  // generated `config.delegateClass = type(of: self)` in the
  // configurationForConnecting method.  That line causes UIKit to create a
  // NEW AppDelegate as the scene delegate — which has reactNativeFactory = nil,
  // so scene(_:willConnectTo:) silently returns and React Native never starts.
  //
  // This step runs unconditionally before the PATCH_MARKER check so it fixes
  // files that were already patched by the old plugin, without needing --clean.
  if (patched.includes(DELEGATE_CLASS_FIXUP_OLD)) {
    patched = patched.replace(DELEGATE_CLASS_FIXUP_OLD, DELEGATE_CLASS_FIXUP_NEW);
  }

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
module.exports.DELEGATE_CLASS_FIXUP_OLD = DELEGATE_CLASS_FIXUP_OLD;
module.exports.DELEGATE_CLASS_FIXUP_NEW = DELEGATE_CLASS_FIXUP_NEW;
module.exports.WINDOW_SETUP_OLD = WINDOW_SETUP_OLD;
module.exports.CLASS_TRANSITION = CLASS_TRANSITION;
