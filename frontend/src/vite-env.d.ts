/// <reference types="vite/client" />

/**
 * Typed environment variables.
 *
 * Only `VITE_`-prefixed values are exposed to the browser bundle. Declaring
 * them here is what turns `import.meta.env.X` from `any` into a checked string,
 * so a typo becomes a compile error rather than `undefined` at runtime.
 */
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_WS_URL?: string;
  readonly VITE_DEFAULT_LOCALE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
