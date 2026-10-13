'use strict';

/**
 * Regression tests for withIosSceneLifecycle.js
 *
 * Covers:
 *   1.  Patch applied once — window block removed, scene methods injected
 *   2.  window property promoted to public (UIWindowSceneDelegate requirement)
 *   3.  configurationForConnecting SETS delegateClass = type(of: self);
 *       factory access goes through UIApplication.shared.delegate
 *   4.  Already-patched file (nil-delegateClass) retroactively gets explicit
 *       delegateClass (step 0.5)
 *   5.  Already-patched file (self.reactNativeFactory guard) retroactively gets
 *       UIApplication.shared.delegate factory access (step 0.6)
 *   6.  Idempotent — second call returns identical string
 *   7.  Throws when window declaration absent from AppDelegate.swift
 *   8.  Throws when WINDOW_SETUP_OLD pattern is absent
 *   9.  Throws when class-boundary pattern is absent
 *   10. configurationForConnecting is inside AppDelegate class body
 *   11. UIWindowSceneDelegate extension is after the AppDelegate class
 *   12. Patched file still contains factory setup lines (not over-removed)
 *   13. Exported constants are stable
 */

const {
  patchAppDelegate,
  PATCH_MARKER,
  WINDOW_DECL_OLD,
  WINDOW_DECL_NEW,
  DELEGATE_CLASS_FIXUP_OLD,
  DELEGATE_CLASS_FIXUP_NEW,
  FACTORY_ACCESS_FIXUP_OLD,
  FACTORY_ACCESS_FIXUP_NEW,
  WINDOW_SETUP_OLD,
  CLASS_TRANSITION,
} = require('../plugins/withIosSceneLifecycle');

// Minimal reproduction of the Expo SDK 54 generated AppDelegate.swift.
// Contains the exact indentation and structure that patchAppDelegate expects.
const APP_DELEGATE_BASE = `import Expo
import React
import ReactAppDependencyProvider

@UIApplicationMain
public class AppDelegate: ExpoAppDelegate {
  var window: UIWindow?

  var reactNativeDelegate: ExpoReactNativeFactoryDelegate?
  var reactNativeFactory: RCTReactNativeFactory?

  public override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
  ) -> Bool {
    let delegate = ReactNativeDelegate()
    let factory = ExpoReactNativeFactory(delegate: delegate)
    delegate.dependencyProvider = RCTAppDependencyProvider()

    reactNativeDelegate = delegate
    reactNativeFactory = factory
    bindReactNativeFactory(factory)

#if os(iOS) || os(tvOS)
    window = UIWindow(frame: UIScreen.main.bounds)
    factory.startReactNative(
      withModuleName: "main",
      in: window,
      launchOptions: launchOptions)
#endif

    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  // Linking API
  public override func application(
    _ app: UIApplication,
    open url: URL,
    options: [UIApplication.OpenURLOptionsKey: Any] = [:]
  ) -> Bool {
    return super.application(app, open: url, options: options) || RCTLinkingManager.application(app, open: url, options: options)
  }
}

class ReactNativeDelegate: ExpoReactNativeFactoryDelegate {
  override func bundleURL() -> URL? {
    return Bundle.main.url(forResource: "main", withExtension: "jsbundle")
  }
}
`;

