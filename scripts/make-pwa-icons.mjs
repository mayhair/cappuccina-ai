// Renders the PWA icon set from Cappuccina.png into assets/icons/.
// Run with: node scripts/make-pwa-icons.mjs
// Uses headless Chromium (the playwright devDependency) so the crop and resize
// run through the browser canvas API, then re-encodes each PNG losslessly in
// Node to keep the files small.
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { deflateSync, inflateSync, crc32 } from 'node:zlib'
import { chromium } from 'playwright'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const sourcePath = join(root, 'Cappuccina.png')
const outDir = join(root, 'assets', 'icons')

// The "any" icons: a tight square crop in pixels of the 1024x1536 source,
// framed like the chat avatar (plates/msg-avatar.webp): the latte top, her
// face, and the top of her shoulders. It stops at x 750, left of her waving
// fingers, and cuts off the cup's left side and handle.
const anyCrop = { x: 178, y: 72, size: 572 }

// The maskable icons. Android launchers show only a middle window of a
// maskable icon, and how big that window is depends on the browser that
// installed the app. Measured from home-screen screenshots (Samsung One UI,
// October 2026), as fractions of the icon:
//   - Chrome installs a WebAPK and shows the middle ~85%, with no badge.
//   - Brave and Firefox add a shortcut instead. They show only the middle
//     60-65%, and the launcher covers the bottom-right of that window, from
//     about 61% across and 60% down, with the browser's logo.
// So there are two maskable icons, one per window (src/lib/pwa.js picks the
// manifest). Both put the same view of the source in their window, so the app
// icon looks the same whichever browser installed it. The shortcut window is
// the average of Brave's and Firefox's.
const maskableWindows = {
  webapk: { x: 40 / 512, y: 38 / 512, size: 437 / 512 },
  shortcut: { x: 98 / 512, y: 104 / 512, size: 320 / 512 },
}
// What every launcher shows: the whole cup, latte and handle included, with
// her eyes, nose and mouth above the badge corner.
const maskableView = { x: 92, y: 33, size: 770 }

// The square of the source a maskable icon covers, so that maskableView lands
// on the window. It reaches past the edges of the source; drawIcon fills that
// part, which falls outside the window.
function maskableCrop(win) {
  const size = maskableView.size / win.size
  return { x: maskableView.x - win.x * size, y: maskableView.y - win.y * size, size }
}

const targets = [
  { file: 'icon-192.png', size: 192, crop: anyCrop },
  { file: 'icon-512.png', size: 512, crop: anyCrop },
  { file: 'maskable-512.png', size: 512, crop: maskableCrop(maskableWindows.webapk) },
  { file: 'maskable-shortcut-512.png', size: 512, crop: maskableCrop(maskableWindows.shortcut) },
  { file: 'apple-touch-icon.png', size: 180, crop: anyCrop },
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
  const w = img.width
  const h = img.height

  // Full-bleed square crop. The source is opaque, so there is no transparency.
  if (crop.x >= 0 && crop.y >= 0 && crop.x + crop.size <= w && crop.y + crop.size <= h) {
    ctx.drawImage(img, crop.x, crop.y, crop.size, crop.size, 0, 0, size, size)
    return canvas.toDataURL('image/png').split(',')[1]
  }

  // The crop reaches past the source: crop from a copy of the source whose
  // edge pixels (night sky, mostly) are stretched outwards on every side.
  const pad = Math.ceil(crop.size)
  const padded = document.createElement('canvas')
  padded.width = w + 2 * pad
  padded.height = h + 2 * pad
  const p = padded.getContext('2d')
  p.drawImage(img, pad, pad)
  p.drawImage(img, 0, 0, 1, h, 0, pad, pad, h)
  p.drawImage(img, w - 1, 0, 1, h, pad + w, pad, pad, h)
  p.drawImage(padded, 0, pad, padded.width, 1, 0, 0, padded.width, pad)
  p.drawImage(padded, 0, pad + h - 1, padded.width, 1, 0, pad + h, padded.width, pad)
  ctx.drawImage(padded, crop.x + pad, crop.y + pad, crop.size, crop.size, 0, 0, size, size)
  return canvas.toDataURL('image/png').split(',')[1]
}

