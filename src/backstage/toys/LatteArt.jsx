import { useEffect, useRef, useState } from 'react'
import { useLanguage } from '../../i18n/LanguageProvider'
import { buzz } from '../prefs'
import { flourish, setPouring } from '../sound'

// Latte art the way baristas do it: pour milk into the crema (it blooms out
// and pushes the crema aside), then etch through it to drag the edges into
// hearts and feathers. A small pixel-warping sim on a 2D canvas.

const N = 320 // canvas pixels per side; CSS scales it to fit
const CUP_R = N / 2
const ETCH_R = 15
const MILK = [247, 237, 222]
const CREMA_IN = [196, 135, 82]
const CREMA_OUT = [104, 60, 32]

// Bilinear sample of `src` at (x, y) into `dst` at index `o`.
function sample(src, dst, o, x, y) {
  x = Math.min(N - 1.001, Math.max(0, x))
  y = Math.min(N - 1.001, Math.max(0, y))
  const x0 = x | 0
  const y0 = y | 0
  const fx = x - x0
  const fy = y - y0
  const i00 = (y0 * N + x0) * 4
  const i10 = i00 + 4
  const i01 = i00 + N * 4
  const i11 = i01 + 4
  for (let k = 0; k < 3; k++) {
    const top = src[i00 + k] + (src[i10 + k] - src[i00 + k]) * fx
    const bottom = src[i01 + k] + (src[i11 + k] - src[i01 + k]) * fx
    dst[o + k] = top + (bottom - top) * fy
  }
}

function createSim(ctx) {
  const image = ctx.createImageData(N, N)
  const px = image.data
  const copy = new Uint8ClampedArray(px.length)

  const fill = () => {
    for (let y = 0; y < N; y++) {
      for (let x = 0; x < N; x++) {
        const d = Math.hypot(x - CUP_R, y - CUP_R) / CUP_R
        const m = Math.min(1, d ** 1.6)
        const grain = (Math.random() - 0.5) * 6
        const o = (y * N + x) * 4
        for (let k = 0; k < 3; k++) px[o + k] = CREMA_IN[k] + (CREMA_OUT[k] - CREMA_IN[k]) * m + grain
        px[o + 3] = 255
      }
    }
  }

  // Warp every pixel within `r` of (cx, cy): `from(x, y, w)` says where its
  // color comes from, with w = 1 at the center fading to 0 at the edge.
  const warp = (cx, cy, r, from) => {
    copy.set(px)
    const x0 = Math.max(0, Math.floor(cx - r))
    const x1 = Math.min(N - 1, Math.ceil(cx + r))
    const y0 = Math.max(0, Math.floor(cy - r))
    const y1 = Math.min(N - 1, Math.ceil(cy + r))
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const d2 = (x - cx) ** 2 + (y - cy) ** 2
        if (d2 >= r * r) continue
        const w = (1 - d2 / (r * r)) ** 2
        const [sx, sy] = from(x, y, w)
        sample(copy, px, (y * N + x) * 4, sx, sy)
      }
    }
  }

  // One frame of pouring at (cx, cy): push what's there outward, then lay a
  // small disc of fresh milk in the middle.
  const pour = (cx, cy, strength) => {
    const reach = 34 + strength * 26
    warp(cx, cy, reach, (x, y, w) => {
      const dx = x - cx
      const dy = y - cy
      const d = Math.hypot(dx, dy) || 1
      const pulled = Math.max(0, d - strength * 3.2 * w) / d
      return [cx + dx * pulled, cy + dy * pulled]
    })
    const r = 6 + strength * 3
    for (let y = Math.floor(cy - r); y <= cy + r; y++) {
      for (let x = Math.floor(cx - r); x <= cx + r; x++) {
        if (x < 0 || y < 0 || x >= N || y >= N) continue
        const d = Math.hypot(x - cx, y - cy)
        if (d > r) continue
        const a = Math.min(1, (r - d) / 2)
        const o = (y * N + x) * 4
        for (let k = 0; k < 3; k++) px[o + k] += (MILK[k] - px[o + k]) * a
      }
    }
  }

  // Drag from (ax, ay) to (bx, by), pulling color along with the tip.
  // `strength` below 1 lets the color lag behind, for a thinner line.
  const etch = (ax, ay, bx, by, strength = 1) => {
    const dist = Math.hypot(bx - ax, by - ay)
    const steps = Math.max(1, Math.ceil(dist / (ETCH_R / 3)))
    const sx = (bx - ax) / steps
    const sy = (by - ay) / steps
    for (let i = 1; i <= steps; i++) {
      const cx = ax + sx * i
      const cy = ay + sy * i
      warp(cx, cy, ETCH_R, (x, y, w) => [x - sx * w * strength, y - sy * w * strength])
    }
  }

  const draw = () => ctx.putImageData(image, 0, 0)

  fill()
  draw()
  return { fill, pour, etch, draw }
}

const frame = () => new Promise((resolve) => requestAnimationFrame(resolve))

