import { useEffect, useRef, useState } from 'react'
import { ThemeToggle } from './ThemeToggle'
import { SparkleIcon, ChatIcon, CapIcon, BulbIcon, GlobeIcon } from './Icons'
import { GoogleButton } from './GoogleButton'
import { EmailAuthForm } from './EmailAuthForm'
import { AUTH_OPTIONS as options } from '../config'
import { LanguageGate } from './LanguageGate'
import { useLanguage } from '../i18n/LanguageProvider'

// Codes the backend can send as ?auth_error=; each has an `auth.<code>` string.
const AUTH_ERROR_CODES = [
  'google_not_configured',
  'invalid_state',
  'missing_code',
  'exchange_failed',
  'email_unverified',
  'access_denied',
  'account_error',
]

export function Sky({ theme }) {
  return (
    <div className="sky" aria-hidden="true">
      <img src="/plates/sky-band.webp" alt="" style={{ opacity: theme === 'light' ? 1 : 0 }} />
      <img src="/plates/sky-dark.webp" alt="" className="dark" style={{ opacity: theme === 'dark' ? 1 : 0 }} />
    </div>
  )
}

export function LandingPage({ theme, onTheme, error, onSignedIn }) {
  const { t } = useLanguage()
  const [pickerOpen, setPickerOpen] = useState(false)
  const [emailOpen, setEmailOpen] = useState(false)
  // The hero column stretches to the grid row and stands Cappuccina at its
  // bottom. The email form makes the row taller, so before it opens the column
  // is fixed at its current height and only the page below her grows.
  const heroRef = useRef(null)
  const [heroHeight, setHeroHeight] = useState(null)
  const pinned = heroHeight != null

  const openEmail = () => {
    setHeroHeight(heroRef.current?.offsetHeight ?? null)
    setEmailOpen(true)
  }

  // On resize, refit the column to the height the grid would have without the
  // form: the page minus the top bar, the folklore card and the grid padding.
  useEffect(() => {
    if (!pinned) return
    const refit = () => {
      const hero = heroRef.current
      const grid = hero?.parentElement
      const page = grid?.parentElement
      if (!page) return
      const style = getComputedStyle(grid)
      const available =
        page.clientHeight -
        (page.querySelector('.landing-top')?.offsetHeight ?? 0) -
        (page.querySelector('.landing-folklore')?.offsetHeight ?? 0) -
        parseFloat(style.paddingTop) -
        parseFloat(style.paddingBottom)
      setHeroHeight(Math.max(available, hero.querySelector('img')?.offsetHeight ?? 0))
    }
    window.addEventListener('resize', refit)
    return () => window.removeEventListener('resize', refit)
  }, [pinned])

  return (
    <>
      <header className="landing-top">
        <button
          type="button"
          className="lang-button"
          aria-label={t('language.label')}
          title={t('language.label')}
          onClick={() => setPickerOpen(true)}
        >
          <GlobeIcon />
        </button>
        <ThemeToggle theme={theme} onChange={onTheme} />
      </header>

      {pickerOpen && <LanguageGate forceOpen onClose={() => setPickerOpen(false)} />}

      <main className="landing-grid">
        <div
          ref={heroRef}
          className={`landing-hero${pinned ? ' is-pinned' : ''}`}
          style={pinned ? { '--hero-height': `${heroHeight}px` } : undefined}
        >
          <img src="/plates/ballerina-hero.webp" alt={t('landing.heroAlt')} />
        </div>

        <div className="landing-copy">
          <h1 className="landing-title">{t('landing.title')}</h1>
          <p className="landing-sub">{t('landing.sub')}</p>

          <div className="landing-actions">
            {options.google && <GoogleButton label={t('landing.google')} />}
            {options.password && options.google && (
              <div className="landing-or" aria-hidden="true">
                <span>{t('landing.or')}</span>
              </div>
            )}
            {options.password &&
              (emailOpen || !options.google ? (
                <EmailAuthForm
                  theme={theme}
                  turnstileSiteKey={options.turnstileSiteKey}
                  onSignedIn={onSignedIn}
                />
              ) : (
                <button
                  type="button"
                  className="landing-secondary"
                  onClick={openEmail}
                >
                  {t('landing.email')}
                </button>
              ))}
            <p className="landing-legal">
              {t('landing.legal.before')}
              <a href="/terms" target="_blank" rel="noopener">{t('landing.legal.terms')}</a>
              {t('landing.legal.and')}
              <a href="/privacy" target="_blank" rel="noopener">{t('landing.legal.privacy')}</a>
              {t('landing.legal.after')}
            </p>
          </div>

          {error && (
            <p className="auth-error" role="alert">
              {AUTH_ERROR_CODES.includes(error) ? t(`auth.${error}`) : t('auth.default')}
            </p>
          )}

          <p className="landing-note">
            <SparkleIcon />
            {t('landing.note')}
          </p>

          <div className="landing-pills" aria-hidden="true">
            <span className="landing-pill"><ChatIcon /> {t('landing.pill.talk')}</span>
            <span className="landing-pill"><CapIcon /> {t('landing.pill.homework')}</span>
            <span className="landing-pill"><BulbIcon /> {t('landing.pill.curious')}</span>
          </div>
        </div>
      </main>

      <footer className="landing-folklore">
        <div className="folklore-card">
          <p className="folklore-title">{t('landing.folklore.title')}</p>
          <p>
            <span className="folklore-line">{t('landing.folklore.1')}</span>
            <span className="folklore-line">
              {t('landing.folklore.2')}{' '}
              <a
                href="https://medium.com/@fabianmosele/can-tung-tung-tung-sahur-and-ballerina-cappuccina-have-copyright-a2dc01e850e9"
                target="_blank"
                rel="noopener noreferrer"
              >
                {t('landing.folklore.learn')}
              </a>
            </span>
            <span className="folklore-line folklore-aside">{t('landing.folklore.3')}</span>
          </p>
        </div>
      </footer>
    </>
  )
}
