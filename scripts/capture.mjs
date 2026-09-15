import { chromium } from 'playwright'
import { preview } from 'vite'
import path from 'node:path'

const outPath = process.argv[2] || '.impeccable/review/hero-repro.png'
const width = parseInt(process.argv[3] || '1536', 10)
const height = parseInt(process.argv[4] || '1024', 10)
const theme = process.argv[5] || 'light'

const server = await preview({ preview: { port: 4173, strictPort: true } })
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width, height } })
await page.addInitScript((t) => {
  try { localStorage.setItem('cappuccina-theme', t) } catch {}
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
}, theme)
await page.goto('http://localhost:4173/', { waitUntil: 'networkidle' })
await page.waitForTimeout(600)
await page.evaluate(() => document.fonts.ready)
await page.waitForTimeout(400)
await page.screenshot({ path: outPath })
await browser.close()
await server.close()
console.log('captured', outPath, width + 'x' + height, theme)
