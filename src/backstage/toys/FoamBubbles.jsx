import { useEffect, useRef, useState } from 'react'
import { motion } from 'motion/react'
import { useLanguage } from '../../i18n/LanguageProvider'
import { buzz, loadLocal, saveLocal } from '../prefs'
import { flourish, play } from '../sound'

// A tray of milk-foam bubbles. Tap to pop one, drag to pop a whole row. When
// the last one pops, a fresh tray foams up by itself.

const COLS = 8
const ROWS = 6
const COUNT = COLS * ROWS
const REFILL_MS = 900

const fresh = () => Array(COUNT).fill(false)

export function FoamBubbles({ calm }) {
  const { t } = useLanguage()
  const [popped, setPopped] = useState(fresh)
  const [batch, setBatch] = useState(0)
  const [total, setTotal] = useState(() => loadLocal('pops', 0))
  const [focus, setFocus] = useState(0)
  const poppedRef = useRef(popped)
  const totalRef = useRef(total)
  const dragging = useRef(false)
  const cells = useRef([])

  const refill = () => {
    poppedRef.current = fresh()
    setPopped(poppedRef.current)
    setBatch((b) => b + 1)
  }

  const pop = (i) => {
    if (poppedRef.current[i]) return
    const next = poppedRef.current.slice()
    next[i] = true
    poppedRef.current = next
    setPopped(next)
    totalRef.current += 1
    setTotal(totalRef.current)
    saveLocal('pops', totalRef.current)
    play('pop')
    buzz(6)
  }

  const allPopped = popped.every(Boolean)
  useEffect(() => {
    if (!allPopped) return
    const id = setTimeout(() => {
      flourish()
      refill()
    }, REFILL_MS)
    return () => clearTimeout(id)
  }, [allPopped])

  // Dragging pops every bubble under the path of the finger or mouse. A fast
  // swipe moves far between events, so the path is filled in between them.
  const lastPoint = useRef(null)
  const popAt = (e) => {
    const [x0, y0] = lastPoint.current ?? [e.clientX, e.clientY]
    const steps = Math.max(1, Math.ceil(Math.hypot(e.clientX - x0, e.clientY - y0) / 8))
    for (let s = 1; s <= steps; s++) {
      const x = x0 + ((e.clientX - x0) * s) / steps
      const y = y0 + ((e.clientY - y0) * s) / steps
      const i = document.elementFromPoint(x, y)?.closest?.('[data-bubble]')?.dataset.bubble
      if (i !== undefined) pop(Number(i))
    }
    lastPoint.current = [e.clientX, e.clientY]
  }
  const endDrag = () => {
    dragging.current = false
    lastPoint.current = null
  }

  const onKeyDown = (e) => {
    const moves = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: COLS, ArrowUp: -COLS, Home: -focus, End: COUNT - 1 - focus }
    let step = moves[e.key]
    if (step === undefined) return
    if (document.documentElement.dir === 'rtl' && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) step = -step
    e.preventDefault()
    const next = Math.min(COUNT - 1, Math.max(0, focus + step))
    setFocus(next)
    cells.current[next]?.focus()
  }

  return (
    <div className="toy bubbles">
      <div
        className="foam-tray"
        role="grid"
        aria-label={t('backstage.bubbles.tray')}
        style={{ '--cols': COLS }}
        onPointerDown={(e) => {
          dragging.current = true
          e.currentTarget.setPointerCapture?.(e.pointerId)
          popAt(e)
        }}
        onPointerMove={(e) => dragging.current && popAt(e)}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={onKeyDown}
      >
        {Array.from({ length: ROWS }, (_, r) => (
          <div className="foam-row" role="row" key={r}>
            {Array.from({ length: COLS }, (_, c) => {
              const i = r * COLS + c
              const isPopped = popped[i]
              return (
                <motion.button
                  key={`${batch}-${i}`}
                  ref={(el) => {
                    cells.current[i] = el
                  }}
                  type="button"
                  role="gridcell"
                  data-bubble={i}
                  tabIndex={i === focus ? 0 : -1}
                  className={`foam-bubble${isPopped ? ' is-popped' : ''}`}
                  aria-label={t(isPopped ? 'backstage.bubbles.popped' : 'backstage.bubbles.bubble')}
                  aria-disabled={isPopped}
                  onFocus={() => setFocus(i)}
                  onKeyDown={(e) => {
                    if (e.key === ' ' || e.key === 'Enter') {
                      e.preventDefault()
                      pop(i)
                    }
                  }}
                  initial={batch === 0 ? false : { scale: 0, opacity: 0 }}
                  animate={{ scale: isPopped ? 0.82 : 1, opacity: 1 }}
                  transition={
                    isPopped
                      ? { type: 'spring', stiffness: 600, damping: 14 }
                      : { type: 'spring', stiffness: calm ? 160 : 320, damping: 16, delay: (r + c) * (calm ? 0.04 : 0.02) }
                  }
                >
                  {isPopped && <span className="foam-burst" aria-hidden="true" />}
                </motion.button>
              )
            })}
          </div>
        ))}
      </div>
      <div className="toy-bar">
        <p className="toy-count">{t('backstage.bubbles.count', { n: total })}</p>
        <button type="button" className="bs-btn" onClick={refill}>
          {t('backstage.bubbles.refill')}
        </button>
      </div>
      <p className="toy-hint">{t('backstage.bubbles.hint')}</p>
    </div>
  )
}
