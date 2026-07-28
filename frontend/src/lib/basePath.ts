/**
 * The path prefix the app is served under.
 *
 * `/` in development and at a host root, `/kavim/` behind a reverse proxy that
 * serves the app under a subpath. Vite bakes its `base` config into
 * `import.meta.env.BASE_URL`, so this is the single runtime source for it —
 * the router's `basename`, the API base, and the health probe all derive from
 * here rather than each hardcoding a prefix that can drift.
 *
 * Everything here returns a value with **no trailing slash**, because every
 * caller concatenates a path that starts with one. `/kavim` + `/api/v1`, never
 * `/kavim/` + `/api/v1`.
 */

/** `''` at the root, `'/kavim'` under a subpath. */
export function basePath(): string {
  const base = import.meta.env.BASE_URL || '/';
  const trimmed = base.endsWith('/') ? base.slice(0, -1) : base;
  return trimmed === '' ? '' : trimmed;
}

/**
 * React Router's `basename`, which wants `'/'` at the root rather than `''`.
 */
export function routerBasename(): string {
  return basePath() || '/';
}

/** Prefix an origin-absolute path with the base. `/login` → `/kavim/login`. */
export function withBase(path: string): string {
  return `${basePath()}${path}`;
}
