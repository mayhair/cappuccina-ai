import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  publicDir: 'assets',
  build: {
    // lightningcss (Vite 8's default CSS minifier) dedupes `backdrop-filter`
    // against its `-webkit-` alias, keeping only the last declaration. Without
    // explicit targets it collapses the source pairs to a single property,
    // which drops blur in Firefox (no `-webkit-` alias). These targets make it
    // emit both the prefixed and standard declarations.
    cssTarget: ['chrome100', 'edge100', 'firefox103', 'safari16'],
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
