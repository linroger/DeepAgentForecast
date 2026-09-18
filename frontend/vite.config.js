import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { devRoutingIdentityPlugin, resolveBackendTarget } from './devServerConfig.mjs'

const backendTarget = resolveBackendTarget()

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue(), devRoutingIdentityPlugin()],
  server: {
    port: 3000,
    strictPort: true,
    // The launcher opens only after UI and API readiness; --no-open stays quiet.
    open: false,
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
        secure: false
      },
      '/health': {
        target: backendTarget,
        changeOrigin: true,
        secure: false
      }
    }
  }
})
