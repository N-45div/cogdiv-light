/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the React dev server forwards /api calls to the Python backend,
// so the browser only ever talks to one origin (no CORS setup needed).
// Inside docker compose the backend is reachable as http://backend:8000.
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  // Two pages: the landing page at / and the console (the app) at /app/.
  build: {
    rollupOptions: {
      input: {
        landing: fileURLToPath(new URL('./index.html', import.meta.url)),
        app: fileURLToPath(new URL('./app/index.html', import.meta.url)),
      },
    },
  },
  server: {
    host: true,
    // changeOrigin so a hosted backend (e.g. the Modal deployment) sees its own host name.
    proxy: { '/api': { target: apiTarget, changeOrigin: true } },
  },
  test: {
    environment: 'node',
  },
})
