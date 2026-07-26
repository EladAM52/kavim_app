import { fileURLToPath, URL } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
// vitest/config re-exports Vite's defineConfig with the `test` key typed.
import { defineConfig } from 'vitest/config';

// The backend is a separate process in development and the same origin in
// production, so everything under /api and /ws is proxied here. That keeps the
// frontend code identical in both environments — no base-URL branching, no CORS
// in production (SPEC §5.5).
const API_TARGET = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react(), tailwindcss()],

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
      '/ws': { target: API_TARGET, ws: true, changeOrigin: true },
      '/health': { target: API_TARGET, changeOrigin: true },
    },
  },

  build: {
    outDir: 'dist',
    sourcemap: true,
    // Budget guard (SPEC NFR-02): a plant-floor phone on 3G cannot afford a
    // fat initial bundle, so an overrun should be noisy.
    chunkSizeWarningLimit: 300,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          query: ['@tanstack/react-query'],
          i18n: ['i18next', 'react-i18next', 'i18next-browser-languagedetector'],
        },
      },
    },
  },

  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: true,
  },
});
