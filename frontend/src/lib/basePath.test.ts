/**
 * The subpath deployment helpers.
 *
 * These four values have to agree, or the failure is confusing rather than
 * obvious: assets 404, or the router matches nothing, or — worst — login works
 * and the next page load signs the user out because the refresh cookie's path
 * never matched the address bar.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { basePath, routerBasename, withBase } from './basePath';

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('at a host root', () => {
  it('adds no prefix', () => {
    vi.stubEnv('BASE_URL', '/');
    expect(basePath()).toBe('');
    expect(withBase('/api/v1')).toBe('/api/v1');
  });

  it("gives the router '/' rather than an empty basename", () => {
    vi.stubEnv('BASE_URL', '/');
    expect(routerBasename()).toBe('/');
  });
});

describe('under a subpath', () => {
  it('strips the trailing slash Vite adds, so joins never double up', () => {
    // Vite normalises `base` to end with a slash; every caller here supplies a
    // path that starts with one.
    vi.stubEnv('BASE_URL', '/kavim/');
    expect(basePath()).toBe('/kavim');
    expect(withBase('/api/v1')).toBe('/kavim/api/v1');
    expect(withBase('/health/ready')).toBe('/kavim/health/ready');
  });

  it('passes the prefix to the router as its basename', () => {
    vi.stubEnv('BASE_URL', '/kavim/');
    expect(routerBasename()).toBe('/kavim');
  });
});
