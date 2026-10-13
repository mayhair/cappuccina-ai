import { useState } from 'react'
import { ThemeToggle } from './ThemeToggle'
import { SparkleIcon, ChatIcon, CapIcon, BulbIcon, GlobeIcon } from './Icons'
import { GoogleButton } from './GoogleButton'
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

export function LandingPage({ theme, onTheme, error }) {
  const { t } = useLanguage()
  const [pickerOpen, setPickerOpen] = useState(false)

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
        <div className="landing-hero">
          <img src="/plates/ballerina-hero.webp" alt={t('landing.heroAlt')} />
        </div>

        <div className="landing-copy">
          <h1 className="landing-title">{t('landing.title')}</h1>
          <p className="landing-sub">{t('landing.sub')}</p>

          <div className="landing-actions">
            <GoogleButton label={t('landing.google')} />
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
