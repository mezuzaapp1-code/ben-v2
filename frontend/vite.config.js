import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const devApiTarget =
    env.VITE_DEV_API_PROXY ?? process.env.VITE_DEV_API_PROXY ?? 'http://127.0.0.1:8002'

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/chat': {
          target: devApiTarget,
          changeOrigin: true,
          timeout: 300_000,
          proxyTimeout: 300_000,
        },
        '/council': {
          target: devApiTarget,
          changeOrigin: true,
          timeout: 300_000,
          proxyTimeout: 300_000,
        },
      },
    },
  }
})
