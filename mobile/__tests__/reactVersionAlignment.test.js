'use strict';

/**
 * Regression guard: React renderer version alignment.
 *
 * react-test-renderer must be the same semver as react itself.  A mismatch
 * (e.g. react@19.3.0 + react-test-renderer@19.1.0) triggers the error
 * "Warning: Invalid hook call" during Jest runs and causes subtle render
 * failures because two different React runtimes are loaded simultaneously.
 *
 * react must also satisfy the peer dependency declared by the installed
 * react-native version.  react-native 0.81.5 requires react ^19.1.0.
 */

const reactPkg = require('react/package.json');
const reactDomPkg = require('react-dom/package.json');
const reactTestRendererPkg = require('react-test-renderer/package.json');
const reactNativePkg = require('react-native/package.json');

describe('React dependency version alignment', () => {
  it('react-test-renderer version exactly matches react version', () => {
    expect(reactTestRendererPkg.version).toBe(reactPkg.version);
  });

  it('react-dom version exactly matches react version', () => {
    expect(reactDomPkg.version).toBe(reactPkg.version);
  });

  it('react version satisfies react-native peer dependency', () => {
    const peerRange = reactNativePkg.peerDependencies?.react ?? '';
    // peerRange = "^X.Y.Z" — caret: same major, minor >= Y
    const match = peerRange.match(/^\^(\d+)\.(\d+)\.\d+$/);
    expect(match).not.toBeNull();
    if (match) {
      const reqMajor = Number(match[1]);
      const reqMinor = Number(match[2]);
      const [actMajor, actMinor] = reactPkg.version.split('.').map(Number);
      expect(actMajor).toBe(reqMajor);
      expect(actMinor).toBeGreaterThanOrEqual(reqMinor);
    }
  });
});
