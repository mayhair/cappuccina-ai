import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, animate, motion, useMotionValue, useTransform } from 'motion/react'
import { useLanguage } from '../../i18n/LanguageProvider'
import { buzz, loadLocal, saveLocal } from '../prefs'
import { chime, play } from '../sound'

// Tap Cappuccina and she does a pirouette; hold and she spins faster; let go
// and she slows down and lands facing you with a little wobble. Every full
// turn rings the next note of a music box.

const KICK = 540 // deg/s a tap gives her, so even a tap is one full turn
const SETTLE_BELOW = 320 // deg/s: slow enough to stop on the next full turn
const DOUBLE_TAP_MS = 320
const CHEER_EVERY = 10
const CHEERS = 5

function physics(calm) {
  return calm
    ? { accel: 420, max: 900, friction: 0.9 }
    : { accel: 900, max: 2200, friction: 0.7 }
}

let puffId = 0

export function Pirouette({ calm }) {
  const { t } = useLanguage()
  const rotate = useMotionValue(0)
  const speed = useMotionValue(0)
  const shadow = useTransform(speed, [0, 2200], [1, 0.72])
  const blurOpacity = useTransform(speed, [600, 2200], [0, calm ? 0 : 0.5])
  const [total, setTotal] = useState(() => loadLocal('pirouettes', 0))
  const totalRef = useRef(total)
  const [cheer, setCheer] = useState(null)
  const [puffs, setPuffs] = useState([])

  const state = useRef({ vel: 0, holding: false, frame: 0, last: 0, settle: null, turns: 0, lastTap: 0 })
  const calmRef = useRef(calm)
  calmRef.current = calm

  // Count turns as the angle passes each multiple of 360. She only ever spins
  // one way, so a high-water mark ignores the wobble around the landing.
  useEffect(() => {
    return rotate.on('change', (deg) => {
      const turns = Math.floor((deg + 1) / 360)
      const s = state.current
      if (turns <= s.turns) return
      s.turns = turns
      chime(turns - 1)
      const next = totalRef.current + 1
      totalRef.current = next
      saveLocal('pirouettes', next)
      setTotal(next)
      if (next % CHEER_EVERY === 0) setCheer(Math.floor(next / CHEER_EVERY) % CHEERS)
    })
  }, [rotate])

  useEffect(() => {
    if (cheer === null) return
    const id = setTimeout(() => setCheer(null), 2200)
    return () => clearTimeout(id)
  }, [cheer])

  useEffect(() => () => {
    cancelAnimationFrame(state.current.frame)
    state.current.settle?.stop()
  }, [])

  const tick = (now) => {
    const s = state.current
    const dt = Math.min((now - s.last) / 1000, 0.05)
    s.last = now
    const { accel, max, friction } = physics(calmRef.current)
    if (s.holding) s.vel = Math.min(s.vel + accel * dt, max)
    else s.vel *= Math.exp(-friction * dt)
    rotate.set(rotate.get() + s.vel * dt)
    speed.set(s.vel)
    if (!s.holding && s.vel < SETTLE_BELOW) {
      land()
      return
    }
    s.frame = requestAnimationFrame(tick)
  }

  // Finish on the next full turn with a springy overshoot.
  const land = () => {
    const s = state.current
    const target = Math.ceil((rotate.get() + 1) / 360) * 360
    s.settle = animate(rotate, target, {
      type: 'spring',
      velocity: s.vel,
      stiffness: calmRef.current ? 60 : 110,
      damping: calmRef.current ? 14 : 8,
    })
    animate(speed, 0, { duration: 0.6 })
    s.vel = 0
  }

  const steam = () => {
    play('steam')
    const count = calmRef.current ? 3 : 6
    const batch = Array.from({ length: count }, (_, i) => ({
      id: ++puffId,
      x: (Math.random() - 0.5) * 70,
      delay: i * 0.07,
      size: 14 + Math.random() * 18,
    }))
    setPuffs((list) => [...list, ...batch])
  }

  const press = () => {
    const s = state.current
    const now = performance.now()
    if (now - s.lastTap < DOUBLE_TAP_MS) steam()
    s.lastTap = now
    buzz(10)
    if (s.settle) {
      s.vel = Math.max(rotate.getVelocity(), 0)
      s.settle.stop()
      s.settle = null
    }
    s.vel = Math.max(s.vel, KICK)
    s.holding = true
    cancelAnimationFrame(s.frame)
    s.last = now
    s.frame = requestAnimationFrame(tick)
  }

  const release = () => {
    state.current.holding = false
  }

  const onKeyDown = (e) => {
    if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) {
      e.preventDefault()
      press()
    }
  }
  const onKeyUp = (e) => {
    if (e.key === ' ' || e.key === 'Enter') release()
  }

  return (
    <div className="toy pirouette">
      <div className="pir-stage">
        <AnimatePresence>
          {cheer !== null && (
            <motion.p
              key={cheer}
              className="pir-cheer"
              role="status"
              initial={{ opacity: 0, y: 8, scale: 0.9 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -6 }}
            >
              {t(`backstage.spin.cheer${cheer}`)}
            </motion.p>
          )}
        </AnimatePresence>
        <div className="pir-steam" aria-hidden="true">
          {puffs.map((p) => (
            <motion.span
              key={p.id}
              className="pir-puff"
              style={{ width: p.size, height: p.size, marginLeft: p.x }}
              initial={{ opacity: 0, y: 0, scale: 0.4 }}
              animate={{ opacity: [0, 0.85, 0], y: calm ? -60 : -110, scale: 1.8 }}
              transition={{ duration: calm ? 2.2 : 1.5, delay: p.delay, ease: 'easeOut' }}
              onAnimationComplete={() => setPuffs((list) => list.filter((x) => x.id !== p.id))}
            />
          ))}
        </div>
        <button
          type="button"
          className="pir-dancer"
          aria-label={t('backstage.spin.dancer')}
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture?.(e.pointerId)
            press()
          }}
          onPointerUp={release}
          onPointerCancel={release}
          onKeyDown={onKeyDown}
          onKeyUp={onKeyUp}
          onBlur={release}
          onContextMenu={(e) => e.preventDefault()}
        >
          <motion.img
            src="/plates/ballerina-hero.webp"
            alt=""
            draggable="false"
            style={{ rotateY: rotate }}
          />
          <motion.span className="pir-whirl" style={{ opacity: blurOpacity }} aria-hidden="true" />
        </button>
        <motion.div className="pir-shadow" style={{ scaleX: shadow }} aria-hidden="true" />
      </div>
      <div className="toy-bar">
        <p className="toy-count">
          {t('backstage.spin.count', { n: total })}
        </p>
        <button type="button" className="bs-btn" onClick={steam}>
          {t('backstage.spin.steam')}
        </button>
      </div>
      <p className="toy-hint">{t('backstage.spin.hint')}</p>
    </div>
  )
}
