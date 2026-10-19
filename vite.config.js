import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import webfontDownload from 'vite-plugin-webfont-dl'
import { legalPages } from './legal/pages.mjs'

export default defineConfig({
  // Zero config: the Google Fonts <link> tags in index.html are replaced at build
  // time with self-hosted @font-face rules and the font files are downloaded.
  plugins: [react(), webfontDownload(), legalPages()],
  publicDir: 'assets',
  build: {
    // lightningcss (Vite 8's default CSS minifier) dedupes `backdrop-filter`
    // against its `-webkit-` alias, keeping only the last declaration. Without
    // explicit targets it collapses the source pairs to a single property,
    // which drops blur in Firefox (no `-webkit-` alias). These targets make it
    // emit both the prefixed and standard declarations.
    cssTarget: ['chrome100', 'edge100', 'firefox103', 'safari16'],
  },
  // Only scan the app's own entry: by default Vite crawls every **/*.html, which
  // includes backend/.venv and the generated sites under backend/data.
  optimizeDeps: {
    entries: ['index.html'],
  },
  server: {
    // Pre-transform the entry files so the browser doesn't wait on a request waterfall.
    warmup: {
      clientFiles: ['./src/main.jsx', './src/App.jsx', './src/i18n/LanguageProvider.*', './src/styles.css'],
    },
    // The backend (venv, generated sites, their dist/) is not frontend source.
    watch: {
      ignored: ['**/backend/**', '**/dist/**'],
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