// Lossless PNG re-encoder. Chromium writes fast, lightly compressed 8-bit RGBA
// PNGs even for opaque images. This decodes one, drops the alpha channel when
// every pixel is opaque, picks the best filter for each row, and deflates at the
// highest level. Handles only 8-bit RGBA, non-interlaced input, which is what
// canvas.toDataURL produces.
function compressPng(png) {
  const SIG = png.subarray(0, 8)
  let width = 0
  let height = 0
  const idat = []
  for (let pos = 8; pos < png.length; ) {
    const len = png.readUInt32BE(pos)
    const type = png.toString('latin1', pos + 4, pos + 8)
    const data = png.subarray(pos + 8, pos + 8 + len)
    if (type === 'IHDR') {
      width = data.readUInt32BE(0)
      height = data.readUInt32BE(4)
      const [depth, colorType, , , interlace] = data.subarray(8)
      if (depth !== 8 || colorType !== 6 || interlace !== 0) return png
    } else if (type === 'IDAT') {
      idat.push(data)
    }
    pos += 12 + len
  }

  // Undo the per-row filters to get raw RGBA pixels.
  const raw = inflateSync(Buffer.concat(idat))
  const stride = width * 4
  const pixels = Buffer.alloc(stride * height)
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)]
    const row = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1))
    const out = pixels.subarray(y * stride, (y + 1) * stride)
    const prev = y > 0 ? pixels.subarray((y - 1) * stride, y * stride) : null
    for (let i = 0; i < stride; i++) {
      const a = i >= 4 ? out[i - 4] : 0
      const b = prev ? prev[i] : 0
      const c = prev && i >= 4 ? prev[i - 4] : 0
      out[i] = (row[i] + unfilterPredict(filter, a, b, c)) & 0xff
    }
  }

  // Drop alpha when the image is fully opaque.
  let opaque = true
  for (let i = 3; i < pixels.length; i += 4) {
    if (pixels[i] !== 255) {
      opaque = false
      break
    }
  }
  const bpp = opaque ? 3 : 4
  const outStride = width * bpp
  const image = Buffer.alloc(outStride * height)
  for (let p = 0, q = 0; p < pixels.length; p += 4, q += bpp) {
    pixels.copy(image, q, p, p + bpp)
  }

  // Re-filter each row with whichever filter gives the smallest sum of
  // absolute values (the standard heuristic), then deflate at level 9.
  const filtered = Buffer.alloc((outStride + 1) * height)
  const candidate = Buffer.alloc(outStride)
  for (let y = 0; y < height; y++) {
    const row = image.subarray(y * outStride, (y + 1) * outStride)
    const prev = y > 0 ? image.subarray((y - 1) * outStride, y * outStride) : null
    let best = 0
    let bestScore = Infinity
    for (let f = 0; f < 5; f++) {
      let score = 0
      for (let i = 0; i < outStride; i++) {
        const a = i >= bpp ? row[i - bpp] : 0
        const b = prev ? prev[i] : 0
        const c = prev && i >= bpp ? prev[i - bpp] : 0
        const v = (row[i] - unfilterPredict(f, a, b, c)) & 0xff
        score += v < 128 ? v : 256 - v
      }
      if (score < bestScore) {
        bestScore = score
        best = f
      }
    }
    const start = y * (outStride + 1)
    filtered[start] = best
    for (let i = 0; i < outStride; i++) {
      const a = i >= bpp ? row[i - bpp] : 0
      const b = prev ? prev[i] : 0
      const c = prev && i >= bpp ? prev[i - bpp] : 0
      candidate[i] = (row[i] - unfilterPredict(best, a, b, c)) & 0xff
    }
    candidate.copy(filtered, start + 1)
  }

  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr.set([8, opaque ? 2 : 6, 0, 0, 0], 8)
  return Buffer.concat([
    SIG,
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', deflateSync(filtered, { level: 9, memLevel: 9 })),
    pngChunk('IEND', Buffer.alloc(0)),
  ])
}

// The value a PNG row filter predicts from the left (a), up (b) and up-left (c)
// bytes: None, Sub, Up, Average or Paeth.
function unfilterPredict(filter, a, b, c) {
  switch (filter) {
    case 1:
      return a
    case 2:
      return b
    case 3:
      return (a + b) >> 1
    case 4: {
      const p = a + b - c
      const pa = Math.abs(p - a)
      const pb = Math.abs(p - b)
      const pc = Math.abs(p - c)
      return pa <= pb && pa <= pc ? a : pb <= pc ? b : c
    }
    default:
      return 0
  }
}

function pngChunk(type, data) {
  const head = Buffer.alloc(8)
  head.writeUInt32BE(data.length, 0)
  head.write(type, 4, 'latin1')
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0)
  return Buffer.concat([head, data, crc])
}

const src = `data:image/png;base64,${(await readFile(sourcePath)).toString('base64')}`
await mkdir(outDir, { recursive: true })

const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage()
  await page.setContent('<!doctype html><html><body></body></html>')
  for (const t of targets) {
    const b64 = await page.evaluate(drawIcon, { src, size: t.size, crop: t.crop })
    const png = compressPng(Buffer.from(b64, 'base64'))
    await writeFile(join(outDir, t.file), png)
    console.log(
      `wrote assets/icons/${t.file} (${t.size}x${t.size}, ${Math.round(png.length / 1024)} KB)`,
    )
  }
} finally {
  await browser.close()
}
