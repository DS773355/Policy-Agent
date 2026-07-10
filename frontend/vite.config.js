import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// VITE_REPO_NAME is injected by GitHub Actions as the actual repo name (Policy-Agent).
// Leave unset for local development — defaults to '/'.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // Exposes Vite dev server to the local network (0.0.0.0)
    port: 5173,
  },
  base: process.env.VITE_REPO_NAME ? `/${process.env.VITE_REPO_NAME}/` : '/',
})