// ── Test 1: patch applied once ─────────────────────────────────────────────────
describe('withIosSceneLifecycle — patchAppDelegate', () => {
  it('removes the UIWindow creation block from didFinishLaunchingWithOptions', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    // Original window creation is gone
    expect(result).not.toContain('UIWindow(frame: UIScreen.main.bounds)');
    expect(result).not.toContain('factory.startReactNative(\n      withModuleName: "main",');
    expect(result).not.toContain('#if os(iOS) || os(tvOS)');
    // Note: #endif remains in the file because of the #if DEBUG diagnostic blocks

    // Patch marker is present
    expect(result).toContain(PATCH_MARKER);

    // Comment replacing the removed block is present
    expect(result).toContain('window creation deferred to UIWindowSceneDelegate');
  });

  it('adds configurationForConnecting that explicitly sets delegateClass = type(of: self)', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    expect(result).toContain('configurationForConnecting connectingSceneSession: UISceneSession');
    expect(result).toContain('UISceneConfiguration(name: "Default Configuration"');
    // Must set delegateClass explicitly so UIKit uses a typed scene delegate
    expect(result).toContain('config.delegateClass = type(of: self)');
    // Must use the multi-line let config / return config form, not a one-liner return
    expect(result).not.toContain('return UISceneConfiguration(name: "Default Configuration"');
  });

  it('adds a UIWindowSceneDelegate extension that accesses the factory via UIApplication.shared.delegate', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    expect(result).toContain('extension AppDelegate: UIWindowSceneDelegate');
    expect(result).toContain('willConnectTo session: UISceneSession');
    expect(result).toContain('UIWindow(windowScene: windowScene)');
    // Factory must come from the original AppDelegate, not from self (which is nil on new instance)
    expect(result).toContain('UIApplication.shared.delegate as? AppDelegate');
    expect(result).toContain('appDelegate.reactNativeFactory');
    // Window must be synced back to original AppDelegate for deep-link routing
    expect(result).toContain('appDelegate.window = win');
    expect(result).toContain('factory.startReactNative(withModuleName: "main", in: win, launchOptions: nil)');
  });

  // ── Test 2: window access control ────────────────────────────────────────────
  it('promotes window to public var to satisfy UIWindowSceneDelegate conformance', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    // public declaration must be present
    expect(result).toContain('  public var window: UIWindow?');

    // internal (non-public) declaration must be gone from the class body
    // Slice before the extension to avoid matching "window =" assignments
    const beforeExtension = result.slice(0, result.indexOf('extension AppDelegate:'));
    expect(beforeExtension).not.toContain('\n  var window: UIWindow?');
  });

  it('accepts a file that already has public var window (no double-patching)', () => {
    const alreadyPublic = APP_DELEGATE_BASE.replace(WINDOW_DECL_OLD, WINDOW_DECL_NEW);
    // Should not throw — the plugin treats an already-public declaration as OK
    const result = patchAppDelegate(alreadyPublic);
    expect(result).toContain('  public var window: UIWindow?');
  });

  // ── Test 3: configurationForConnecting MUST set delegateClass ─────────────────
  it('configurationForConnecting sets delegateClass = type(of: self)', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    const configMethod = result.slice(
      result.indexOf('configurationForConnecting'),
      result.indexOf('}\n', result.indexOf('configurationForConnecting')) + 2
    );
    // delegateClass must be present — UIKit uses it to create a typed scene delegate
    expect(configMethod).toContain('config.delegateClass = type(of: self)');
    // One-liner return form must NOT be present (use let config / return config)
    expect(configMethod).not.toContain('return UISceneConfiguration(');
  });

  // ── Test 4: step 0.5 retroactively adds delegateClass to already-patched files ─
  it('retroactively adds config.delegateClass to a file already patched by old plugin (step 0.5)', () => {
    // Simulate a file patched by the old plugin version (commits up to 88df50d)
    // that returned UISceneConfiguration without setting delegateClass.
    const phase4Patched = patchAppDelegate(APP_DELEGATE_BASE);
    const oldPatchedFile = phase4Patched.replace(DELEGATE_CLASS_FIXUP_NEW, DELEGATE_CLASS_FIXUP_OLD);

    // Sanity checks: old file lacks delegateClass, uses nil-delegateClass one-liner
    expect(oldPatchedFile).not.toContain('config.delegateClass = type(of: self)');
    expect(oldPatchedFile).toContain('return UISceneConfiguration(name: "Default Configuration"');
    // It already has PATCH_MARKER (patched by old plugin)
    expect(oldPatchedFile).toContain(PATCH_MARKER);

    // Running the current plugin must add delegateClass even though PATCH_MARKER
    // is present (step 0.5 runs before the idempotency guard).
    const fixed = patchAppDelegate(oldPatchedFile);
    expect(fixed).toContain('config.delegateClass = type(of: self)');
    expect(fixed).not.toContain(DELEGATE_CLASS_FIXUP_OLD);
  });

  // ── Test 5: step 0.6 retroactively fixes factory access ─────────────────────
  it('retroactively fixes factory access from self.reactNativeFactory to UIApplication.shared.delegate (step 0.6)', () => {
    // Simulate a file patched by an older plugin that used self.reactNativeFactory.
    // When delegateClass is set, UIKit creates a new AppDelegate as scene delegate;
    // self.reactNativeFactory is nil on that instance → React Native never starts.
    const phase4Patched = patchAppDelegate(APP_DELEGATE_BASE);
    const oldFactoryFile = phase4Patched.replace(FACTORY_ACCESS_FIXUP_NEW, FACTORY_ACCESS_FIXUP_OLD);

    // Sanity checks: old pattern present, new pattern absent
    expect(oldFactoryFile).toContain(PATCH_MARKER);
    expect(oldFactoryFile).toContain(FACTORY_ACCESS_FIXUP_OLD);
    expect(oldFactoryFile).not.toContain(FACTORY_ACCESS_FIXUP_NEW);

    // Running the current plugin must fix the factory access even though
    // PATCH_MARKER is present (step 0.6 runs before the idempotency guard).
    const fixed = patchAppDelegate(oldFactoryFile);
    expect(fixed).toContain('UIApplication.shared.delegate as? AppDelegate');
    expect(fixed).toContain('appDelegate.reactNativeFactory');
    expect(fixed).toContain('appDelegate.window = win');
    expect(fixed).not.toContain('let factory = reactNativeFactory else { return }');
  });

  // ── Test 6: idempotent ──────────────────────────────────────────────────────
  it('is idempotent — second call returns byte-for-byte identical output', () => {
    const once = patchAppDelegate(APP_DELEGATE_BASE);
    const twice = patchAppDelegate(once);

    expect(twice).toBe(once);
  });

  // ── Test: throws when window declaration absent ──────────────────────────────
  it('throws with [withIosSceneLifecycle] prefix when window declaration is absent', () => {
    // Remove the window property entirely (neither internal nor public)
    const noWindowDecl = APP_DELEGATE_BASE
      .replace('  var window: UIWindow?\n\n', '');

    expect(() => patchAppDelegate(noWindowDecl)).toThrow('[withIosSceneLifecycle]');
    expect(() => patchAppDelegate(noWindowDecl)).toThrow('window');
  });

  // ── Test: throws when window block absent ──────────────────────────────────
  it('throws with [withIosSceneLifecycle] prefix when UIWindow block is missing', () => {
    const noWindowBlock = APP_DELEGATE_BASE.replace(WINDOW_SETUP_OLD, '// already removed');

    expect(() => patchAppDelegate(noWindowBlock)).toThrow('[withIosSceneLifecycle]');
    expect(() => patchAppDelegate(noWindowBlock)).toThrow('UIWindow creation block not found');
  });

  // ── Test: throws when class boundary absent ──────────────────────────────────
  it('throws with [withIosSceneLifecycle] prefix when class boundary is missing', () => {
    const noTransition = APP_DELEGATE_BASE.replace(CLASS_TRANSITION, '\n}\n\nclass SomethingElse');

    expect(() => patchAppDelegate(noTransition)).toThrow('[withIosSceneLifecycle]');
    expect(() => patchAppDelegate(noTransition)).toThrow('class boundary');
  });

  // ── Test: configurationForConnecting is inside AppDelegate class ─────────────
  it('places configurationForConnecting inside the AppDelegate class body (before the extension)', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    const configMethod = result.indexOf('configurationForConnecting');
    const extensionPos = result.indexOf('extension AppDelegate: UIWindowSceneDelegate');

    // Both must be present
    expect(configMethod).toBeGreaterThan(0);
    expect(extensionPos).toBeGreaterThan(0);

    // configurationForConnecting must appear before the UIWindowSceneDelegate extension
    expect(configMethod).toBeLessThan(extensionPos);
  });

  // ── Test: UIWindowSceneDelegate extension is after the AppDelegate class ──────
  it('places UIWindowSceneDelegate extension after the AppDelegate class closing brace', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    const classClosePos = result.indexOf('\n}\n\nextension AppDelegate');
    const extensionPos = result.indexOf('extension AppDelegate: UIWindowSceneDelegate');

    expect(extensionPos).toBeGreaterThan(classClosePos);
  });

  // ── Test: factory setup lines are preserved ──────────────────────────────────
  it('preserves the React Native factory setup lines in didFinishLaunchingWithOptions', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    expect(result).toContain('let delegate = ReactNativeDelegate()');
    expect(result).toContain('let factory = ExpoReactNativeFactory(delegate: delegate)');
    expect(result).toContain('reactNativeFactory = factory');
    expect(result).toContain('bindReactNativeFactory(factory)');
    expect(result).toContain('return super.application(application, didFinishLaunchingWithOptions: launchOptions)');
  });
});

