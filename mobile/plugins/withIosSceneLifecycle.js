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
 *        returns a `UISceneConfiguration` with `delegateClass` explicitly
 *        set to `type(of: self)`.  UIKit instantiates a new object of that
 *        class as the scene delegate, so factory access in
 *        `scene(_:willConnectTo:options:)` MUST go through
 *        `UIApplication.shared.delegate as? AppDelegate` — NOT through
 *        `self.reactNativeFactory`, which is nil on the new instance.
 *     c. Add an `extension AppDelegate: UIWindowSceneDelegate` block with
 *        `scene(_:willConnectTo:options:)` that retrieves the factory via
 *        `UIApplication.shared.delegate as? AppDelegate`, creates
 *        `UIWindow(windowScene:)`, syncs the window back to the original
 *        AppDelegate instance via `appDelegate.window = win` (required for
 *        deep-link routing via `UIApplication.shared.delegate.window`),
 *        and calls `startReactNative`.
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
 * Retroactive fix (step 0.5) for files already patched by an older version of
 * this plugin (commits up to 88df50d) that returned a UISceneConfiguration
 * WITHOUT setting delegateClass, relying on an undocumented UIKit fallback.
 *
 * The correct behaviour is to set `config.delegateClass = type(of: self)`
 * explicitly.  Factory access in the scene callback then goes through
 * UIApplication.shared.delegate (see FACTORY_ACCESS_FIXUP / step 0.6).
 *
 * OLD = the nil-delegateClass one-liner from commits up to 88df50d
 * NEW = explicit delegateClass with diagnostic NSLog
 */
const DELEGATE_CLASS_FIXUP_OLD =
  '    return UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)';
const DELEGATE_CLASS_FIXUP_NEW =
  '    #if DEBUG\n' +
  '    NSLog("[dealfindio:scene] configurationForConnecting — selfType=%@",\n' +
  '          NSStringFromClass(type(of: self)))\n' +
  '    #endif\n' +
  '    let config = UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)\n' +
  '    config.delegateClass = type(of: self)\n' +
  '    return config';

/**
 * Retroactive fix (step 0.6) for files already patched by an older version of
 * this plugin that used `self.reactNativeFactory` directly in
 * `scene(_:willConnectTo:)`.
 *
 * When `config.delegateClass = type(of: self)` is set, UIKit instantiates a
 * NEW AppDelegate as the scene delegate.  That new instance has
 * reactNativeFactory = nil, so `self.reactNativeFactory` fails the guard
 * silently and React Native never starts.
 *
 * The fix retrieves the factory via `UIApplication.shared.delegate as?
 * AppDelegate`, which always returns the original, initialised instance, and
 * syncs the new UIWindow back to that instance for deep-link routing.
 *
 * OLD = the self.reactNativeFactory guard form from older plugin versions
 * NEW = UIApplication.shared.delegate + window sync + diagnostic NSLog
 */
const FACTORY_ACCESS_FIXUP_OLD =
  '    guard let windowScene = scene as? UIWindowScene,\n' +
  '          let factory = reactNativeFactory else { return }\n' +
  '    window = UIWindow(windowScene: windowScene)\n' +
  '    factory.startReactNative(withModuleName: "main", in: window, launchOptions: nil)';
const FACTORY_ACCESS_FIXUP_NEW =
  '    guard let windowScene = scene as? UIWindowScene,\n' +
  '          let appDelegate = UIApplication.shared.delegate as? AppDelegate,\n' +
  '          let factory = appDelegate.reactNativeFactory else { return }\n' +
  '    #if DEBUG\n' +
  '    NSLog("[dealfindio:scene] scene willConnectTo — selfType=%@ adFactory=set jsLoc=%@",\n' +
  '          NSStringFromClass(type(of: self)),\n' +
  '          UserDefaults.standard.string(forKey: "RCT_jsLocation") ?? "NOT_SET")\n' +
  '    #endif\n' +
  '    let win = UIWindow(windowScene: windowScene)\n' +
  '    window = win\n' +
  '    appDelegate.window = win\n' +
  '    factory.startReactNative(withModuleName: "main", in: win, launchOptions: nil)\n' +
  '    #if DEBUG\n' +
  '    NSLog("[dealfindio:scene] startReactNative done — rootVC=%@ isKey=%d isHidden=%d",\n' +
  '          String(describing: win.rootViewController),\n' +
  '          win.isKeyWindow ? 1 : 0, win.isHidden ? 1 : 0)\n' +
  '    #endif';

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
 * Scene-configuration callback inserted at the end of the AppDelegate class body.
 *
 * `delegateClass` is explicitly set to `type(of: self)` so UIKit creates a typed
 * scene-delegate object.  Factory access in scene(_:willConnectTo:) goes through
 * UIApplication.shared.delegate, not self.reactNativeFactory.
 */
