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
    const b64 = await page.evaluate(drawIcon, { src, size: t.size, crop: crops[t.mode] })
    const png = compressPng(Buffer.from(b64, 'base64'))
    await writeFile(join(outDir, t.file), png)
    console.log(
      `wrote assets/icons/${t.file} (${t.size}x${t.size}, ${t.mode}, ${Math.round(png.length / 1024)} KB)`,
    )
  }
} finally {
  await browser.close()
}
