import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import webfontDownload from 'vite-plugin-webfont-dl'
import { readFileSync } from 'node:fs'
import { legalPages } from './legal/pages.mjs'

// Serves /manifest-shortcut.webmanifest, which src/lib/pwa.js gives to browsers
// that install the app as a home-screen shortcut (Brave, Firefox): the same
// manifest, with the maskable icon framed for a shortcut (see
// scripts/make-pwa-icons.mjs).
function shortcutManifest() {
  const fileName = 'manifest-shortcut.webmanifest'
  const render = () => {
    const manifest = JSON.parse(readFileSync(new URL('./assets/manifest.webmanifest', import.meta.url), 'utf8'))
    manifest.icons = manifest.icons.map((icon) =>
      icon.purpose === 'maskable' ? { ...icon, src: '/icons/maskable-shortcut-512.png' } : icon,
    )
    return `${JSON.stringify(manifest, null, 2)}\n`
  }
  return {
    name: 'shortcut-manifest',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url.split('?')[0] !== `/${fileName}`) return next()
        res.setHeader('Content-Type', 'application/manifest+json')
        res.end(render())
      })
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName, source: render() })
    },
  }
}

export default defineConfig({
  // Zero config: the Google Fonts <link> tags in index.html are replaced at build
  // time with self-hosted @font-face rules and the font files are downloaded.
  plugins: [react(), webfontDownload(), legalPages(), shortcutManifest()],
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