// Ready-made designs, drawn with the same pour and etch moves a person makes.
const DESIGNS = {
  async heart(sim, alive) {
    await pourAt(sim, alive, N / 2, N * 0.45, 1.5, 60, 4)
    await etchLine(sim, alive, N / 2, N * 0.2, N / 2, N * 0.8, 16, 0.8)
  },
  async tulip(sim, alive) {
    // Each new blob pushes into the one before it, leaving a crema line between.
    for (const [n, y] of [0.32, 0.5, 0.66].entries()) {
      await pourAt(sim, alive, N / 2, N * y, 1.1, 22 - n * 4, 0)
    }
    await etchLine(sim, alive, N / 2, N * 0.8, N / 2, N * 0.24, 16, 0.5)
  },
  async flower(sim, alive) {
    await pourAt(sim, alive, N / 2, N / 2, 1.6, 70, 0)
    // Pull the crema in from the rim in spokes, like petals.
    for (let k = 0; k < 8 && alive(); k++) {
      const a = (k / 8) * Math.PI * 2
      const at = (r) => [N / 2 + Math.cos(a) * r, N / 2 + Math.sin(a) * r]
      await etchLine(sim, alive, ...at(N * 0.42), ...at(N * 0.08), 8, 1)
    }
  },
}

async function pourAt(sim, alive, x, y, strength, frames, wobble) {
  for (let i = 0; i < frames && alive(); i++) {
    sim.pour(x + Math.sin(i / 5) * wobble, y, strength)
    sim.draw()
    await frame()
  }
}

async function etchLine(sim, alive, ax, ay, bx, by, frames, strength) {
  let prev = [ax, ay]
  for (let i = 1; i <= frames && alive(); i++) {
    const next = [ax + ((bx - ax) * i) / frames, ay + ((by - ay) * i) / frames]
    sim.etch(...prev, ...next, strength)
    sim.draw()
    prev = next
    await frame()
  }
}

export function LatteArt({ calm }) {
  const { t } = useLanguage()
  const canvasRef = useRef(null)
  const simRef = useRef(null)
  const [tool, setTool] = useState('pour')
  const [busy, setBusy] = useState(false)
  const pointer = useRef({ down: false, x: 0, y: 0, frame: 0 })
  const runId = useRef(0)
  const calmRef = useRef(calm)
  calmRef.current = calm

  useEffect(() => {
    simRef.current = createSim(canvasRef.current.getContext('2d', { willReadFrequently: true }))
    return () => {
      runId.current += 1
      cancelAnimationFrame(pointer.current.frame)
      setPouring(false)
    }
  }, [])

  const toCanvas = (e) => {
    const rect = canvasRef.current.getBoundingClientRect()
    return [((e.clientX - rect.left) / rect.width) * N, ((e.clientY - rect.top) / rect.height) * N]
  }

  const pourLoop = () => {
    const p = pointer.current
    if (!p.down) return
    simRef.current.pour(p.x, p.y, calmRef.current ? 0.9 : 1.3)
    simRef.current.draw()
    p.frame = requestAnimationFrame(pourLoop)
  }

  const onPointerDown = (e) => {
    if (busy) return
    e.currentTarget.setPointerCapture?.(e.pointerId)
    const [x, y] = toCanvas(e)
    Object.assign(pointer.current, { down: true, x, y })
    buzz(6)
    if (tool === 'pour') {
      setPouring(true)
      pourLoop()
    }
  }

  const onPointerMove = (e) => {
    const p = pointer.current
    if (!p.down) return
    const [x, y] = toCanvas(e)
    if (tool === 'etch') {
      simRef.current.etch(p.x, p.y, x, y)
      simRef.current.draw()
    }
    p.x = x
    p.y = y
  }

  const onPointerUp = () => {
    pointer.current.down = false
    cancelAnimationFrame(pointer.current.frame)
    setPouring(false)
  }

  const newCup = () => {
    runId.current += 1
    setBusy(false)
    simRef.current.fill()
    simRef.current.draw()
  }

  const surprise = async () => {
    const id = ++runId.current
    const alive = () => runId.current === id
    const names = Object.keys(DESIGNS)
    const name = names[Math.floor(Math.random() * names.length)]
    setBusy(true)
    simRef.current.fill()
    setPouring(true)
    await DESIGNS[name](simRef.current, alive)
    setPouring(false)
    if (alive()) {
      setBusy(false)
      flourish()
    }
  }

  return (
    <div className="toy latte">
      <div className="latte-saucer">
        <div className="latte-cup">
          <canvas
            ref={canvasRef}
            width={N}
            height={N}
            className={`latte-canvas tool-${tool}`}
            aria-label={t('backstage.latte.canvas')}
            role="img"
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
          />
        </div>
      </div>
      <div className="toy-bar">
        <div className="bs-seg" role="radiogroup" aria-label={t('backstage.latte.tool')}>
          {['pour', 'etch'].map((id) => (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={tool === id}
              className="bs-seg-btn"
              onClick={() => setTool(id)}
            >
              {t(`backstage.latte.${id}`)}
            </button>
          ))}
        </div>
        <button type="button" className="bs-btn" onClick={surprise} disabled={busy}>
          {t('backstage.latte.surprise')}
        </button>
        <button type="button" className="bs-btn ghost" onClick={newCup}>
          {t('backstage.latte.new')}
        </button>
      </div>
      <p className="toy-hint">{t(`backstage.latte.hint.${tool}`)}</p>
    </div>
  )
}
