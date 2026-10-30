import { useEffect, useRef, useState } from 'react'
import {
  forgotPassword,
  login,
  register,
  resendConfirmation,
} from '../api/authClient'
import { useLanguage } from '../i18n/LanguageProvider'
import { Turnstile } from './Turnstile'

// Error codes the server sends as detail.code; each has an `auth.email.<code>` string.
const ERROR_CODES = [
  'invalid_email',
  'password_too_short',
  'password_too_long',
  'turnstile_failed',
  'email_unconfirmed',
  'bad_credentials',
  'mail_failed',
  'link_invalid',
  'wrong_password',
]

export function authErrorText(t, err) {
  if (err?.status === 429) return t('auth.email.rate_limited')
  if (ERROR_CODES.includes(err?.code)) return t(`auth.email.${err.code}`)
  return t('auth.default')
}

const RESEND_COOLDOWN_MS = 30_000

/**
 * Email + password sign-in, sign-up and "forgot password", shown on the
 * landing page under the Google button. Sign-up and reset end on a "check your
 * inbox" screen; the link in the email finishes them (AuthLinkPage).
 */
export function EmailAuthForm({ theme, turnstileSiteKey, onSignedIn }) {
  const { t, lang } = useLanguage()
  // signin | signup | forgot | sent
  const [view, setView] = useState('signin')
  // What the "sent" screen is waiting on: 'confirm' or 'reset'.
  const [sentKind, setSentKind] = useState('confirm')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [captcha, setCaptcha] = useState('')
  const [error, setError] = useState('')
  const [unconfirmed, setUnconfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [resendNote, setResendNote] = useState('')
  // The resend button rests for a while after each email.
  const [cooling, setCooling] = useState(false)
  const turnstileRef = useRef(null)

  useEffect(() => {
    if (!cooling) return
    const timer = setTimeout(() => setCooling(false), RESEND_COOLDOWN_MS)
    return () => clearTimeout(timer)
  }, [cooling])

  const switchView = (to) => {
    setView(to)
    setError('')
    setUnconfirmed(false)
    setResendNote('')
  }

  const showSent = (kind) => {
    setSentKind(kind)
    setCooling(true)
    switchView('sent')
  }

  const run = async (action) => {
    if (busy) return
    setBusy(true)
    setError('')
    setUnconfirmed(false)
    try {
      await action()
    } catch (err) {
      setError(authErrorText(t, err))
      setUnconfirmed(err?.code === 'email_unconfirmed')
    } finally {
      setBusy(false)
    }
  }

  const submit = (e) => {
    e.preventDefault()
    if (view === 'signin') {
      run(async () => onSignedIn(await login({ email, password })))
    } else if (view === 'signup') {
      run(async () => {
        try {
          await register({ email, password, name, turnstileToken: captcha, lang })
        } finally {
          // A Turnstile token is good for one attempt only.
          turnstileRef.current?.reset()
        }
        setPassword('')
        showSent('confirm')
      })
    } else if (view === 'forgot') {
      run(async () => {
        await forgotPassword({ email, lang })
        showSent('reset')
      })
    }
  }

  const resend = () =>
    run(async () => {
      if (sentKind === 'reset') await forgotPassword({ email, lang })
      else await resendConfirmation({ email, lang })
      setCooling(true)
      setResendNote(t('auth.email.resent'))
    })

  // From "please confirm your email first" on the sign-in form.
  const resendFromSignIn = () =>
    run(async () => {
      await resendConfirmation({ email, lang })
      showSent('confirm')
    })

  if (view === 'sent') {
    return (
      <div className="email-auth" aria-live="polite">
        <p className="email-auth-title">{t('auth.email.sentTitle')}</p>
        <p className="email-auth-text">
          {t(sentKind === 'reset' ? 'auth.email.sentReset' : 'auth.email.sentConfirm', { email })}
        </p>
        <p className="email-auth-hint">{t('auth.email.sentSpam')}</p>
        {error && <p className="auth-error" role="alert">{error}</p>}
        {resendNote && !error && <p className="email-auth-ok">{resendNote}</p>}
        <div className="email-auth-links">
          <button
            type="button"
            className="email-auth-link"
            onClick={resend}
            disabled={busy || cooling}
          >
            {t('auth.email.resend')}
          </button>
          <button type="button" className="email-auth-link" onClick={() => switchView('signin')}>
            {t('auth.email.backToSignIn')}
          </button>
        </div>
      </div>
    )
  }

  const signingUp = view === 'signup'
  const forgot = view === 'forgot'
  const needsCaptcha = signingUp && Boolean(turnstileSiteKey)

  return (
    <form className="email-auth" onSubmit={submit}>
      {!forgot && (
        <div className="email-auth-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={!signingUp}
            className={`email-auth-tab${!signingUp ? ' active' : ''}`}
            onClick={() => switchView('signin')}
          >
            {t('auth.email.signInTab')}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={signingUp}
            className={`email-auth-tab${signingUp ? ' active' : ''}`}
            onClick={() => switchView('signup')}
          >
            {t('auth.email.signUpTab')}
          </button>
        </div>
      )}
      {forgot && (
        <>
          <p className="email-auth-title">{t('auth.email.forgotTitle')}</p>
          <p className="email-auth-text">{t('auth.email.forgotText')}</p>
        </>
      )}

      {signingUp && (
        <label className="email-auth-field">
          <span>{t('auth.email.name')}</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="name"
            maxLength={80}
            placeholder={t('auth.email.namePlaceholder')}
          />
        </label>
      )}

      <label className="email-auth-field">
        <span>{t('auth.email.email')}</span>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          maxLength={254}
          required
        />
      </label>

      {!forgot && (
        <label className="email-auth-field">
          <span>{t('auth.email.password')}</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={signingUp ? 'new-password' : 'current-password'}
            minLength={signingUp ? 8 : undefined}
            maxLength={256}
            required
          />
          {signingUp && <small>{t('auth.email.passwordHint')}</small>}
        </label>
      )}

      {needsCaptcha && (
        <Turnstile
          ref={turnstileRef}
          siteKey={turnstileSiteKey}
          theme={theme}
          lang={lang}
          onToken={setCaptcha}
        />
      )}

      {error && (
        <p className="auth-error" role="alert">
          {error}
          {unconfirmed && (
            <>
              {' '}
              <button type="button" className="email-auth-link inline" onClick={resendFromSignIn}>
                {t('auth.email.resend')}
              </button>
            </>
          )}
        </p>
      )}

      <button
        type="submit"
        className="email-auth-submit"
        disabled={busy || (needsCaptcha && !captcha)}
      >
        {busy
          ? t('auth.email.working')
          : t(signingUp ? 'auth.email.signUp' : forgot ? 'auth.email.sendLink' : 'auth.email.signIn')}
      </button>

      <div className="email-auth-links">
        {view === 'signin' && (
          <button type="button" className="email-auth-link" onClick={() => switchView('forgot')}>
            {t('auth.email.forgot')}
          </button>
        )}
        {forgot && (
          <button type="button" className="email-auth-link" onClick={() => switchView('signin')}>
            {t('auth.email.backToSignIn')}
          </button>
        )}
      </div>
    </form>
  )
}
