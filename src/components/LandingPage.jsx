import { ThemeToggle } from './ThemeToggle'
import { SparkleIcon, ChatIcon, CapIcon, BulbIcon } from './Icons'

export function Sky({ theme }) {
  return (
    <div className="sky" aria-hidden="true">
      <img src="/plates/sky-band.png" alt="" style={{ opacity: theme === 'light' ? 1 : 0 }} />
      <img src="/plates/sky-dark.png" alt="" className="dark" style={{ opacity: theme === 'dark' ? 1 : 0 }} />
    </div>
  )
}

export function LandingPage({ theme, onTheme, onStart, onLogin }) {
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
          <h1 className="landing-title">Hi! I&rsquo;m Ballerina Cappuccina</h1>
          <p className="landing-sub">
            Your AI chat friend — ask me anything, or let&rsquo;s learn together
          </p>

          <div className="landing-actions">
            <button type="button" className="landing-cta" onClick={onStart}>
              Create an account
            </button>
            <button type="button" className="landing-secondary" onClick={onLogin}>
              Log in
            </button>
          </div>

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
