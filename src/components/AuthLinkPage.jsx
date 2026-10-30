import { useEffect, useRef, useState } from 'react'
import { confirmEmail, resetPassword } from '../api/authClient'
import { useLanguage } from '../i18n/LanguageProvider'
import { authErrorText } from './EmailAuthForm'

export const AUTH_LINK_PATHS = ['/confirm-email', '/reset-password']

// The token arrives in the query string. It is read once, then dropped from
// the address bar so it doesn't linger in history or end up in a screenshot.
const readToken = () => new URLSearchParams(window.location.search).get('token') || ''

/**
 * Where the links in confirmation and reset emails land. Confirming POSTs the
 * token straight away (so a mail scanner that only opens the link can't use
 * it up); a reset asks for the new password first. Both end signed in.
 */
export function AuthLinkPage({ path, onSignedIn, onLeave }) {
  const { t, lang } = useLanguage()
  const [token] = useState(readToken)
  const confirming = path === '/confirm-email'
  // working | form | error
  const [phase, setPhase] = useState(confirming ? 'working' : 'form')
  const [error, setError] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const started = useRef(false)

  useEffect(() => {
    if (window.location.search) {
      window.history.replaceState(window.history.state, '', window.location.pathname)
    }
  }, [])

  useEffect(() => {
    if (!confirming || started.current) return
    started.current = true
    if (!token) {
      setError(t('auth.email.link_invalid'))
      setPhase('error')
      return
    }
    confirmEmail(token)
      .then(onSignedIn)
      .catch((err) => {
        setError(authErrorText(t, err))
        setPhase('error')
      })
  }, [confirming, token, onSignedIn, t])

  const submitReset = async (e) => {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      onSignedIn(await resetPassword({ token, newPassword: password, lang }))
    } catch (err) {
      setError(authErrorText(t, err))
      if (err?.code === 'link_invalid') setPhase('error')
      setBusy(false)
    }
  }

  return (
    <main className="auth-link-page">
      <div className="email-auth auth-link-card">
        {phase === 'working' && (
          <p className="email-auth-title" aria-live="polite">{t('auth.link.confirming')}</p>
        )}

        {phase === 'form' && (
          <form onSubmit={submitReset} className="email-auth-stack">
            <p className="email-auth-title">{t('auth.link.resetTitle')}</p>
            <label className="email-auth-field">
              <span>{t('auth.link.newPassword')}</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                minLength={8}
                maxLength={256}
                required
                autoFocus
              />
              <small>{t('auth.email.passwordHint')}</small>
            </label>
            {error && <p className="auth-error" role="alert">{error}</p>}
            <button type="submit" className="email-auth-submit" disabled={busy}>
              {busy ? t('auth.email.working') : t('auth.link.save')}
            </button>
          </form>
        )}

        {phase === 'error' && (
          <>
            <p className="email-auth-title">{t('auth.link.failedTitle')}</p>
            <p className="auth-error" role="alert">{error}</p>
            <p className="email-auth-text">
              {t(confirming ? 'auth.link.failedConfirm' : 'auth.link.failedReset')}
            </p>
            <button type="button" className="email-auth-submit" onClick={onLeave}>
              {t('auth.link.home')}
            </button>
          </>
        )}
      </div>
    </main>
  )
}
