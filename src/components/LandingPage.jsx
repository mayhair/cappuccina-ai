import { ThemeToggle } from './ThemeToggle'
import { SparkleIcon, ChatIcon, CapIcon, BulbIcon } from './Icons'
import { GoogleButton } from './GoogleButton'

const AUTH_ERROR_MESSAGES = {
  google_not_configured: 'Google sign-in isn\u2019t set up yet.',
  invalid_state: 'That sign-in link expired. Please try again.',
  missing_code: 'Google didn\u2019t finish signing you in. Please try again.',
  exchange_failed: 'We couldn\u2019t reach Google. Please try again.',
  email_unverified: 'That Google account\u2019s email isn\u2019t verified.',
  access_denied: 'Sign-in was cancelled.',
  account_error: 'We couldn\u2019t open your account. Please try again.',
}

export function Sky({ theme }) {
  return (
    <div className="sky" aria-hidden="true">
      <img src="/plates/sky-band.png" alt="" style={{ opacity: theme === 'light' ? 1 : 0 }} />
      <img src="/plates/sky-dark.png" alt="" className="dark" style={{ opacity: theme === 'dark' ? 1 : 0 }} />
    </div>
  )
}

export function LandingPage({ theme, onTheme, error }) {
  return (
    <>
      <header className="landing-top">
        <ThemeToggle theme={theme} onChange={onTheme} />
      </header>

      <main className="landing-grid">
        <div className="landing-hero">
          <img
            src="/plates/ballerina-hero.png"
            alt="Ballerina Cappuccina, a cartoon ballerina with a cappuccino cup for a head, waving hello"
          />
        </div>

        <div className="landing-copy">
          <h1 className="landing-title">Hi! I'm Ballerina Cappuccina</h1>
          <p className="landing-sub">
            Your AI chat friend — ask me anything, or let's learn together
          </p>

          <div className="landing-actions">
            <GoogleButton label="Continue with Google" />
          </div>

          {error && (
            <p className="auth-error" role="alert">
              {AUTH_ERROR_MESSAGES[error] ?? 'Sign-in didn\u2019t work. Please try again.'}
            </p>
          )}

          <p className="landing-note">
            <SparkleIcon />
            I&rsquo;m an AI chatbot, not a person
          </p>

          <div className="landing-pills" aria-hidden="true">
            <span className="landing-pill"><ChatIcon /> Just talking</span>
            <span className="landing-pill"><CapIcon /> Homework help</span>
            <span className="landing-pill"><BulbIcon /> Curious questions</span>
          </div>
        </div>
      </main>
    </>
  )
}
