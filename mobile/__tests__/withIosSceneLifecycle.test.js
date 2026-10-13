'use strict';

/**
 * Regression tests for withIosSceneLifecycle.js
 *
 * Covers:
 *   1. Patch applied once — window block removed, scene methods injected
 *   2. Idempotent — second call returns identical string
 *   3. Throws when WINDOW_SETUP_OLD pattern is absent
 *   4. Throws when class-boundary pattern is absent
 *   5. configurationForConnecting is inside AppDelegate class body
 *   6. UIWindowSceneDelegate extension is after the AppDelegate class
 *   7. Patched file still contains factory setup lines (not over-removed)
 *   8. Exported constants are stable
 */

const {
  patchAppDelegate,
  PATCH_MARKER,
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
    expect(result).not.toContain('#endif');

    // Patch marker is present
    expect(result).toContain(PATCH_MARKER);

    // Comment replacing the removed block is present
    expect(result).toContain('window creation deferred to UIWindowSceneDelegate');
  });

  it('adds configurationForConnecting returning a UISceneConfiguration', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    expect(result).toContain('configurationForConnecting connectingSceneSession: UISceneSession');
    expect(result).toContain('UISceneConfiguration(name: "Default Configuration"');
    expect(result).toContain('config.delegateClass = type(of: self)');
  });

  it('adds a UIWindowSceneDelegate extension that creates UIWindow(windowScene:)', () => {
    const result = patchAppDelegate(APP_DELEGATE_BASE);

    expect(result).toContain('extension AppDelegate: UIWindowSceneDelegate');
    expect(result).toContain('willConnectTo session: UISceneSession');
    expect(result).toContain('UIWindow(windowScene: windowScene)');
    expect(result).toContain('factory.startReactNative(withModuleName: "main", in: window, launchOptions: nil)');
  });

  // ── Test 2: idempotent ──────────────────────────────────────────────────────
  it('is idempotent — second call returns byte-for-byte identical output', () => {
    const once = patchAppDelegate(APP_DELEGATE_BASE);
    const twice = patchAppDelegate(once);

    expect(twice).toBe(once);
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

// ── Test 8: exported constants ─────────────────────────────────────────────────
describe('withIosSceneLifecycle — exported constants', () => {
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
