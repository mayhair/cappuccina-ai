// Renders the PWA icon set from Cappuccina.jpg into assets/icons/.
// Run with: node scripts/make-pwa-icons.mjs
// Uses headless Chromium (the playwright devDependency) so the crop and resize
// run through the browser canvas API.
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const sourcePath = join(root, 'Cappuccina.jpg')
const outDir = join(root, 'assets', 'icons')

// Square crops in source pixels of the 1024x1536 source. Both are framed like
// the chat avatar (plates/msg-avatar.webp): the latte top, part of the handle on
// the left, her face, and the top of her shoulders. Both stop at x 750, left of
// her waving fingers.
const crops = {
  // Tight crop: the cup's left side and handle are cut off at the left edge.
  any: { x: 178, y: 72, size: 572 },
  // Wider crop, so the face, the latte and part of the handle all stay inside
  // the maskable safe zone (a centered circle of radius 0.4 * size).
  maskable: { x: 110, y: 60, size: 640 },
}

const targets = [
  { file: 'icon-192.png', size: 192, mode: 'any' },
  { file: 'icon-512.png', size: 512, mode: 'any' },
  { file: 'maskable-512.png', size: 512, mode: 'maskable' },
  { file: 'apple-touch-icon.png', size: 180, mode: 'any' },
]

// Runs inside the browser page. Returns the PNG as base64.
async function drawIcon({ src, size, crop }) {
  const img = new Image()
  img.src = src
  await img.decode()

  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')
  ctx.imageSmoothingQuality = 'high'
  // Full-bleed square crop. The source is opaque, so there is no transparency.
  ctx.drawImage(img, crop.x, crop.y, crop.size, crop.size, 0, 0, size, size)
  return canvas.toDataURL('image/png').split(',')[1]
}

const src = `data:image/jpeg;base64,${(await readFile(sourcePath)).toString('base64')}`
await mkdir(outDir, { recursive: true })

const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage()
  await page.setContent('<!doctype html><html><body></body></html>')
  for (const t of targets) {
    const b64 = await page.evaluate(drawIcon, { src, size: t.size, crop: crops[t.mode] })
    await writeFile(join(outDir, t.file), Buffer.from(b64, 'base64'))
    console.log(`wrote assets/icons/${t.file} (${t.size}x${t.size}, ${t.mode})`)
  }
} finally {
  await browser.close()
}
