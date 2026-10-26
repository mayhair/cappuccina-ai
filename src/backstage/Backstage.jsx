import { useEffect, useRef, useState } from 'react'
import { useLanguage } from '../i18n/LanguageProvider'
import { canVibrate, setPref, usePrefs } from './prefs'
import { Pirouette } from './toys/Pirouette'
import { FoamBubbles } from './toys/FoamBubbles'
import { LatteArt } from './toys/LatteArt'
import { CafeBeats } from './toys/CafeBeats'

const TOYS = [
  { id: 'spin', Toy: Pirouette, Icon: SpinIcon },
  { id: 'bubbles', Toy: FoamBubbles, Icon: BubblesIcon },
  { id: 'latte', Toy: LatteArt, Icon: CupIcon },
  { id: 'beats', Toy: CafeBeats, Icon: NoteIcon },
]

// The open toy lives in the URL hash (#beats), so a toy can be linked to and
// the back button steps between toys.
function toyFromHash() {
  const id = window.location.hash.slice(1)
  return TOYS.some((toy) => toy.id === id) ? id : TOYS[0].id
}

// Back to the chat the user came from, when they came from one.
function backHref() {
  try {
    const ref = new URL(document.referrer)
    if (ref.origin === window.location.origin && ref.pathname.startsWith('/c/')) return ref.pathname
  } catch {
    // no referrer
  }
  return '/'
}

// Follow the app's theme: its saved choice, else the device setting. The
// first paint is already right (backstage/index.html sets it); this keeps it
// right when the device or another tab changes it.
function useAppTheme() {
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => {
      let saved = null
      try {
        saved = localStorage.getItem('cappuccina-theme')
      } catch {
        // storage unavailable
      }
      const dark = saved === 'dark' || (saved !== 'light' && media.matches)
      document.documentElement.dataset.theme = dark ? 'dark' : 'light'
      const pageBg = getComputedStyle(document.documentElement).getPropertyValue('--page-bg').trim()
      if (pageBg) document.querySelector('meta[name="theme-color"]')?.setAttribute('content', pageBg)
    }
    apply()
    media.addEventListener('change', apply)
    window.addEventListener('storage', apply)
    return () => {
      media.removeEventListener('change', apply)
      window.removeEventListener('storage', apply)
    }
  }, [])
}

export function Backstage() {
  const { t } = useLanguage()
  const prefs = usePrefs()
  const [toy, setToy] = useState(toyFromHash)
  const [back] = useState(backHref)
  const tabRefs = useRef({})
  useAppTheme()

  useEffect(() => {
    const onHash = () => setToy(toyFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const choose = (id) => {
    if (id === toy) return
    window.location.hash = id
    setToy(id)
  }

  // Arrow keys move between tabs, as in any tab list.
  const onTabKey = (e) => {
    const i = TOYS.findIndex((x) => x.id === toy)
    const step = { ArrowRight: 1, ArrowLeft: -1, Home: -i, End: TOYS.length - 1 - i }[e.key]
    if (step === undefined) return
    e.preventDefault()
    const rtl = document.documentElement.dir === 'rtl' && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')
    const next = TOYS[(i + (rtl ? -step : step) + TOYS.length) % TOYS.length].id
    choose(next)
    tabRefs.current[next]?.focus()
  }

  const { Toy } = TOYS.find((x) => x.id === toy)

  return (
    <div className={`backstage${prefs.calm ? ' is-calm' : ''}`}>
      <div className="bs-sky" aria-hidden="true" />
      <header className="bs-header">
        <a className="bs-back" href={back}>
          <BackIcon aria-hidden="true" />
          {t('backstage.back')}
        </a>
        <div className="bs-heading">
          <h1>{t('backstage.title')}</h1>
          <p>{t('backstage.subtitle')}</p>
        </div>
      </header>

      <div className="bs-tabs" role="tablist" aria-label={t('backstage.toys')} onKeyDown={onTabKey}>
        {TOYS.map(({ id, Icon }) => (
          <button
            key={id}
            ref={(el) => {
              tabRefs.current[id] = el
            }}
            type="button"
            role="tab"
            id={`tab-${id}`}
            aria-selected={id === toy}
            aria-controls="bs-panel"
            tabIndex={id === toy ? 0 : -1}
            className="bs-tab"
            onClick={() => choose(id)}
          >
            <Icon aria-hidden="true" />
            <span>{t(`backstage.toy.${id}`)}</span>
          </button>
        ))}
      </div>

      <main className="bs-stage" id="bs-panel" role="tabpanel" aria-labelledby={`tab-${toy}`}>
        <Toy key={toy} calm={prefs.calm} />
      </main>

      <section className="bs-prefs" aria-label={t('backstage.prefs')}>
        <label className="bs-switch">
          <input type="checkbox" checked={prefs.sound} onChange={(e) => setPref('sound', e.target.checked)} />
          <span className="bs-switch-track" aria-hidden="true" />
          {t('backstage.sound')}
        </label>
        <label className="bs-volume">
          <span className="sr-only">{t('backstage.volume')}</span>
          <SpeakerIcon aria-hidden="true" />
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={prefs.volume}
            disabled={!prefs.sound}
            onChange={(e) => setPref('volume', Number(e.target.value))}
          />
        </label>
        {canVibrate && (
          <label className="bs-switch">
            <input type="checkbox" checked={prefs.haptics} onChange={(e) => setPref('haptics', e.target.checked)} />
            <span className="bs-switch-track" aria-hidden="true" />
            {t('backstage.vibration')}
          </label>
        )}
        <label className="bs-switch" title={t('backstage.calmHint')}>
          <input type="checkbox" checked={prefs.calm} onChange={(e) => setPref('calm', e.target.checked)} />
          <span className="bs-switch-track" aria-hidden="true" />
          {t('backstage.calm')}
        </label>
      </section>
    </div>
  )
}

function BackIcon(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M14.5 6 8.5 12l6 6" />
    </svg>
  )
}

function SpinIcon(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M20 12a8 8 0 1 1-2.6-5.9" />
      <path d="M20 4v4h-4" />
      <circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none" />
    </svg>
  )
}

function BubblesIcon(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" {...props}>
      <circle cx="8.5" cy="14.5" r="5" />
      <circle cx="17" cy="8" r="3.4" />
      <circle cx="17.5" cy="17.5" r="2" />
    </svg>
  )
}

function CupIcon(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M4 9h13v4a6 6 0 0 1-6 6h-1a6 6 0 0 1-6-6Z" />
      <path d="M17 10.5h1.5a2.5 2.5 0 0 1 0 5H16.6" />
      <path d="M8.5 3.5c-.7 1 .7 1.8 0 3M12.5 3.5c-.7 1 .7 1.8 0 3" />
    </svg>
  )
}

function NoteIcon(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M9 18V5l11-2v13" />
      <circle cx="6.5" cy="18" r="2.5" />
      <circle cx="17.5" cy="16" r="2.5" />
    </svg>
  )
}

function SpeakerIcon(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4Z" />
      <path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" />
    </svg>
  )
}
