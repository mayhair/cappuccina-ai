import { useEffect, useState } from 'react'
import { PersonIcon } from './Icons'
import { changePassword, updateProfile } from '../api/authClient'

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

function PasswordForm() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  const reset = () => {
    setCurrent('')
    setNext('')
    setConfirm('')
  }

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    if (next !== confirm) {
      setError("New passwords don't match")
      return
    }
    setBusy(true)
    setError('')
    setSaved(false)
    try {
      await changePassword({ currentPassword: current, newPassword: next })
      setSaved(true)
      reset()
    } catch (err) {
      setError(err.message || 'Something went wrong — try again')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="settings-form" onSubmit={submit}>
      <div className="settings-row">
        <label className="settings-label" htmlFor="settings-current">
          Current password
        </label>
        <input
          id="settings-current"
          className="settings-input"
          type="password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          autoComplete="current-password"
          required
        />
      </div>
      <div className="settings-row">
        <label className="settings-label" htmlFor="settings-new">
          New password
        </label>
        <input
          id="settings-new"
          className="settings-input"
          type="password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          autoComplete="new-password"
          minLength={8}
          required
        />
      </div>
      <div className="settings-row">
        <label className="settings-label" htmlFor="settings-confirm">
          Confirm new password
        </label>
        <input
          id="settings-confirm"
          className="settings-input"
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password"
          minLength={8}
          required
        />
      </div>
      {error && (
        <p className="settings-msg error" role="alert">
          {error}
        </p>
      )}
      {saved && !error && <p className="settings-msg ok">Password changed ✓</p>}
      <div className="settings-actions">
        <button type="submit" className="settings-save" disabled={busy}>
          {busy ? 'Saving…' : 'Change password'}
        </button>
      </div>
    </form>
  )
}

export function SettingsDialog({ user, open, onClose, onUserUpdated }) {
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
        </aside>
        <div className="settings-pane">
          <h3 className="settings-heading">Account</h3>
          <section className="settings-section">
            <h4>Profile</h4>
            <ProfileForm user={user} onUpdated={onUserUpdated} />
          </section>
          <section className="settings-section">
            <h4>Password</h4>
            <PasswordForm />
          </section>
        </div>
      </div>
    </div>
  )
}
