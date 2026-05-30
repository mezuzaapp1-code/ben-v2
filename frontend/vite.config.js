import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/chat': {
        target: 'https://ben-v2-production.up.railway.app',
        changeOrigin: true,
        timeout: 300_000,
        proxyTimeout: 300_000,
      },
      '/council': {
        target: 'https://ben-v2-production.up.railway.app',
        changeOrigin: true,
        timeout: 300_000,
        proxyTimeout: 300_000,
      },
    },
  },
})
