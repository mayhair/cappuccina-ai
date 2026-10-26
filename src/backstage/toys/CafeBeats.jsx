import { useEffect, useRef, useState } from 'react'
import { motion } from 'motion/react'
import { useLanguage } from '../../i18n/LanguageProvider'
import { buzz, loadLocal, saveLocal } from '../prefs'
import { play, unlockAudio } from '../sound'

// An 8-step looper made of café sounds. Tap squares to build a beat; it keeps
// playing while you change it, and it's saved for next time.

const ROWS = ['clink', 'spoon', 'steam', 'tung']
const STEPS = 8
const TEMPO = { min: 60, max: 150, start: 96 }

const STARTER = [
  [false, false, false, true, false, false, false, true],
  [false, false, true, false, false, false, true, false],
  [false, false, false, false, false, false, true, false],
  [true, false, false, false, true, false, false, false],
]

const empty = () => ROWS.map(() => Array(STEPS).fill(false))

function loadBeat() {
  const saved = loadLocal('beat', null)
  const valid = Array.isArray(saved) && saved.length === ROWS.length
    && saved.every((row) => Array.isArray(row) && row.length === STEPS)
  return valid ? saved.map((row) => row.map(Boolean)) : STARTER
}

export function CafeBeats({ calm }) {
  const { t } = useLanguage()
  const [beat, setBeat] = useState(loadBeat)
  const [tempo, setTempo] = useState(() => {
    const saved = Number(loadLocal('tempo', TEMPO.start))
    return Number.isFinite(saved) ? Math.min(TEMPO.max, Math.max(TEMPO.min, saved)) : TEMPO.start
  })
  const [playing, setPlaying] = useState(false)
  const [step, setStep] = useState(-1)
  const [focus, setFocus] = useState([0, 0])
  const beatRef = useRef(beat)
  const seqRef = useRef(null)
  const toneRef = useRef(null)
  const cells = useRef({})

  useEffect(() => {
    beatRef.current = beat
    saveLocal('beat', beat)
  }, [beat])

  useEffect(() => {
    saveLocal('tempo', tempo)
    toneRef.current?.getTransport().bpm.rampTo(tempo, 0.1)
  }, [tempo])

  const stop = () => {
    const Tone = toneRef.current
    if (Tone) {
      Tone.getTransport().stop()
      Tone.getTransport().cancel()
    }
    seqRef.current?.dispose()
    seqRef.current = null
    setPlaying(false)
    setStep(-1)
  }

  // Leaving the toy stops the music.
  useEffect(() => () => stop(), [])

  const start = async () => {
    const Tone = await unlockAudio()
    toneRef.current = Tone
    const transport = Tone.getTransport()
    transport.bpm.value = tempo
    seqRef.current?.dispose()
    seqRef.current = new Tone.Sequence(
      (time, i) => {
        ROWS.forEach((name, r) => {
          if (beatRef.current[r][i]) play(name, time)
        })
        Tone.getDraw().schedule(() => setStep(i), time)
      },
      [...Array(STEPS).keys()],
      '8n',
    ).start(0)
    transport.start('+0.05')
    setPlaying(true)
  }

  const toggle = (r, i) => {
    const on = !beatRef.current[r][i]
    const next = beatRef.current.map((row) => row.slice())
    next[r][i] = on
    beatRef.current = next
    setBeat(next)
    buzz(5)
    // While stopped, a square you turn on plays its sound so you hear it.
    if (on && !playing) play(ROWS[r])
  }

  const surprise = () => {
    const density = [0.3, 0.3, 0.12, 0.4]
    setBeat(ROWS.map((_, r) => Array.from({ length: STEPS }, (_, i) => (r === 3 && i % 4 === 0) || Math.random() < density[r])))
  }

  const onKeyDown = (e) => {
    const [r, i] = focus
    const rtl = document.documentElement.dir === 'rtl'
    const moves = {
      ArrowRight: [0, rtl ? -1 : 1],
      ArrowLeft: [0, rtl ? 1 : -1],
      ArrowDown: [1, 0],
      ArrowUp: [-1, 0],
    }
    const move = moves[e.key]
    if (!move) return
    e.preventDefault()
    const next = [
      Math.min(ROWS.length - 1, Math.max(0, r + move[0])),
      Math.min(STEPS - 1, Math.max(0, i + move[1])),
    ]
    setFocus(next)
    cells.current[next.join('-')]?.focus()
  }

  return (
    <div className="toy beats">
      <div className="beat-grid" role="grid" aria-label={t('backstage.beats.grid')} onKeyDown={onKeyDown}>
        {ROWS.map((name, r) => (
          <div className="beat-row" role="row" key={name}>
            <span className="beat-label" role="rowheader">
              {t(`backstage.beats.${name}`)}
            </span>
            {beat[r].map((on, i) => {
              const isFocus = focus[0] === r && focus[1] === i
              return (
                <motion.button
                  key={i}
                  ref={(el) => {
                    cells.current[`${r}-${i}`] = el
                  }}
                  type="button"
                  role="gridcell"
                  aria-pressed={on}
                  aria-label={t('backstage.beats.cell', { sound: t(`backstage.beats.${name}`), n: i + 1 })}
                  tabIndex={isFocus ? 0 : -1}
                  className={`beat-cell row-${name}${on ? ' is-on' : ''}${step === i ? ' is-now' : ''}${i % 4 === 0 ? ' is-downbeat' : ''}`}
                  onFocus={() => setFocus([r, i])}
                  onClick={() => toggle(r, i)}
                  animate={step === i && on && !calm ? { scale: [1, 1.14, 1] } : { scale: 1 }}
                  transition={{ duration: 0.22 }}
                />
              )
            })}
          </div>
        ))}
      </div>
      <div className="toy-bar">
        <button type="button" className="bs-btn primary" onClick={playing ? stop : start} aria-pressed={playing}>
          {t(playing ? 'backstage.beats.stop' : 'backstage.beats.play')}
        </button>
        <label className="beat-tempo">
          <span>{t('backstage.beats.tempo', { n: tempo })}</span>
          <input
            type="range"
            min={TEMPO.min}
            max={TEMPO.max}
            step="1"
            value={tempo}
            onChange={(e) => setTempo(Number(e.target.value))}
          />
        </label>
        <button type="button" className="bs-btn" onClick={surprise}>
          {t('backstage.beats.surprise')}
        </button>
        <button type="button" className="bs-btn ghost" onClick={() => setBeat(empty())}>
          {t('backstage.beats.clear')}
        </button>
      </div>
      <p className="toy-hint">{t('backstage.beats.hint')}</p>
    </div>
  )
}
