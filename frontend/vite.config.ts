import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // The proxy target is the one place the backend port is written down on the
  // frontend side. It is server-side only (never shipped to the browser):
  // set VITE_DEV_API_TARGET for a remote/LAN backend, or leave it for the
  // usual local uvicorn on :8000.
  const env = loadEnv(mode, '.', '')
  const apiTarget = env.VITE_DEV_API_TARGET?.trim() || 'http://localhost:8000'

  const apiProxy = {
    '/api': {
      target: apiTarget,
      changeOrigin: true,
      ws: true,
      rewrite: (path: string) => path.replace(/^\/api/, ''),
    },
  }

  return {
    plugins: [react()],
    server: {
      host: true,
      allowedHosts: ['.trycloudflare.com'],
      proxy: apiProxy,
    },
    preview: {
      host: true,
      allowedHosts: ['.trycloudflare.com'],
      proxy: apiProxy,
    },
  }
})