// ── Exported constants ─────────────────────────────────────────────────────────
describe('withIosSceneLifecycle — exported constants', () => {
  it('WINDOW_DECL_OLD matches the exact Expo SDK 54 generated window declaration', () => {
    // This is the precise string Expo SDK 54 generates in AppDelegate.swift.
    // If it changes, the public-access patch will silently no-op and the Swift
    // compiler will fail with an access-control error at build time.
    expect(WINDOW_DECL_OLD).toBe('  var window: UIWindow?');
    expect(WINDOW_DECL_NEW).toBe('  public var window: UIWindow?');
  });

  it('DELEGATE_CLASS_FIXUP_OLD/NEW convert nil-delegateClass form to explicit delegateClass', () => {
    // OLD = the nil-delegateClass one-liner from commits up to 88df50d
    expect(DELEGATE_CLASS_FIXUP_OLD).toContain('return UISceneConfiguration(');
    expect(DELEGATE_CLASS_FIXUP_OLD).not.toContain('delegateClass');
    // NEW = the explicit delegateClass form with NSLog diagnostic
    expect(DELEGATE_CLASS_FIXUP_NEW).toContain('config.delegateClass = type(of: self)');
    expect(DELEGATE_CLASS_FIXUP_NEW).toContain('return config');
    expect(DELEGATE_CLASS_FIXUP_NEW).not.toContain('return UISceneConfiguration(');
  });

  it('FACTORY_ACCESS_FIXUP_OLD/NEW cover the old and new factory access patterns', () => {
    // OLD = self.reactNativeFactory guard from older plugin versions
    expect(FACTORY_ACCESS_FIXUP_OLD).toContain('let factory = reactNativeFactory else { return }');
    expect(FACTORY_ACCESS_FIXUP_OLD).toContain('factory.startReactNative(withModuleName: "main", in: window, launchOptions: nil)');
    expect(FACTORY_ACCESS_FIXUP_OLD).not.toContain('UIApplication.shared.delegate');
    // NEW = UIApplication.shared.delegate + window sync + NSLog
    expect(FACTORY_ACCESS_FIXUP_NEW).toContain('UIApplication.shared.delegate as? AppDelegate');
    expect(FACTORY_ACCESS_FIXUP_NEW).toContain('appDelegate.reactNativeFactory');
    expect(FACTORY_ACCESS_FIXUP_NEW).toContain('appDelegate.window = win');
    expect(FACTORY_ACCESS_FIXUP_NEW).toContain('factory.startReactNative(withModuleName: "main", in: win, launchOptions: nil)');
    expect(FACTORY_ACCESS_FIXUP_NEW).toContain('RCT_jsLocation');
  });

  it('WINDOW_SETUP_OLD matches the exact block generated by Expo SDK 54', () => {
    // Verify constant contains the key identifiers that must match the generated file
    expect(WINDOW_SETUP_OLD).toContain('#if os(iOS) || os(tvOS)');
    expect(WINDOW_SETUP_OLD).toContain('UIWindow(frame: UIScreen.main.bounds)');
    expect(WINDOW_SETUP_OLD).toContain('factory.startReactNative(');
    expect(WINDOW_SETUP_OLD).toContain('withModuleName: "main",');
    expect(WINDOW_SETUP_OLD).toContain('#endif');
  });

  it('CLASS_TRANSITION matches the boundary between AppDelegate and ReactNativeDelegate', () => {
    expect(CLASS_TRANSITION).toContain('class ReactNativeDelegate');
    // Must start with a newline+} to anchor to the AppDelegate class closing brace
    expect(CLASS_TRANSITION).toMatch(/^\n\}/);
  });
});
