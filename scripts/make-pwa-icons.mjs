// Renders the PWA icon set from Cappuccina.jpg into assets/icons/.
// Run with: node scripts/make-pwa-icons.mjs
// Uses headless Chromium (the playwright devDependency) so the crop, resize
// and feathering all run through the browser canvas API.
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const sourcePath = join(root, 'Cappuccina.jpg')
const outDir = join(root, 'assets', 'icons')

// Square crop in source pixels. The cup head spans roughly x 80-810, y 80-560
// of the 909x1078 source, so this square keeps the whole cup, handle included.
const crop = { x: 50, y: 0, size: 800 }

const targets = [
  { file: 'icon-192.png', size: 192, mode: 'any' },
  { file: 'icon-512.png', size: 512, mode: 'any' },
  { file: 'maskable-512.png', size: 512, mode: 'maskable' },
  { file: 'apple-touch-icon.png', size: 180, mode: 'any' },
]

// Runs inside the browser page. Returns the PNG as base64.
async function drawIcon({ src, size, mode, crop }) {
  const img = new Image()
  img.src = src
  await img.decode()

  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')
  ctx.imageSmoothingQuality = 'high'

  if (mode === 'any') {
    // Full-bleed square crop. The source is opaque, so there is no transparency.
    ctx.drawImage(img, crop.x, crop.y, crop.size, crop.size, 0, 0, size, size)
  } else {
    // Maskable: the cup must stay inside the central safe zone (radius 0.4 * size).
    // Step 1: flat fill with the average sky colour from the top band of the source.
    const probe = document.createElement('canvas')
    probe.width = img.width
    probe.height = 40
    const pctx = probe.getContext('2d')
    pctx.drawImage(img, 0, 0, img.width, 40, 0, 0, img.width, 40)
    const px = pctx.getImageData(0, 0, img.width, 40).data
    let r = 0
    let g = 0
    let b = 0
    let n = 0
    for (let i = 0; i < px.length; i += 4) {
      r += px[i]
      g += px[i + 1]
      b += px[i + 2]
      n++
    }
    ctx.fillStyle = `rgb(${Math.round(r / n)}, ${Math.round(g / n)}, ${Math.round(b / n)})`
    ctx.fillRect(0, 0, size, size)

    // Step 2: draw the whole square source (the cup sits at x 80-810 of 909, so
    // it keeps a clear margin from the edges), scaled to 72% of the icon and
    // centred on the cup. The outer 7% on each side is feathered to transparent
    // so the picture edges blend into the flat sky colour.
    const side = img.width
    const fgSize = size * 0.72
    const scale = fgSize / side
    const fgLeft = size / 2 - (side / 2) * scale
    const fgTop = size / 2 - 320 * scale // cup centre (y ~320 in source) at canvas centre
    const fg = document.createElement('canvas')
    fg.width = size
    fg.height = size
    const fctx = fg.getContext('2d')
    fctx.imageSmoothingQuality = 'high'
    fctx.drawImage(img, 0, 0, side, side, fgLeft, fgTop, fgSize, fgSize)
    // 'destination-in' keeps only the pixels covered by the gradient's alpha.
    // Apply one horizontal and one vertical feather mask in turn.
    const f = 0.07
    fctx.globalCompositeOperation = 'destination-in'
    const feather = (x0, x1, y0, y1) => {
      const g = fctx.createLinearGradient(x0, y0, x1, y1)
      g.addColorStop(0, 'rgba(0, 0, 0, 0)')
      g.addColorStop(f, 'rgba(0, 0, 0, 1)')
      g.addColorStop(1 - f, 'rgba(0, 0, 0, 1)')
      g.addColorStop(1, 'rgba(0, 0, 0, 0)')
      fctx.fillStyle = g
      fctx.fillRect(0, 0, size, size)
    }
    feather(fgLeft, fgLeft + fgSize, 0, 0)
    feather(0, 0, fgTop, fgTop + fgSize)
    ctx.drawImage(fg, 0, 0)
  }

  return canvas.toDataURL('image/png').split(',')[1]
}

const src = `data:image/jpeg;base64,${(await readFile(sourcePath)).toString('base64')}`
await mkdir(outDir, { recursive: true })

const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage()
  await page.setContent('<!doctype html><html><body></body></html>')
  for (const t of targets) {
    const b64 = await page.evaluate(drawIcon, { src, size: t.size, mode: t.mode, crop })
    await writeFile(join(outDir, t.file), Buffer.from(b64, 'base64'))
    console.log(`wrote assets/icons/${t.file} (${t.size}x${t.size}, ${t.mode})`)
  }
} finally {
  await browser.close()
}
