import { useState } from 'react'
import { login, register } from '../api/authClient'
import { ThemeToggle } from './ThemeToggle'

export function AuthView({ mode, theme, onTheme, onSignedIn }) {
  const [signingUp, setSigningUp] = useState(mode === 'signup')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const switchMode = (to) => {
    setSigningUp(to === 'signup')
    setError('')
  }

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const user = signingUp
        ? await register({ email, password, name })
        : await login({ email, password })
      onSignedIn(user)
    } catch (err) {
      setError(err.message || 'Something went wrong — try again')
      setBusy(false)
    }
  }

  return (
    <div className="auth">
      <header className="landing-top">
        <ThemeToggle theme={theme} onChange={onTheme} />
      </header>
      <main className="auth-center">
        <form className="auth-card" onSubmit={submit}>
          <h1 className="welcome-title">
            {signingUp ? 'Create an account' : 'Welcome back'}
          </h1>
          <p className="welcome-sub">Sign in to keep your chats on every device</p>

          {signingUp && (
            <label className="auth-field">
              <span>Name</span>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoComplete="name"
                placeholder="What should I call you?"
                aria-label="Your name"
              />
            </label>
          )}

          <label className="auth-field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              required
              aria-label="Email address"
            />
          </label>

          <label className="auth-field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={signingUp ? 'new-password' : 'current-password'}
              minLength={signingUp ? 8 : undefined}
              required
              aria-label="Password"
            />
          </label>

          {error && (
            <p className="auth-error" role="alert">
              {error}
            </p>
          )}

          <button type="submit" className="landing-cta" disabled={busy}>
            {busy ? 'One moment…' : signingUp ? 'Create an account' : 'Log in'}
          </button>

          <p className="auth-switch">
            {signingUp ? (
              <>
                Already have an account?{' '}
                <button type="button" className="auth-link" onClick={() => switchMode('login')}>
                  Log in
                </button>
              </>
            ) : (
              <>
                New here?{' '}
                <button type="button" className="auth-link" onClick={() => switchMode('signup')}>
                  Create an account
                </button>
              </>
            )}
          </p>
        </form>
      </main>
    </div>
  )
}
