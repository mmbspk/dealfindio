import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    open: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_URL || 'http://localhost:8010',
        changeOrigin: true,
        secure: false,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq, req) => {
            if (req.headers['authorization']) {
              proxyReq.setHeader('authorization', req.headers['authorization'])
            }
          })
        },
      },
    },
  },
  build: {
    target: 'ES2020',
    minify: 'terser',
    sourcemap: false,
    outDir: 'dist',
  },
  preview: {
    port: 4173,
  },
})
