import { useEffect, useState } from 'react'
import { PersonIcon, MailIcon } from './Icons'
import { updateProfile } from '../api/authClient'
import { connectGmailUrl, disconnectGmail, gmailStatus } from '../api/gmailClient'

function ProfileForm({ user, onUpdated }) {
  const [name, setName] = useState(user.displayName)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  const dirty = name !== user.displayName

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    setSaved(false)
    try {
      const updated = await updateProfile({ name })
      onUpdated(updated)
      setSaved(true)
    } catch (err) {
      setError(err.message || 'Something went wrong — try again')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="settings-form" onSubmit={submit}>
      <div className="settings-row">
        <label className="settings-label" htmlFor="settings-name">
          Display name
        </label>
        <input
          id="settings-name"
          className="settings-input"
          type="text"
          value={name}
          onChange={(e) => {
            setName(e.target.value)
            setSaved(false)
          }}
          autoComplete="name"
          placeholder="What should I call you?"
        />
      </div>
      {error && (
        <p className="settings-msg error" role="alert">
          {error}
        </p>
      )}
      {saved && !error && <p className="settings-msg ok">Name updated ✓</p>}
      <div className="settings-actions">
        <button type="submit" className="settings-save" disabled={busy || !dirty}>
          {busy ? 'Saving…' : 'Save name'}
        </button>
      </div>
    </form>
  )
}

function GmailForm({ user, notice, onNoticeClear, onUserUpdated }) {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    gmailStatus()
      .then((s) => {
        if (!cancelled) setStatus(s)
      })
      .catch(() => {
        if (!cancelled) setStatus({ connected: false })
      })
    return () => {
      cancelled = true
    }
  }, [])

  const disconnect = async () => {
    setBusy(true)
    setError('')
    try {
      await disconnectGmail()
      setStatus({ connected: false })
      onUserUpdated({ ...user, gmailConnected: false })
      onNoticeClear()
    } catch (err) {
      setError(err.message || 'Could not disconnect — try again')
    } finally {
      setBusy(false)
    }
  }

  const connected = status?.connected ?? user.gmailConnected

  return (
    <div className="settings-form">
      <p className="settings-hint">
        Let Cappuccina read your inbox and write email drafts. She can never send
        email — every draft waits for you to press Send.
      </p>

      {notice === 'connected' && (
        <p className="settings-msg ok">Gmail connected ✓</p>
      )}
      {notice === 'error' && (
        <p className="settings-msg error" role="alert">
          Connecting Gmail didn&rsquo;t finish. Please try again.
        </p>
      )}
      {error && (
        <p className="settings-msg error" role="alert">
          {error}
        </p>
      )}

      <p className="settings-status">
        <MailIcon />
        {connected ? 'Gmail is connected' : 'Gmail is not connected'}
      </p>

      <div className="settings-actions">
        {connected ? (
          <button
            type="button"
            className="settings-save"
            onClick={disconnect}
            disabled={busy || status === null}
          >
            {busy ? 'Disconnecting…' : 'Disconnect Gmail'}
          </button>
        ) : (
          <a className="settings-save" href={connectGmailUrl}>
            Connect Gmail
          </a>
        )}
      </div>
    </div>
  )
}

export function SettingsDialog({
  user,
  open,
  onClose,
  onUserUpdated,
  gmailNotice = '',
  onGmailNoticeClear = () => {},
}) {
  const [section, setSection] = useState('account')

  useEffect(() => {
    if (!open) return
    setSection('account')
    const esc = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className="settings-veil"
      role="dialog"
      aria-modal="true"
      aria-label="Settings"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="settings">
        <aside className="settings-nav">
          <button
            type="button"
            className="settings-close"
            aria-label="Close settings"
            onClick={onClose}
          >
            ✕
          </button>
          <button
            type="button"
            className={`settings-nav-item${section === 'account' ? ' active' : ''}`}
            onClick={() => setSection('account')}
          >
            <PersonIcon />
            Account
          </button>
          <button
            type="button"
            className={`settings-nav-item${section === 'connections' ? ' active' : ''}`}
            onClick={() => setSection('connections')}
          >
            <MailIcon />
            Connections
          </button>
        </aside>
        <div className="settings-pane">
          {section === 'account' ? (
            <>
              <h3 className="settings-heading">Account</h3>
              <section className="settings-section">
                <h4>Profile</h4>
                <ProfileForm user={user} onUpdated={onUserUpdated} />
              </section>
              <section className="settings-section">
                <h4>Sign-in</h4>
                <p className="settings-hint">
                  You sign in with your Google account ({user.email}).
                </p>
              </section>
            </>
          ) : (
            <>
              <h3 className="settings-heading">Connections</h3>
              <section className="settings-section">
                <h4>Gmail</h4>
                <GmailForm
                  user={user}
                  notice={gmailNotice}
                  onNoticeClear={onGmailNoticeClear}
                  onUserUpdated={onUserUpdated}
                />
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