const CONFIG_FOR_CONNECTING = [
  '',
  '  ' + PATCH_MARKER + ': required by iOS 26+ SDK.  Sets delegateClass = type(of: self)',
  '  ' + PATCH_MARKER + ': so UIKit creates a typed scene delegate.  Factory access uses',
  '  ' + PATCH_MARKER + ': UIApplication.shared.delegate in scene(_:willConnectTo:).',
  '  public func application(',
  '    _ application: UIApplication,',
  '    configurationForConnecting connectingSceneSession: UISceneSession,',
  '    options: UIScene.ConnectionOptions',
  '  ) -> UISceneConfiguration {',
  '    #if DEBUG',
  '    NSLog("[dealfindio:scene] configurationForConnecting — selfType=%@",',
  '          NSStringFromClass(type(of: self)))',
  '    #endif',
  '    let config = UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)',
  '    config.delegateClass = type(of: self)',
  '    return config',
  '  }',
].join('\n');

/**
 * UIWindowSceneDelegate extension that creates the app's main window once a
 * UIWindowScene is available.
 *
 * Factory is retrieved from the original AppDelegate via
 * UIApplication.shared.delegate (not self.reactNativeFactory, which is nil on
 * the new scene-delegate instance created by UIKit when delegateClass is set).
 * The new UIWindow is synced back to the original AppDelegate instance so that
 * RCTLinkingManager and Expo Router can find it via UIApplication.shared.delegate.window.
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
  '    guard let windowScene = scene as? UIWindowScene,',
  '          let appDelegate = UIApplication.shared.delegate as? AppDelegate,',
  '          let factory = appDelegate.reactNativeFactory else { return }',
  '    #if DEBUG',
  '    NSLog("[dealfindio:scene] scene willConnectTo — selfType=%@ adFactory=set jsLoc=%@",',
  '          NSStringFromClass(type(of: self)),',
  '          UserDefaults.standard.string(forKey: "RCT_jsLocation") ?? "NOT_SET")',
  '    #endif',
  '    let win = UIWindow(windowScene: windowScene)',
  '    window = win',
  '    appDelegate.window = win',
  '    factory.startReactNative(withModuleName: "main", in: win, launchOptions: nil)',
  '    #if DEBUG',
  '    NSLog("[dealfindio:scene] startReactNative done — rootVC=%@ isKey=%d isHidden=%d",',
  '          String(describing: win.rootViewController),',
  '          win.isKeyWindow ? 1 : 0, win.isHidden ? 1 : 0)',
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
  // Runs even when the file was already patched by an older version of this
  // plugin that pre-dates the access-control fix.  Safe to re-run.
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

  // ── Step 0.5 — retroactively add config.delegateClass = type(of: self) ───────
  //
  // Older plugin versions (commits up to 88df50d) returned UISceneConfiguration
  // without delegateClass, relying on an undocumented UIKit fallback.  This step
  // upgrades already-patched files to the explicit-delegateClass form without
  // requiring --clean.  Non-throwing: the old pattern may never be present on
  // clean builds from the current plugin.
  if (patched.includes(DELEGATE_CLASS_FIXUP_OLD)) {
    patched = patched.replace(DELEGATE_CLASS_FIXUP_OLD, DELEGATE_CLASS_FIXUP_NEW);
  }

  // ── Step 0.6 — retroactively fix factory access in scene(_:willConnectTo:) ──
  //
  // Older plugin versions accessed `self.reactNativeFactory` directly.  When
  // delegateClass is set, UIKit creates a NEW AppDelegate as scene delegate and
  // self.reactNativeFactory is nil on that instance → React Native never starts.
  // This step upgrades already-patched files to use UIApplication.shared.delegate.
  // Non-throwing: the old pattern may not be present on clean builds.
  if (patched.includes(FACTORY_ACCESS_FIXUP_OLD)) {
    patched = patched.replace(FACTORY_ACCESS_FIXUP_OLD, FACTORY_ACCESS_FIXUP_NEW);
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
module.exports.FACTORY_ACCESS_FIXUP_OLD = FACTORY_ACCESS_FIXUP_OLD;
module.exports.FACTORY_ACCESS_FIXUP_NEW = FACTORY_ACCESS_FIXUP_NEW;
module.exports.WINDOW_SETUP_OLD = WINDOW_SETUP_OLD;
module.exports.CLASS_TRANSITION = CLASS_TRANSITION;
