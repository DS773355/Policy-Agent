import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// VITE_REPO_NAME is set automatically by the GitHub Actions workflow.
// Leave unset for local development (defaults to '/').
export default defineConfig({
  plugins: [react()],
  base: process.env.VITE_REPO_NAME ? `/${process.env.VITE_REPO_NAME}/` : '/',
})
