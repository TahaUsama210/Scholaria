/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API origin when it differs from the page's (default: same origin). */
  readonly VITE_API_BASE_URL?: string;
}
