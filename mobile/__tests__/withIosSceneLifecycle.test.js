'use strict';

/**
 * Regression tests for withIosSceneLifecycle.js
 *
 * Covers:
 *   1.  Patch applied once — window block removed, scene methods injected
 *   2.  window property promoted to public (UIWindowSceneDelegate requirement)
 *   3.  configurationForConnecting does NOT set delegateClass (instance isolation fix)
 *   4.  Already-patched file with delegateClass is retroactively fixed (step 0.5)
 *   5.  Idempotent — second call returns identical string
 *   6.  Throws when window declaration absent from AppDelegate.swift
 *   7.  Throws when WINDOW_SETUP_OLD pattern is absent
 *   8.  Throws when class-boundary pattern is absent
 *   9.  configurationForConnecting is inside AppDelegate class body
 *   10. UIWindowSceneDelegate extension is after the AppDelegate class
 *   11. Patched file still contains factory setup lines (not over-removed)
 *   12. Exported constants are stable
 */

const {
  patchAppDelegate,
  PATCH_MARKER,
  WINDOW_DECL_OLD,
  WINDOW_DECL_NEW,
  DELEGATE_CLASS_FIXUP_OLD,
  DELEGATE_CLASS_FIXUP_NEW,
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

  it('adds configurationForConnecting returning a UISceneConfiguration', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    expect(result).toContain('configurationForConnecting connectingSceneSession: UISceneSession');
    expect(result).toContain('UISceneConfiguration(name: "Default Configuration"');
    // Must NOT set delegateClass — doing so would cause UIKit to create a new
    // AppDelegate instance as scene delegate, which would have reactNativeFactory = nil
    expect(result).not.toContain('config.delegateClass = type(of: self)');
    // The method must return the configuration directly (no intermediate let)
    expect(result).toContain('return UISceneConfiguration(name: "Default Configuration"');
  });

  it('adds a UIWindowSceneDelegate extension that creates UIWindow(windowScene:)', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    expect(result).toContain('extension AppDelegate: UIWindowSceneDelegate');
    expect(result).toContain('willConnectTo session: UISceneSession');
    expect(result).toContain('UIWindow(windowScene: windowScene)');
    expect(result).toContain('factory.startReactNative(withModuleName: "main", in: window, launchOptions: nil)');
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

  // ── Test 3: configurationForConnecting must NOT set delegateClass ─────────────
  it('configurationForConnecting does not set delegateClass on UISceneConfiguration', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    // The generated configurationForConnecting must not contain delegateClass.
    // Setting it causes UIKit to create a NEW AppDelegate as scene delegate,
    // which has reactNativeFactory = nil → scene(_:willConnectTo:) guard fails
    // silently → React Native never starts → black screen.
    const configMethod = result.slice(
      result.indexOf('configurationForConnecting'),
      result.indexOf('}\n', result.indexOf('configurationForConnecting')) + 2
    );
    expect(configMethod).not.toContain('delegateClass');
    expect(configMethod).toContain('return UISceneConfiguration(');
  });

  // ── Test 4: step 0.5 retroactively fixes already-patched files ───────────────
  it('retroactively removes config.delegateClass from a file already patched by old plugin', () => {
    // Simulate a file that was patched by the OLD plugin version which set
    // config.delegateClass = type(of: self).
    const oldPatchedFile = patchAppDelegate(APP_DELEGATE_BASE)
      .replace(DELEGATE_CLASS_FIXUP_NEW, DELEGATE_CLASS_FIXUP_OLD);

    // Sanity-check: the simulated old file has delegateClass assignment
    expect(oldPatchedFile).toContain('config.delegateClass = type(of: self)');
    // It also already has PATCH_MARKER (patched by old plugin)
    expect(oldPatchedFile).toContain(PATCH_MARKER);

    // Running the current plugin must fix the delegateClass line even though
    // the PATCH_MARKER is already present (step 0.5 runs before the guard).
    const fixed = patchAppDelegate(oldPatchedFile);
    // The Swift assignment must be gone (checking the assignment form, not just the word)
    expect(fixed).not.toContain('config.delegateClass = type(of: self)');
    expect(fixed).toContain('return UISceneConfiguration(');
  });

  // ── Test 2: idempotent ──────────────────────────────────────────────────────
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

  // ── Test 3: throws when window block absent ──────────────────────────────────
  it('throws with [withIosSceneLifecycle] prefix when UIWindow block is missing', () => {
    const noWindowBlock = APP_DELEGATE_BASE.replace(WINDOW_SETUP_OLD, '// already removed');

    expect(() => patchAppDelegate(noWindowBlock)).toThrow('[withIosSceneLifecycle]');
    expect(() => patchAppDelegate(noWindowBlock)).toThrow('UIWindow creation block not found');
  });

  // ── Test 4: throws when class boundary absent ────────────────────────────────
  it('throws with [withIosSceneLifecycle] prefix when class boundary is missing', () => {
    const noTransition = APP_DELEGATE_BASE.replace(CLASS_TRANSITION, '\n}\n\nclass SomethingElse');

    expect(() => patchAppDelegate(noTransition)).toThrow('[withIosSceneLifecycle]');
    expect(() => patchAppDelegate(noTransition)).toThrow('class boundary');
  });

  // ── Test 5: configurationForConnecting is inside AppDelegate class ───────────
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

  // ── Test 6: UIWindowSceneDelegate extension is after the AppDelegate class ───
  it('places UIWindowSceneDelegate extension after the AppDelegate class closing brace', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    const classClosePos = result.indexOf('\n}\n\nextension AppDelegate');
    const extensionPos = result.indexOf('extension AppDelegate: UIWindowSceneDelegate');

    expect(extensionPos).toBeGreaterThan(classClosePos);
  });

  // ── Test 7: factory setup lines are preserved ────────────────────────────────
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

  it('DELEGATE_CLASS_FIXUP_OLD covers the exact body generated by old plugin', () => {
    // This constant drives step 0.5, which retroactively removes the line that
    // caused UIKit to create a new AppDelegate as the scene delegate.
    expect(DELEGATE_CLASS_FIXUP_OLD).toContain('let config = UISceneConfiguration(');
    expect(DELEGATE_CLASS_FIXUP_OLD).toContain('config.delegateClass = type(of: self)');
    expect(DELEGATE_CLASS_FIXUP_OLD).toContain('return config');
    // The replacement must be the one-liner form without delegateClass
    expect(DELEGATE_CLASS_FIXUP_NEW).toContain('return UISceneConfiguration(');
    expect(DELEGATE_CLASS_FIXUP_NEW).not.toContain('delegateClass');
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
