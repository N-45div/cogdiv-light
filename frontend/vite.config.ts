/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the React dev server forwards /api calls to the Python backend,
// so the browser only ever talks to one origin (no CORS setup needed).
// Inside docker compose the backend is reachable as http://backend:8000.
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: { '/api': apiTarget },
  },
  test: {
    environment: 'node',
  },
})
