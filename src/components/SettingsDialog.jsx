import { useEffect, useState } from 'react'
import { PersonIcon, MailIcon, CalendarIcon, ShieldIcon } from './Icons'
import { updateProfile, setAdultMode } from '../api/authClient'
import { isAdultBirthday } from '../lib/adultMode'
import { connectGmailUrl, disconnectGmail, gmailStatus } from '../api/gmailClient'
import {
  connectCalendarUrl,
  calendarStatus,
  disconnectCalendar,
} from '../api/calendarClient'

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

function AdultModeForm({ user, onUpdated }) {
  const [birthday, setBirthday] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const adult = Boolean(user.adultMode)

  const save = async (enabled) => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      onUpdated(await setAdultMode(enabled))
    } catch (err) {
      setError(err.message || 'Something went wrong — try again')
    } finally {
      setBusy(false)
    }
  }

  const submit = (e) => {
    e.preventDefault()
    if (!isAdultBirthday(birthday)) {
      setError('That birthday isn\u2019t 18 or older — check the date and try again.')
      return
    }
    setBirthday('') // the date is never kept
    save(true)
  }

  if (adult) {
    return (
      <div className="settings-form">
        <p className="settings-status">
          <ShieldIcon />
          Adult mode is on
        </p>
        <p className="settings-hint">
          Cappuccina won&rsquo;t assume you&rsquo;re a minor and treats adult topics
          plainly. She&rsquo;s still an AI and still says when she isn&rsquo;t sure.
        </p>
        {error && (
          <p className="settings-msg error" role="alert">
            {error}
          </p>
        )}
        <div className="settings-actions">
          <button type="button" className="settings-save" onClick={() => save(false)} disabled={busy}>
            {busy ? 'Turning off…' : 'Turn off adult mode'}
          </button>
        </div>
      </div>
    )
  }

  return (
    <form className="settings-form" onSubmit={submit}>
      <p className="settings-hint">
        Adult mode loosens Cappuccina&rsquo;s safety boundaries. To turn it on, confirm
        you&rsquo;re 18 or older by entering your birthday — it&rsquo;s checked in your
        browser and never saved.
      </p>
      <div className="settings-row">
        <label className="settings-label" htmlFor="settings-birthday">
          Your birthday
        </label>
        <input
          id="settings-birthday"
          className="settings-input"
          type="date"
          value={birthday}
          onChange={(e) => {
            setBirthday(e.target.value)
            setError('')
          }}
        />
      </div>
      {error && (
        <p className="settings-msg error" role="alert">
          {error}
        </p>
      )}
      <div className="settings-actions">
        <button type="submit" className="settings-save" disabled={busy || !birthday}>
          {busy ? 'Enabling…' : 'Enable adult mode'}
        </button>
      </div>
    </form>
  )
}

function ConnectionForm({
  icon: Icon,
  serviceName,
  description,
  connectUrl,
  loadStatus,
  disconnect,
  connectedKey,
  user,
  notice,
  onNoticeClear,
  onUserUpdated,
}) {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    loadStatus()
      .then((s) => {
        if (!cancelled) setStatus(s)
      })
      .catch(() => {
        if (!cancelled) setStatus({ connected: false })
      })
    return () => {
      cancelled = true
    }
  }, [loadStatus])

  const handleDisconnect = async () => {
    setBusy(true)
    setError('')
    try {
      await disconnect()
      setStatus({ connected: false })
      onUserUpdated({ ...user, [connectedKey]: false })
      onNoticeClear()
    } catch (err) {
      setError(err.message || 'Could not disconnect — try again')
    } finally {
      setBusy(false)
    }
  }

  const connected = status?.connected ?? user[connectedKey]

  return (
    <div className="settings-form">
      <p className="settings-hint">{description}</p>

      {notice === 'connected' && (
        <p className="settings-msg ok">{serviceName} connected ✓</p>
      )}
      {notice === 'error' && (
        <p className="settings-msg error" role="alert">
          Connecting {serviceName} didn&rsquo;t finish. Please try again.
        </p>
      )}
      {error && (
        <p className="settings-msg error" role="alert">
          {error}
        </p>
      )}

      <p className="settings-status">
        <Icon />
        {connected
          ? `${serviceName} is connected`
          : `${serviceName} is not connected`}
      </p>

      <div className="settings-actions">
        {connected ? (
          <button
            type="button"
            className="settings-save"
            onClick={handleDisconnect}
            disabled={busy || status === null}
          >
            {busy ? 'Disconnecting…' : `Disconnect ${serviceName}`}
          </button>
        ) : (
          <a className="settings-save" href={connectUrl}>
            Connect {serviceName}
          </a>
        )}
      </div>
    </div>
  )
}

function GmailForm({ user, notice, onNoticeClear, onUserUpdated }) {
  return (
    <ConnectionForm
      icon={MailIcon}
      serviceName="Gmail"
      description="Let Cappuccina read your inbox and write email drafts. She can never send email — every draft waits for you to press Send."
      connectUrl={connectGmailUrl}
      loadStatus={gmailStatus}
      disconnect={disconnectGmail}
      connectedKey="gmailConnected"
      user={user}
      notice={notice}
      onNoticeClear={onNoticeClear}
      onUserUpdated={onUserUpdated}
    />
  )
}

function CalendarForm({ user, notice, onNoticeClear, onUserUpdated }) {
  return (
    <ConnectionForm
      icon={CalendarIcon}
      serviceName="Google Calendar"
      description="Let Cappuccina see your schedule and add or edit events for you. She can never delete an event on her own — she asks you to confirm first."
      connectUrl={connectCalendarUrl}
      loadStatus={calendarStatus}
      disconnect={disconnectCalendar}
      connectedKey="calendarConnected"
      user={user}
      notice={notice}
      onNoticeClear={onNoticeClear}
      onUserUpdated={onUserUpdated}
    />
  )
}

export function SettingsDialog({
  user,
  open,
  onClose,
  onUserUpdated,
  gmailNotice = '',
  onGmailNoticeClear = () => {},
  calendarNotice = '',
  onCalendarNoticeClear = () => {},
}) {
  const [section, setSection] = useState('account')

  // Reset to the first section only when the dialog opens. `onClose` is a new
  // function on every parent render, so keying this off it would also fire on
  // an unrelated user update (e.g. saving the adult-mode flag) and yank the
  // pane back to Account.
  useEffect(() => {
    if (!open) return
    setSection('account')
  }, [open])

  useEffect(() => {
    if (!open) return
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
          <button
            type="button"
            className={`settings-nav-item${section === 'adult' ? ' active' : ''}`}
            onClick={() => setSection('adult')}
          >
            <ShieldIcon />
            Adult mode
          </button>
        </aside>
        <div className="settings-pane">
          {section === 'account' && (
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
          )}
          {section === 'connections' && (
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
              <section className="settings-section">
                <h4>Google Calendar</h4>
                <CalendarForm
                  user={user}
                  notice={calendarNotice}
                  onNoticeClear={onCalendarNoticeClear}
                  onUserUpdated={onUserUpdated}
                />
              </section>
            </>
          )}
          {section === 'adult' && (
            <>
              <h3 className="settings-heading">Adult mode</h3>
              <section className="settings-section">
                <h4>18+ content</h4>
                <AdultModeForm user={user} onUpdated={onUserUpdated} />
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
